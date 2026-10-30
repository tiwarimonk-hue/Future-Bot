import os
import time
import json
import hmac
import hashlib
import uuid
import math
import threading
import websocket
from datetime import datetime, timezone, timedelta
from pybit.unified_trading import HTTP
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==========================================
# 1. TIMEZONE & DASHBOARD TELEMETRY STATE
# ==========================================
IST = timezone(timedelta(hours=5, minutes=30))
data_lock = threading.Lock()

dashboard_data = {
    "target_symbols": "Scanning Market (Top 3)...",
    "funding_rates": "--",
    "action_direction": "PARALLEL LONG CAPTURE ($1 MARGIN | MAX 20x)",
    "status": "Initializing Parallel Fast-Lane Engine...",
    "target_settlement": "-- IST",
    "entry_target": "-- IST (T - 20ms)",
    "exit_target": "-- IST (T + 10ms)",
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
        if len(dashboard_data['logs']) > 120:
            dashboard_data['logs'].pop(0)

def add_ledger_entry(trade_info):
    with data_lock:
        dashboard_data['ledger'].insert(0, trade_info)
        if len(dashboard_data['ledger']) > 30:
            dashboard_data['ledger'].pop()

# ==========================================
# 2. BYBIT SERVER CLOCK SYNCHRONIZATION
# ==========================================
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
    """Sub-millisecond hybrid busy-wait loop"""
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
# 3. WEB DASHBOARD WITH DETAILED LEDGER
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
<title>Bybit Parallel Funding Engine</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', -apple-system, sans-serif; }
body { background-color: #0b0e11; color: #eaecef; padding: 20px; display: flex; justify-content: center; }
.container { width: 100%; max-width: 1150px; display: flex; flex-direction: column; gap: 16px; }
.header { background: #1e2329; padding: 18px 24px; border-radius: 12px; border: 1px solid #2b313a; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 18px; color: #f7a600; font-weight: 700; }
.badge { background: rgba(14, 203, 129, 0.15); color: #0ecb81; padding: 6px 14px; border-radius: 20px; font-size: 12px; font-weight: 700; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; }
.card { background: #1e2329; padding: 16px; border-radius: 12px; border: 1px solid #2b313a; }
.card-title { font-size: 11px; color: #848e9c; text-transform: uppercase; margin-bottom: 6px; font-weight: 600; }
.card-value { font-size: 15px; font-weight: 700; color: #f7a600; word-break: break-all; }
.section-card { background: #1e2329; padding: 18px; border-radius: 12px; border: 1px solid #2b313a; }
#log-box { background: #0b0e11; border: 1px solid #2b313a; border-radius: 8px; padding: 14px; height: 230px; overflow-y: auto; font-family: 'Courier New', monospace; font-size: 12px; color: #0ecb81; line-height: 1.5; }
.ledger-container { max-height: 420px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; margin-top: 10px; }
.ledger-card { background: #181c22; border: 1px solid #2b313a; border-radius: 8px; padding: 14px; display: flex; flex-direction: column; gap: 8px; font-size: 12px; }
.ledger-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #2b313a; padding-bottom: 8px; }
.coin-title { font-weight: 700; color: #f7a600; font-size: 15px; }
.rate-badge { color: #0ecb81; font-weight: 700; background: rgba(14, 203, 129, 0.1); padding: 2px 8px; border-radius: 4px; }
.status-pill { font-weight: 700; padding: 3px 10px; border-radius: 6px; font-size: 11px; text-transform: uppercase; }
.status-success { background: rgba(14, 203, 129, 0.15); color: #0ecb81; border: 1px solid rgba(14, 203, 129, 0.3); }
.status-warn { background: rgba(247, 166, 0, 0.15); color: #f7a600; border: 1px solid rgba(247, 166, 0, 0.3); }
.status-error { background: rgba(246, 70, 93, 0.15); color: #f6465d; border: 1px solid rgba(246, 70, 93, 0.3); }
.ledger-details { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; color: #b7bdc6; }
.detail-item span { color: #eaecef; font-weight: 600; }
.error-tag { color: #f6465d; font-family: monospace; background: rgba(246, 70, 93, 0.08); padding: 4px 8px; border-radius: 4px; margin-top: 4px; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>⚡ Bybit Parallel Funding Engine (Top 3 | $1 Margin | Capped 20x)</h1>
    <div class="badge" id="ws-status">INITIALIZING</div>
  </div>
  <div class="grid">
    <div class="card"><div class="card-title">Targets (Top 3)</div><div class="card-value" id="coins">-</div></div>
    <div class="card"><div class="card-title">Live Rates</div><div class="card-value" id="rates" style="color:#0ecb81;">-</div></div>
    <div class="card"><div class="card-title">Settlement Time</div><div class="card-value" id="settlement" style="color:#38bdf8;">--</div></div>
    <div class="card"><div class="card-title">Entry Window</div><div class="card-value" id="entry" style="color:#0ecb81; font-size:13px;">T - 20ms</div></div>
    <div class="card"><div class="card-title">Exit Window</div><div class="card-value" id="exit" style="color:#f6465d; font-size:13px;">T + 10ms</div></div>
    <div class="card"><div class="card-title">Clock Drift</div><div class="card-value" id="offset">0.0 ms</div></div>
  </div>

  <div class="section-card">
    <div style="font-size: 14px; font-weight: 700; margin-bottom: 6px;">📋 Execution Ledger (Detailed Error & Fill Tracker)</div>
    <div class="ledger-container" id="ledger-box">
      <div style="color: #848e9c; text-align: center; padding: 25px;">No trades recorded in ledger yet.</div>
    </div>
  </div>

  <div class="section-card">
    <div style="font-size: 14px; font-weight: 700; margin-bottom: 6px;">🖥 Live Terminal Logs</div>
    <div id="log-box"></div>
  </div>
</div>
<script>
async function updateDashboard() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    document.getElementById('ws-status').innerText = data.status;
    document.getElementById('coins').innerText = data.target_symbols;
    document.getElementById('rates').innerText = data.funding_rates;
    document.getElementById('settlement').innerText = data.target_settlement;
    document.getElementById('entry').innerText = data.entry_target;
    document.getElementById('exit').innerText = data.exit_target;
    document.getElementById('offset').innerText = data.clock_offset_ms + ' ms';
    
    const logBox = document.getElementById('log-box');
    logBox.innerHTML = data.logs.map(l => `<div>${l}</div>`).join('');
    logBox.scrollTop = logBox.scrollHeight;

    const ledgerBox = document.getElementById('ledger-box');
    if (data.ledger && data.ledger.length > 0) {
      ledgerBox.innerHTML = data.ledger.map(item => `
        <div class="ledger-card">
          <div class="ledger-header">
            <div>
              <span class="coin-title">${item.coin}</span>
              <span class="rate-badge">${item.rate}</span>
              <span style="color:#848e9c; margin-left: 8px;">(${item.leverage}x | Qty: ${item.qty})</span>
            </div>
            <div class="status-pill ${item.badge_class}">${item.badge_text}</div>
          </div>
          <div class="ledger-details">
            <div class="detail-item">🚀 <b>Entry:</b> <span>${item.entry_time}</span> | ${item.entry_result}</div>
            <div class="detail-item">⏰ <b>Exit (T+10ms):</b> <span>${item.exit_time}</span> | ${item.exit_result}</div>
            <div class="detail-item">🛡 <b>Emergency Exits:</b> <span>${item.emergency_action}</span></div>
          </div>
          ${item.error_message ? `<div class="error-tag">⚠ Error Details: ${item.error_message}</div>` : ''}
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
API_KEY = os.environ.get('BYBIT_API_KEY', 'YOUR_API_KEY_HERE')
API_SECRET = os.environ.get('BYBIT_API_SECRET', 'YOUR_API_SECRET_HERE')

MIN_FUNDING_RATE_THRESHOLD = -0.003  # -0.3% Threshold
INITIAL_ENTRY_MARGIN_USD = 1.0       # $1 Margin per Coin
MAX_TARGET_COINS = 3                 # Top 3 Coins Parallel
MAX_ALLOWED_LEVERAGE = 20.0          # Max leverage strictly capped at 20x

ws_client = None
ws_ready = False
ws_lock = threading.Lock()

def get_ws_auth_payload():
    expires = int(time.time() * 1000) + 10000
    val = f'GET/realtime{expires}'
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        val.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return {"op": "auth", "args": [API_KEY, expires, signature]}

def on_ws_message(ws, message):
    try:
        data = json.loads(message)
    except Exception:
        return
    op = data.get("op", "")
    if op == "auth":
        global ws_ready
        if data.get("success") is True or data.get("retCode") == 0:
            ws_ready = True
            with data_lock:
                dashboard_data['status'] = "WS AUTHENTICATED (READY)"
            add_ui_log("⚡ WebSocket Fast-Lane Order Pipeline Authenticated & READY")

def on_ws_open(ws):
    add_ui_log("WebSocket Connection Opened. Authenticating...")
    ws.send(json.dumps(get_ws_auth_payload()))

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
                ws.send(json.dumps({"op": "ping"}))
            except Exception:
                break
    threading.Thread(target=run, daemon=True).start()

def connect_websocket():
    global ws_client
    while True:
        try:
            ws_url = "wss://stream.bybit.com/v5/trade"
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

def send_fast_ws_order(payload):
    """Sends orders over WebSocket for ultra-low latency execution"""
    global ws_client, ws_ready
    if ws_ready and ws_client:
        try:
            with ws_lock:
                ws_client.send(json.dumps(payload))
            return True, "WS_SENT"
        except Exception as e:
            return False, f"WS_ERROR: {str(e)}"
    return False, "WS_NOT_READY"

# ==========================================
# 5. SCANNER & ADVANCED CALCULATIONS
# ==========================================
def scan_top_funding_opportunities(http_client):
    try:
        tickers = http_client.get_tickers(category="linear")["result"]["list"]
    except Exception as e:
        add_ui_log(f"Market Scan API Error: {e}")
        return []

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
        # Valid if settlement is at least 30s away and rate meets threshold
        if time_diff > 30000 and rate <= MIN_FUNDING_RATE_THRESHOLD:
            valid_candidates.append({
                "symbol": sym,
                "funding_rate": rate,
                "next_funding_time": next_time_ms,
                "last_price": float(t.get("lastPrice", 0)),
                "window_type": f"{round(time_diff / (3600 * 1000), 1)}H"
            })

    if not valid_candidates:
        return []

    # Focus on the earliest settlement window
    earliest_time = min(c['next_funding_time'] for c in valid_candidates)
    imminent_candidates = [c for c in valid_candidates if abs(c['next_funding_time'] - earliest_time) <= 600000]
    
    # Sort by lowest (most negative) funding rate first
    imminent_candidates.sort(key=lambda x: x['funding_rate'])
    return imminent_candidates[:MAX_TARGET_COINS]

def prepare_coin_parameters(http_client, symbol, price, margin_usd=1.0):
    """
    Leverage strictly capped at 20x.
    If exchange max leverage is less than 20x, automatically use max permitted leverage.
    """
    try:
        inst_info = http_client.get_instruments_info(category="linear", symbol=symbol)["result"]["list"][0]
        exchange_max_lev = float(inst_info.get("leverageFilter", {}).get("maxLeverage", 20.0))
        lot_filter = inst_info['lotSizeFilter']
        qty_step = float(lot_filter["qtyStep"])
        min_qty = float(lot_filter['minOrderQty'])
    except Exception as e:
        add_ui_log(f"[{symbol}] Failed to read instrument info: {e}. Using safe defaults.")
        exchange_max_lev = 20.0
        qty_step = 1.0
        min_qty = 1.0

    # Rule: Maximum 20x, but if coin max is below 20x, pick exchange max
    applied_leverage = min(MAX_ALLOWED_LEVERAGE, exchange_max_lev)

    try:
        http_client.set_leverage(
            category="linear", 
            symbol=symbol, 
            buyLeverage=str(applied_leverage), 
            sellLeverage=str(applied_leverage)
        )
    except Exception:
        pass

    qty_str = f"{qty_step:.8f}".rstrip("0")
    qty_decimals = len(qty_str.split(".")[1]) if "." in qty_str else 0

    notional = margin_usd * applied_leverage
    calc_qty = max(min_qty, math.floor((notional / price) / qty_step) * qty_step)
    formatted_qty = f"{calc_qty:.{qty_decimals}f}" if qty_decimals > 0 else str(int(calc_qty))

    return {
        "symbol": symbol,
        "qty": formatted_qty,
        "leverage": applied_leverage,
        "qty_decimals": qty_decimals
    }

# ==========================================
# 6. PARALLEL INDEPENDENT WORKER THREAD
# ==========================================
def parallel_coin_executor(http_client, coin_plan, settle_epoch):
    """
    Dedicated worker thread per coin.
    - Entry: T - 20ms
    - Exit:  T + 10ms
    - Emergency Exits: Strictly at T + 1s, T + 2s, and T + 10s
    - Granular error capturing recorded into Ledger
    """
    symbol = coin_plan['symbol']
    rate_str = coin_plan['rate_str']
    qty = coin_plan['qty']
    qty_decimals = coin_plan['qty_decimals']
    leverage = coin_plan['leverage']

    t_entry = settle_epoch - 20   # T - 20ms
    t_exit = settle_epoch + 10    # T + 10ms

    entry_result_str = "--"
    exit_result_str = "--"
    emergency_action_str = "None (Position Flat)"
    error_log = []
    trade_success = False

    # Pre-built payload
    buy_payload = {
        "reqId": str(uuid.uuid4()),
        "op": "order.create",
        "args": [{
            "category": "linear",
            "symbol": symbol,
            "side": "Buy",
            "orderType": "Market",
            "qty": qty,
            "positionIdx": 0
        }]
    }

    # 1. PARALLEL ENTRY (T - 20ms)
    precision_wait_until(t_entry)
    entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
    ws_sent, ws_msg = send_fast_ws_order(buy_payload)

    if ws_sent:
        entry_result_str = "Sent via WebSocket (Fast-Lane)"
    else:
        # Fallback to REST HTTP
        try:
            http_client.place_order(category="linear", symbol=symbol, side="Buy", orderType="Market", qty=qty, positionIdx=0)
            entry_result_str = "Sent via REST Fallback"
        except Exception as e:
            err_str = f"Entry Order Failed: {str(e)}"
            entry_result_str = "FAILED"
            error_log.append(err_str)
            add_ui_log(f"❌ [{symbol}] {err_str}")

    add_ui_log(f"🚀 [{symbol}] ENTRY SENT: {qty} @ {leverage}x at {entry_time_str} | {entry_result_str}")

    # 2. PARALLEL EXIT (T + 10ms)
    precision_wait_until(t_exit)
    exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
    
    sell_payload = {
        "reqId": str(uuid.uuid4()),
        "op": "order.create",
        "args": [{
            "category": "linear",
            "symbol": symbol,
            "side": "Sell",
            "orderType": "Market",
            "qty": qty,
            "reduceOnly": True,
            "positionIdx": 0
        }]
    }

    ws_exit_sent, ws_exit_msg = send_fast_ws_order(sell_payload)
    if ws_exit_sent:
        exit_result_str = "Sent via WebSocket (Fast-Lane)"
    else:
        try:
            http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=qty, reduceOnly=True, positionIdx=0)
            exit_result_str = "Sent via REST Fallback"
        except Exception as e:
            err_str = f"Exit Order Failed: {str(e)}"
            exit_result_str = "FAILED"
            error_log.append(err_str)
            add_ui_log(f"❌ [{symbol}] {err_str}")

    add_ui_log(f"⚡ [{symbol}] EXIT SENT: {qty} at {exit_time_str} | {exit_result_str}")

    # 3. EMERGENCY EXITS (STRICTLY AT T + 1s, T + 2s, and T + 10s)
    emergency_times = [
        ("T+1s", settle_epoch + 1000),
        ("T+2s", settle_epoch + 2000),
        ("T+10s", settle_epoch + 10000)
    ]

    position_cleared = False
    triggered_stages = []

    for stage_name, target_ms in emergency_times:
        precision_wait_until(target_ms)
        try:
            pos_resp = http_client.get_positions(category="linear", symbol=symbol)
            positions = pos_resp.get("result", {}).get("list", [])
            pos_size = float(positions[0].get("size", 0)) if positions else 0.0

            if pos_size > 0:
                rem_qty = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
                http_client.place_order(
                    category="linear", 
                    symbol=symbol, 
                    side="Sell", 
                    orderType="Market", 
                    qty=rem_qty, 
                    reduceOnly=True, 
                    positionIdx=0
                )
                action_msg = f"Triggered at {stage_name} (Closed {rem_qty})"
                triggered_stages.append(action_msg)
                add_ui_log(f"🛡 [{symbol}] EMERGENCY EXIT: {action_msg}")
            else:
                position_cleared = True
                break
        except Exception as e:
            err_str = f"{stage_name} Check Error: {str(e)}"
            error_log.append(err_str)
            add_ui_log(f"⚠ [{symbol}] {err_str}")

    if triggered_stages:
        emergency_action_str = " | ".join(triggered_stages)
        badge_text = "EMERGENCY CLOSED"
        badge_class = "status-warn"
    elif not error_log and position_cleared:
        emergency_action_str = "Clean (Filled instantly at T+10ms)"
        badge_text = "CAPTURED (CLEAN)"
        badge_class = "status-success"
    else:
        badge_text = "EXECUTION ERROR"
        badge_class = "status-error"

    # Push comprehensive entry to the Ledger
    add_ledger_entry({
        "coin": symbol,
        "rate": rate_str,
        "qty": qty,
        "leverage": leverage,
        "entry_time": entry_time_str,
        "entry_result": entry_result_str,
        "exit_time": exit_time_str,
        "exit_result": exit_result_str,
        "emergency_action": emergency_action_str,
        "error_message": " || ".join(error_log) if error_log else "",
        "badge_text": badge_text,
        "badge_class": badge_class
    })

# ==========================================
# 7. MAIN CAPTURE ENGINE LOOP
# ==========================================
def run_funding_capture_engine():
    http_client = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
    add_ui_log("🔥 Engine Initialized: Top 3 Coins | $1 Margin | Capped 20x | T-20ms Entry | T+10ms Exit")

    while not ws_ready:
        time.sleep(0.1)

    while True:
        sync_bybit_clock(http_client)
        targets = scan_top_funding_opportunities(http_client)

        if not targets:
            with data_lock:
                dashboard_data['target_symbols'] = "Scanning (No coin <= -0.3%)..."
                dashboard_data['funding_rates'] = "--"
                dashboard_data['status'] = "SCANNING: Searching for targets <= -0.3%"
            time.sleep(3)
            continue

        settle_epoch = targets[0]['next_funding_time']
        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
        symbols_str = ", ".join([c['symbol'] for c in targets])
        rates_str = ", ".join([f"{c['funding_rate']*100:+.4f}%" for c in targets])

        with data_lock:
            dashboard_data['target_symbols'] = symbols_str
            dashboard_data['funding_rates'] = rates_str
            dashboard_data['target_settlement'] = settle_dt.strftime('%H:%M:%S IST')
            dashboard_data['status'] = f"ARMED ({len(targets)} Targets): {symbols_str}"
            dashboard_data['entry_target'] = f"{settle_dt.strftime('%H:%M:%S')}.980 IST (T - 20ms)"
            dashboard_data['exit_target'] = f"{settle_dt.strftime('%H:%M:%S')}.010 IST (T + 10ms)"

        add_ui_log(f"🎯 ARMED TOP {len(targets)} TARGETS: {symbols_str} | Settlement: {dashboard_data['target_settlement']}")

        t_precalc = settle_epoch - 2000      # T - 2s: Calculations & RAM locking
        last_5min_scan = time.time()
        last_rate_update = time.time()
        interrupted = False

        # --- DYNAMIC RE-SCAN & LIVE RATE UPDATE LOOP ---
        while True:
            now_ms = get_synced_time_ms()
            if now_ms >= t_precalc:
                break

            time.sleep(1.0)

            # 1. LIVE DYNAMIC FUNDING RATE UPDATE (Every 3 seconds)
            if time.time() - last_rate_update >= 3.0:
                last_rate_update = time.time()
                try:
                    active_rates = []
                    threshold_breached = False
                    for t in targets:
                        tick = http_client.get_tickers(category="linear", symbol=t['symbol'])["result"]["list"][0]
                        cur_rate = float(tick.get("fundingRate", t['funding_rate']))
                        t['funding_rate'] = cur_rate
                        t['last_price'] = float(tick.get("lastPrice", t['last_price']))
                        active_rates.append(f"{cur_rate * 100:+.4f}%")

                        # If rate weakens above -0.3%
                        if cur_rate > MIN_FUNDING_RATE_THRESHOLD:
                            add_ui_log(f"⚠️ THRESHOLD BREACH: {t['symbol']} weakened to {cur_rate*100:+.4f}%. Re-arming!")
                            threshold_breached = True

                    with data_lock:
                        dashboard_data['funding_rates'] = ", ".join(active_rates)

                    if threshold_breached:
                        interrupted = True
                        break
                except Exception:
                    pass

            # 2. 5-MINUTE SCHEDULED RE-SCAN & RE-ARM
            if time.time() - last_5min_scan >= 300:
                last_5min_scan = time.time()
                add_ui_log("🔄 5-Minute Interval: Full Market Rescan Running...")
                sync_bybit_clock(http_client)
                fresh_targets = scan_top_funding_opportunities(http_client)
                if fresh_targets:
                    fresh_symbols = [c['symbol'] for c in fresh_targets]
                    current_symbols = [c['symbol'] for c in targets]
                    if fresh_symbols != current_symbols or fresh_targets[0]['next_funding_time'] < settle_epoch:
                        add_ui_log(f"⚡ RE-ARMING: Target coins switched to {', '.join(fresh_symbols)}")
                        interrupted = True
                        break

        if interrupted:
            continue

        # --- T - 2s PRE-CALCULATION PHASE ---
        precision_wait_until(t_precalc)
        add_ui_log("⚙️ T - 2s Pre-Calculation: Computing precise quantities & checking leverage <= 20x...")
        sync_bybit_clock(http_client)

        prepared_plans = []
        for t in targets:
            sym = t['symbol']
            try:
                live_price = float(http_client.get_tickers(category="linear", symbol=sym)["result"]["list"][0]["lastPrice"])
            except Exception:
                live_price = t['last_price']

            calc = prepare_coin_parameters(http_client, sym, live_price, INITIAL_ENTRY_MARGIN_USD)
            calc['rate_str'] = f"{t['funding_rate']*100:+.4f}%"
            prepared_plans.append(calc)
            add_ui_log(f"  ✔ [{sym}] Qty: {calc['qty']} | Locked Leverage: {calc['leverage']}x (Max 20x cap) | $1 Margin")

        # --- PARALLEL INDEPENDENT LAUNCH ---
        worker_threads = []
        for plan in prepared_plans:
            th = threading.Thread(
                target=parallel_coin_executor,
                args=(http_client, plan, settle_epoch),
                daemon=True
            )
            worker_threads.append(th)
            th.start()

        # Wait for all 3 coin threads to complete their full cycle
        for th in worker_threads:
            th.join()

        add_ui_log("✅ All parallel workers finished cycle. Looking for next settlement...")
        time.sleep(5)

if __name__ == '__main__':
    run_funding_capture_engine()
