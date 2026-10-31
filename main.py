import os
import time
import json
import math
import threading
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
    "action_direction": "PARALLEL LIMIT CAPTURE ($1 MARGIN | MAX 20x)",
    "status": "Initializing Parallel Limit Engine...",
    "target_settlement": "-- IST",
    "entry_target": "-- IST (T - 250ms Limit)",
    "exit_target": "-- IST (T + 7s Hard Exit)",
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
    """Sub-millisecond hybrid sleep & spin-wait loop"""
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
# 3. WEB DASHBOARD WITH DETAILED ERROR LEDGER
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
.error-tag { color: #f6465d; font-family: monospace; background: rgba(246, 70, 93, 0.08); padding: 6px 10px; border-radius: 4px; margin-top: 4px; border: 1px solid rgba(246, 70, 93, 0.2); }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>⚡ Bybit Parallel Funding Engine (Top 3 | $1 Margin | Capped 20x)</h1>
    <div class="badge" id="ws-status">LIVE</div>
  </div>
  <div class="grid">
    <div class="card"><div class="card-title">Targets (Top 3)</div><div class="card-value" id="coins">-</div></div>
    <div class="card"><div class="card-title">Live Rates</div><div class="card-value" id="rates" style="color:#0ecb81;">-</div></div>
    <div class="card"><div class="card-title">Settlement Time</div><div class="card-value" id="settlement" style="color:#38bdf8;">--</div></div>
    <div class="card"><div class="card-title">Entry Window</div><div class="card-value" id="entry" style="color:#0ecb81; font-size:13px;">T - 250ms (Limit)</div></div>
    <div class="card"><div class="card-title">Hard Exit</div><div class="card-value" id="exit" style="color:#f6465d; font-size:13px;">T + 7s</div></div>
    <div class="card"><div class="card-title">Clock Drift</div><div class="card-value" id="offset">0.0 ms</div></div>
  </div>

  <div class="section-card">
    <div style="font-size: 14px; font-weight: 700; margin-bottom: 6px;">📋 Execution Ledger (Real Fill & Error Verification)</div>
    <div class="ledger-container" id="ledger-box">
      <div style="color: #848e9c; text-align: center; padding: 25px;">No trades recorded yet.</div>
    </div>
  </div>

  <div class="section-card">
    <div style="font-size: 14px; font-weight: 700; margin-bottom: 6px;">🖥 Terminal Logs</div>
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
            <div class="detail-item">🚀 <b>Limit Entry (T-250ms):</b> <span>${item.entry_time}</span> | ${item.entry_result}</div>
            <div class="detail-item">🎯 <b>Limit Price / SL:</b> <span>${item.limit_price}</span> / <span>${item.sl_price}</span></div>
            <div class="detail-item">⏰ <b>Hard Exit (T+7s):</b> <span>${item.exit_time}</span> | ${item.exit_result}</div>
            <div class="detail-item">🛡 <b>Emergency Action:</b> <span>${item.emergency_action}</span></div>
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
# 4. CONFIGURATION & ENGINE SETTINGS
# ==========================================
API_KEY = os.environ.get('BYBIT_API_KEY', 'YOUR_API_KEY_HERE')
API_SECRET = os.environ.get('BYBIT_API_SECRET', 'YOUR_API_SECRET_HERE')

MIN_FUNDING_RATE_THRESHOLD = -0.003  # -0.3% Threshold
INITIAL_ENTRY_MARGIN_USD = 1.0       # $1 Margin strictly kept
MAX_TARGET_COINS = 3                 # Top 3 Coins Parallel
MAX_ALLOWED_LEVERAGE = 20.0          # Strictly capped at 20x

# ==========================================
# 5. SCANNER & CALCULATION LOGIC
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
        price_filter = inst_info.get('priceFilter', {})
        qty_step = float(lot_filter["qtyStep"])
        min_qty = float(lot_filter['minOrderQty'])
        min_notional = float(lot_filter.get("minNotionalValue", 5.0))
        tick_size = float(price_filter.get("tickSize", "0.0001"))
    except Exception as e:
        add_ui_log(f"[{symbol}] Instrument info read error: {e}")
        exchange_max_lev = 20.0
        qty_step = 1.0
        min_qty = 1.0
        min_notional = 5.0
        tick_size = 0.0001

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

    tick_str = f"{tick_size:.8f}".rstrip("0")
    price_decimals = len(tick_str.split(".")[1]) if "." in tick_str else 2

    notional = margin_usd * applied_leverage
    calc_qty = math.floor((notional / price) / qty_step) * qty_step

    if calc_qty < min_qty:
        calc_qty = min_qty
    if (calc_qty * price) < min_notional:
        calc_qty = math.ceil((min_notional / price) / qty_step) * qty_step

    formatted_qty = f"{calc_qty:.{qty_decimals}f}" if qty_decimals > 0 else str(int(calc_qty))

    return {
        "symbol": symbol,
        "qty": formatted_qty,
        "leverage": applied_leverage,
        "qty_decimals": qty_decimals,
        "price_decimals": price_decimals,
        "tick_size": tick_size,
        "last_price": price
    }

# ==========================================
# 6. PARALLEL LIMIT ORDER WORKER THREAD
# ==========================================
def parallel_coin_executor(http_client, coin_plan, settle_epoch):
    """
    Dedicated worker thread per coin:
    - Calculation & Limit Entry with 1% SL at T - 250ms (Min 0.6% discount floor)
    - Hard Exit at T + 7000ms (7s)
    - Emergency Exits strictly at T + 50s and T + 60s
    """
    symbol = coin_plan['symbol']
    rate = coin_plan['rate']
    rate_str = coin_plan['rate_str']
    qty = coin_plan['qty']
    qty_decimals = coin_plan['qty_decimals']
    price_decimals = coin_plan['price_decimals']
    tick_size = coin_plan['tick_size']
    leverage = coin_plan['leverage']

    t_entry = settle_epoch - 250      # T - 250ms Limit Order
    t_exit = settle_epoch + 7000      # T + 7s Hard Exit

    entry_result_str = "--"
    exit_result_str = "--"
    emergency_action_str = "None (Position Flat)"
    error_log = []
    entry_confirmed = False
    limit_order_id = None
    limit_price_str = "--"
    sl_price_str = "--"

    # 1. PRECISE LIMIT ENTRY AT T - 250ms
    precision_wait_until(t_entry)
    entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

    try:
        try:
            ticker_data = http_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
            live_price = float(ticker_data.get("lastPrice", coin_plan['last_price']))
            live_rate = float(ticker_data.get("fundingRate", rate))
        except Exception:
            live_price = coin_plan['last_price']
            live_rate = rate

        # Discount formula: 110% of funding rate magnitude, clamped to min 0.6% (0.006)
        calc_discount = abs(live_rate) * 1.10
        effective_discount = max(0.006, calc_discount)
        raw_limit_price = live_price * (1.0 - effective_discount)

        limit_price = round(math.floor(raw_limit_price / tick_size) * tick_size, price_decimals)
        limit_price_str = f"{limit_price:.{price_decimals}f}"

        # 1% Stop Loss below limit price
        raw_sl_price = limit_price * 0.99
        sl_price = round(math.floor(raw_sl_price / tick_size) * tick_size, price_decimals)
        sl_price_str = f"{sl_price:.{price_decimals}f}"

        # Place Buy Limit Order with Stop Loss attached directly on Bybit engine
        entry_resp = http_client.place_order(
            category="linear",
            symbol=symbol,
            side="Buy",
            orderType="Limit",
            price=limit_price_str,
            qty=qty,
            timeInForce="GTC",
            positionIdx=0,
            stopLoss=sl_price_str,
            slTriggerBy="LastPrice"
        )

        if entry_resp.get("retCode") == 0:
            limit_order_id = entry_resp["result"].get("orderId", "")
            entry_result_str = f"LIMIT PLACED (ID: {limit_order_id[:8]}..)"
            entry_confirmed = True
            add_ui_log(f"🎯 [{symbol}] LIMIT BUY PLACED at {limit_price_str} (Disc: {effective_discount*100:.2f}% | SL: {sl_price_str} | Qty: {qty}) at {entry_time_str}")
        else:
            err_msg = f"RetCode {entry_resp.get('retCode')}: {entry_resp.get('retMsg')}"
            entry_result_str = "REJECTED BY BYBIT"
            error_log.append(err_msg)
            add_ui_log(f"❌ [{symbol}] Limit Entry Rejected: {err_msg}")
    except Exception as e:
        err_msg = f"Entry Exception: {str(e)}"
        entry_result_str = "EXCEPTION FAILED"
        error_log.append(err_msg)
        add_ui_log(f"❌ [{symbol}] Entry Exception: {err_msg}")

    # 2. HARD EXIT AT T + 7s (Cancel unfilled limit order & market close open position)
    precision_wait_until(t_exit)
    exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

    # Cancel limit order if still open
    try:
        if limit_order_id:
            http_client.cancel_order(category="linear", symbol=symbol, orderId=limit_order_id)
        else:
            http_client.cancel_all_orders(category="linear", symbol=symbol)
    except Exception:
        pass

    # Check and close open position
    pos_filled = False
    try:
        pos_resp = http_client.get_positions(category="linear", symbol=symbol)
        positions = pos_resp.get("result", {}).get("list", [])
        pos_size = float(positions[0].get("size", 0)) if positions else 0.0

        if pos_size > 0:
            pos_filled = True
            formatted_exit_qty = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
            exit_resp = http_client.place_order(
                category="linear",
                symbol=symbol,
                side="Sell",
                orderType="Market",
                qty=formatted_exit_qty,
                reduceOnly=True,
                positionIdx=0
            )
            if exit_resp.get("retCode") == 0:
                exit_result_str = f"CLOSED (T+7s Market)"
                add_ui_log(f"⚡ [{symbol}] HARD EXIT CLOSED: {formatted_exit_qty} at {exit_time_str}")
            else:
                err_msg = f"Exit RetCode {exit_resp.get('retCode')}: {exit_resp.get('retMsg')}"
                exit_result_str = "EXIT REJECTED"
                error_log.append(err_msg)
        else:
            exit_result_str = "NO OPEN POSITION (LIMIT UNFILLED / SL HIT)"
            add_ui_log(f"ℹ [{symbol}] Position Flat at T+7s (Unfilled or SL Hit)")
    except Exception as e:
        err_msg = f"Hard Exit Exception: {str(e)}"
        exit_result_str = "EXCEPTION FAILED"
        error_log.append(err_msg)

    # 3. EMERGENCY EXITS (STRICTLY AT T + 50s and T + 60s)
    emergency_times = [
        ("T+50s", settle_epoch + 50000),
        ("T+60s", settle_epoch + 60000)
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

    # Status Determination for Ledger
    if not entry_confirmed:
        badge_text = "ENTRY REJECTED"
        badge_class = "status-error"
    elif triggered_stages:
        emergency_action_str = " | ".join(triggered_stages)
        badge_text = "EMERGENCY CLOSED"
        badge_class = "status-warn"
    elif pos_filled and position_cleared:
        emergency_action_str = "Clean (Closed at T+7s)"
        badge_text = "CAPTURED (CLEAN)"
        badge_class = "status-success"
    else:
        badge_text = "FLAT / UNFILLED"
        badge_class = "status-warn"

    add_ledger_entry({
        "coin": symbol,
        "rate": rate_str,
        "qty": qty,
        "leverage": leverage,
        "limit_price": limit_price_str,
        "sl_price": sl_price_str,
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
    add_ui_log("🔥 Limit Capture Engine Loaded: Top 3 Coins | $1 Margin | Capped 20x | T-250ms Limit | T+7s Hard Exit")

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
        entry_dt = datetime.fromtimestamp((settle_epoch - 250) / 1000, tz=IST)
        symbols_str = ", ".join([c['symbol'] for c in targets])
        rates_str = ", ".join([f"{c['funding_rate']*100:+.4f}%" for c in targets])

        with data_lock:
            dashboard_data['target_symbols'] = symbols_str
            dashboard_data['funding_rates'] = rates_str
            dashboard_data['target_settlement'] = settle_dt.strftime('%H:%M:%S IST')
            dashboard_data['status'] = f"ARMED ({len(targets)} Targets): {symbols_str}"
            dashboard_data['entry_target'] = f"{entry_dt.strftime('%H:%M:%S')}.750 IST (T - 250ms Limit)"
            dashboard_data['exit_target'] = f"{settle_dt.strftime('%H:%M:%S')}.000 + 7s IST"

        add_ui_log(f"🎯 ARMED TOP {len(targets)} COINS: {symbols_str} | Settlement: {dashboard_data['target_settlement']}")

        # Pre-calculation locked at T - 2s so worker threads are primed for T - 250ms
        t_precalc = settle_epoch - 2000
        last_5min_scan = time.time()
        last_rate_update = time.time()
        interrupted = False

        # --- DYNAMIC RE-SCAN & LIVE RATE UPDATE LOOP ---
        while True:
            now_ms = get_synced_time_ms()
            if now_ms >= t_precalc:
                break

            time.sleep(1.0)

            # 1. LIVE FUNDING RATE UPDATE (Every 3 seconds)
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

            # 2. 5-MINUTE FULL MARKET RE-SCAN & AUTO RE-ARM
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

        # --- PRE-CALCULATION PHASE (Locks leverage & lot step filters at T - 2s) ---
        precision_wait_until(t_precalc)
        add_ui_log("⚙️ T - 2s Pre-Flight: Locking leverage & lot filters...")
        sync_bybit_clock(http_client)

        prepared_plans = []
        for t in targets:
            sym = t['symbol']
            try:
                live_price = float(http_client.get_tickers(category="linear", symbol=sym)["result"]["list"][0]["lastPrice"])
            except Exception:
                live_price = t['last_price']

            calc = prepare_coin_parameters(http_client, sym, live_price, INITIAL_ENTRY_MARGIN_USD)
            calc['rate'] = t['funding_rate']
            calc['rate_str'] = f"{t['funding_rate']*100:+.4f}%"
            prepared_plans.append(calc)
            add_ui_log(f"  ✔ [{sym}] Qty: {calc['qty']} | Leverage: {calc['leverage']}x | TickSize: {calc['tick_size']}")

        # --- PARALLEL INDEPENDENT THREAD LAUNCH ---
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

        add_ui_log("✅ All parallel workers finished cycle. Preparing next round...")
        time.sleep(5)

if __name__ == '__main__':
    run_funding_capture_engine()
