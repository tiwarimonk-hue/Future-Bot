import os
import time
import json
import hmac
import hashlib
import math
import threading
import websocket
import requests
from urllib.parse import urlencode
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler


# ==========================================
# 1. TIMEZONE & DASHBOARD TELEMETRY STATE
# ==========================================

IST = timezone(timedelta(hours=5, minutes=30))
data_lock = threading.Lock()

dashboard_data = {
    "active_slots": "0 / 3 Max",
    "funding_rate": "0.00%",
    "action_direction": "--",
    "status": "Initializing Multi-Coin Funding Engine (Max 3)...",
    "clock_offset_ms": 0.0,
    "logs": [],
    "ledger": [],
    "active_trades": []
}


def add_ui_log(message):
    timestamp = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
    line = f'[{timestamp}] {message}'
    print(line)
    with data_lock:
        dashboard_data['logs'].append(line)
        if len(dashboard_data['logs']) > 70:
            dashboard_data['logs'].pop(0)


def add_ledger_entry(trade_info):
    with data_lock:
        dashboard_data['ledger'].insert(0, trade_info)
        if len(dashboard_data['ledger']) > 20:
            dashboard_data['ledger'].pop()


# ==========================================
# 2. BINANCE HTTP CLIENT & CLOCK SYNCHRONIZATION
# ==========================================

class BinanceHTTP:
    def __init__(self, api_key, api_secret, testnet=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
        self.session = requests.Session()
        self.session.headers.update({
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/json"
        })

    def _sign(self, params):
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return query_string + "&signature=" + signature

    def get_server_time(self):
        url = f"{self.base_url}/fapi/v1/time"
        resp = self.session.get(url).json()
        server_time_ms = resp.get("serverTime", int(time.time() * 1000))
        return {"result": {"timeNano": int(server_time_ms * 1e6)}}

    def get_tickers(self, category="linear"):
        url_pi = f"{self.base_url}/fapi/v1/premiumIndex"
        pi_data = self.session.get(url_pi).json()

        url_pr = f"{self.base_url}/fapi/v1/ticker/price"
        pr_data = self.session.get(url_pr).json()
        price_map = {item["symbol"]: float(item["price"]) for item in pr_data} if isinstance(pr_data, list) else {}

        list_items = []
        if isinstance(pi_data, list):
            for item in pi_data:
                sym = item.get("symbol", "")
                funding_rate = item.get("lastFundingRate", "0")
                next_funding_time = item.get("nextFundingTime", 0)
                last_price = price_map.get(sym, 0.0)
                list_items.append({
                    "symbol": sym,
                    "fundingRate": str(funding_rate),
                    "nextFundingTime": next_funding_time,
                    "lastPrice": str(last_price)
                })
        return {"result": {"list": list_items}}

    def get_instruments_info(self, category="linear", symbol=None):
        url = f"{self.base_url}/fapi/v1/exchangeInfo"
        data = self.session.get(url).json()
        symbols_info = data.get("symbols", [])
        
        list_items = []
        for s in symbols_info:
            if symbol and s["symbol"] != symbol:
                continue
            sym_name = s["symbol"]
            max_lev = 20.0
            tick_size = "0.0001"
            qty_step = "1"
            min_qty = "1"
            
            for f in s.get("filters", []):
                if f["filterType"] == "PRICE_FILTER":
                    tick_size = f["tickSize"]
                elif f["filterType"] == "LOT_SIZE":
                    qty_step = f["stepSize"]
                    min_qty = f["minQty"]
            
            list_items.append({
                "symbol": sym_name,
                "leverageFilter": {"maxLeverage": str(max_lev)},
                "priceFilter": {"tickSize": tick_size},
                "lotSizeFilter": {"qtyStep": qty_step, "minOrderQty": min_qty}
            })
        return {"result": {"list": list_items}}

    def set_leverage(self, category="linear", symbol=None, buyLeverage=None, sellLeverage=None):
        url = f"{self.base_url}/fapi/v1/leverage"
        lev = int(float(buyLeverage or 20))
        params = {
            "symbol": symbol,
            "leverage": lev,
            "timestamp": int(time.time() * 1000)
        }
        signed_query = self._sign(params)
        resp = self.session.post(f"{url}?{signed_query}")
        return resp.json()

    def cancel_all_orders(self, category="linear", symbol=None):
        url = f"{self.base_url}/fapi/v1/allOpenOrders"
        params = {
            "symbol": symbol,
            "timestamp": int(time.time() * 1000)
        }
        signed_query = self._sign(params)
        resp = self.session.delete(f"{url}?{signed_query}")
        return resp.json()

    def place_order(self, category="linear", symbol=None, side=None, orderType=None, qty=None, price=None, reduceOnly=False, positionIdx=0):
        url = f"{self.base_url}/fapi/v1/order"
        params = {
            "symbol": symbol,
            "side": str(side).upper(),
            "type": str(orderType).upper(),
            "quantity": str(qty),
            "timestamp": int(time.time() * 1000)
        }
        if str(orderType).upper() == "LIMIT":
            params["price"] = str(price)
            params["timeInForce"] = "GTC"
        if reduceOnly:
            params["reduceOnly"] = "true"
            
        signed_query = self._sign(params)
        resp = self.session.post(f"{url}?{signed_query}")
        return resp.json()

    def get_positions(self, category="linear", symbol=None):
        url = f"{self.base_url}/fapi/v2/positionRisk"
        params = {
            "timestamp": int(time.time() * 1000)
        }
        signed_query = self._sign(params)
        resp = self.session.get(f"{url}?{signed_query}").json()
        
        list_items = []
        if isinstance(resp, list):
            for p in resp:
                if symbol and p["symbol"] != symbol:
                    continue
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    list_items.append({
                        "symbol": p["symbol"],
                        "size": str(abs(amt)),
                        "avgPrice": str(p.get("entryPrice", 0))
                    })
        if not list_items:
            list_items.append({"symbol": symbol, "size": "0", "avgPrice": "0"})
        return {"result": {"list": list_items}}

    def set_stop_loss(self, category="linear", symbol=None, stopPrice=None, positionIdx=0):
        try:
            open_orders_url = f"{self.base_url}/fapi/v1/openOrders"
            op_params = {"symbol": symbol, "timestamp": int(time.time() * 1000)}
            op_signed = self._sign(op_params)
            open_orders = self.session.get(f"{open_orders_url}?{op_signed}").json()
            if isinstance(open_orders, list):
                for ord in open_orders:
                    if ord.get("type") in ["TAKE_PROFIT_MARKET", "STOP_MARKET"]:
                        cancel_url = f"{self.base_url}/fapi/v1/order"
                        c_params = {"symbol": symbol, "orderId": ord["orderId"], "timestamp": int(time.time() * 1000)}
                        c_signed = self._sign(c_params)
                        self.session.delete(f"{cancel_url}?{c_signed}")
        except Exception:
            pass

        url = f"{self.base_url}/fapi/v1/order"
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "STOP_MARKET",
            "stopPrice": str(stopPrice),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "timestamp": int(time.time() * 1000)
        }
        signed_query = self._sign(params)
        resp = self.session.post(f"{url}?{signed_query}")
        return resp.json()


clock_offset_ms = 0.0

def sync_binance_clock(http_client):
    global clock_offset_ms
    try:
        t_send = time.time() * 1000
        server_time_resp = http_client.get_server_time()
        t_recv = time.time() * 1000

        server_time = float(server_time_resp['result']['timeNano']) / 1e6
        rtt = t_recv - t_send
        estimated_server_time = server_time + (rtt / 2.0)
        clock_offset_ms = estimated_server_time - t_recv
        
        with data_lock:
            dashboard_data['clock_offset_ms'] = round(clock_offset_ms, 2)
    except Exception:
        pass


def get_synced_time_ms():
    return (time.time() * 1000) + clock_offset_ms


def precision_wait_until(target_time_ms):
    while True:
        now = get_synced_time_ms()
        diff = target_time_ms - now
        if diff <= 0:
            break
        elif diff > 10:
            time.sleep(0.002)
        elif diff > 2:
            time.sleep(0.0005)


# ==========================================
# 3. WEB DASHBOARD (THREADED HTTP SERVER)
# ==========================================

class FundingDashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/status':
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with data_lock:
                payload = json.dumps(dashboard_data).encode('utf-8')
            self.wfile.write(payload)
            return

        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        html_content = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Binance Multi-Coin Funding Engine (Max 3)</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }
body { background-color: #0b0e11; color: #eaecef; padding: 20px; display: flex; justify-content: center; }
.container { width: 100%; max-width: 1100px; display: flex; flex-direction: column; gap: 16px; }
.header { background: #1e2329; padding: 18px 24px; border-radius: 12px; border: 1px solid #2b313a; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 16px; color: #f7a600; }
.badge { background: rgba(14, 203, 129, 0.15); color: #0ecb81; padding: 6px 14px; border-radius: 20px; font-size: 12px; font-weight: 600; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; }
.card { background: #1e2329; padding: 16px; border-radius: 12px; border: 1px solid #2b313a; }
.card-title { font-size: 11px; color: #848e9c; text-transform: uppercase; margin-bottom: 6px; }
.card-value { font-size: 18px; font-weight: 700; color: #f7a600; }
.section-card { background: #1e2329; padding: 18px; border-radius: 12px; border: 1px solid #2b313a; }
#log-box { background: #0b0e11; border: 1px solid #2b313a; border-radius: 8px; padding: 14px; height: 180px; overflow-y: auto; font-family: 'Courier New', monospace; font-size: 12px; color: #0ecb81; line-height: 1.5; }
.coin-list { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.coin-chip { background: #181c22; border: 1px solid #2b313a; border-radius: 8px; padding: 10px 14px; display: flex; justify-content: space-between; align-items: center; font-size: 13px; }
.ledger-container { max-height: 250px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; margin-top: 10px; }
.ledger-item { background: #181c22; border: 1px solid #2b313a; border-radius: 8px; padding: 12px; display: flex; justify-content: space-between; align-items: center; font-size: 13px; }
.ledger-info { display: flex; flex-direction: column; gap: 4px; }
.coin-name { font-weight: 700; color: #f7a600; font-size: 14px; }
.rate-tag { color: #0ecb81; font-weight: 600; }
.ledger-status { font-weight: 600; padding: 4px 10px; border-radius: 6px; font-size: 12px; text-align: right; }
.status-profit { background: rgba(14, 203, 129, 0.15); color: #0ecb81; }
.status-cancel { background: rgba(246, 70, 93, 0.15); color: #f6465d; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>⚡ Multi-Coin Funding Engine</h1>
    <div class="badge" id="ws-status">RUNNING</div>
  </div>
  <div class="grid">
    <div class="card"><div class="card-title">Active Slots</div><div class="card-value" id="slots">0 / 3 Max</div></div>
    <div class="card"><div class="card-title">Clock Drift</div><div class="card-value" id="offset">0.0 ms</div></div>
  </div>

  <div class="section-card">
    <div style="font-size: 14px; font-weight: 600; margin-bottom: 6px; color: #f7a600;">🔥 Currently Active Coins / Trades</div>
    <div class="coin-list" id="active-coins-box">
      <div style="color: #848e9c; font-size: 12px; text-align: center; padding: 10px;">No active trades right now.</div>
    </div>
  </div>
  
  <div class="section-card">
    <div style="font-size: 14px; font-weight: 600; margin-bottom: 10px;">📋 Execution Ledger</div>
    <div class="ledger-container" id="ledger-box">
      <div style="color: #848e9c; text-align: center; padding: 15px;">No trades executed yet.</div>
    </div>
  </div>

  <div class="section-card">
    <div style="font-size: 14px; font-weight: 600; margin-bottom: 10px;">🖥 System Logs</div>
    <div id="log-box"></div>
  </div>
</div>
<script>
async function updateDashboard() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    document.getElementById('slots').innerText = data.active_slots;
    document.getElementById('offset').innerText = data.clock_offset_ms + ' ms';
    
    const activeCoinsBox = document.getElementById('active-coins-box');
    if (data.active_trades && data.active_trades.length > 0) {
      activeCoinsBox.innerHTML = data.active_trades.map(t => `
        <div class="coin-chip">
          <div><span style="font-weight:700; color:#f7a600;">${t.symbol}</span> <span style="font-size:11px; color:#848e9c;">(${t.window})</span></div>
          <div style="color: #0ecb81; font-weight: 600;">Rate: ${t.rate}</div>
        </div>
      `).join('');
    } else {
      activeCoinsBox.innerHTML = '<div style="color: #848e9c; font-size: 12px; text-align: center; padding: 10px;">No active trades right now.</div>';
    }

    const logBox = document.getElementById('log-box');
    logBox.innerHTML = data.logs.map(l => `<div>${l}</div>`).join('');
    logBox.scrollTop = logBox.scrollHeight;

    const ledgerBox = document.getElementById('ledger-box');
    if (data.ledger && data.ledger.length > 0) {
      ledgerBox.innerHTML = data.ledger.map(item => `
        <div class="ledger-item">
          <div class="ledger-info">
            <div class="coin-name">${item.coin} <span class="rate-tag">(${item.rate})</span></div>
            <div style="color: #848e9c; font-size: 11px;">Entry: ${item.entry_time} | Exit: ${item.exit_time}</div>
          </div>
          <div class="ledger-status ${item.status_class}">${item.status_text}</div>
        </div>
      `).join('');
    }
  } catch (e) {}
}
setInterval(updateDashboard, 1000);
updateDashboard();
</script>
</body>
</html>"""
        self.wfile.write(html_content.encode("utf-8"))


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = ThreadingHTTPServer(('0.0.0.0', port), FundingDashboardHandler)
    server.serve_forever()


threading.Thread(target=run_web_server, daemon=True).start()


# ==========================================
# 4. WEBSOCKET FAST-LANE PIPELINE
# ==========================================

API_KEY = os.environ.get('BINANCE_API_KEY', 'YOUR_API_KEY_HERE')
API_SECRET = os.environ.get('BINANCE_API_SECRET', 'YOUR_API_SECRET_HERE')

MIN_FUNDING_RATE_THRESHOLD = -0.0045  # -0.45% Threshold
INITIAL_ENTRY_MARGIN_USD = 1.0        # $1 Initial Margin
MAX_CONCURRENT_TRADES = 3             # Max 3 coins simultaneously

ws_client = None
ws_ready = True


def connect_websocket():
    while True:
        try:
            ws_url = "wss://fstream.binance.com/ws/!miniTicker@arr"
            ws_client = websocket.WebSocketApp(
                ws_url,
                on_open=lambda ws: add_ui_log("Market Stream WebSocket Connected."),
                on_message=lambda ws, msg: None,
                on_error=lambda ws, err: None,
                on_close=lambda ws, c, m: None
            )
            ws_client.run_forever()
        except Exception:
            pass
        time.sleep(5)


threading.Thread(target=connect_websocket, daemon=True).start()


# ==========================================
# 5. MARKET SCANNER & ORDER CALCULATIONS
# ==========================================

TRADFI_BLACKLIST = ["UNITREE", "TSLA", "AAPL", "NVDA", "AMZN", "MSFT", "GOOG", "META", "SPX", "NDX", "XAU", "XAG"]

def scan_top_funding_opportunities(http_client, max_count=3):
    try:
        tickers = http_client.get_tickers(category="linear")["result"]["list"]
    except Exception:
        return []

    now_ms = int(get_synced_time_ms())
    candidates = []

    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
            
        base_name = sym.replace("USDT", "")
        if any(black in base_name for black in TRADFI_BLACKLIST):
            continue

        raw_rate = t.get("fundingRate", "")
        raw_next = t.get("nextFundingTime", "")
        if not raw_rate or not raw_next:
            continue
        try:
            rate = float(raw_rate)
            next_time_ms = int(raw_next)
        except ValueError:
            continue

        time_diff = next_time_ms - now_ms
        
        if 0 < time_diff <= 8 * 3600 * 1000 and rate <= MIN_FUNDING_RATE_THRESHOLD:
            window_type = '1H' if time_diff <= 1.5 * 3600 * 1000 else ('4H' if time_diff <= 4.5 * 3600 * 1000 else '8H')
            candidates.append({
                "symbol": sym,
                "funding_rate": rate,
                "next_funding_time": next_time_ms,
                "last_price": float(t.get("lastPrice", 0)),
                "window_type": window_type
            })

    candidates.sort(key=lambda x: x['funding_rate'])
    return candidates[:max_count]


def calculate_qty_for_price(http_client, symbol, price, margin_usd=1.0):
    try:
        inst_info = http_client.get_instruments_info(category="linear", symbol=symbol)["result"]["list"][0]
        exchange_max_lev = float(inst_info.get("leverageFilter", {}).get("maxLeverage", 20))
        max_leverage = min(exchange_max_lev, 20.0)
        
        price_filter = inst_info.get("priceFilter", {})
        tick_size = float(price_filter.get("tickSize", "0.0001"))
        lot_filter = inst_info['lotSizeFilter']
        qty_step = float(lot_filter["qtyStep"])
        if qty_step <= 0:
            qty_step = 1.0
        min_qty = float(lot_filter['minOrderQty'])
    except Exception:
        max_leverage = 20.0
        tick_size = 0.0001
        qty_step = 1.0
        min_qty = 1.0

    try:
        http_client.set_leverage(category="linear", symbol=symbol, buyLeverage=str(max_leverage), sellLeverage=str(max_leverage))
    except Exception:
        pass

    qty_str = f"{qty_step:.8f}".rstrip("0")
    qty_decimals = len(qty_str.split(".")[1]) if "." in qty_str else 0

    tick_str = f"{tick_size:.8f}".rstrip("0")
    price_decimals = len(tick_str.split(".")[1]) if "." in tick_str else 2

    if price < 1.0:
        price_decimals = max(price_decimals, 5)
    elif price < 10.0:
        price_decimals = max(price_decimals, 4)

    notional = margin_usd * max_leverage
    calc_qty = max(min_qty, math.floor((notional / price) / qty_step) * qty_step)
    
    if qty_decimals > 0:
        calc_qty = round(calc_qty, qty_decimals)
        qty_formatted = f"{calc_qty:.{qty_decimals}f}"
    else:
        qty_formatted = str(int(round(calc_qty)))

    return qty_formatted, max_leverage, price_decimals, qty_step, min_qty, qty_decimals


def cancel_all_open_orders(http_client, symbol):
    try:
        http_client.cancel_all_orders(category="linear", symbol=symbol)
        add_ui_log(f"🛑 ALL PENDING LIMIT ORDERS CANCELLED for {symbol}")
    except Exception as e:
        add_ui_log(f"⚠ Failed to cancel open orders: {e}")


# ==========================================
# 6. INDIVIDUAL COIN TRADE WORKER (WITH 15-MIN RE-ARM & FUNDING UPDATE)
# ==========================================

def run_trade_worker(http_client, opportunity):
    symbol = opportunity['symbol']
    rate = opportunity['funding_rate']
    settle_epoch = opportunity['next_funding_time']
    window_type = opportunity['window_type']
    
    rate_percent = f"{rate * 100:+.4f}%"

    with data_lock:
        dashboard_data['active_trades'].append({
            "symbol": symbol,
            "window": window_type,
            "rate": rate_percent
        })

    try:
        last_scan_time = time.time()

        while True:
            now_ms = get_synced_time_ms()
            t_rescan = settle_epoch - 65000
            t_entry = settle_epoch - 5000  # T - 5 seconds

            if now_ms >= t_rescan:
                break

            time.sleep(2.0)

            # Har 15 minute (900 seconds) me funding rate aur settlement time ko update (re-arm) karein
            if time.time() - last_scan_time >= 900:
                last_scan_time = time.time()
                add_ui_log(f"🔄 15-MIN RE-ARM & FUNDING UPDATE for {symbol}...")
                sync_binance_clock(http_client)
                try:
                    tickers = http_client.get_tickers(category="linear")["result"]["list"]
                    for t in tickers:
                        if t.get("symbol") == symbol:
                            new_rate = float(t.get("fundingRate", rate))
                            new_next = int(t.get("nextFundingTime", settle_epoch))
                            if new_next > 0:
                                rate = new_rate
                                settle_epoch = new_next
                                rate_percent = f"{rate * 100:+.4f}%"
                                
                                # Dashboard par rate update karein
                                with data_lock:
                                    for tr in dashboard_data['active_trades']:
                                        if tr['symbol'] == symbol:
                                            tr['rate'] = rate_percent
                                add_ui_log(f"🔄 Re-armed & Updated [{window_type}]: {symbol} | Rate: {rate_percent}")
                            break
                except Exception as e:
                    add_ui_log(f"⚠ Failed to re-arm funding for {symbol}: {e}")

        # T-65s pre-entry final rescan check
        precision_wait_until(settle_epoch - 65000)
        sync_binance_clock(http_client)

        t_entry = settle_epoch - 5000
        precision_wait_until(t_entry)
        entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
        
        try:
            live_ticker = http_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
            live_price = float(live_ticker.get("lastPrice", opportunity['last_price']))
        except Exception:
            live_price = opportunity['last_price']

        initial_qty, leverage, price_decimals, qty_step, min_qty, qty_decimals = calculate_qty_for_price(
            http_client, symbol, live_price, INITIAL_ENTRY_MARGIN_USD
        )
        
        entry_success = False
        try:
            http_client.place_order(category="linear", symbol=symbol, side="Buy", orderType="Market", qty=initial_qty, positionIdx=0)
            add_ui_log(f"🚀 INITIAL MARKET ENTRY (T-5s) [BUY]: {initial_qty} {symbol} ($1 Margin @ {leverage}x)")
            entry_success = True
        except Exception as e:
            add_ui_log(f"Initial entry failed for {symbol}: {e}")

        if entry_success:
            time.sleep(0.3)
            current_avg_price = live_price
            try:
                pos_info = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
                current_avg_price = float(pos_info.get("avgPrice", current_avg_price))
            except Exception:
                pass

            # 1 LIMIT ORDER AT 110% OF FUNDING RATE OFFSET
            abs_funding_pct = abs(rate)
            dca_offset = abs_funding_pct * 1.10
            dca_target_price = round(current_avg_price * (1.0 - dca_offset), price_decimals)

            if dca_target_price > 0:
                safe_qty_step = qty_step if (qty_step and qty_step > 0) else 1.0
                notional = INITIAL_ENTRY_MARGIN_USD * leverage
                calc_dca_qty = max(min_qty, math.floor((notional / dca_target_price) / safe_qty_step) * safe_qty_step)
                dca_qty = f"{calc_dca_qty:.{qty_decimals}f}" if qty_decimals > 0 else str(int(calc_dca_qty))

                try:
                    http_client.place_order(
                        category="linear",
                        symbol=symbol,
                        side="Buy",
                        orderType="Limit",
                        price=f"{dca_target_price:.{price_decimals}f}",
                        qty=dca_qty,
                        positionIdx=0
                    )
                    add_ui_log(f"📐 [{symbol}] 1 LIMIT ORDER PLACED AT 110% FUNDING (-{dca_offset*100:.2f}%) | Price: {dca_target_price:.{price_decimals}f} | Qty: {dca_qty}")
                except Exception as e:
                    add_ui_log(f"[{symbol}] Limit Order Failed: {e}")

            last_known_avg = current_avg_price
            stop_loss_placed = False
            t_cancel = settle_epoch + 7000
            t_exit = settle_epoch + 8000
            t_emergency = settle_epoch + 10000
            cancelled_at_7s = False

            while True:
                now_ms = get_synced_time_ms()
                
                if now_ms >= t_cancel and not cancelled_at_7s:
                    cancel_all_open_orders(http_client, symbol)
                    cancelled_at_7s = True

                if now_ms >= t_exit:
                    break

                try:
                    pos_info = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
                    pos_size = float(pos_info.get("size", 0))

                    if pos_size == 0:
                        add_ui_log(f"✅ [{symbol}] POSITION CLOSED! Cancelling remaining orders...")
                        cancel_all_open_orders(http_client, symbol)
                        break

                    new_avg = float(pos_info.get("avgPrice", last_known_avg))
                    
                    if not stop_loss_placed and new_avg > 0 and abs(new_avg - last_known_avg) > (10 ** -price_decimals):
                        last_known_avg = new_avg
                        sl_price = round(new_avg * 0.99, price_decimals)
                        
                        http_client.set_stop_loss(
                            category="linear",
                            symbol=symbol,
                            stopPrice=f"{sl_price:.{price_decimals}f}",
                            positionIdx=0
                        )
                        stop_loss_placed = True
                        add_ui_log(f"🔄 [{symbol}] LIMIT ORDER FILLED & CONFIRMED! INSTANT STOP LOSS PLACED at -1% -> New Avg: {new_avg:.{price_decimals}f} | SL: {sl_price:.{price_decimals}f}")

                except Exception:
                    pass

                time.sleep(0.2)

        precision_wait_until(settle_epoch + 8000)
        exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
        
        status_text = "SAFE EXIT FAILED"
        status_class = "status-cancel"

        try:
            pos_info = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
            pos_qty = float(pos_info.get("size", 0))

            if pos_qty > 0:
                cancel_all_open_orders(http_client, symbol)
                http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=str(pos_qty), reduceOnly=True, positionIdx=0)
                add_ui_log(f"⏰ [{symbol}] HARD EXIT DISPATCHED (SELL MARKET): {pos_qty} at {exit_time_str}")
                status_text = "HARD EXIT (T+8s)"
                status_class = "status-cancel"
            else:
                status_text = "EXITED SUCCESSFULLY"
                status_class = "status-profit"
        except Exception as e:
            add_ui_log(f"[{symbol}] Exit check failed: {e}")

        precision_wait_until(settle_epoch + 10000)
        try:
            pos_info = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
            pos_qty = float(pos_info.get("size", 0))

            if pos_qty > 0:
                add_ui_log(f"🚨 [{symbol}] EMERGENCY FALLBACK EXIT (T+10s): Forcing clean close...")
                cancel_all_open_orders(http_client, symbol)
                for attempt in range(3):
                    try:
                        http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=str(pos_qty), reduceOnly=True, positionIdx=0)
                        time.sleep(0.3)
                        check_pos = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
                        if float(check_pos.get("size", 0)) == 0:
                            break
                    except Exception:
                        time.sleep(0.5)
                status_text = "EMERGENCY CLEANUP"
                status_class = "status-cancel"
        except Exception:
            pass

        add_ledger_entry({
            "coin": f"{symbol} ({window_type})",
            "rate": rate_percent,
            "entry_time": entry_time_str,
            "exit_time": exit_time_str,
            "status_text": status_text,
            "status_class": status_class
        })

    except Exception as e:
        add_ui_log(f"⚠ Worker exception for {symbol}: {e}")
    finally:
        with data_lock:
            dashboard_data['active_trades'] = [t for t in dashboard_data['active_trades'] if t['symbol'] != symbol]


# ==========================================
# 7. MAIN MULTI-COIN SCANNER LOOP
# ==========================================

def run_funding_capture_engine():
    http_client = BinanceHTTP(api_key=API_KEY, api_secret=API_SECRET)
    add_ui_log("Multi-Coin Funding Engine Active ($1 Margin | Threshold: -0.45% | Max 3 Coins | 15-min Re-arm)...")

    active_symbols = set()

    while True:
        try:
            sync_binance_clock(http_client)
            opportunities = scan_top_funding_opportunities(http_client, max_count=MAX_CONCURRENT_TRADES)

            with data_lock:
                active_count = len(active_symbols)
                dashboard_data['active_slots'] = f"{active_count} / {MAX_CONCURRENT_TRADES} Max"

            if not opportunities:
                with data_lock:
                    dashboard_data['status'] = "SCANNING: No coin <= -0.45% found"
                time.sleep(3)
                continue

            with data_lock:
                dashboard_data['status'] = f"FOUND {len(opportunities)} OPPORTUNITIES. Spawning threads..."

            for opp in opportunities:
                sym = opp['symbol']
                if sym not in active_symbols and len(active_symbols) < MAX_CONCURRENT_TRADES:
                    active_symbols.add(sym)
                    
                    def worker_wrapper(o=opp, s=sym):
                        try:
                            run_trade_worker(http_client, o)
                        finally:
                            active_symbols.discard(s)

                    threading.Thread(target=worker_wrapper, daemon=True).start()
                    add_ui_log(f"🚀 Spawned dedicated thread for {sym} ({opp['funding_rate']*100:+.4f}%)")

            time.sleep(5)
        except Exception as outer_e:
            add_ui_log(f"⚠ Main loop exception caught: {outer_e}")
            time.sleep(3)


if __name__ == '__main__':
    run_funding_capture_engine()
