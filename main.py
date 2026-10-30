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
# 1. TIMEZONE & TELEMETRY STATE
# ==========================================
IST = timezone(timedelta(hours=5, minutes=30))
data_lock = threading.Lock()

dashboard_data = {
    "target_symbols": "Scanning Market (Top 3)...",
    "funding_rates": "--",
    "status": "Initializing Engine...",
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
# 2. CLOCK SYNCHRONIZATION
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
# 3. WEBSOCKET PIPELINE WITH ERROR PARSING
# ==========================================
API_KEY = os.environ.get('BYBIT_API_KEY', 'YOUR_API_KEY_HERE')
API_SECRET = os.environ.get('BYBIT_API_SECRET', 'YOUR_API_SECRET_HERE')

MIN_FUNDING_RATE_THRESHOLD = -0.003
INITIAL_ENTRY_MARGIN_USD = 1.0
MAX_TARGET_COINS = 3
MAX_ALLOWED_LEVERAGE = 20.0

ws_client = None
ws_ready = False
ws_lock = threading.Lock()
ws_response_tracker = {}

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
    ret_code = data.get("retCode", 0)
    ret_msg = data.get("retMsg", "")
    req_id = data.get("reqId", "")

    # Auth Response
    if op == "auth":
        global ws_ready
        if data.get("success") is True or ret_code == 0:
            ws_ready = True
            with data_lock:
                dashboard_data['status'] = "WS AUTHENTICATED (READY)"
            add_ui_log("⚡ WebSocket Fast-Lane Order Pipeline Authenticated & READY")
        else:
            add_ui_log(f"❌ WS Auth Failed: {ret_msg} (Code: {ret_code})")

    # Order Create Response (Fix for Bug 3)
    if op == "order.create" or req_id:
        if req_id in ws_response_tracker:
            ws_response_tracker[req_id]["responded"] = True
            ws_response_tracker[req_id]["retCode"] = ret_code
            ws_response_tracker[req_id]["retMsg"] = ret_msg
            if ret_code != 0:
                add_ui_log(f"🚨 BYBIT WS REJECTED [{req_id}]: {ret_msg} (Code: {ret_code})")
            else:
                add_ui_log(f"✅ BYBIT WS CONFIRMED [{req_id}]: Order Accepted!")

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

def send_fast_ws_order(payload, req_id):
    """Payload includes valid V5 headers (Fix for Bug 1)"""
    global ws_client, ws_ready
    if ws_ready and ws_client:
        try:
            ws_response_tracker[req_id] = {"responded": False, "retCode": -1, "retMsg": ""}
            with ws_lock:
                ws_client.send(json.dumps(payload))
            return True
        except Exception as e:
            return False
    return False

# ==========================================
# 4. SCANNER & CALCULATION ENGINE
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

    earliest_time = min(c['next_funding_time'] for c in valid_candidates)
    imminent_candidates = [c for c in valid_candidates if abs(c['next_funding_time'] - earliest_time) <= 600000]
    imminent_candidates.sort(key=lambda x: x['funding_rate'])
    return imminent_candidates[:MAX_TARGET_COINS]

def prepare_coin_parameters(http_client, symbol, price, margin_usd=1.0):
    try:
        inst_info = http_client.get_instruments_info(category="linear", symbol=symbol)["result"]["list"][0]
        exchange_max_lev = float(inst_info.get("leverageFilter", {}).get("maxLeverage", 20.0))
        lot_filter = inst_info['lotSizeFilter']
        qty_step = float(lot_filter["qtyStep"])
        min_qty = float(lot_filter['minOrderQty'])
    except Exception as e:
        exchange_max_lev = 20.0
        qty_step = 1.0
        min_qty = 1.0

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
# 5. PARALLEL WORKER THREAD
# ==========================================
def parallel_coin_executor(http_client, coin_plan, settle_epoch):
    symbol = coin_plan['symbol']
    rate_str = coin_plan['rate_str']
    qty = coin_plan['qty']
    qty_decimals = coin_plan['qty_decimals']
    leverage = coin_plan['leverage']

    t_entry = settle_epoch - 20
    t_exit = settle_epoch + 10

    entry_req_id = f"buy_{symbol}_{int(time.time()*1000)}"
    exit_req_id = f"sell_{symbol}_{int(time.time()*1000)}"

    # Proper Bybit V5 Header Structure
    buy_payload = {
        "reqId": entry_req_id,
        "header": {
            "X-BAPI-TIMESTAMP": str(int(get_synced_time_ms()))
        },
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

    # 1. ENTRY (T - 20ms)
    precision_wait_until(t_entry)
    entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
    ws_sent = send_fast_ws_order(buy_payload, entry_req_id)
    
    # REST Fallback if WS not available
    if not ws_sent:
        try:
            http_client.place_order(category="linear", symbol=symbol, side="Buy", orderType="Market", qty=qty, positionIdx=0)
            entry_result_str = "Sent via REST"
        except Exception as e:
            entry_result_str = f"REST Error: {e}"
    else:
        entry_result_str = "Dispatched via WebSocket"

    add_ui_log(f"🚀 [{symbol}] ENTRY SENT at {entry_time_str} | {entry_result_str}")

    # 2. EXIT (T + 10ms)
    precision_wait_until(t_exit)
    exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

    sell_payload = {
        "reqId": exit_req_id,
        "header": {
            "X-BAPI-TIMESTAMP": str(int(get_synced_time_ms()))
        },
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

    ws_exit_sent = send_fast_ws_order(sell_payload, exit_req_id)
    if not ws_exit_sent:
        try:
            http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=qty, reduceOnly=True, positionIdx=0)
            exit_result_str = "Sent via REST"
        except Exception as e:
            exit_result_str = f"REST Error: {e}"
    else:
        exit_result_str = "Dispatched via WebSocket"

    add_ui_log(f"⚡ [{symbol}] EXIT SENT at {exit_time_str} | {exit_result_str}")

    # 3. VERIFY ENTRY WAS ACTUALLY PLACED (Fix for Bug 4: Ghost Fill)
    time.sleep(0.3)
    entry_verified = False
    ws_entry_resp = ws_response_tracker.get(entry_req_id, {})
    if ws_entry_resp.get("retCode") == 0:
        entry_verified = True
    else:
        # Double check on exchange
        try:
            pos_chk = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"][0]
            if float(pos_chk.get("size", 0)) > 0 or float(pos_chk.get("cumRealisedPnl", 0)) != 0:
                entry_verified = True
        except Exception:
            pass

    # 4. EMERGENCY EXITS (T+1s, T+2s, T+10s)
    emergency_times = [
        ("T+1s", settle_epoch + 1000),
        ("T+2s", settle_epoch + 2000),
        ("T+10s", settle_epoch + 10000)
    ]

    emergency_action_str = "None"
    triggered = []

    for stage_name, target_ms in emergency_times:
        precision_wait_until(target_ms)
        try:
            pos_resp = http_client.get_positions(category="linear", symbol=symbol)
            positions = pos_resp.get("result", {}).get("list", [])
            pos_size = float(positions[0].get("size", 0)) if positions else 0.0

            if pos_size > 0:
                rem_qty = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
                http_client.place_order(
                    category="linear", symbol=symbol, side="Sell", 
                    orderType="Market", qty=rem_qty, reduceOnly=True, positionIdx=0
                )
                act = f"Closed {rem_qty} at {stage_name}"
                triggered.append(act)
                add_ui_log(f"🛡 [{symbol}] EMERGENCY TRIGGERED: {act}")
            else:
                break
        except Exception:
            pass

    # Granular Ledger Logging
    if not entry_verified:
        err_msg = ws_entry_resp.get("retMsg", "Order rejected by Bybit")
        badge_text = "ENTRY REJECTED"
        badge_class = "status-error"
        error_detail = f"Bybit Error: {err_msg} (Code: {ws_entry_resp.get('retCode', -1)})"
    elif triggered:
        badge_text = "EMERGENCY CLOSED"
        badge_class = "status-warn"
        emergency_action_str = " | ".join(triggered)
        error_detail = ""
    else:
        badge_text = "CAPTURED (CLEAN)"
        badge_class = "status-success"
        emergency_action_str = "Clean (Filled at T+10ms)"
        error_detail = ""

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
        "error_message": error_detail,
        "badge_text": badge_text,
        "badge_class": badge_class
    })

# ==========================================
# 6. MAIN ENGINE LOOP
# ==========================================
def run_funding_capture_engine():
    http_client = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
    add_ui_log("🔥 Engine Ready: Top 3 Coins | $1 Margin | Capped 20x | T-20ms Entry | T+10ms Exit")

    while not ws_ready:
        time.sleep(0.1)

    while True:
        sync_bybit_clock(http_client)
        targets = scan_top_funding_opportunities(http_client)

        if not targets:
            with data_lock:
                dashboard_data['target_symbols'] = "Scanning (No coin <= -0.3%)..."
                dashboard_data['funding_rates'] = "--"
                dashboard_data['status'] = "SCANNING"
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
            dashboard_data['status'] = f"ARMED: {symbols_str}"

        # Fix for Bug 5: Pre-calculation moved to T - 5s
        t_precalc = settle_epoch - 5000
        last_3s_update = time.time()
        interrupted = False

        while True:
            now_ms = get_synced_time_ms()
            if now_ms >= t_precalc:
                break

            time.sleep(1.0)
            if time.time() - last_3s_update >= 3.0:
                last_3s_update = time.time()
                try:
                    active_rates = []
                    for t in targets:
                        tick = http_client.get_tickers(category="linear", symbol=t['symbol'])["result"]["list"][0]
                        cur_rate = float(tick.get("fundingRate", t['funding_rate']))
                        t['funding_rate'] = cur_rate
                        t['last_price'] = float(tick.get("lastPrice", t['last_price']))
                        active_rates.append(f"{cur_rate * 100:+.4f}%")
                        if cur_rate > MIN_FUNDING_RATE_THRESHOLD:
                            interrupted = True
                            break
                    with data_lock:
                        dashboard_data['funding_rates'] = ", ".join(active_rates)
                except Exception:
                    pass

        if interrupted:
            continue

        # --- T - 5s PRE-CALCULATION ---
        precision_wait_until(t_precalc)
        add_ui_log("⚙️ T - 5s Pre-Calculation running in advance...")
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

        # Launch worker threads well before T-20ms mark
        worker_threads = []
        for plan in prepared_plans:
            th = threading.Thread(
                target=parallel_coin_executor,
                args=(http_client, plan, settle_epoch),
                daemon=True
            )
            worker_threads.append(th)
            th.start()

        for th in worker_threads:
            th.join()

        add_ui_log("✅ Parallel cycle complete.")
        time.sleep(5)

# Web server dashboard runner
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

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), FundingDashboardHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

if __name__ == '__main__':
    run_funding_capture_engine()
