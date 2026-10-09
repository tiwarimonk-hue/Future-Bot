import os
import time
import json
import hmac
import hashlib
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
    "status": "Initializing Binance Dynamic Engine...",
    "target_settlement": "-- IST",
    "entry_target": "-- IST (T + 950ms)",
    "exit_target": "-- IST (T + 8000ms)",
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
# 2. BINANCE API & CLOCK SYNCHRONIZATION
# ==========================================
API_KEY = os.environ.get('BINANCE_API_KEY', 'YOUR_API_KEY_HERE')
API_SECRET = os.environ.get('BINANCE_API_SECRET', 'YOUR_API_SECRET_HERE')
BINANCE_FUTURES_URL = "https://fapi.binance.com"

MIN_FUNDING_RATE_THRESHOLD = -0.004  # -0.4% Threshold
ENTRY_MARGIN_USD = 5.0              # $5 Fixed Margin

clock_offset_ms = 0.0

def binance_public_get(endpoint, params=None):
    url = f"{BINANCE_FUTURES_URL}{endpoint}"
    resp = requests.get(url, params=params, timeout=5)
    return resp.json()

def binance_signed_request(method, endpoint, params=None):
    if params is None:
        params = {}
    params['timestamp'] = int(get_synced_time_ms())
    query_string = urllib.parse.urlencode(params)
    signature = hmac.new(API_SECRET.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
    full_url = f"{BINANCE_FUTURES_URL}{endpoint}?{query_string}&signature={signature}"
    headers = {"X-MBX-APIKEY": API_KEY}
    
    if method == "GET":
        resp = requests.get(full_url, headers=headers, timeout=5)
    elif method == "POST":
        resp = requests.post(full_url, headers=headers, timeout=5)
    elif method == "DELETE":
        resp = requests.delete(full_url, headers=headers, timeout=5)
    return resp.json()

def sync_binance_clock():
    global clock_offset_ms
    try:
        t_send = time.time() * 1000
        res = binance_public_get("/fapi/v1/time")
        t_recv = time.time() * 1000

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
# 3. WEB DASHBOARD & SELF-PING KEEPALIVE
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
<title>Binance Funding Capture Engine</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }
body { background-color: #0b0e11; color: #eaecef; padding: 20px; display: flex; justify-content: center; }
.container { width: 100%; max-width: 1100px; display: flex; flex-direction: column; gap: 16px; }
.header { background: #1e2329; padding: 18px 24px; border-radius: 12px; border: 1px solid #2b313a; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 18px; color: #f0b90b; }
.badge { background: rgba(14, 203, 129, 0.15); color: #0ecb81; padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 600; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; }
.card { background: #1e2329; padding: 16px; border-radius: 12px; border: 1px solid #2b313a; }
.card-title { font-size: 11px; color: #848e9c; text-transform: uppercase; margin-bottom: 6px; }
.card-value { font-size: 18px; font-weight: 700; color: #f0b90b; }
.section-card { background: #1e2329; padding: 18px; border-radius: 12px; border: 1px solid #2b313a; }
#log-box { background: #0b0e11; border: 1px solid #2b313a; border-radius: 8px; padding: 14px; height: 220px; overflow-y: auto; font-family: 'Courier New', monospace; font-size: 12px; color: #0ecb81; line-height: 1.5; }
.ledger-container { max-height: 350px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; margin-top: 10px; }
.ledger-item { background: #181c22; border: 1px solid #2b313a; border-radius: 8px; padding: 12px; display: flex; justify-content: space-between; align-items: center; font-size: 13px; }
.ledger-info { display: flex; flex-direction: column; gap: 4px; }
.coin-name { font-weight: 700; color: #f0b90b; font-size: 14px; }
.rate-tag { color: #0ecb81; font-weight: 600; }
.ledger-status { font-weight: 600; padding: 4px 10px; border-radius: 6px; font-size: 12px; text-align: right; }
.status-profit { background: rgba(14, 203, 129, 0.15); color: #0ecb81; }
.status-cancel { background: rgba(246, 70, 93, 0.15); color: #f6465d; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>⚡ Binance Funding Capture Engine ($5 Margin + Max Leverage)</h1>
    <div class="badge" id="ws-status">INITIALIZING</div>
  </div>
  <div class="grid">
    <div class="card"><div class="card-title">Live Target Coin</div><div class="card-value" id="coin">-</div></div>
    <div class="card"><div class="card-title">Funding Rate</div><div class="card-value" id="rate" style="color:#0ecb81;">-</div></div>
    <div class="card"><div class="card-title">Direction</div><div class="card-value" id="direction" style="color:#f0b90b;">--</div></div>
    <div class="card"><div class="card-title">Entry Target</div><div class="card-value" id="entry" style="color:#0ecb81; font-size:14px;">-</div></div>
    <div class="card"><div class="card-title">Exit Target</div><div class="card-value" id="exit" style="color:#f6465d; font-size:14px;">-</div></div>
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
            <div style="color: #848e9c; font-size: 11px;">Entry Executed: ${item.entry_time} | Exit Closed: ${item.exit_time}</div>
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

def self_ping_worker():
    port = int(os.environ.get("PORT", 10000))
    time.sleep(5)
    while True:
        try:
            requests.get(f"http://127.0.0.1:{port}/api/status", timeout=5)
        except Exception:
            pass
        time.sleep(300)  # Har 5 minute mein khud ko ping karega

threading.Thread(target=run_web_server, daemon=True).start()
threading.Thread(target=self_ping_worker, daemon=True).start()

# ==========================================
# 4. WEBSOCKET PIPELINE
# ==========================================
ws_ready = False

def on_ws_open(ws):
    global ws_ready
    ws_ready = True
    with data_lock:
        dashboard_data['status'] = "BINANCE WS LIVE"
    add_ui_log("Binance WebSocket Pipeline Connected & Active")

def on_ws_message(ws, message):
    pass

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
            time.sleep(15)
            try:
                ws.send(json.dumps({"method": "PING"}))
            except Exception:
                break
    threading.Thread(target=run, daemon=True).start()

def connect_websocket():
    while True:
        try:
            ws_url = "wss://fstream.binance.com/ws"
            ws = websocket.WebSocketApp(
                ws_url,
                on_open=on_ws_open,
                on_message=on_ws_message,
                on_close=on_ws_close,
                on_error=on_ws_error
            )
            start_heartbeat(ws)
            ws.run_forever()
        except Exception:
            pass
        time.sleep(2)

threading.Thread(target=connect_websocket, daemon=True).start()

# ==========================================
# 5. MARKET SCANNER & CALCULATIONS
# ==========================================
def get_funding_intervals():
    intervals = {}
    try:
        info = binance_public_get("/fapi/v1/exchangeInfo")
        for s in info.get("symbols", []):
            sym = s.get("symbol")
            fi = s.get("fundingIntervalHours", 8)
            intervals[sym] = int(fi)
    except Exception:
        pass
    return intervals

def scan_best_funding_opportunity():
    try:
        tickers = binance_public_get("/fapi/v1/premiumIndex")
        intervals = get_funding_intervals()
    except Exception:
        return None

    now_ms = int(get_synced_time_ms())
    candidates_1h, candidates_4h, candidates_8h = [], [], []

    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        raw_rate = t.get("lastFundingRate", "")
        raw_next = t.get("nextFundingTime", "")
        if not raw_rate or not raw_next:
            continue
        try:
            rate = float(raw_rate)
            next_time_ms = int(raw_next)
            mark_price = float(t.get("markPrice", 0))
        except ValueError:
            continue

        time_diff = next_time_ms - now_ms
        if 0 < time_diff <= 8 * 3600 * 1000:
            interval = intervals.get(sym, 8)
            coin_data = {
                "symbol": sym,
                "funding_rate": rate,
                "next_funding_time": next_time_ms,
                "last_price": mark_price,
                "interval": interval
            }
            if interval == 1 or time_diff <= 1.5 * 3600 * 1000:
                candidates_1h.append(coin_data)
            elif interval == 4 or time_diff <= 4.5 * 3600 * 1000:
                candidates_4h.append(coin_data)
            else:
                candidates_8h.append(coin_data)

    valid_1h = [c for c in candidates_1h if c['funding_rate'] <= MIN_FUNDING_RATE_THRESHOLD]
    if valid_1h:
        best = min(valid_1h, key=lambda x: x['funding_rate'])
        best['window_type'] = '1H'
        return best

    valid_4h = [c for c in candidates_4h if c['funding_rate'] <= MIN_FUNDING_RATE_THRESHOLD]
    if valid_4h:
        best = min(valid_4h, key=lambda x: x['funding_rate'])
        best['window_type'] = '4H'
        return best

    valid_8h = [c for c in candidates_8h if c['funding_rate'] <= MIN_FUNDING_RATE_THRESHOLD]
    if valid_8h:
        best = min(valid_8h, key=lambda x: x['funding_rate'])
        best['window_type'] = '8H'
        return best

    return None

def set_max_leverage_and_get_qty(symbol, price, margin_usd=5.0):
    max_leverage = 25
    step_size = 1.0
    min_qty = 1.0

    try:
        brackets = binance_signed_request("GET", "/fapi/v1/leverageBracket", {"symbol": symbol})
        if isinstance(brackets, list) and len(brackets) > 0:
            b_list = brackets[0].get("brackets", [])
            if b_list:
                max_leverage = max([b.get("initialLeverage", 25) for b in b_list])
    except Exception:
        pass

    try:
        binance_signed_request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(max_leverage)})
    except Exception:
        pass

    try:
        info = binance_public_get("/fapi/v1/exchangeInfo")
        for s in info.get("symbols", []):
            if s.get("symbol") == symbol:
                for f in s.get("filters", []):
                    if f.get("filterType") == "LOT_SIZE":
                        step_size = float(f.get("stepSize", "1"))
                        min_qty = float(f.get("minQty", "1"))
                        break
                break
    except Exception:
        pass

    step_str = f"{step_size:.8f}".rstrip("0")
    qty_decimals = len(step_str.split(".")[1]) if "." in step_str else 0

    notional = margin_usd * max_leverage
    calc_qty = max(min_qty, math.floor((notional / price) / step_size) * step_size)
    qty_formatted = f"{calc_qty:.{qty_decimals}f}" if qty_decimals > 0 else str(int(calc_qty))

    return qty_formatted, max_leverage

# ==========================================
# 6. ENGINE MAIN LOOP
# ==========================================
def run_funding_capture_engine():
    add_ui_log("Binance Dynamic Engine Active (Threshold: -0.4%). Waiting for WS...")

    while not ws_ready:
        time.sleep(0.1)

    while True:
        sync_binance_clock()
        opportunity = scan_best_funding_opportunity()

        if not opportunity:
            with data_lock:
                dashboard_data['target_symbol'] = "Scanning..."
                dashboard_data['funding_rate'] = "0.00%"
                dashboard_data['action_direction'] = "--"
                dashboard_data['status'] = "SCANNING: No coin <= -0.4% found"
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
            dashboard_data['action_direction'] = "LONG CAPTURE ($5 MARGIN)"

        t_rescan = settle_epoch - 65000
        t_entry = settle_epoch + 950      # T + 950ms Execution
        t_exit = settle_epoch + 8000      # T + 8000ms Hard Exit

        entry_ist = datetime.fromtimestamp(t_entry / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]
        exit_ist = datetime.fromtimestamp(t_exit / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]

        with data_lock:
            dashboard_data['entry_target'] = f"{entry_ist} IST (T + 950ms)"
            dashboard_data['exit_target'] = f"{exit_ist} IST (T + 8000ms)"
            dashboard_data['status'] = f"ARMED [{window_type}]: {symbol} | Rate: {rate_percent}"

        add_ui_log(f"🎯 TARGET ARMED [{window_type}]: {symbol} | Rate: {rate_percent} | Entry: T+950ms")

        last_scan_time = time.time()
        interrupted = False

        while True:
            now_ms = get_synced_time_ms()
            if now_ms >= t_rescan:
                break

            time.sleep(2.0)

            if time.time() - last_scan_time >= 900:
                last_scan_time = time.time()
                add_ui_log("🔍 Scheduled 15-minute background market rescan...")
                sync_binance_clock()
                new_opp = scan_best_funding_opportunity()

                if new_opp:
                    new_rate_str = f"{new_opp['funding_rate'] * 100:+.4f}%"
                    if new_opp['symbol'] != symbol:
                        new_win = new_opp['window_type']
                        if (window_type == '8H' and new_win in ['1H', '4H']) or (window_type == '4H' and new_win == '1H') or (new_opp['funding_rate'] < rate):
                            add_ui_log(f"⚡ SWITCHING TARGET: Found better coin {new_opp['symbol']} ({new_rate_str})")
                            interrupted = True
                            break
                    else:
                        rate = new_opp['funding_rate']
                        with data_lock:
                            dashboard_data['funding_rate'] = new_rate_str
                            dashboard_data['status'] = f"ARMED [{window_type}]: {symbol} | Rate: {new_rate_str}"
                        add_ui_log(f"🔄 Rescan Result: Same coin {symbol} active | Live Rate: {new_rate_str}")

        if interrupted:
            continue

        precision_wait_until(t_rescan)
        add_ui_log("⚡ T-65s Pre-Entry Final Rescan running...")
        sync_binance_clock()
        final_opp = scan_best_funding_opportunity()

        if final_opp:
            if final_opp['symbol'] != symbol and final_opp['funding_rate'] < rate:
                add_ui_log(f"🔄 PRE-ENTRY SWITCH: Upgraded to {final_opp['symbol']} ({final_opp['funding_rate']*100:.4f}%)")
                opportunity = final_opp
                symbol = opportunity['symbol']
                rate = opportunity['funding_rate']
            elif final_opp['symbol'] == symbol:
                opportunity = final_opp
                rate = opportunity['funding_rate']

        precision_wait_until(t_entry)
        entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

        try:
            live_price = opportunity['last_price']
            qty, max_lev = set_max_leverage_and_get_qty(symbol, live_price, ENTRY_MARGIN_USD)
        except Exception as e:
            add_ui_log(f"Order prep failed: {e}")
            time.sleep(5)
            continue

        entry_success = False
        try:
            order_res = binance_signed_request("POST", "/fapi/v1/order", {
                "symbol": symbol,
                "side": "BUY",
                "type": "MARKET",
                "quantity": qty
            })
            if "orderId" in order_res:
                add_ui_log(f"🚀 MARKET ENTRY EXECUTED (BUY): {qty} {symbol} ($5 Margin @ {max_lev}x)")
                entry_success = True
            else:
                add_ui_log(f"Entry order rejected: {order_res.get('msg', 'Unknown Error')}")
        except Exception as e:
            add_ui_log(f"Entry execution error: {e}")

        precision_wait_until(t_exit)
        exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

        status_text = "EXIT FAILED"
        status_class = "status-cancel"

        if entry_success:
            try:
                positions = binance_signed_request("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
                pos_qty = 0.0
                if isinstance(positions, list):
                    for p in positions:
                        if p.get("symbol") == symbol:
                            pos_qty = abs(float(p.get("positionAmt", 0)))
                            break

                if pos_qty > 0:
                    exit_res = binance_signed_request("POST", "/fapi/v1/order", {
                        "symbol": symbol,
                        "side": "SELL",
                        "type": "MARKET",
                        "quantity": str(pos_qty),
                        "reduceOnly": "true"
                    })
                    add_ui_log(f"⏰ HARD EXIT EXECUTED (SELL MARKET): {pos_qty} {symbol} at {exit_time_str}")
                    status_text = "HARD EXIT (T+8s)"
                    status_class = "status-profit"
                else:
                    status_text = "NO POSITION OPEN"
                    status_class = "status-cancel"
            except Exception as e:
                add_ui_log(f"Hard Exit Failed: {e}")
        else:
            status_text = "ENTRY FAILED"
            status_class = "status-cancel"

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
