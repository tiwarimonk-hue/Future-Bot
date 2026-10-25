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
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==========================================
# 1. TIMEZONE & DASHBOARD TELEMETRY STATE
# ==========================================

IST = timezone(timedelta(hours=5, minutes=30))
data_lock = threading.Lock()

dashboard_data = {
    "target_symbol": "Scanning Market...",
    "funding_rate": "0.00%",
    "action_direction": "--",
    "status": "Initializing WebSocket Funding Scanner...",
    "target_settlement": "-- IST",
    "entry_target": "-- IST (T - 5000ms)",
    "exit_target": "-- IST (T + 8000ms / Emergency T+10s)",
    "clock_offset_ms": 0.0,
    "logs": [],
    "ledger": []
}

def add_ui_log(message):
    timestamp = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
    line = f'[{timestamp}] {message}'
    print(line)
    with data_lock:
        dashboard_data['logs'].append(line)
        if len(dashboard_data['logs']) > 80:
            dashboard_data['logs'].pop(0)

def add_ledger_entry(trade_info):
    with data_lock:
        dashboard_data['ledger'].insert(0, trade_info)
        if len(dashboard_data['ledger']) > 25:
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

    def place_stop_loss(self, symbol=None, stopPrice=None, workingType="MARK_PRICE"):
        url = f"{self.base_url}/fapi/v1/order"
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "STOP_MARKET",
            "stopPrice": str(stopPrice),
            "closePosition": "true",
            "workingType": workingType,
            "timestamp": int(time.time() * 1000)
        }
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
# 3. WEB DASHBOARD
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
<title>Binance WebSocket Funding Engine</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }
body { background-color: #0b0e11; color: #eaecef; padding: 20px; display: flex; justify-content: center; }
.container { width: 100%; max-width: 1100px; display: flex; flex-direction: column; gap: 16px; }
.header { background: #1e2329; padding: 18px 24px; border-radius: 12px; border: 1px solid #2b313a; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 18px; color: #f7a600; }
.badge { background: rgba(14, 203, 129, 0.15); color: #0ecb81; padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 600; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; }
.card { background: #1e2329; padding: 16px; border-radius: 12px; border: 1px solid #2b313a; }
.card-title { font-size: 11px; color: #848e9c; text-transform: uppercase; margin-bottom: 6px; }
.card-value { font-size: 18px; font-weight: 700; color: #f7a600; }
.section-card { background: #1e2329; padding: 18px; border-radius: 12px; border: 1px solid #2b313a; }
#log-box { background: #0b0e11; border: 1px solid #2b313a; border-radius: 8px; padding: 14px; height: 220px; overflow-y: auto; font-family: 'Courier New', monospace; font-size: 12px; color: #0ecb81; line-height: 1.5; }
.ledger-container { max-height: 350px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; margin-top: 10px; }
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
    <h1>⚡ Binance WebSocket Funding Engine ($1 Margin + 110% Limit + 1% SL)</h1>
    <div class="badge" id="ws-status">INITIALIZING</div>
  </div>
  <div class="grid">
    <div class="card"><div class="card-title">Top Target Coins (Max 3)</div><div class="card-value" id="coin" style="font-size:13px;">-</div></div>
    <div class="card"><div class="card-title">Funding Rate</div><div class="card-value" id="rate" style="color:#0ecb81;">-</div></div>
    <div class="card"><div class="card-title">Direction</div><div class="card-value" id="direction" style="color:#f7a600; font-size:13px;">--</div></div>
    <div class="card"><div class="card-title">Entry (T - 5s)</div><div class="card-value" id="entry" style="color:#0ecb81; font-size:13px;">-</div></div>
    <div class="card"><div class="card-title">Exit (T + 8s / Emergency)</div><div class="card-value" id="exit" style="color:#f6465d; font-size:12px;">-</div></div>
    <div class="card"><div class="card-title">Clock Drift</div><div class="card-value" id="offset">0.0 ms</div></div>
  </div>
  
  <div class="section-card">
    <div style="font-size: 14px; font-weight: 600; margin-bottom: 10px;">📋 Execution Ledger</div>
    <div class="ledger-container" id="ledger-box">
      <div style="color: #848e9c; text-align: center; padding: 20px;">No trades executed yet.</div>
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
    document.getElementById('ws-status').innerText = data.status;
    document.getElementById('coin').innerText = data.target_symbol;
    document.getElementById('rate').innerText = data.funding_rate;
    document.getElementById('direction').innerText = data.action_direction;
    document.getElementById('entry').innerText = data.entry_target;
    document.getElementById('exit').innerText = data.exit_target;
    document.getElementById('offset').innerText = data.clock_offset_ms + ' ms';
    
    const logBox = document.getElementById('log-box');
    logBox.innerHTML = data.logs.map(l => `<div>${l}</div>`).join('');
    logBox.scrollTop = logBox.scrollHeight;

    const ledgerBox = document.getElementById('ledger-box');
    if (data.ledger && data.ledger.length > 0) {
      ledgerBox.innerHTML = data.ledger.map(item => `
        <script>
        <div class="ledger-item">
          <div class="ledger-info">
            <div class="coin-name">${item.coin} <span class="rate-tag">(${item.rate})</span></div>
            <div style="color: #848e9c; font-size: 11px;">Entry Sent: ${item.entry_time} | Exit Closed: ${item.exit_time}</div>
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
    server = HTTPServer(('0.0.0.0', port), FundingDashboardHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ==========================================
# 4. WEBSOCKET REAL-TIME DATA ENGINE
# ==========================================

API_KEY = os.environ.get('BINANCE_API_KEY', 'YOUR_API_KEY_HERE')
API_SECRET = os.environ.get('BINANCE_API_SECRET', 'YOUR_API_SECRET_HERE')

MIN_FUNDING_RATE_THRESHOLD = -0.0045   # -0.45% Threshold
INITIAL_ENTRY_MARGIN_USD = 1.0         # $1 Initial Margin

live_market_data = {}  # Store real-time prices & info from websocket

def start_websocket_stream():
    def on_message(ws, message):
        try:
            data = json.loads(message)
            # Handling !miniTicker@arr stream
            if isinstance(data, list):
                for tick in data:
                    sym = tick.get('s')
                    price = float(tick.get('c', 0))
                    if sym:
                        if sym not in live_market_data:
                            live_market_data[sym] = {}
                        live_market_data[sym]['lastPrice'] = price
            elif isinstance(data, dict):
                # Handling single streams if needed
                if 'data' in data:
                    tick = data['data']
                    sym = tick.get('s')
                    price = float(tick.get('c', 0))
                    if sym:
                        if sym not in live_market_data:
                            live_market_data[sym] = {}
                        live_market_data[sym]['lastPrice'] = price
        except Exception:
            pass

    def on_open(ws):
        add_ui_log("🚀 Binance WebSocket Stream Connected Successfully!")

    def on_error(ws, error):
        pass

    def on_close(ws, close_status_code, close_msg):
        add_ui_log("⚠ WebSocket Closed. Reconnecting in 5s...")

    while True:
        try:
            ws_url = "wss://fstream.binance.com/ws/!miniTicker@arr"
            ws = websocket.WebSocketApp(
                ws_url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception:
            pass
        time.sleep(5)

threading.Thread(target=start_websocket_stream, daemon=True).start()

# ==========================================
# 5. REST + WEBSOCKET HYBRID SCANNER
# ==========================================

def scan_top_funding_opportunities(http_client, limit=3):
    try:
        # Fetching premiumIndex via REST only once every scan cycle (avoids rate limit)
        url_pi = f"{http_client.base_url}/fapi/v1/premiumIndex"
        resp = http_client.session.get(url_pi)
        pi_data = resp.json()

        if not isinstance(pi_data, list):
            return []

        now_ms = int(get_synced_time_ms())
        candidates = []

        for item in pi_data:
            sym = item.get("symbol", "")
            if not sym.endswith("USDT"):
                continue

            raw_rate = item.get("lastFundingRate", "0")
            raw_next = item.get("nextFundingTime", 0)
            if not raw_rate or not raw_next:
                continue

            try:
                rate = float(raw_rate)
                next_time_ms = int(raw_next)
            except ValueError:
                continue

            time_diff = next_time_ms - now_ms

            if 0 < time_diff <= 24 * 3600 * 1000:
                if rate <= MIN_FUNDING_RATE_THRESHOLD:
                    # Get live price from websocket cache if available, else fallback to markPrice
                    live_p = live_market_data.get(sym, {}).get('lastPrice', float(item.get('markPrice', 0)))
                    candidates.append({
                        "symbol": sym,
                        "funding_rate": rate,
                        "next_funding_time": next_time_ms,
                        "last_price": live_p,
                        "window_type": "1H" if time_diff <= 1.5 * 3600 * 1000 else ("4H" if time_diff <= 4.5 * 3600 * 1000 else "8H")
                    })

        candidates.sort(key=lambda x: x['funding_rate'])
        return candidates[:limit]
    except Exception as e:
        add_ui_log(f"⚠ Scanner error: {e}")
        return []

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
    qty_formatted = f"{calc_qty:.{qty_decimals}f}" if qty_decimals > 0 else str(int(calc_qty))

    return qty_formatted, max_leverage, price_decimals, qty_step, min_qty, qty_decimals

# ==========================================
# 6. INDIVIDUAL COIN TRADE EXECUTION THREAD
# ==========================================

def execute_single_coin_trade(http_client, opportunity):
    symbol = opportunity['symbol']
    rate = opportunity['funding_rate']
    settle_epoch = opportunity['next_funding_time']
    window_type = opportunity['window_type']
    rate_percent = f"{rate * 100:+.4f}%"
    
    t_entry = settle_epoch - 5000       # T - 5s
    t_cancel = settle_epoch + 3000      # T + 3s
    t_exit = settle_epoch + 8000        # T + 8s
    t_emergency = settle_epoch + 10000  # T + 10s

    entry_ist = datetime.fromtimestamp(t_entry / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]
    exit_ist = datetime.fromtimestamp(t_exit / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]

    with data_lock:
        dashboard_data['entry_target'] = f"{entry_ist} IST (T - 5000ms)"
        dashboard_data['exit_target'] = f"{exit_ist} IST (Emergency T+10s)"

    add_ui_log(f"🎯 ARMED [{window_type}]: {symbol} | Rate: {rate_percent} | Entry: T-5s")

    precision_wait_until(t_entry)
    entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
    
    # Get latest price from websocket cache or fallback
    live_price = live_market_data.get(symbol, {}).get('lastPrice', opportunity['last_price'])

    qty, leverage, price_decimals, qty_step, min_qty, qty_decimals = calculate_qty_for_price(
        http_client, symbol, live_price, INITIAL_ENTRY_MARGIN_USD
    )
    
    abs_funding_pct = abs(rate)
    limit_offset = abs_funding_pct * 1.10
    limit_price = round(live_price * (1.0 - limit_offset), price_decimals)
    
    add_ui_log(f"📐 PLACING 110% FUNDING LIMIT BUY: {symbol} at {limit_price:.{price_decimals}f} (-{limit_offset*100:.2f}%) | $1 Margin | Qty: {qty}")

    try:
        http_client.place_order(
            category="linear",
            symbol=symbol,
            side="Buy",
            orderType="Limit",
            price=f"{limit_price:.{price_decimals}f}",
            qty=qty,
            positionIdx=0
        )
        add_ui_log(f"🚀 LIMIT ORDER PLACED (BUY): {qty} {symbol} @ {limit_price:.{price_decimals}f}")
    except Exception as e:
        add_ui_log(f"Limit order failed for {symbol}: {e}")
        return

    sl_set = False
    fill_price = limit_price

    while True:
        now_ms = get_synced_time_ms()
        if now_ms >= t_cancel:
            break
            
        try:
            pos_info = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
            pos_size = float(pos_info.get("size", 0))
            avg_price = float(pos_info.get("avgPrice", 0))
            
            if pos_size > 0 and not sl_set:
                fill_price = avg_price if avg_price > 0 else limit_price
                sl_price = round(fill_price * 0.99, price_decimals)
                
                try:
                    http_client.place_stop_loss(
                        symbol=symbol,
                        stopPrice=f"{sl_price:.{price_decimals}f}",
                        workingType="MARK_PRICE"
                    )
                    add_ui_log(f"🛑 STOP-LOSS SET AT: {sl_price:.{price_decimals}f} (-1.0% from fill {fill_price})")
                    sl_set = True
                except Exception as sle:
                    add_ui_log(f"⚠ Failed to set Stop-Loss for {symbol}: {sle}")
                break
        except Exception:
            pass
        time.sleep(0.3)

    try:
        http_client.cancel_all_orders(category="linear", symbol=symbol)
    except Exception:
        pass

    precision_wait_until(t_exit)
    exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
    
    status_text = "CLOSED"
    status_class = "status-profit"

    try:
        pos_info = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
        pos_qty = float(pos_info.get("size", 0))

        if pos_qty > 0:
            http_client.cancel_all_orders(category="linear", symbol=symbol)
            http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=str(pos_qty), reduceOnly=True, positionIdx=0)
            add_ui_log(f"⏰ HARD EXIT DISPATCHED (SELL MARKET): {pos_qty} {symbol} at {exit_time_str}")
            status_text = "HARD EXIT (T+8s)"
            status_class = "status-cancel"
        else:
            status_text = "CAPTURED / SL HIT"
            status_class = "status-profit"
    except Exception as e:
        add_ui_log(f"Exit check failed for {symbol}: {e}")

    precision_wait_until(t_emergency)
    try:
        pos_info = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
        pos_qty = float(pos_info.get("size", 0))

        if pos_qty > 0:
            add_ui_log(f"🚨 EMERGENCY FALLBACK EXIT (T+10s): {symbol} position still open ({pos_qty}). Forcing clean close...")
            http_client.cancel_all_orders(category="linear", symbol=symbol)
            http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=str(pos_qty), reduceOnly=True, positionIdx=0)
    except Exception as e:
        add_ui_log(f"Emergency exit check failed for {symbol}: {e}")

    add_ledger_entry({
        "coin": f"{symbol} ({window_type})",
        "rate": rate_percent,
        "entry_time": entry_time_str,
        "exit_time": exit_time_str,
        "status_text": status_text,
        "status_class": status_class
    })

# ==========================================
# 7. MAIN SCANNER LOOP
# ==========================================

def run_funding_capture_engine():
    http_client = BinanceHTTP(api_key=API_KEY, api_secret=API_SECRET)
    add_ui_log("WebSocket Funding Engine Active ($1 Margin | Threshold: -0.45% | 5-Min Scan). Scanner Starting...")

    processed_trades = set()

    while True:
        try:
            sync_binance_clock(http_client)
            opportunities = scan_top_funding_opportunities(http_client, limit=3)

            if not opportunities:
                with data_lock:
                    dashboard_data['target_symbol'] = "Scanning..."
                    dashboard_data['funding_rate'] = "0.00%"
                    dashboard_data['action_direction'] = "--"
                    dashboard_data['status'] = "SCANNING: No coin <= -0.45% found"
                time.sleep(30)
                continue

            coins_str = ", ".join([f"{o['symbol']} ({o['funding_rate']*100:+.2f}%)" for o in opportunities])
            with data_lock:
                dashboard_data['target_symbol'] = coins_str
                dashboard_data['funding_rate'] = f"{opportunities[0]['funding_rate']*100:+.2f}% (Top)"
                dashboard_data['action_direction'] = "LONG + 110% Limit + 1% SL"
                dashboard_data['status'] = f"ARMED: Tracking {len(opportunities)} coin(s)"

            add_ui_log(f"🔍 Rescan completed. Top opportunities: {coins_str}")

            for opp in opportunities:
                trade_key = (opp['symbol'], opp['next_funding_time'])
                if trade_key not in processed_trades:
                    processed_trades.add(trade_key)
                    threading.Thread(target=execute_single_coin_trade, args=(http_client, opp), daemon=True).start()

            time.sleep(300)
        except Exception as outer_e:
            add_ui_log(f"⚠ Main loop exception caught (Prevented Crash): {outer_e}")
            time.sleep(15)

if __name__ == '__main__':
    run_funding_capture_engine()
