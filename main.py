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
from dotenv import load_dotenv

# Auto load API keys from .env file
load_dotenv()

# ==========================================
# 1. API KEYS & CONFIGURATION
# ==========================================
API_KEY = os.environ.get('BINANCE_API_KEY', '')
API_SECRET = os.environ.get('BINANCE_API_SECRET', '')
BINANCE_FUTURES_URL = "https://fapi.binance.com"

# HTTP Session setup with persistent connection headers
http_session = requests.Session()
http_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Connection": "keep-alive"
})

MIN_FUNDING_RATE_THRESHOLD = -0.004  # -0.4% or more negative
ENTRY_MARGIN_USD = 1.0              # $1 Margin

# ==========================================
# 2. TIMEZONE & DASHBOARD TELEMETRY STATE
# ==========================================
IST = timezone(timedelta(hours=5, minutes=30))
data_lock = threading.Lock()

dashboard_data = {
    "target_symbol": "Scanning Market...",
    "funding_rate": "0.00%",
    "action_direction": "--",
    "status": "Initializing Binance Dynamic Engine...",
    "futures_balance": "Fetching...",
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
        if len(dashboard_data['logs']) > 80:
            dashboard_data['logs'].pop(0)

def add_ledger_entry(trade_info):
    with data_lock:
        dashboard_data['ledger'].insert(0, trade_info)
        if len(dashboard_data['ledger']) > 25:
            dashboard_data['ledger'].pop()

# ==========================================
# 3. BINANCE API & CLOCK SYNCHRONIZATION
# ==========================================
clock_offset_ms = 0.0

def binance_public_get(endpoint, params=None):
    try:
        url = f"{BINANCE_FUTURES_URL}{endpoint}"
        resp = http_session.get(url, params=params, timeout=5)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

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
            resp = http_session.get(full_url, headers=headers, timeout=5)
        elif method == "POST":
            resp = http_session.post(full_url, headers=headers, timeout=5)
        elif method == "DELETE":
            resp = http_session.delete(full_url, headers=headers, timeout=5)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def get_futures_usdt_balance():
    try:
        res = binance_signed_request("GET", "/fapi/v2/balance")
        if isinstance(res, list):
            for asset in res:
                if asset.get('asset') == 'USDT':
                    bal = float(asset.get('balance', 0.0))
                    cross_pnl = float(asset.get('crossUnpnl', 0.0))
                    available = bal + cross_pnl
                    return f"${available:.2f} USDT"
        elif isinstance(res, dict) and "msg" in res:
            return f"API Error: {res.get('msg')}"
    except Exception as e:
        return f"Error: {str(e)}"
    return "$0.00 USDT"

def sync_binance_clock():
    global clock_offset_ms
    try:
        t_send = time.time() * 1000
        res = binance_public_get("/fapi/v1/time")
        t_recv = time.time() * 1000

        if isinstance(res, dict) and 'serverTime' in res:
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
        else:
            time.sleep(0.0001)

# ==========================================
# 4. STARTUP CHECK & ORPHAN POSITION CLEANUP
# ==========================================
def cleanup_orphan_positions():
    add_ui_log("🔍 Performing startup check for lingering open positions...")
    try:
        positions = binance_signed_request("GET", "/fapi/v2/positionRisk")
        if isinstance(positions, list):
            for pos in positions:
                amt = float(pos.get('positionAmt', 0.0))
                sym = pos.get('symbol')
                if amt != 0:
                    add_ui_log(f"⚠️ Found active orphan position: {sym} (Amt: {amt}). Closing immediately...")
                    side = "SELL" if amt > 0 else "BUY"
                    res = place_market_order(sym, side, abs(amt), reduce_only=True)
                    status_val = res.get('status', 'ERR') if isinstance(res, dict) else 'ERR'
                    add_ui_log(f"🧹 Cleanup order sent for {sym}. Response: {status_val}")
        else:
            add_ui_log("✅ No open positions found on startup.")
    except Exception as e:
        add_ui_log(f"⚠️ Exception during startup position cleanup: {e}")

# ==========================================
# 5. ORDER EXECUTION API
# ==========================================
def place_market_order(symbol, side, quantity, reduce_only=False):
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": str(quantity)
    }
    if reduce_only:
        params["reduceOnly"] = "true"
    return binance_signed_request("POST", "/fapi/v1/order", params)

# ==========================================
# 6. WEB DASHBOARD SERVER & KEEP-ALIVE PING
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
<title>Binance Dynamic Funding Arbitrage Engine</title>
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
    <h1>⚡ Binance Dynamic Engine ($1 Margin + Max 25x Leverage)</h1>
    <div class="badge" id="ws-status">INITIALIZING</div>
  </div>
  <div class="grid">
    <div class="card"><div class="card-title">Live Target Coin</div><div class="card-value" id="coin">-</div></div>
    <div class="card"><div class="card-title">Funding Rate</div><div class="card-value" id="rate" style="color:#0ecb81;">-</div></div>
    <div class="card"><div class="card-title">Futures USDT Balance</div><div class="card-value" id="balance" style="color:#0ecb81;">Fetching...</div></div>
    <div class="card"><div class="card-title">Entry Target</div><div class="card-value" id="entry" style="color:#0ecb81; font-size:13px;">-</div></div>
    <div class="card"><div class="card-title">Exit Target</div><div class="card-value" id="exit" style="color:#f6465d; font-size:13px;">-</div></div>
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
    document.getElementById('balance').innerText = data.futures_balance;
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
    try:
        server = HTTPServer(('0.0.0.0', port), FundingDashboardHandler)
        server.serve_forever()
    except Exception as e:
        print(f"Web server exception: {e}")

threading.Thread(target=run_web_server, daemon=True).start()

# Self-Ping Keep-Alive Thread (Prevents Sleep Mode on Cloud/Render)
def self_ping_keep_alive():
    time.sleep(10)
    port = int(os.environ.get("PORT", 10000))
    ping_url = f"http://127.0.0.1:{port}/api/status"
    add_ui_log(f"🔄 Self-ping keep-alive thread initialized.")
    while True:
        try:
            time.sleep(300)
            requests.get(ping_url, timeout=5)
        except Exception:
            pass

threading.Thread(target=self_ping_keep_alive, daemon=True).start()

# ==========================================
# 7. WEBSOCKET PIPELINE (LIVE MARK PRICE & PING-PONG)
# ==========================================
ws_ready = False

def on_ws_open(ws):
    global ws_ready
    ws_ready = True
    with data_lock:
        dashboard_data['status'] = "BINANCE WS LIVE"
    add_ui_log("Binance WebSocket Connected (1s Mark Price Stream)")

def on_ws_message(ws, message):
    try:
        data = json.loads(message)
        items = data if isinstance(data, list) else [data]
        with data_lock:
            current_target = dashboard_data.get('target_symbol', '')
            clean_target = current_target.split(' ')[0].strip()
            if clean_target and not clean_target.startswith("Scanning"):
                for item in items:
                    sym = item.get('s') or item.get('symbol')
                    if sym == clean_target:
                        rate = float(item.get('r') or item.get('fundingRate', 0.0))
                        dashboard_data['funding_rate'] = f"{rate * 100:+.4f}%"
                        break
    except Exception:
        pass

def on_ws_close(ws, code, msg):
    global ws_ready
    ws_ready = False
    with data_lock:
        dashboard_data["status"] = "WS DISCONNECTED"
    add_ui_log("⚠️ WebSocket Disconnected. Reconnecting...")

def on_ws_error(ws, error):
    global ws_ready
    ws_ready = False

def connect_websocket():
    global ws_ready
    while True:
        try:
            ws_url = "wss://fstream.binance.com/ws/!markPrice@arr@1s"
            ws = websocket.WebSocketApp(
                ws_url,
                on_open=on_ws_open,
                on_message=on_ws_message,
                on_close=on_ws_close,
                on_error=on_ws_error
            )
            ws.run_forever(ping_interval=15, ping_timeout=10)
        except Exception as e:
            add_ui_log(f"WebSocket Exception: {e}")
        ws_ready = False
        time.sleep(3)

threading.Thread(target=connect_websocket, daemon=True).start()

# ==========================================
# 8. MARKET SCANNER & CALCULATIONS
# ==========================================
def scan_best_funding_opportunity():
    try:
        tickers = binance_public_get("/fapi/v1/premiumIndex")
        if not isinstance(tickers, list):
            return None
    except Exception:
        return None

    now_ms = int(get_synced_time_ms())
    candidates = []

    for t in tickers:
        if not isinstance(t, dict):
            continue
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
        if 0 < time_diff <= 8 * 3600 * 1000 and rate <= MIN_FUNDING_RATE_THRESHOLD:
            candidates.append({
                "symbol": sym,
                "funding_rate": rate,
                "next_funding_time": next_time_ms,
                "last_price": mark_price,
                "time_diff": time_diff
            })

    if not candidates:
        return None

    best = min(candidates, key=lambda x: x['next_funding_time'])
    
    if best['time_diff'] <= 1.5 * 3600 * 1000:
        best['window_type'] = '1H'
    elif best['time_diff'] <= 4.5 * 3600 * 1000:
        best['window_type'] = '4H'
    else:
        best['window_type'] = '8H'

    return best

def set_leverage_and_get_qty(symbol, price, margin_usd=1.0):
    max_leverage = 25
    step_size = 1.0
    min_qty = 1.0

    if price <= 0:
        price = 1.0

    try:
        brackets = binance_signed_request("GET", "/fapi/v1/leverageBracket", {"symbol": symbol})
        if isinstance(brackets, list) and len(brackets) > 0:
            b_list = brackets[0].get("brackets", [])
            if b_list:
                ex_max = max([b.get("initialLeverage", 25) for b in b_list])
                max_leverage = min(25, ex_max)
    except Exception:
        pass

    try:
        binance_signed_request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(max_leverage)})
    except Exception:
        pass

    try:
        info = binance_public_get("/fapi/v1/exchangeInfo")
        if isinstance(info, dict):
            for s in info.get("symbols", []):
                if s.get("symbol") == symbol:
                    for f in s.get("filters", []):
                        if f.get("filterType") == "LOT_SIZE":
                            step_size = float(f.get("stepSize", "1"))
                            min_qty = float(f.get("minQty", "1"))
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
# 9. MAIN EXECUTION ENGINE LOOP
# ==========================================
def run_funding_capture_engine():
    add_ui_log("Binance Dynamic Engine Active. Syncing clock with Binance...")
    sync_binance_clock()  # FIX APPLIED HERE: Sync clock before making signed calls
    add_ui_log("Performing Startup Cleanup...")
    cleanup_orphan_positions()

    current_opportunity = None

    while True:
        try:
            ws_check_count = 0
            while not ws_ready:
                time.sleep(1.0)
                ws_check_count += 1
                if ws_check_count > 15:
                    add_ui_log("Waiting for WebSocket connection...")
                    ws_check_count = 0

            sync_binance_clock()
            bal_str = get_futures_usdt_balance()
            with data_lock:
                dashboard_data['futures_balance'] = bal_str

            if not current_opportunity:
                current_opportunity = scan_best_funding_opportunity()
                if not current_opportunity:
                    with data_lock:
                        dashboard_data['target_symbol'] = "Scanning..."
                        dashboard_data['funding_rate'] = "0.00%"
                        dashboard_data['action_direction'] = "--"
                        dashboard_data['status'] = "SCANNING: No coin <= -0.4% found. Next scan in 5m..."
                    add_ui_log("🔍 No coin found matching threshold. Waiting 5 minutes for next scan...")
                    time.sleep(300)
                    continue

            symbol = current_opportunity['symbol']
            rate = current_opportunity['funding_rate']
            settle_epoch = current_opportunity['next_funding_time']
            settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
            rate_percent = f"{rate * 100:+.4f}%"
            window_type = current_opportunity['window_type']

            with data_lock:
                dashboard_data['target_symbol'] = f"{symbol} ({window_type})"
                dashboard_data['funding_rate'] = rate_percent
                dashboard_data['target_settlement'] = settle_dt.strftime('%H:%M:%S IST')
                dashboard_data['action_direction'] = "LONG CAPTURE ($1 MARGIN)"

            t_entry = settle_epoch + 950
            t_exit = settle_epoch + 8000
            t_emergency = settle_epoch + 15000

            entry_ist = datetime.fromtimestamp(t_entry / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]
            exit_ist = datetime.fromtimestamp(t_exit / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]

            with data_lock:
                dashboard_data['entry_target'] = f"{entry_ist} IST (T + 950ms)"
                dashboard_data['exit_target'] = f"{exit_ist} IST (T + 8000ms)"
                dashboard_data['status'] = f"ARMED [{window_type}]: {symbol} | Rate: {rate_percent}"

            add_ui_log(f"🎯 TARGET ARMED [{window_type}]: {symbol} | Rate: {rate_percent}")

            # --- 5-MINUTE INTERVAL SCAN & FUNDING UPDATE WHILE ARMED ---
            better_opp = None
            abort_current = False
            last_scan_time = 0
            SCAN_INTERVAL = 300  # 5 minutes

            while True:
                now_ms = get_synced_time_ms()
                diff = t_entry - now_ms
                
                if diff <= 5000:
                    break

                current_time = time.time()
                
                if current_time - last_scan_time >= SCAN_INTERVAL:
                    last_scan_time = current_time
                    
                    try:
                        bal_str = get_futures_usdt_balance()
                        with data_lock:
                            dashboard_data['futures_balance'] = bal_str

                        tickers_check = binance_public_get("/fapi/v1/premiumIndex")
                        if isinstance(tickers_check, list):
                            coin_still_valid = False
                            latest_current_rate = 0.0
                            for t in tickers_check:
                                if t.get("symbol") == symbol:
                                    latest_current_rate = float(t.get("lastFundingRate", 0.0))
                                    coin_still_valid = True
                                    break
                            
                            add_ui_log(f"🔍 [5-Min Scan] {symbol} Current Funding Rate: {latest_current_rate*100:+.4f}%")
                            
                            if coin_still_valid and latest_current_rate > MIN_FUNDING_RATE_THRESHOLD:
                                add_ui_log(f"⚠️ WARNING: {symbol} rate worsened to {latest_current_rate*100:+.4f}%! Aborting trade...")
                                abort_current = True
                                break
                            
                            rate_percent = f"{latest_current_rate * 100:+.4f}%"
                            current_opportunity['funding_rate'] = latest_current_rate
                            with data_lock:
                                dashboard_data['funding_rate'] = rate_percent

                        scanned_opp = scan_best_funding_opportunity()
                        if scanned_opp and scanned_opp['symbol'] != symbol:
                            if scanned_opp['funding_rate'] < current_opportunity['funding_rate']:
                                add_ui_log(f"🔥 Better priority coin found during 5-min scan! Switching: {symbol} -> {scanned_opp['symbol']} ({scanned_opp['funding_rate']*100:+.4f}%)")
                                better_opp = scanned_opp
                                break
                                
                    except Exception as e:
                        add_ui_log(f"⚠️ Error during 5-min funding scan: {e}")

                time.sleep(1.0)

            if abort_current:
                add_ui_log("🔄 Trade aborted due to weakened funding rate. Resuming normal scanning...")
                current_opportunity = None
                time.sleep(5)
                continue

            if better_opp:
                current_opportunity = better_opp
                continue

            now_ms = get_synced_time_ms()
            if t_entry - now_ms < 500:
                add_ui_log("⏰ Entry window too close or missed. Skipping to avoid late execution...")
                current_opportunity = None
                time.sleep(5)
                continue

            rate = current_opportunity['funding_rate']
            rate_percent = f"{rate * 100:+.4f}%"

            calc_qty, lev = set_leverage_and_get_qty(symbol, current_opportunity['last_price'], ENTRY_MARGIN_USD)
            add_ui_log(f"⚙ Configured {symbol}: Leverage {lev}x | Quantity: {calc_qty}")

            add_ui_log(f"⏳ Waiting for precision entry target: {entry_ist} IST")
            precision_wait_until(t_entry)

            # --- ENTRY ORDER EXECUTION ---
            entry_res = place_market_order(symbol, "BUY", calc_qty)
            entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
            
            if isinstance(entry_res, dict):
                entry_status = entry_res.get('status', 'ERR')
                error_msg = entry_res.get('msg', '')
            else:
                entry_status = 'ERR'
                error_msg = str(entry_res)

            add_ui_log(f"🚀 BUY Executed for {symbol} at {entry_time_str} | Status: {entry_status} {error_msg}")

            if entry_status != 'FILLED':
                add_ui_log(f"⚠️ CRITICAL: Entry order failed or not filled! Aborting cycle...")
                with data_lock:
                    dashboard_data['status'] = f"ENTRY FAILED: {entry_status}"
                current_opportunity = None
                time.sleep(10)
                continue

            # --- HARD EXIT EXECUTION AT T + 8s WITH FALLBACK ---
            precision_wait_until(t_exit)

            add_ui_log(f"🏁 Executing Hard Exit (SELL) for {symbol} at T + 8s...")
            exit_res = place_market_order(symbol, "SELL", calc_qty, reduce_only=True)
            exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

            exit_success = False
            if isinstance(exit_res, dict):
                if exit_res.get('status') == 'FILLED':
                    exit_success = True
                elif 'code' in exit_res:
                    add_ui_log(f"⚠️ Hard Exit ReduceOnly rejected: {exit_res.get('msg')}. Trying normal market exit...")
                    exit_res = place_market_order(symbol, "SELL", calc_qty, reduce_only=False)
                    if isinstance(exit_res, dict) and exit_res.get('status') == 'FILLED':
                        exit_success = True

            # --- EMERGENCY CHECK (T + 15s) IF STILL NOT CLOSED ---
            if not exit_success:
                add_ui_log("⚠️ Exit order not immediately confirmed. Checking actual position status...")
                precision_wait_until(t_emergency)
                positions = binance_signed_request("GET", "/fapi/v2/positionRisk")
                if isinstance(positions, list):
                    for pos in positions:
                        if pos.get('symbol') == symbol:
                            amt = float(pos.get('positionAmt', 0))
                            if amt != 0:
                                rem_amt = abs(amt)
                                side = "SELL" if amt > 0 else "BUY"
                                add_ui_log(f"🚨 EMERGENCY: Position still open ({amt}). Forcing direct market close...")
                                emergency_res = place_market_order(symbol, side, rem_amt, reduce_only=False)
                                add_ui_log(f"Emergency close response: {emergency_res}")
                                exit_success = True

            status_text = "SUCCESS" if exit_success else "FAILED"
            status_class = "status-profit" if status_text == "SUCCESS" else "status-cancel"

            add_ledger_entry({
                "coin": symbol,
                "rate": rate_percent,
                "entry_time": entry_time_str,
                "exit_time": exit_time_str,
                "status_text": status_text,
                "status_class": status_class
            })

            add_ui_log(f"✅ Cycle finished with status: {status_text}. Resuming scan...")
            current_opportunity = None
            time.sleep(10)

        except Exception as e:
            add_ui_log(f"Engine Exception: {e}")
            current_opportunity = None
            time.sleep(5)

if __name__ == "__main__":
    run_funding_capture_engine()
