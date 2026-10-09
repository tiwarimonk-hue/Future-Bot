import os
import time
import json
import hmac
import hashlib
import uuid
import math
import threading
import urllib.parse
import requests
import websocket
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
    "status": "Initializing Dynamic Priority Scanner...",
    "target_settlement": "-- IST",
    "entry_target": "-- IST (T + 950ms)",
    "exit_target": "-- IST (T + 8000ms / Hard Exit)",
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
        if len(dashboard_data['logs']) > 70:
            dashboard_data['logs'].pop(0)

def add_ledger_entry(trade_info):
    with data_lock:
        dashboard_data['ledger'].insert(0, trade_info)
        if len(dashboard_data['ledger']) > 20:
            dashboard_data['ledger'].pop()


# ==========================================
# 2. BINANCE HTTP CLIENT & CLOCK SYNC
# ==========================================

class BinanceHTTP:
    def __init__(self, testnet=False, api_key="", api_secret=""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
        self.session = requests.Session()
        if api_key:
            self.session.headers.update({"X-MBX-APIKEY": api_key})

    def _sign(self, params):
        params['timestamp'] = int(time.time() * 1000)
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return query_string + f"&signature={signature}"

    def get_server_time(self):
        resp = self.session.get(f"{self.base_url}/fapi/v1/time")
        data = resp.json()
        server_time_ms = data.get("serverTime", int(time.time() * 1000))
        # Structuring like Bybit response so sync_bybit_clock works seamlessly
        return {"result": {"timeNano": int(server_time_ms * 1e6)}}

    def get_tickers(self, category="linear"):
        prem_resp = self.session.get(f"{self.base_url}/fapi/v1/premiumIndex").json()
        price_resp = self.session.get(f"{self.base_url}/fapi/v1/ticker/price").json()
        price_map = {item['symbol']: float(item['price']) for item in price_resp}
        
        result_list = []
        if isinstance(prem_resp, list):
            for item in prem_resp:
                sym = item.get("symbol")
                if not sym:
                    continue
                result_list.append({
                    "symbol": sym,
                    "fundingRate": item.get("lastFundingRate", "0"),
                    "nextFundingTime": item.get("nextFundingTime", 0),
                    "lastPrice": price_map.get(sym, 0.0)
                })
        return {"result": {"list": result_list}}

    def get_instruments_info(self, category="linear", symbol=None):
        exchange_info = self.session.get(f"{self.base_url}/fapi/v1/exchangeInfo").json()
        symbols_info = exchange_info.get("symbols", [])
        
        try:
            brackets = self.session.get(f"{self.base_url}/fapi/v1/leverageBracket").json()
            bracket_map = {b['symbol']: b['brackets'][0]['initialLeverage'] for b in brackets if b.get('brackets')}
        except Exception:
            bracket_map = {}

        list_out = []
        for s in symbols_info:
            if symbol and s['symbol'] != symbol:
                continue
            sym = s['symbol']
            max_lev = bracket_map.get(sym, 20)
            
            tick_size = "0.0001"
            min_qty = "1"
            qty_step = "1"
            
            for f in s.get('filters', []):
                if f['filterType'] == 'PRICE_FILTER':
                    tick_size = f['tickSize']
                elif f['filterType'] == 'LOT_SIZE':
                    min_qty = f['minQty']
                    qty_step = f['stepSize']
            
            list_out.append({
                "leverageFilter": {"maxLeverage": float(max_lev)},
                "priceFilter": {"tickSize": tick_size},
                "lotSizeFilter": {"minOrderQty": min_qty, "qtyStep": qty_step}
            })
        return {"result": {"list": list_out}}

    def set_leverage(self, category="linear", symbol="", buyLeverage="", sellLeverage=""):
        params = {
            "symbol": symbol,
            "leverage": int(float(buyLeverage))
        }
        query = self._sign(params)
        resp = self.session.post(f"{self.base_url}/fapi/v1/leverage?{query}")
        return resp.json()

    def cancel_all_orders(self, category="linear", symbol=""):
        params = {"symbol": symbol}
        query = self._sign(params)
        resp = self.session.delete(f"{self.base_url}/fapi/v1/allOpenOrders?{query}")
        return resp.json()

    def place_order(self, category="linear", symbol="", side="", orderType="", qty="", reduceOnly=False, positionIdx=0):
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": orderType.upper(),
            "quantity": qty
        }
        if reduceOnly:
            params["reduceOnly"] = "true"
        query = self._sign(params)
        resp = self.session.post(f"{self.base_url}/fapi/v1/order?{query}")
        res_data = resp.json()
        if "code" in res_data and res_data["code"] != 200 and res_data["code"] != 0:
            raise Exception(res_data.get("msg", "Binance Order Error"))
        return res_data

    def get_positions(self, category="linear", symbol=""):
        params = {"symbol": symbol}
        query = self._sign(params)
        resp = self.session.get(f"{self.base_url}/fapi/v2/positionRisk?{query}")
        data = resp.json()
        positions = []
        if isinstance(data, list):
            for p in data:
                if p.get("symbol") == symbol:
                    positions.append({"size": abs(float(p.get("positionAmt", 0)))})
        return {"result": {"list": positions if positions else [{"size": 0.0}]}}


clock_offset_ms = 0.0

def sync_bybit_clock(http_client):
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
    def log_message(self, format, *args):
        return

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
<title>Binance Dynamic Funding Engine</title>
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
    <h1>⚡ Binance Dynamic Funding Engine ($3 Margin | Nearest Settlement First)</h1>
    <div class="badge" id="ws-status">INITIALIZING</div>
  </div>
  <div class="grid">
    <div class="card"><div class="card-title">Live Target Coin</div><div class="card-value" id="coin">-</div></div>
    <div class="card"><div class="card-title">Funding Rate</div><div class="card-value" id="rate" style="color:#0ecb81;">-</div></div>
    <div class="card"><div class="card-title">Direction</div><div class="card-value" id="direction" style="color:#f7a600;">--</div></div>
    <div class="card"><div class="card-title">Entry (T + 950ms)</div><div class="card-value" id="entry" style="color:#0ecb81; font-size:14px;">-</div></div>
    <div class="card"><div class="card-title">Exit (T + 8000ms)</div><div class="card-value" id="exit" style="color:#f6465d; font-size:14px;">-</div></div>
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
# 4. WEBSOCKET FAST-LANE PIPELINE
# ==========================================

API_KEY = os.environ.get('BINANCE_API_KEY', os.environ.get('BYBIT_API_KEY', 'YOUR_API_KEY_HERE'))
API_SECRET = os.environ.get('BINANCE_API_SECRET', os.environ.get('BYBIT_API_SECRET', 'YOUR_API_SECRET_HERE'))

MIN_FUNDING_RATE_THRESHOLD = -0.003  # -0.3% Threshold
INITIAL_ENTRY_MARGIN_USD = 3.0       # $3 Initial Entry Margin

ws_client = None
ws_ready = False

def on_ws_message(ws, message):
    try:
        data = json.loads(message)
    except Exception:
        return

def on_ws_open(ws):
    global ws_ready
    ws_ready = True
    with data_lock:
        dashboard_data['status'] = "WS CONNECTED (READY)"
    add_ui_log("WebSocket Connection Opened & LIVE")

def on_ws_close(ws, code, msg):
    global ws_ready
    ws_ready = False
    with data_lock:
        dashboard_data["status"] = "WS DISCONNECTED"

def on_ws_error(ws, error):
    global ws_ready
    ws_ready = False

def start_heartbeat(ws):
    def run():
        while True:
            time.sleep(20)
            try:
                ws.send(json.dumps({"method": "ping"}))
            except Exception:
                break
    threading.Thread(target=run, daemon=True).start()

def connect_websocket():
    global ws_client
    while True:
        try:
            ws_url = "wss://fstream.binance.com/ws"
            ws_client = websocket.WebSocketApp(
                ws_url,
                on_open=on_ws_open,
                on_message=on_ws_message,
                on_close=on_ws_close,
                on_error=on_ws_error
            )
            start_heartbeat(ws_client)
            ws_client.run_forever()
        except Exception:
            pass
        time.sleep(2)

threading.Thread(target=connect_websocket, daemon=True).start()


# ==========================================
# 5. MARKET SCANNER & ORDER CALCULATIONS
# ==========================================

def scan_best_funding_opportunity(http_client):
    try:
        tickers = http_client.get_tickers(category="linear")["result"]["list"]
    except Exception:
        return None

    now_ms = int(get_synced_time_ms())
    valid_candidates = []

    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
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

        if time_diff > 60000 and rate <= MIN_FUNDING_RATE_THRESHOLD:
            diff_hours = round(time_diff / (3600 * 1000), 1)
            valid_candidates.append({
                "symbol": sym,
                "funding_rate": rate,
                "next_funding_time": next_time_ms,
                "last_price": float(t.get("lastPrice", 0)),
                "window_type": f"{diff_hours}H"
            })

    if not valid_candidates:
        return None

    earliest_time = min(c['next_funding_time'] for c in valid_candidates)
    imminent_candidates = [c for c in valid_candidates if abs(c['next_funding_time'] - earliest_time) <= 600000]
    imminent_candidates.sort(key=lambda x: x['funding_rate'])

    return imminent_candidates[0]

def calculate_qty_for_price(http_client, symbol, price, margin_usd=3.0):
    try:
        inst_info = http_client.get_instruments_info(category="linear", symbol=symbol)["result"]["list"][0]
        max_leverage = float(inst_info.get("leverageFilter", {}).get("maxLeverage", 20))
        price_filter = inst_info.get("priceFilter", {})
        tick_size = float(price_filter.get("tickSize", "0.0001"))
        lot_filter = inst_info['lotSizeFilter']
        qty_step = float(lot_filter["qtyStep"])
        min_qty = float(lot_filter['minOrderQty'])
    except Exception:
        max_leverage = 20.0
        tick_size = 0.0001
        qty_step = 1.0
        min_qty = 1.0

    if max_leverage >= 50.0:
        applied_leverage = 20.0
    elif max_leverage > 25.0:
        applied_leverage = 25.0
    else:
        applied_leverage = max_leverage

    try:
        http_client.set_leverage(category="linear", symbol=symbol, buyLeverage=str(applied_leverage), sellLeverage=str(applied_leverage))
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

    notional = margin_usd * applied_leverage
    calc_qty = max(min_qty, math.floor((notional / price) / qty_step) * qty_step)
    qty_formatted = f"{calc_qty:.{qty_decimals}f}" if qty_decimals > 0 else str(int(calc_qty))

    return qty_formatted, applied_leverage, price_decimals, qty_step, min_qty, qty_decimals

def cancel_all_open_orders(http_client, symbol):
    try:
        http_client.cancel_all_orders(category="linear", symbol=symbol)
        add_ui_log(f"🛑 ALL PENDING ORDERS CANCELLED for {symbol}")
    except Exception as e:
        add_ui_log(f"⚠ Failed to cancel open orders: {e}")


# ==========================================
# 6. FUNDING CAPTURE ENGINE MAIN LOOP
# ==========================================

def run_funding_capture_engine():
    http_client = BinanceHTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
    add_ui_log("Dynamic Funding Engine Active ($3 Margin | Single Order T+950ms | Threshold -0.3%). Waiting for WS...")

    while not ws_ready:
        time.sleep(0.1)

    while True:
        sync_bybit_clock(http_client)
        opportunity = scan_best_funding_opportunity(http_client)

        if not opportunity:
            with data_lock:
                dashboard_data['target_symbol'] = "Scanning..."
                dashboard_data['funding_rate'] = "0.00%"
                dashboard_data['action_direction'] = "--"
                dashboard_data['status'] = "SCANNING: No coin <= -0.3% found"
            time.sleep(3)
            continue

        symbol = opportunity['symbol']
        rate = opportunity['funding_rate']
        settle_epoch = opportunity['next_funding_time']
        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
        rate_percent = f"{rate * 100:+.4f}%"
        window_type = opportunity['window_type']

        with data_lock:
            dashboard_data['target_symbol'] = f"{symbol} ({window_type})"
            dashboard_data['funding_rate'] = rate_percent
            dashboard_data['target_settlement'] = settle_dt.strftime('%H:%M:%S IST')
            dashboard_data['action_direction'] = "LONG CAPTURE ($3 MARGIN)"

        t_rescan = settle_epoch - 65000
        t_entry = settle_epoch + 950    # Entry at T + 950ms
        t_exit = settle_epoch + 8000    # Hard exit at T + 8000ms (T + 8s)

        entry_ist = datetime.fromtimestamp(t_entry / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]
        exit_ist = datetime.fromtimestamp(t_exit / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]

        with data_lock:
            dashboard_data['entry_target'] = f"{entry_ist} IST (T + 950ms)"
            dashboard_data['exit_target'] = f"{exit_ist} IST (T + 8000ms / Hard Exit)"
            dashboard_data['status'] = f"ARMED [{window_type}]: {symbol} | Rate: {rate_percent}"

        add_ui_log(f"🎯 TARGET ARMED [{window_type}]: {symbol} | Rate: {rate_percent} | Entry Target: T+950ms")

        last_scan_time = time.time()
        interrupted = False

        while True:
            now_ms = get_synced_time_ms()
            if now_ms >= t_rescan:
                break

            time.sleep(2.0)
            
            if time.time() - last_scan_time >= 900:
                last_scan_time = time.time()
                add_ui_log("🔍 Performing scheduled 15-minute background market rescan...")
                sync_bybit_clock(http_client)
                new_opp = scan_best_funding_opportunity(http_client)
                
                if new_opp:
                    new_rate_str = f"{new_opp['funding_rate'] * 100:+.4f}%"
                    
                    if new_opp['symbol'] != symbol:
                        if new_opp['next_funding_time'] < settle_epoch:
                            add_ui_log(f"⚡ SWITCHING TARGET: Found earlier funding coin {new_opp['symbol']} ({new_rate_str})")
                            interrupted = True
                            break
                        elif new_opp['next_funding_time'] == settle_epoch and new_opp['funding_rate'] < rate:
                            add_ui_log(f"⚡ SWITCHING TARGET: Found better rate coin {new_opp['symbol']} ({new_rate_str}) for same time window")
                            interrupted = True
                            break
                        else:
                            add_ui_log(f"ℹ Rescan Ignored: {new_opp['symbol']} settles later than active target {symbol}")
                    else:
                        rate = new_opp['funding_rate']
                        with data_lock:
                            dashboard_data['funding_rate'] = new_rate_str
                            dashboard_data['status'] = f"ARMED [{window_type}]: {symbol} | Rate: {new_rate_str}"
                        add_ui_log(f"🔄 Rescan Result: Same coin {symbol} active | Live Rate updated to {new_rate_str}")
                else:
                    add_ui_log("🔍 Rescan Result: No coin below -0.3% threshold found.")

        if interrupted:
            continue

        precision_wait_until(t_rescan)
        add_ui_log("⚡ T-65s Pre-Entry Final Rescan running...")
        sync_bybit_clock(http_client)
        final_opp = scan_best_funding_opportunity(http_client)

        if final_opp:
            if final_opp['symbol'] != symbol:
                if final_opp['next_funding_time'] < settle_epoch or (final_opp['next_funding_time'] == settle_epoch and final_opp['funding_rate'] < rate):
                    add_ui_log(f"🔄 PRE-ENTRY SWITCH: Upgraded to {final_opp['symbol']} ({final_opp['funding_rate']*100:.4f}%)")
                    opportunity = final_opp
                    symbol = opportunity['symbol']
                    rate = opportunity['funding_rate']
                    settle_epoch = opportunity['next_funding_time']
                    window_type = opportunity['window_type']

                    t_entry = settle_epoch + 950
                    t_exit = settle_epoch + 8000
            else:
                opportunity = final_opp
                rate = opportunity['funding_rate']
                rate_str = f"{rate * 100:+.4f}%"
                with data_lock:
                    dashboard_data['funding_rate'] = rate_str
                    dashboard_data['status'] = f"ARMED [{window_type}]: {symbol} | Rate: {rate_str}"

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
            add_ui_log(f"🚀 ENTRY EXECUTED (BUY MARKET): {initial_qty} {symbol} ($3 Margin @ {leverage}x) at {entry_time_str}")
            entry_success = True
        except Exception as e:
            add_ui_log(f"Entry execution failed: {e}")

        precision_wait_until(t_exit)
        exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
        
        status_text = "ENTRY FAILED"
        status_class = "status-cancel"

        if entry_success:
            try:
                pos_info = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
                pos_qty = float(pos_info.get("size", 0))

                if pos_qty > 0:
                    formatted_exit_qty = f"{pos_qty:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_qty))
                    http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=formatted_exit_qty, reduceOnly=True, positionIdx=0)
                    add_ui_log(f"⏰ HARD EXIT DISPATCHED (SELL MARKET): {formatted_exit_qty} {symbol} at {exit_time_str}")
                    status_text = "CLOSED (T+8s)"
                    status_class = "status-profit"
                else:
                    status_text = "POSITION CLOSED"
                    status_class = "status-profit"
            except Exception as e:
                add_ui_log(f"Hard Exit check failed: {e}")

        add_ledger_entry({
            "coin": f"{symbol} ({window_type})",
            "rate": f"{rate * 100:+.4f}%",
            "entry_time": entry_time_str,
            "exit_time": exit_time_str,
            "status_text": status_text,
            "status_class": status_class
        })

        time.sleep(3)


if __name__ == '__main__':
    run_funding_capture_engine()
