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
    "target_symbol": "Scanning Market...",
    "funding_rate": "0.00%",
    "action_direction": "--",
    "status": "Initializing Dynamic Priority Scanner...",
    "target_settlement": "-- IST",
    "entry_target": "-- IST (T - 50000ms)",
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
        if len(dashboard_data['logs']) > 70:
            dashboard_data['logs'].pop(0)

def add_ledger_entry(trade_info):
    with data_lock:
        dashboard_data['ledger'].insert(0, trade_info)
        if len(dashboard_data['ledger']) > 20:
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
<title>Bybit Dynamic Funding Engine</title>
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
    <h1>⚡ Bybit Dynamic Funding Engine ($2 Initial + 90% Funding TP)</h1>
    <div class="badge" id="ws-status">INITIALIZING</div>
  </div>
  <div class="grid">
    <div class="card"><div class="card-title">Live Target Coin</div><div class="card-value" id="coin">-</div></div>
    <div class="card"><div class="card-title">Funding Rate</div><div class="card-value" id="rate" style="color:#0ecb81;">-</div></div>
    <div class="card"><div class="card-title">Direction</div><div class="card-value" id="direction" style="color:#f7a600;">--</div></div>
    <div class="card"><div class="card-title">Entry (T - 50s)</div><div class="card-value" id="entry" style="color:#0ecb81; font-size:14px;">-</div></div>
    <div class="card"><div class="card-title">Exit (T + 8s / Emergency T+10s)</div><div class="card-value" id="exit" style="color:#f6465d; font-size:13px;">-</div></div>
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
API_KEY = os.environ.get('BYBIT_API_KEY', 'YOUR_API_KEY_HERE')
API_SECRET = os.environ.get('BYBIT_API_SECRET', 'YOUR_API_SECRET_HERE')

MIN_FUNDING_RATE_THRESHOLD = -0.004  # -0.4% Threshold
INITIAL_ENTRY_MARGIN_USD = 2.0      # $2 Initial Entry Margin

ws_client = None
ws_ready = False

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
            add_ui_log("WebSocket Trade Pipeline Authenticated & LIVE")

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

# ==========================================
# 5. MARKET SCANNER & ORDER CALCULATIONS
# ==========================================
def scan_best_funding_opportunity(http_client):
    try:
        tickers = http_client.get_tickers(category="linear")["result"]["list"]
    except Exception:
        return None

    now_ms = int(get_synced_time_ms())
    candidates_1h = []
    candidates_4h = []
    candidates_8h = []

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
        
        if 0 < time_diff <= 8 * 3600 * 1000:
            coin_data = {
                "symbol": sym,
                "funding_rate": rate,
                "next_funding_time": next_time_ms,
                "last_price": float(t.get("lastPrice", 0))
            }
            if time_diff <= 1.5 * 3600 * 1000:
                candidates_1h.append(coin_data)
            elif time_diff <= 4.5 * 3600 * 1000:
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

def calculate_qty_for_price(http_client, symbol, price, margin_usd=2.0):
    try:
        inst_info = http_client.get_instruments_info(category="linear", symbol=symbol)["result"]["list"][0]
        exchange_max_lev = float(inst_info.get("leverageFilter", {}).get("maxLeverage", 20))
        max_leverage = min(exchange_max_lev, 20.0)  # Strictly capping leverage to max 20x
        
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

def cancel_all_open_orders(http_client, symbol):
    try:
        http_client.cancel_all_orders(category="linear", symbol=symbol)
        add_ui_log(f"🛑 ALL PENDING LIMIT ORDERS CANCELLED for {symbol}")
    except Exception as e:
        add_ui_log(f"⚠ Failed to cancel open orders: {e}")

# ==========================================
# 6. DYNAMIC DCA ENGINE MAIN LOOP
# ==========================================
def run_funding_capture_engine():
    http_client = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
    add_ui_log("Dynamic Funding Engine Active ($2 Margin | TP = 90% of Funding). Waiting for WS...")

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
            dashboard_data['action_direction'] = "LONG CAPTURE + $2 DCA"

        t_rescan = settle_epoch - 65000  # 15 seconds before entry (T-50s)
        t_entry = settle_epoch - 50000
        t_cancel = settle_epoch + 7000
        t_exit = settle_epoch + 8000
        t_emergency = settle_epoch + 10000  # Emergency safety fallback at T + 10s

        entry_ist = datetime.fromtimestamp(t_entry / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]
        exit_ist = datetime.fromtimestamp(t_exit / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]

        with data_lock:
            dashboard_data['entry_target'] = f"{entry_ist} IST (T - 50000ms)"
            dashboard_data['exit_target'] = f"{exit_ist} IST (Emergency T+10s)"
            dashboard_data['status'] = f"ARMED [{window_type}]: {symbol} | Rate: {rate_percent}"

        add_ui_log(f"🎯 TARGET ARMED [{window_type}]: {symbol} | Rate: {rate_percent} | Entry: T-50s")

        # CONTINUOUS BACKGROUND RE-SCANNING / DYNAMIC INTERRUPT LOOP
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
                
                if new_opp and new_opp['symbol'] != symbol:
                    new_win = new_opp['window_type']
                    if (window_type == '8H' and new_win in ['1H', '4H']) or (window_type == '4H' and new_win == '1H') or (new_opp['funding_rate'] < rate):
                        add_ui_log(f"⚡ SWITCHING TARGET: Found better coin {new_opp['symbol']} ({new_opp['funding_rate']*100:.4f}%)")
                        interrupted = True
                        break

        if interrupted:
            continue

        # ==========================================
        # T - 65 SECONDS PRE-ENTRY FINAL RESCAN & THRESHOLD CHECK
        # ==========================================
        precision_wait_until(t_rescan)
        add_ui_log("⚡ T-65s Pre-Entry Final Rescan running...")
        sync_bybit_clock(http_client)
        final_opp = scan_best_funding_opportunity(http_client)

        if final_opp:
            if final_opp['symbol'] != symbol and final_opp['funding_rate'] < rate:
                add_ui_log(f"🔄 PRE-ENTRY SWITCH: Upgraded to {final_opp['symbol']} ({final_opp['funding_rate']*100:.4f}%)")
                opportunity = final_opp
                symbol = opportunity['symbol']
                rate = opportunity['funding_rate']
            elif final_opp['symbol'] == symbol:
                opportunity = final_opp
                rate = opportunity['funding_rate']

        # Check if rate dropped below threshold (e.g., -0.3% which is > -0.4%)
        if rate > MIN_FUNDING_RATE_THRESHOLD:
            add_ui_log(f"⚠ THRESHOLD BREACH / WEAK RATE: {symbol} rate is now {rate*100:+.4f}% (Above -0.4%). Skipping trade & restarting scan...")
            time.sleep(2)
            continue

        # ==========================================
        # T - 50 SECONDS ENTRY EXECUTION ($2 INITIAL)
        # ==========================================
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
            add_ui_log(f"🚀 INITIAL ENTRY EXECUTED (BUY): {initial_qty} {symbol} ($2 Margin @ {leverage}x)")
            entry_success = True
        except Exception as e:
            add_ui_log(f"Initial entry failed: {e}")

        if entry_success:
            time.sleep(0.3)
            current_avg_price = live_price
            try:
                pos_info = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
                current_avg_price = float(pos_info.get("avgPrice", current_avg_price))
            except Exception:
                pass

            # Calculate TP Offset as 90% of absolute funding rate
            abs_funding_pct = abs(rate)
            tp_offset = abs_funding_pct * 0.90

            # 1. Take Profit (90% of Funding Rate)
            tp_price = round(current_avg_price * (1.0 + tp_offset), price_decimals)
            try:
                http_client.set_trading_stop(
                    category="linear",
                    symbol=symbol,
                    takeProfit=f"{tp_price:.{price_decimals}f}",
                    tpTriggerBy="LastPrice",
                    positionIdx=0
                )
                add_ui_log(f"🎯 INITIAL TP SET AT: {tp_price:.{price_decimals}f} (+{tp_offset*100:.4f}% / 90% of Funding)")
            except Exception as e:
                add_ui_log(f"TP Set Failed: {e}")

            # 2. Dynamic 6 DCA Limit Orders Calculation ($2 margin each)
            dca1_offset = abs_funding_pct * 0.90
            dca2_offset = dca1_offset + 0.003
            dca3_offset = dca2_offset + 0.003
            dca4_offset = dca3_offset + 0.003
            dca5_offset = dca4_offset + 0.003
            dca6_offset = dca5_offset + 0.005

            dca_orders_spec = [
                {"offset": dca1_offset, "margin": 2.0, "label": "90% Funding"},
                {"offset": dca2_offset, "margin": 2.0, "label": "+0.3%"},
                {"offset": dca3_offset, "margin": 2.0, "label": "+0.3%"},
                {"offset": dca4_offset, "margin": 2.0, "label": "+0.3%"},
                {"offset": dca5_offset, "margin": 2.0, "label": "+0.3%"},
                {"offset": dca6_offset, "margin": 2.0, "label": "+0.5%"}
            ]

            add_ui_log("📐 PLACING 6 DYNAMIC DCA LIMIT ORDERS ($2 EACH)...")
            for spec in dca_orders_spec:
                pct = spec["offset"]
                order_margin = spec["margin"]
                dca_target_price = round(current_avg_price * (1.0 - pct), price_decimals)
                
                notional = order_margin * leverage
                calc_dca_qty = max(min_qty, math.floor((notional / dca_target_price) / qty_step) * qty_step)
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
                    add_ui_log(f" └─ Limit Order ({spec['label']}) Placed at {dca_target_price:.{price_decimals}f} (-{pct*100:.2f}%) | ${order_margin} Margin | Qty: {dca_qty}")
                except Exception as e:
                    add_ui_log(f"Limit Order (-{pct*100:.2f}%) Failed: {e}")

            # 3. Dynamic Average & TP Monitoring Loop until T + 7s
            last_known_avg = current_avg_price
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
                        add_ui_log("✅ TAKE PROFIT HIT! Position closed. Cancelling remaining orders...")
                        cancel_all_open_orders(http_client, symbol)
                        break

                    new_avg = float(pos_info.get("avgPrice", last_known_avg))
                    
                    if new_avg > 0 and abs(new_avg - last_known_avg) > (10 ** -price_decimals):
                        last_known_avg = new_avg
                        new_tp_price = round(new_avg * (1.0 + tp_offset), price_decimals)
                        
                        http_client.set_trading_stop(
                            category="linear",
                            symbol=symbol,
                            takeProfit=f"{new_tp_price:.{price_decimals}f}",
                            tpTriggerBy="LastPrice",
                            positionIdx=0
                        )
                        add_ui_log(f"🔄 DCA FILLED! New Avg Price: {new_avg:.{price_decimals}f} | Updated TP: {new_tp_price:.{price_decimals}f} (+{tp_offset*100:.4f}%)")

                except Exception:
                    pass

                time.sleep(0.5)

        # ==========================================
        # 4. T + 8s HARD EXIT
        # ==========================================
        precision_wait_until(t_exit)
        exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
        
        status_text = "SAFE EXIT FAILED"
        status_class = "status-cancel"

        try:
            pos_info = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
            pos_qty = float(pos_info.get("size", 0))

            if pos_qty > 0:
                cancel_all_open_orders(http_client, symbol)
                http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=str(pos_qty), reduceOnly=True, positionIdx=0)
                add_ui_log(f"⏰ HARD EXIT DISPATCHED (SELL MARKET): {pos_qty} {symbol} at {exit_time_str}")
                status_text = "HARD EXIT (T+8s)"
                status_class = "status-cancel"
            else:
                status_text = "TP HIT (90% Funding)"
                status_class = "status-profit"
        except Exception as e:
            add_ui_log(f"Exit check failed: {e}")

        # ==========================================
        # 5. T + 10s EMERGENCY FALLBACK EXIT CLEANUP
        # ==========================================
        precision_wait_until(t_emergency)
        try:
            pos_info = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
            pos_qty = float(pos_info.get("size", 0))

            if pos_qty > 0:
                add_ui_log(f"🚨 EMERGENCY FALLBACK EXIT (T+10s): Position still open ({pos_qty} {symbol}). Forcing clean close...")
                cancel_all_open_orders(http_client, symbol)
                
                for attempt in range(3):
                    try:
                        http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=str(pos_qty), reduceOnly=True, positionIdx=0)
                        time.sleep(0.3)
                        check_pos = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
                        if float(check_pos.get("size", 0)) == 0:
                            add_ui_log(f"✅ Emergency cleanup successful on attempt {attempt+1}")
                            break
                    except Exception as inner_e:
                        add_ui_log(f"Emergency order attempt {attempt+1} failed: {inner_e}")
                        time.sleep(0.5)
                        
                status_text = "EMERGENCY CLEANUP (T+10s)"
                status_class = "status-cancel"
        except Exception as e:
            add_ui_log(f"Emergency exit check failed: {e}")

        add_ledger_entry({
            "coin": f"{symbol} ({window_type})",
            "rate": rate_percent,
            "entry_time": entry_time_str,
            "exit_time": exit_time_str,
            "status_text": status_text,
            "status_class": status_class
        })

        time.sleep(3)

if __name__ == '__main__':
    run_funding_capture_engine()
