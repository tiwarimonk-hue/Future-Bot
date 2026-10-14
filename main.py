import os
import time
import json
import hmac
import hashlib
import math
import uuid
import threading
import urllib.parse
import requests
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

# Local development ke liye .env support
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

IST = timezone(timedelta(hours=5, minutes=30))
data_lock = threading.Lock()

# Persistent session for HTTP requests
session = requests.Session()

# --- STRATEGY & RISK SETTINGS ---
TRADE_MARGIN_USD = 0.35  
LEVERAGE = 20
POSITION_SIZE_USD = TRADE_MARGIN_USD * LEVERAGE  # $7.0 USD

MIN_24H_VOLUME_USD = 50_000_000  # Minimum $50M liquidity
THRESHOLD_PCT = 0.008            # 0.8% price movement (Pump or Dump)
DROP_TIME_WINDOW_SEC = 5         # 5 seconds window

TP_PCT = 0.015                   # 1.5% Take Profit
HOLD_TIME_SECONDS = 90           # 90 seconds exact hold timeout (Hard Exit)

MAX_CONCURRENT_TRADES = 1        # Strict max 1 trade limit at a time
COIN_COOLDOWN_SECONDS = 120      # 2 minutes strict cooldown per coin

MAX_CONSECUTIVE_LOSSES = 10      # 10 Consecutive Losses Limit
PAUSE_DURATION_SECONDS = 3 * 3600  # 3 Hours pause after 10 losses

tp_hits_count = 0
timeout_exits_count = 0
consecutive_losses = 0
trading_paused_until = 0
clock_offset_ms = 0.0

active_trade_symbols = set()
coin_last_trade_time = {}
price_history = {}  # symbol -> list of (timestamp, price)
exchange_info_cache = {}
open_position_timestamps = {}  # symbol -> open time tracking for watchdog

# Live tracking stats for dashboard
dashboard_data = {
    "status": "INITIALIZING...",
    "active_trades_count": 0,
    "tp_hits": 0,
    "timeout_exits": 0,
    "losses": 0,
    "paused_until": 0,
    "clock_offset_ms": 0.0,
    "scans": [],
    "logs": []
}

def add_ui_log(message):
    timestamp = datetime.now(IST).strftime('%H:%M:%S')
    line = f'[{timestamp}] {message}'
    print(line)
    with data_lock:
        dashboard_data['logs'].insert(0, line)
        if len(dashboard_data['logs']) > 50:
            dashboard_data['logs'].pop()

def add_scan(message):
    with data_lock:
        dashboard_data['scans'].insert(0, message)
        if len(dashboard_data['scans']) > 10:
            dashboard_data['scans'].pop()

API_KEY = os.environ.get('BINANCE_API_KEY')
API_SECRET = os.environ.get('BINANCE_API_SECRET')
BINANCE_FUTURES_URL = "https://fapi.binance.com"

def binance_public_get(endpoint, params=None):
    url = f"{BINANCE_FUTURES_URL}{endpoint}"
    try:
        resp = session.get(url, params=params, timeout=5)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429 or resp.status_code == 418:
            time.sleep(5)
    except Exception:
        pass
    return {}

def binance_signed_request(method, endpoint, params=None):
    if params is None:
        params = {}
    try:
        params['timestamp'] = int(get_synced_time_ms())
        sorted_params = sorted(params.items(), key=lambda x: x[0])
        query_string = urllib.parse.urlencode(sorted_params)
        signature = hmac.new(API_SECRET.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        full_url = f"{BINANCE_FUTURES_URL}{endpoint}?{query_string}&signature={signature}"
        headers = {"X-MBX-APIKEY": API_KEY}
        
        if method == "GET":
            resp = session.get(full_url, headers=headers, timeout=5)
        elif method == "POST":
            resp = session.post(full_url, headers=headers, timeout=5)
        elif method == "DELETE":
            resp = session.delete(full_url, headers=headers, timeout=5)
        
        data = resp.json()
        if isinstance(data, dict) and "code" in data:
            if data["code"] < 0:
                add_ui_log(f"⚠️ Binance API Error [{endpoint}]: {data}")
                if data["code"] == -1003:
                    time.sleep(15)
        return data
    except Exception:
        pass
    return {}

def sync_binance_clock():
    global clock_offset_ms
    try:
        t_send = time.time() * 1000
        res = binance_public_get("/fapi/v1/time")
        t_recv = time.time() * 1000
        if res and 'serverTime' in res:
            server_time = float(res['serverTime'])
            rtt = t_recv - t_send
            estimated_server_time = server_time + (rtt / 2.0)
            clock_offset_ms = estimated_server_time - t_recv
            with data_lock:
                dashboard_data['clock_offset_ms'] = round(clock_offset_ms, 2)
    except Exception:
        pass

def get_synced_time_ms():
    return (time.time() * 1000) + clock_offset_ms

def cancel_all_orders_rest(symbol):
    try:
        binance_signed_request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
    except Exception:
        pass

# ==========================================
# TRADING EXECUTION & STRICT VALIDATION
# ==========================================
def set_leverage(symbol):
    try:
        binance_signed_request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": LEVERAGE})
    except Exception:
        pass

def validate_coin_for_trading(symbol):
    try:
        info = binance_public_get("/fapi/v1/exchangeInfo")
        if isinstance(info, dict) and "symbols" in info:
            for s in info["symbols"]:
                if s.get("symbol") == symbol:
                    if s.get("status") != "TRADING":
                        return False
                    max_lev = 20
                    min_notional = 5.0
                    for f in s.get("filters", []):
                        if f.get("filterType") == "LEVERAGE_FILTER":
                            max_lev = int(f.get("maxLeverage", 20))
                        elif f.get("filterType") == "MIN_NOTIONAL":
                            min_notional = float(f.get("notional", 5.0))
                    if max_lev < LEVERAGE or min_notional > 5.0:
                        return False
                    return True
    except Exception:
        pass
    return False

def fetch_symbol_precision(symbol):
    global exchange_info_cache
    if symbol in exchange_info_cache:
        return exchange_info_cache[symbol]
    
    try:
        info = binance_public_get("/fapi/v1/exchangeInfo")
        if isinstance(info, dict) and "symbols" in info:
            for s in info["symbols"]:
                if s.get("symbol") == symbol:
                    step_size = "0.01"
                    min_qty = "0.01"
                    tick_size = "0.01"
                    for f in s.get("filters", []):
                        if f.get("filterType") == "LOT_SIZE":
                            step_size = f.get("stepSize", "0.01")
                            min_qty = f.get("minQty", "0.01")
                        elif f.get("filterType") == "PRICE_FILTER":
                            tick_size = f.get("tickSize", "0.01")
                    res = (Decimal(step_size), Decimal(min_qty), Decimal(tick_size))
                    exchange_info_cache[symbol] = res
                    return res
    except Exception:
        pass
    return Decimal("0.01"), Decimal("0.01"), Decimal("0.01")

def format_price(price, tick_size):
    try:
        d_price = Decimal(str(price))
        quantized = d_price.quantize(tick_size, rounding=ROUND_DOWN)
        tick_str = f"{tick_size:f}".rstrip("0")
        decimals = len(tick_str.split(".")[1]) if "." in tick_str else 0
        return f"{quantized:.{decimals}f}"
    except Exception:
        return str(price)

def format_qty(qty, step_size):
    try:
        d_qty = Decimal(str(qty))
        d_step = Decimal(str(step_size))
        quantized = (d_qty // d_step) * d_step
        
        step_str = f"{d_step:f}".rstrip("0")
        if "." in step_str:
            decimals = len(step_str.split(".")[1])
            return f"{quantized:.{decimals}f}"
        else:
            return str(int(quantized))
    except Exception:
        return str(qty)

def get_active_positions_count_from_exchange():
    try:
        pos_risk = binance_signed_request("GET", "/fapi/v2/positionRisk")
        if isinstance(pos_risk, list):
            count = 0
            for p in pos_risk:
                try:
                    if float(p.get("positionAmt", 0)) != 0:
                        count += 1
                except ValueError:
                    pass
            return count
    except Exception:
        pass
    return len(active_trade_symbols)

def handle_trade_result(symbol, is_win):
    global tp_hits_count, timeout_exits_count, consecutive_losses, trading_paused_until
    with data_lock:
        active_trade_symbols.discard(symbol)
        open_position_timestamps.pop(symbol, None)
        dashboard_data['active_trades_count'] = get_active_positions_count_from_exchange()
        if is_win:
            tp_hits_count += 1
            consecutive_losses = 0
            dashboard_data['tp_hits'] = tp_hits_count
            dashboard_data['losses'] = consecutive_losses
        else:
            timeout_exits_count += 1
            consecutive_losses += 1
            dashboard_data['timeout_exits'] = timeout_exits_count
            dashboard_data['losses'] = consecutive_losses
    
    if is_win:
        add_ui_log(f"✅ [{symbol}] Trade WON (1.5% TP Hit)! Total TP: {tp_hits_count}")
    else:
        add_ui_log(f"❌ [{symbol}] Trade TIMEOUT (90s Hard Exit)! Losses: {consecutive_losses}/10")
        if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            trading_paused_until = time.time() + PAUSE_DURATION_SECONDS
            add_ui_log("🚨 10 Losses! 3-Hour PAUSE triggered.")

def execute_scalp_trade(symbol, entry_price, trade_side):
    global trading_paused_until
    try:
        if not validate_coin_for_trading(symbol):
            with data_lock:
                active_trade_symbols.discard(symbol)
                dashboard_data['active_trades_count'] = get_active_positions_count_from_exchange()
            return

        set_leverage(symbol)
        
        step_size, min_qty, tick_size = fetch_symbol_precision(symbol)
        
        d_pos_size = Decimal(str(POSITION_SIZE_USD))
        d_entry = Decimal(str(entry_price))
        raw_qty = d_pos_size / d_entry
        
        calc_qty = max(min_qty, raw_qty)
        amount = format_qty(calc_qty, step_size)
        
        action_name = "MARKET BUY (LONG)" if trade_side == "BUY" else "MARKET SELL (SHORT)"
        add_ui_log(f"🟢 Executing {action_name}: {symbol} (Qty: {amount})")
        
        order_res = binance_signed_request("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": trade_side,
            "type": "MARKET",
            "quantity": str(amount)
        })
        
        if not order_res or not isinstance(order_res, dict) or "orderId" not in order_res:
            add_ui_log(f"[{symbol}] Market Order Failed: {order_res}")
            cancel_all_orders_rest(symbol)
            with data_lock:
                active_trade_symbols.discard(symbol)
                dashboard_data['active_trades_count'] = get_active_positions_count_from_exchange()
            return
        
        order_id = order_res.get("orderId")
        time.sleep(0.3)
        
        chk = binance_signed_request("GET", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id})
        fill_price_float = entry_price
        if chk and isinstance(chk, dict) and "avgPrice" in chk:
            try:
                fill_price_float = float(chk.get("avgPrice", entry_price))
            except ValueError:
                pass
        
        add_ui_log(f"⚡ [{symbol}] Market Order FILLED at {fill_price_float}! Placing 1.5% TP order.")
        
        with data_lock:
            open_position_timestamps[symbol] = time.time()
        
        d_fill = Decimal(str(fill_price_float))
        d_tp_pct = Decimal(str(TP_PCT))

        if trade_side == "BUY":
            tp_raw = d_fill * (Decimal("1") + d_tp_pct)
            exit_side = "SELL"
        else:
            tp_raw = d_fill * (Decimal("1") - d_tp_pct)
            exit_side = "BUY"
        
        tp_price = format_price(tp_raw, tick_size)
        
        # Place 1.5% Take Profit Limit Order
        tp_id = None
        tp_res = binance_signed_request("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": exit_side,
            "type": "LIMIT",
            "quantity": str(amount),
            "price": str(tp_price),
            "timeInForce": "GTC",
            "reduceOnly": "true"
        })
        if tp_res and isinstance(tp_res, dict) and "orderId" in tp_res:
            tp_id = tp_res.get("orderId")
            add_ui_log(f"✅ [{symbol}] Take Profit Placed at {tp_price} (1.5%)")
        
        is_win = False
        trade_closed = False
        trade_start_time = time.time()
        
        # Monitor trade for 90 seconds (Waiting for TP hit or 90s timeout)
        while time.time() - trade_start_time < HOLD_TIME_SECONDS:
            time.sleep(1.0)
            
            pos_risk = binance_signed_request("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
            pos_amt = 0.0
            if isinstance(pos_risk, list):
                for p in pos_risk:
                    if p.get("symbol") == symbol:
                        try:
                            pos_amt = float(p.get("positionAmt", 0))
                        except ValueError:
                            pass
                        break
            
            if pos_amt == 0:
                trade_closed = True
                if tp_id:
                    tp_check = binance_signed_request("GET", "/fapi/v1/order", {"symbol": symbol, "orderId": tp_id})
                    if isinstance(tp_check, dict) and tp_check.get("status") == "FILLED":
                        is_win = True
                break
        
        if not trade_closed:
            add_ui_log(f"🔴 [{symbol}] 90s Hold time reached! Force Market Exit & Closing TP.")
            cancel_all_orders_rest(symbol)
            
            pos_risk = binance_signed_request("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
            current_amt = 0.0
            if isinstance(pos_risk, list):
                for p in pos_risk:
                    if p.get("symbol") == symbol:
                        try:
                            current_amt = float(p.get("positionAmt", 0))
                        except ValueError:
                            pass
                        break
            if current_amt != 0:
                market_exit_side = "SELL" if current_amt > 0 else "BUY"
                binance_signed_request("POST", "/fapi/v1/order", {
                    "symbol": symbol,
                    "side": market_exit_side,
                    "type": "MARKET",
                    "quantity": str(abs(current_amt)),
                    "reduceOnly": "true"
                })
            is_win = False
        
        cancel_all_orders_rest(symbol)
        handle_trade_result(symbol, is_win)

    except Exception as e:
        add_ui_log(f"[{symbol}] Trade Error: {e}")
        cancel_all_orders_rest(symbol)
        with data_lock:
            active_trade_symbols.discard(symbol)
            open_position_timestamps.pop(symbol, None)
            dashboard_data['active_trades_count'] = get_active_positions_count_from_exchange()

# ==========================================
# GLOBAL POSITION GUARDIAN (WATCHDOG THREAD)
# ==========================================
def position_guardian_worker():
    time.sleep(15)
    while True:
        try:
            pos_risk = binance_signed_request("GET", "/fapi/v2/positionRisk")
            if isinstance(pos_risk, list):
                now = time.time()
                for p in pos_risk:
                    try:
                        amt = float(p.get("positionAmt", 0))
                    except ValueError:
                        amt = 0.0
                    sym = p.get("symbol")
                    if amt != 0 and sym:
                        with data_lock:
                            open_time = open_position_timestamps.get(sym, 0)
                        
                        if open_time > 0 and (now - open_time) > 105:
                            add_ui_log(f"🛡️ WATCHDOG: [{sym}] Position stuck past 105s limit! Forcing immediate exit.")
                            cancel_all_orders_rest(sym)
                            exit_side = "SELL" if amt > 0 else "BUY"
                            binance_signed_request("POST", "/fapi/v1/order", {
                                "symbol": sym,
                                "side": exit_side,
                                "type": "MARKET",
                                "quantity": str(abs(amt)),
                                "reduceOnly": "true"
                            })
                            handle_trade_result(sym, False)
        except Exception:
            pass
        time.sleep(10)

threading.Thread(target=position_guardian_worker, daemon=True).start()

# ==========================================
# DYNAMIC MARKET SCANNER (5-SEC WINDOW)
# ==========================================
def dynamic_market_scanner():
    global active_trade_symbols
    add_ui_log("🚀 Dynamic Market Scanner Active ($50M+ Liq, 0.8% Trend-Following in 5s | Max 1 Trade | Fresh Only)...")
    
    while True:
        try:
            if time.time() < trading_paused_until:
                time.sleep(5)
                continue

            tickers = binance_public_get("/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(3)
                continue

            current_active_count = get_active_positions_count_from_exchange()
            with data_lock:
                dashboard_data['active_trades_count'] = current_active_count

            # Agar already ek trade chal rahi hai, toh naye signals ko turant skip karo
            if current_active_count >= MAX_CONCURRENT_TRADES or len(active_trade_symbols) > 0:
                price_history.clear()
                time.sleep(1)
                continue

            now = time.time()

            for t in tickers:
                if get_active_positions_count_from_exchange() >= MAX_CONCURRENT_TRADES or len(active_trade_symbols) > 0:
                    break

                sym = t.get("symbol", "")
                if not sym.endswith("USDT") or "_" in sym:
                    continue

                try:
                    vol = float(t.get("quoteVolume", 0))
                    price = float(t.get("lastPrice", 0))
                except ValueError:
                    continue

                if vol < MIN_24H_VOLUME_USD:
                    continue

                # 2-minute strict cooldown check per coin
                last_trade = coin_last_trade_time.get(sym, 0)
                if now - last_trade < COIN_COOLDOWN_SECONDS:
                    continue

                if sym not in price_history:
                    price_history[sym] = []

                price_history[sym].append((now, price))
                price_history[sym] = [(ts, p) for ts, p in price_history[sym] if now - ts <= DROP_TIME_WINDOW_SEC]

                if len(price_history[sym]) >= 2:
                    oldest_price = price_history[sym][0][1]
                    pct_change = (price - oldest_price) / oldest_price

                    trade_side = None
                    if pct_change <= -THRESHOLD_PCT:
                        trade_side = "SELL"  # Market 0.8% Dump hua -> SELL (Short)
                    elif pct_change >= THRESHOLD_PCT:
                        trade_side = "BUY"   # Market 0.8% Pump hua -> BUY (Long)

                    if trade_side:
                        with data_lock:
                            if get_active_positions_count_from_exchange() < MAX_CONCURRENT_TRADES and len(active_trade_symbols) == 0 and sym not in active_trade_symbols:
                                active_trade_symbols.add(sym)
                                coin_last_trade_time[sym] = time.time()
                                dashboard_data['active_trades_count'] = get_active_positions_count_from_exchange()
                                trigger_trade = True
                            else:
                                trigger_trade = False

                        if trigger_trade:
                            move_type = "Dumped" if trade_side == "SELL" else "Pumped"
                            move_display = f"{pct_change*100:.2f}%"
                            add_scan(f"[{datetime.now(IST).strftime('%H:%M:%S')}] {sym} {move_type} {move_display} in 5s! Triggering {trade_side}.")
                            threading.Thread(target=execute_scalp_trade, args=(sym, price, trade_side), daemon=True).start()
                            break

        except Exception:
            pass

        time.sleep(1)

threading.Thread(target=dynamic_market_scanner, daemon=True).start()

# ==========================================
# EMBEDDED HTTP SERVER & DASHBOARD
# ==========================================
class BotDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path == '/status':
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with data_lock:
                dashboard_data['paused_until'] = trading_paused_until
                dashboard_data['active_trades_count'] = get_active_positions_count_from_exchange()
                payload = json.dumps(dashboard_data).encode('utf-8')
            self.wfile.write(payload)
            return

        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HFT Pump/Dump Scalper Bot</title>
    <style>
        body { background-color: #16181c; color: #b7bdc6; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 12px; }
        .card { background-color: #20242d; border-radius: 10px; padding: 12px; margin-bottom: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .header-card { display: flex; justify-content: space-between; align-items: stretch; gap: 10px; }
        .title-box { flex: 1; }
        .title { color: #fcd535; font-size: 15px; font-weight: bold; margin: 0; line-height: 1.4; }
        .status-badge { background-color: #1e3a29; color: #0ecb81; padding: 8px; border-radius: 8px; font-size: 11px; font-weight: bold; display: flex; flex-direction: column; justify-content: center; text-align: center; min-width: 90px; }
        .status-badge.paused { background-color: #3a1e1e; color: #f6465d; }
        .label { font-size: 10px; text-transform: uppercase; color: #848e9c; margin-bottom: 4px; font-weight: 600; letter-spacing: 0.5px; }
        .value { font-size: 16px; font-weight: bold; color: #eaecef; }
        .text-green { color: #0ecb81; }
        .text-red { color: #f6465d; }
        .text-yellow { color: #fcd535; }
        .ledger { font-size: 12px; max-height: 180px; overflow-y: auto; line-height: 1.5; }
        .ledger p { margin: 3px 0; border-bottom: 1px solid #2b3139; padding-bottom: 3px; }
        .stats-grid { display: flex; gap: 10px; }
        .stats-box { flex: 1; background-color: #16181c; padding: 8px; border-radius: 6px; text-align: center; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-thumb { background: #474d57; border-radius: 4px; }
    </style>
    <script>
        async function fetchData() {
            try {
                const res = await fetch("/status");
                const data = await res.json();
                
                let statusBox = document.getElementById("status-box");
                if (data.paused_until > 0) {
                    let remaining = Math.round(data.paused_until - (Date.now()/1000));
                    if (remaining > 0) {
                        let hrs = Math.floor(remaining / 3600);
                        let mins = Math.floor((remaining % 3600) / 60);
                        let secs = remaining % 60;
                        statusBox.className = "status-badge paused";
                        statusBox.innerHTML = `PAUSED<br>${hrs}h ${mins}m ${secs}s`;
                    } else {
                        statusBox.className = "status-badge";
                        statusBox.innerHTML = `ARMED<br>Running`;
                    }
                } else {
                    statusBox.className = "status-badge";
                    statusBox.innerHTML = `ARMED<br>Losses: ${data.losses}/10`;
                }
                
                document.getElementById("active-trades").innerText = data.active_trades_count + " / 1 Max";
                document.getElementById("tp-count").innerText = data.tp_hits;
                document.getElementById("timeout-count").innerText = data.timeout_exits;
                document.getElementById("drift").innerText = data.clock_offset_ms + " ms";
                
                let scansHtml = data.scans.map(s => `<p>${s}</p>`).join("");
                document.getElementById("radar-feed").innerHTML = scansHtml || "Scanning market for 0.8% pumps/dumps in 5s...";
                
                let logsHtml = data.logs.map(log => `<p>${log}</p>`).join("");
                document.getElementById("ledger").innerHTML = logsHtml;
            } catch(e) {}
        }
        setInterval(fetchData, 1000);
    </script>
</head>
<body>
    <div class="card header-card">
        <div class="title-box">
            <h1 class="title">⚡ Binance Scalper Bot Dashboard<br>($0.35 Margin + 20x | 0.8% & 1 Max Trade & 90s Hold)</h1>
        </div>
        <div id="status-box" class="status-badge">LOADING...</div>
    </div>
    
    <div class="card">
        <div class="stats-grid">
            <div class="stats-box">
                <div class="label">Active Trades</div>
                <div id="active-trades" class="value text-yellow">0 / 1 Max</div>
            </div>
            <div class="stats-box">
                <div class="label">Total TP Hits (1.5%)</div>
                <div id="tp-count" class="value text-green">0</div>
            </div>
            <div class="stats-box">
                <div class="label">Timeout Exits (90s)</div>
                <div id="timeout-count" class="value text-red">0</div>
            </div>
        </div>
    </div>

    <div class="card">
        <div class="label">📊 LIVE PUMP/DUMP RADAR (0.8% / 5s FRESH ONLY)</div>
        <div id="radar-feed" class="ledger text-yellow" style="max-height: 140px; overflow-y: auto;">-</div>
    </div>

    <div class="card">
        <div class="label">CLOCK DRIFT</div>
        <div id="drift" class="value text-yellow">0.00 ms</div>
    </div>

    <div class="card">
        <div class="label">📋 Execution Ledger & Logs (TP / Timeout Tracker)</div>
        <div id="ledger" class="ledger"></div>
    </div>
</body>
</html>'''
        self.wfile.write(html_content.encode("utf-8"))

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), BotDashboardHandler)
    server.serve_forever()

def self_ping_worker():
    port = int(os.environ.get("PORT", 10000))
    time.sleep(5)
    while True:
        try:
            session.get(f"http://127.0.0.1:{port}/status", timeout=5)
        except Exception:
            pass
        time.sleep(30)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=self_ping_worker, daemon=True).start()
    
    add_ui_log("Syncing clock and exchange settings...")
    sync_binance_clock()
    add_ui_log("⚡ Binance Scalper Bot initialized with 0.8% Threshold, Max 1 Trade, 90s Hold & Fresh-Only Filter!")
    
    while True:
        time.sleep(1)
