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
import websocket
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
TRADE_MARGIN_USD = 0.45  
LEVERAGE = 20
POSITION_SIZE_USD = TRADE_MARGIN_USD * LEVERAGE

TP_PCT = 0.0006  
SL_PCT = 0.0015  
HOLD_TIME_SECONDS = 10

MAX_CONSECUTIVE_LOSSES = 5
PAUSE_DURATION_SECONDS = 5 * 3600  
SUCCESS_COOLDOWN_SECONDS = 120 

consecutive_losses = 0
trading_paused_until = 0
clock_offset_ms = 0.0

COIN_CONFIG = {
    'SOLUSDT': {'symbol': 'SOLUSDT', 'ccxt_symbol': 'SOL/USDT', 'threshold': 10000},
    'DOGEUSDT': {'symbol': 'DOGEUSDT', 'ccxt_symbol': 'DOGE/USDT', 'threshold': 5000},
    'XRPUSDT': {'symbol': 'XRPUSDT', 'ccxt_symbol': 'XRP/USDT', 'threshold': 5000},
    'BNBUSDT': {'symbol': 'BNBUSDT', 'ccxt_symbol': 'BNB/USDT', 'threshold': 10000}
}
active_trades = {info['symbol']: False for info in COIN_CONFIG.values()}

# Live tracking stats for dashboard
dashboard_data = {
    "status": "INITIALIZING...",
    "active_coin": "NONE",
    "losses": 0,
    "paused_until": 0,
    "clock_offset_ms": 0.0,
    "coin_stats": {
        'SOLUSDT': {'time': '-', 'price': 0.0, 'volume_usd': 0.0, 'last_liq_time': 'None', 'last_liq_side': '-', 'last_liq_notional': 0, 'status': 'SCANNING (Live)'},
        'DOGEUSDT': {'time': '-', 'price': 0.0, 'volume_usd': 0.0, 'last_liq_time': 'None', 'last_liq_side': '-', 'last_liq_notional': 0, 'status': 'SCANNING (Live)'},
        'XRPUSDT': {'time': '-', 'price': 0.0, 'volume_usd': 0.0, 'last_liq_time': 'None', 'last_liq_side': '-', 'last_liq_notional': 0, 'status': 'SCANNING (Live)'},
        'BNBUSDT': {'time': '-', 'price': 0.0, 'volume_usd': 0.0, 'last_liq_time': 'None', 'last_liq_side': '-', 'last_liq_notional': 0, 'status': 'SCANNING (Live)'}
    },
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
    except Exception:
        pass
    return {}

def binance_signed_request(method, endpoint, params=None):
    if params is None:
        params = {}
    try:
        params['timestamp'] = int(get_synced_time_ms())
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(API_SECRET.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        full_url = f"{BINANCE_FUTURES_URL}{endpoint}?{query_string}&signature={signature}"
        headers = {"X-MBX-APIKEY": API_KEY}
        
        if method == "GET":
            resp = session.get(full_url, headers=headers, timeout=5)
        elif method == "POST":
            resp = session.post(full_url, headers=headers, timeout=5)
        elif method == "DELETE":
            resp = session.delete(full_url, headers=headers, timeout=5)
        
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        add_ui_log(f"Signed Request Error ({endpoint}): {e}")
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

# ==========================================
# WEBSOCKET API (WS-FAPI) FOR ORDERS
# ==========================================
ws_fapi_client = None
ws_fapi_ready = False
ws_pending_requests = {}
ws_req_lock = threading.Lock()

def on_ws_fapi_message(ws, message):
    try:
        data = json.loads(message)
        req_id = data.get("id")
        if req_id and req_id in ws_pending_requests:
            with ws_req_lock:
                req_info = ws_pending_requests.pop(req_id)
                req_info["result"] = data
                req_info["event"].set()
    except Exception:
        pass

def on_ws_fapi_open(ws):
    global ws_fapi_ready
    ws_fapi_ready = True
    add_ui_log("⚡ Binance WS-FAPI Connected & Ready for Orders")

def on_ws_fapi_close(ws, code, msg):
    global ws_fapi_ready
    ws_fapi_ready = False

def on_ws_fapi_error(ws, error):
    global ws_fapi_ready
    ws_fapi_ready = False

def connect_ws_fapi():
    global ws_fapi_client
    while True:
        try:
            ws_url = "wss://ws-fapi.binance.com/ws-fapi/v1"
            ws_fapi_client = websocket.WebSocketApp(
                ws_url,
                on_open=on_ws_fapi_open,
                on_message=on_ws_fapi_message,
                on_close=on_ws_fapi_close,
                on_error=on_ws_fapi_error
            )
            ws_fapi_client.run_forever(ping_interval=20, ping_timeout=10)
        except Exception:
            pass
        ws_fapi_ready = False
        time.sleep(2)

threading.Thread(target=connect_ws_fapi, daemon=True).start()

def send_ws_fapi_order(symbol, side, quantity, price=None, order_type="MARKET", time_in_force=None, reduce_only=False, stop_price=None):
    if not ws_fapi_ready or not ws_fapi_client:
        raise Exception("WS-FAPI not ready")
    
    req_id = str(uuid.uuid4())
    params = {
        "apiKey": API_KEY,
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "quantity": str(quantity),
        "timestamp": int(get_synced_time_ms())
    }
    if price is not None:
        params["price"] = str(price)
    if time_in_force:
        params["timeInForce"] = time_in_force
    if reduce_only:
        params["reduceOnly"] = "true"
    if stop_price is not None:
        params["stopPrice"] = str(stop_price)

    sorted_params = sorted(params.items())
    query_string = urllib.parse.urlencode(sorted_params)
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    params["signature"] = signature

    payload = {
        "id": req_id,
        "method": "order.place",
        "params": params
    }

    event = threading.Event()
    req_info = {"event": event, "result": None}
    with ws_req_lock:
        ws_pending_requests[req_id] = req_info

    ws_fapi_client.send(json.dumps(payload))
    
    if event.wait(timeout=3.0):
        res = req_info["result"]
        if res and "status" in res and res["status"] == 200:
            return res.get("result", {})
        else:
            raise Exception(f"WS Order Error: {res}")
    else:
        with ws_req_lock:
            ws_pending_requests.pop(req_id, None)
        raise Exception("WS Order Timeout")

def cancel_all_ws_orders(symbol):
    try:
        req_id = str(uuid.uuid4())
        params = {
            "apiKey": API_KEY,
            "symbol": symbol,
            "timestamp": int(get_synced_time_ms())
        }
        query_string = urllib.parse.urlencode(sorted(params.items()))
        params["signature"] = hmac.new(API_SECRET.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        
        payload = {"id": req_id, "method": "allOpenOrders.cancel", "params": params}
        event = threading.Event()
        req_info = {"event": event, "result": None}
        with ws_req_lock:
            ws_pending_requests[req_id] = req_info
        ws_fapi_client.send(json.dumps(payload))
        event.wait(timeout=2.0)
    except Exception:
        binance_signed_request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})

# ==========================================
# TRADING EXECUTION LOGIC
# ==========================================
def set_leverage(symbol):
    try:
        binance_signed_request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": LEVERAGE})
        add_ui_log(f"Leverage set to {LEVERAGE}x for {symbol}")
    except Exception as e:
        add_ui_log(f"Leverage error {symbol}: {e}")

def get_precision_info(symbol):
    step_size = 0.01
    min_qty = 0.01
    tick_size = 0.01
    try:
        info = binance_public_get("/fapi/v1/exchangeInfo")
        for s in info.get("symbols", []):
            if s.get("symbol") == symbol:
                for f in s.get("filters", []):
                    if f.get("filterType") == "LOT_SIZE":
                        step_size = float(f.get("stepSize", "0.01"))
                        min_qty = float(f.get("minQty", "0.01"))
                    elif f.get("filterType") == "PRICE_FILTER":
                        tick_size = float(f.get("tickSize", "0.01"))
                break
    except Exception:
        pass
    return step_size, min_qty, tick_size

def format_number(val, step):
    step_str = f"{step:.8f}".rstrip("0")
    decimals = len(step_str.split(".")[1]) if "." in step_str else 0
    return f"{round(val, decimals):.{decimals}f}"

def handle_trade_result(is_win):
    global consecutive_losses, trading_paused_until
    with data_lock:
        dashboard_data['active_coin'] = "NONE"
    
    if is_win:
        consecutive_losses = 0
        trading_paused_until = time.time() + SUCCESS_COOLDOWN_SECONDS
        add_ui_log("✅ Trade WON (TP Hit)! 2-Min Rest Cooldown.")
    else:
        consecutive_losses += 1
        add_ui_log(f"❌ Trade LOST. Losses: {consecutive_losses}/{MAX_CONSECUTIVE_LOSSES}")
        if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            trading_paused_until = time.time() + PAUSE_DURATION_SECONDS
            add_ui_log("🚨 5 Consecutive Losses! 5-Hour PAUSE triggered.")

def execute_trade(symbol, entry_price):
    global trading_paused_until
    try:
        active_trades[symbol] = True
        with data_lock:
            dashboard_data['active_coin'] = f"{symbol} (SCALPING)"
        
        step_size, min_qty, tick_size = get_precision_info(symbol)
        raw_qty = POSITION_SIZE_USD / entry_price
        calc_qty = max(min_qty, math.floor(raw_qty / step_size) * step_size)
        amount = format_number(calc_qty, step_size)
        price = format_number(entry_price, tick_size)
        
        add_ui_log(f"🟢 ENTRY: {symbol} at {price} (Qty: {amount})")
        
        order_res = send_ws_fapi_order(
            symbol=symbol, side="BUY", quantity=amount, price=price,
            order_type="LIMIT", time_in_force="GTX"
        )
        
        order_id = order_res.get("orderId")
        time.sleep(1)
        
        check_order = binance_signed_request("GET", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id})
        
        if check_order.get("status") == "FILLED":
            add_ui_log(f"[{symbol}] Filled! Placing TP/SL limits.")
            
            fill_price = float(check_order.get("avgPrice", entry_price))
            tp_raw = fill_price * (1 + TP_PCT)
            sl_raw = fill_price * (1 - SL_PCT)
            
            tp_price = format_number(tp_raw, tick_size)
            sl_price = format_number(sl_raw, tick_size)
            
            tp_res = send_ws_fapi_order(
                symbol=symbol, side="SELL", quantity=amount, price=tp_price,
                order_type="LIMIT", time_in_force="GTX", reduce_only=True
            )
            tp_id = tp_res.get("orderId")
            
            send_ws_fapi_order(
                symbol=symbol, side="SELL", quantity=amount, order_type="STOP_MARKET",
                reduce_only=True, stop_price=sl_price
            )
            
            is_win = False
            trade_closed = False
            
            for _ in range(HOLD_TIME_SECONDS):
                time.sleep(1)
                pos_risk = binance_signed_request("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
                pos_amt = 0.0
                if isinstance(pos_risk, list):
                    for p in pos_risk:
                        if p.get("symbol") == symbol:
                            pos_amt = float(p.get("positionAmt", 0))
                            break
                
                if pos_amt == 0:
                    trade_closed = True
                    tp_check = binance_signed_request("GET", "/fapi/v1/order", {"symbol": symbol, "orderId": tp_id})
                    if tp_check.get("status") == "FILLED":
                        is_win = True
                    break
            
            if not trade_closed:
                add_ui_log(f"🔴 [{symbol}] 10s Timeout! Force Market Exit.")
                cancel_all_ws_orders(symbol)
                
                pos_risk = binance_signed_request("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
                current_amt = 0.0
                if isinstance(pos_risk, list):
                    for p in pos_risk:
                        if p.get("symbol") == symbol:
                            current_amt = float(p.get("positionAmt", 0))
                            break
                if current_amt != 0:
                    side = "SELL" if current_amt > 0 else "BUY"
                    send_ws_fapi_order(symbol=symbol, side=side, quantity=abs(current_amt), order_type="MARKET", reduce_only=True)
                is_win = False
            
            cancel_all_ws_orders(symbol)
            handle_trade_result(is_win)
        else:
            add_ui_log(f"[{symbol}] Order missed/unfilled. Canceling.")
            cancel_all_ws_orders(symbol)
            with data_lock:
                dashboard_data['active_coin'] = "NONE"

    except Exception as e:
        add_ui_log(f"[{symbol}] Trade Error: {e}")
        with data_lock:
            dashboard_data['active_coin'] = "NONE"
    finally:
        active_trades[symbol] = False

# ==========================================
# FAST LIGHTWEIGHT LIQUIDITY & PRICE TRACKER
# ==========================================
def poll_market_and_liquidity():
    processed_liq_ids = set()
    while True:
        try:
            # 1. Fetch 24hr Ticker (Prices + Total USD Volume/Liquidity in one fast call)
            tickers = binance_public_get("/fapi/v1/ticker/24hr")
            if isinstance(tickers, list):
                for t in tickers:
                    raw_sym = t.get("symbol")
                    if raw_sym in COIN_CONFIG:
                        price = float(t.get("lastPrice", 0))
                        volume_usd = float(t.get("quoteVolume", 0))  # Total 24h USDT volume (Liquidity indicator)
                        with data_lock:
                            dashboard_data['coin_stats'][raw_sym]['price'] = price
                            dashboard_data['coin_stats'][raw_sym]['volume_usd'] = volume_usd
                            dashboard_data['coin_stats'][raw_sym]['time'] = datetime.now(IST).strftime('%H:%M:%S')

            # 2. Fetch Recent Liquidations via REST API Fallback
            for raw_sym, info in COIN_CONFIG.items():
                symbol = info['symbol']
                threshold = info['threshold']
                liq_res = binance_public_get("/fapi/v1/forceOrders", {"symbol": raw_sym, "limit": 2})
                if isinstance(liq_res, list):
                    for ord_data in liq_res:
                        liq_id = f"{raw_sym}_{ord_data.get('time')}_{ord_data.get('price')}"
                        if liq_id not in processed_liq_ids:
                            processed_liq_ids.add(liq_id)
                            if len(processed_liq_ids) > 100:
                                processed_liq_ids.pop()
                            
                            price = float(ord_data.get('price', 0))
                            qty = float(ord_data.get('origQty', 0))
                            notional = price * qty
                            side = ord_data.get('side', 'SELL')
                            scan_time = datetime.now(IST).strftime('%H:%M:%S')
                            
                            is_paused = time.time() < trading_paused_until
                            is_active = active_trades.get(symbol, False)
                            
                            if side == 'SELL':
                                if is_paused:
                                    status_desc = "PAUSED (Cooldown)"
                                elif is_active:
                                    status_desc = "SKIPPED (In Trade)"
                                elif notional >= threshold:
                                    status_desc = "⚡ TRIGGERED TRADE"
                                    threading.Thread(target=execute_trade, args=(symbol, price), daemon=True).start()
                                else:
                                    status_desc = f"SKIPPED (< ${threshold})"
                            else:
                                status_desc = f"BUY LIQ (${notional:.0f})"

                            with data_lock:
                                dashboard_data['coin_stats'][raw_sym]['last_liq_time'] = scan_time
                                dashboard_data['coin_stats'][raw_sym]['last_liq_side'] = side
                                dashboard_data['coin_stats'][raw_sym]['last_liq_notional'] = round(notional, 2)
                                dashboard_data['coin_stats'][raw_sym]['status'] = status_desc
                            
                            add_scan(f"[{scan_time}] {raw_sym} ({side}) | Size: ${notional:.0f} @ {price} ➔ {status_desc}")
        except Exception:
            pass
        time.sleep(2)

threading.Thread(target=poll_market_and_liquidity, daemon=True).start()

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
                dashboard_data['losses'] = consecutive_losses
                dashboard_data['paused_until'] = trading_paused_until
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
    <title>HFT Liquidation Scalper Bot</title>
    <style>
        body { background-color: #16181c; color: #b7bdc6; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 12px; }
        .card { background-color: #20242d; border-radius: 10px; padding: 12px; margin-bottom: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .header-card { display: flex; justify-content: space-between; align-items: stretch; gap: 10px; }
        .title-box { flex: 1; }
        .title { color: #fcd535; font-size: 15px; font-weight: bold; margin: 0; line-height: 1.4; }
        .status-badge { background-color: #1e3a29; color: #0ecb81; padding: 8px; border-radius: 8px; font-size: 11px; font-weight: bold; display: flex; flex-direction: column; justify-content: center; text-align: center; min-width: 80px; }
        .status-badge.paused { background-color: #3a1e1e; color: #f6465d; }
        .label { font-size: 10px; text-transform: uppercase; color: #848e9c; margin-bottom: 4px; font-weight: 600; letter-spacing: 0.5px; }
        .value { font-size: 16px; font-weight: bold; color: #eaecef; }
        .text-green { color: #0ecb81; }
        .text-red { color: #f6465d; }
        .text-yellow { color: #fcd535; }
        .coins-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
        .coin-box { background-color: #16181c; border-radius: 8px; padding: 8px; border: 1px solid #2b3139; }
        .coin-name { font-size: 12px; font-weight: bold; color: #fcd535; display: flex; justify-content: space-between; }
        .coin-val { font-size: 12px; color: #eaecef; margin-top: 3px; font-weight: bold; }
        .liq-box { font-size: 10px; margin-top: 4px; background: #252a34; padding: 4px 6px; border-radius: 4px; display: flex; justify-content: space-between; }
        .last-liq { font-size: 10px; margin-top: 3px; color: #fcd535; }
        .coin-status { font-size: 10px; margin-top: 4px; font-weight: bold; }
        .ledger { font-size: 12px; max-height: 180px; overflow-y: auto; line-height: 1.5; }
        .ledger p { margin: 3px 0; border-bottom: 1px solid #2b3139; padding-bottom: 3px; }
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
                        statusBox.className = "status-badge paused";
                        statusBox.innerHTML = `PAUSED<br>${remaining}s left`;
                    } else {
                        statusBox.className = "status-badge";
                        statusBox.innerHTML = `ARMED<br>Running`;
                    }
                } else {
                    statusBox.className = "status-badge";
                    statusBox.innerHTML = `ARMED<br>Losses: ${data.losses}/5`;
                }
                
                document.getElementById("target-coin").innerText = data.active_coin;
                document.getElementById("drift").innerText = data.clock_offset_ms + " ms";
                
                let coinsHtml = "";
                for (let [ticker, info] of Object.entries(data.coin_stats)) {
                    let colorClass = info.status.includes("TRIGGERED") ? "text-green" : (info.status.includes("SKIPPED") || info.status.includes("PAUSED") ? "text-red" : "text-yellow");
                    let liqText = info.last_liq_notional > 0 ? `${info.last_liq_side} Liq: $${info.last_liq_notional}` : "Waiting for Liq...";
                    let volM = (info.volume_usd / 1000000).toFixed(1);
                    
                    coinsHtml += `
                        <div class="coin-box">
                            <div class="coin-name"><span>${ticker}</span> <span>${info.time}</span></div>
                            <div class="coin-val">$${info.price}</div>
                            <div class="liq-box">
                                <span class="text-yellow" title="24h Volume / Liquidity">24h Vol: $${volM}M</span>
                            </div>
                            <div class="last-liq">🔥 ${liqText}</div>
                            <div class="coin-status ${colorClass}">${info.status}</div>
                        </div>
                    `;
                }
                document.getElementById("coins-grid").innerHTML = coinsHtml;
                
                let scansHtml = data.scans.map(s => `<p>${s}</p>`).join("");
                document.getElementById("liquidation-feed").innerHTML = scansHtml || "Scanning market for liquidations...";
                
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
            <h1 class="title">⚡ Binance WS-FAPI HFT Scalper<br>($0.45 Margin + 20x Leverage)</h1>
        </div>
        <div id="status-box" class="status-badge">LOADING...</div>
    </div>
    
    <div class="card">
        <div class="label">LIVE TARGET COIN (ACTIVE TRADE)</div>
        <div id="target-coin" class="value text-yellow">NONE</div>
    </div>

    <div class="card">
        <div class="label">🪙 COIN PRICES, 24H LIQUIDITY (VOLUME) & RECENT LIQS</div>
        <div id="coins-grid" class="coins-grid"></div>
    </div>

    <div class="card">
        <div class="label">📊 LIVE LIQUIDATION & ACTIVITY RADAR</div>
        <div id="liquidation-feed" class="ledger text-yellow" style="max-height: 140px; overflow-y: auto;">-</div>
    </div>

    <div class="card">
        <div class="label">CLOCK DRIFT</div>
        <div id="drift" class="value text-yellow">0.00 ms</div>
    </div>

    <div class="card">
        <div class="label">📋 Execution Ledger & Logs</div>
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
        time.sleep(60)

# ==========================================
# INITIALIZATION & BACKGROUND STARTUP
# ==========================================
def initialize_bot():
    add_ui_log("Syncing clock and exchange settings...")
    sync_binance_clock()
    for config in COIN_CONFIG.values():
        set_leverage(config['symbol'])
    add_ui_log("⚡ HFT Bot initialized with Fast Liquidity & Ticker Tracker!")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=self_ping_worker, daemon=True).start()
    threading.Thread(target=initialize_bot, daemon=True).start()
    
    while True:
        time.sleep(1)
