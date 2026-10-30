import os
import time
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
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
    "status": "Initializing Bulletproof HTTP HFT Engine...",
    "target_settlement": "-- IST",
    "entry_target": "-- IST (T-20ms HTTP Buy)",
    "exit_target": "-- IST (T+10ms | T+300ms | T+10s | T+2m Emergency)",
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
        if len(dashboard_data['logs']) > 100:
            dashboard_data['logs'].pop(0)

def add_ledger_entry(trade_info):
    with data_lock:
        dashboard_data['ledger'].insert(0, trade_info)
        if len(dashboard_data['ledger']) > 25:
            dashboard_data['ledger'].pop()

# ==========================================
# 2. HIGH-PRECISION CLOCK SYNCHRONIZATION
# ==========================================
clock_offset_ms = 0.0
perf_base = 0.0
synced_base = 0.0

def sync_bybit_clock(http_client):
    global clock_offset_ms, perf_base, synced_base
    try:
        t_send = time.perf_counter() * 1000
        server_time_resp = http_client.get_server_time()
        t_recv = time.perf_counter() * 1000

        server_time = float(server_time_resp['result']['timeNano']) / 1e6
        rtt = t_recv - t_send
        estimated_server_time = server_time + (rtt / 2.0)
        
        wall_recv = time.time() * 1000
        clock_offset_ms = estimated_server_time - wall_recv
        
        perf_base = time.perf_counter() * 1000
        synced_base = estimated_server_time

        with data_lock:
            dashboard_data['clock_offset_ms'] = round(clock_offset_ms, 2)
    except Exception:
        pass

def get_synced_time_ms():
    global perf_base, synced_base
    if perf_base == 0.0:
        return (time.time() * 1000) + clock_offset_ms
    elapsed = (time.perf_counter() * 1000) - perf_base
    return synced_base + elapsed

def ultra_precision_wait(target_time_ms):
    while True:
        now = get_synced_time_ms()
        diff = target_time_ms - now
        if diff <= 0:
            break
        elif diff > 50:
            time.sleep(0.01)
        elif diff > 5:
            time.sleep(0.001)
        else:
            pass  # Spin-lock for absolute microsecond precision

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
<title>Bybit Bulletproof HTTP HFT Engine</title>
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
.status-error { background: rgba(246, 70, 93, 0.15); color: #f6465d; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>⚡ Bybit Bulletproof HTTP Engine ($1 | T-20ms Buy | T+10ms Sell | Verified Ledger)</h1>
    <div class="badge" id="ws-status">RUNNING</div>
  </div>
  <div class="grid">
    <div class="card"><div class="card-title">Target Coins</div><div class="card-value" id="coin" style="font-size:14px;">-</div></div>
    <div class="card"><div class="card-title">Funding Rates</div><div class="card-value" id="rate" style="color:#0ecb81; font-size:14px;">-</div></div>
    <div class="card"><div class="card-title">Direction</div><div class="card-value" id="direction" style="color:#f7a600;">--</div></div>
    <div class="card"><div class="card-title">Entry Target</div><div class="card-value" id="entry" style="color:#0ecb81; font-size:12px;">-</div></div>
    <div class="card"><div class="card-title">Exit Target</div><div class="card-value" id="exit" style="color:#f6465d; font-size:12px;">-</div></div>
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
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), FundingDashboardHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ==========================================
# 4. CONFIG & INSTRUMENT UTILS
# ==========================================
API_KEY = os.environ.get('BYBIT_API_KEY', 'YOUR_API_KEY_HERE')
API_SECRET = os.environ.get('BYBIT_API_SECRET', 'YOUR_API_SECRET_HERE')

MIN_FUNDING_RATE_THRESHOLD = -0.0039  # -0.39% Threshold to catch -0.4% coins
MARGIN_USD = 1.0                      # $1 Fixed Margin per coin

def get_instrument_specs(http_client, symbol):
    try:
        inst_info = http_client.get_instruments_info(category="linear", symbol=symbol)["result"]["list"][0]
        exchange_max_lev = float(inst_info.get("leverageFilter", {}).get("maxLeverage", 20))
        max_leverage = min(exchange_max_lev, 20.0)
        
        lot_filter = inst_info['lotSizeFilter']
        qty_step = float(lot_filter["qtyStep"])
        min_qty = float(lot_filter['minOrderQty'])

        try:
            http_client.set_leverage(category="linear", symbol=symbol, buyLeverage=str(max_leverage), sellLeverage=str(max_leverage))
        except Exception:
            pass

        qty_str = f"{qty_step:.8f}".rstrip("0")
        qty_decimals = len(qty_str.split(".")[1]) if "." in qty_str else 0

        return max_leverage, qty_step, min_qty, qty_decimals
    except Exception:
        return 20.0, 1.0, 1.0, 0

def scan_top_funding_opportunities(http_client, max_count=3):
    try:
        resp = http_client.get_tickers(category="linear")
        tickers = resp.get("result", {}).get("list", [])
        if not tickers:
            add_ui_log("⚠ Scan Warning: No tickers returned from Bybit API.")
            return []

        now_ms = int(get_synced_time_ms())
        all_candidates = []

        for t in tickers:
            try:
                sym = str(t.get("symbol", ""))
                if not sym.endswith("USDT"):
                    continue
                raw_rate = t.get("fundingRate")
                raw_next = t.get("nextFundingTime")
                if raw_rate is None or raw_next is None:
                    continue

                rate = float(raw_rate)
                next_time_ms = int(raw_next)
                last_price = float(t.get("lastPrice", 0))

                time_diff = next_time_ms - now_ms
                
                # Check for settlement within 8.5 hours & rate <= -0.39%
                if 0 < time_diff <= 8.5 * 3600 * 1000 and rate <= MIN_FUNDING_RATE_THRESHOLD:
                    hours_diff = time_diff / (3600 * 1000)
                    
                    # Priority Tiers: 1h -> 4h -> 8h
                    if hours_diff <= 1.5:
                        priority_tier = 1
                    elif hours_diff <= 4.5:
                        priority_tier = 2
                    else:
                        priority_tier = 3

                    all_candidates.append({
                        "symbol": sym,
                        "funding_rate": rate,
                        "next_funding_time": next_time_ms,
                        "last_price": last_price,
                        "priority_tier": priority_tier,
                        "hours_diff": hours_diff
                    })
            except Exception:
                continue

        if not all_candidates:
            add_ui_log("⚠ Scan Info: No coins found matching 1h/4h/8h and negative rate criteria.")
            return []

        # Sort by Priority Tier (1h first, then 4h, 8h), then by most negative rate
        all_candidates.sort(key=lambda x: (x['priority_tier'], x['funding_rate']))

        for c in all_candidates[:max_count]:
            add_ui_log(f"🎯 Selected: {c['symbol']} | Rate: {c['funding_rate']*100:+.3f}% | Settle in: {c['hours_diff']:.1f}h (Tier {c['priority_tier']})")

        target_settle = all_candidates[0]['next_funding_time']
        cluster = [c for c in all_candidates if abs(c['next_funding_time'] - target_settle) <= 15000]

        return cluster[:max_count]
    except Exception as e:
        add_ui_log(f"⚠ Scan Error: {e}")
        return []

# ==========================================
# 5. PRODUCTION ENGINE MAIN LOOP (HTTP HFT)
# ==========================================
def run_production_engine():
    http_client = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
    add_ui_log("🚀 Bulletproof HTTP HFT Engine Active (Continuous 5-Min Rescan & Verified Ledger)")

    while True:
        try:
            sync_bybit_clock(http_client)
            
            opportunities = []
            while True:
                opportunities = scan_top_funding_opportunities(http_client, max_count=3)
                if opportunities:
                    settle_epoch = opportunities[0]['next_funding_time']
                    time_until_settle = settle_epoch - get_synced_time_ms()
                    if 0 < time_until_settle <= 600000: # Within 10 mins of settlement
                        break
                
                with data_lock:
                    dashboard_data['status'] = "SCANNING/RE-ARMING (Every 5m)..."
                    dashboard_data['target_symbol'] = "Searching Top Rates (1h/4h/8h)..."
                    dashboard_data['funding_rate'] = "0.00%"
                
                add_ui_log("🔄 Rescanning market for best funding opportunities (Next check in 300s)...")
                time.sleep(300)

            settle_epoch = opportunities[0]['next_funding_time']
            settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
            
            pre_fetched_specs = {}
            for opp in opportunities:
                sym = opp['symbol']
                lev, q_step, m_qty, q_dec = get_instrument_specs(http_client, sym)
                pre_fetched_specs[sym] = {
                    "leverage": lev, "qty_step": q_step,
                    "min_qty": m_qty, "qty_decimals": q_dec
                }

            symbols_str = ", ".join([o['symbol'] for o in opportunities])
            rates_str = ", ".join([f"{o['funding_rate']*100:+.2f}%" for o in opportunities])

            with data_lock:
                dashboard_data['target_symbol'] = symbols_str
                dashboard_data['funding_rate'] = rates_str
                dashboard_data['target_settlement'] = settle_dt.strftime('%H:%M:%S IST')
                dashboard_data['action_direction'] = "LOCKED: T-20ms Buy -> T+10ms Sell -> Multi-Emergency"

            # ==========================================
            # TIMINGS SETUP
            # ==========================================
            t_pre_compute = settle_epoch - 800     # T - 800ms
            t_entry = settle_epoch - 20            # T - 20ms (Buy Entry)
            t_exit = settle_epoch + 10             # T + 10ms (Sell Exit)
            t_emergency_300ms = settle_epoch + 300 # T + 300ms Emergency
            t_emergency_10s = settle_epoch + 10000 # T + 10 seconds Emergency
            t_emergency_2m = settle_epoch + 120000 # T + 2 minutes Emergency

            entry_ist = datetime.fromtimestamp(t_entry / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]
            exit_ist = datetime.fromtimestamp(t_exit / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]

            with data_lock:
                dashboard_data['entry_target'] = f"{entry_ist} IST (T - 20ms Buy)"
                dashboard_data['exit_target'] = f"{exit_ist} IST (Sell) | Safeties: 300ms, 10s, 2m"
                dashboard_data['status'] = f"ARMED: {symbols_str}"

            add_ui_log(f"🎯 TARGETS ARMED: {symbols_str} | Rates: {rates_str}")

            # Wait until T - 800ms for pre-computation
            ultra_precision_wait(t_pre_compute)
            add_ui_log("🔍 T-800ms: Pre-computing order payloads...")
            ready_orders = []

            def prepare_coin_payload(opp):
                symbol = opp['symbol']
                rate = opp['funding_rate']
                specs = pre_fetched_specs.get(symbol, {
                    "leverage": 20.0, "qty_step": 1.0, "min_qty": 1.0, "qty_decimals": 0
                })
                
                try:
                    live_ticker = http_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
                    live_price = float(live_ticker.get("lastPrice", opp['last_price']))
                except Exception:
                    live_price = opp['last_price']

                if rate > MIN_FUNDING_RATE_THRESHOLD:
                    return None

                lev = specs["leverage"]
                q_step = specs["qty_step"]
                m_qty = specs["min_qty"]
                q_dec = specs["qty_decimals"]

                notional = MARGIN_USD * lev
                calc_qty = max(m_qty, math.floor((notional / live_price) / q_step) * q_step)
                qty_str = f"{calc_qty:.{q_dec}f}" if q_dec > 0 else str(int(calc_qty))

                return (symbol, rate, qty_str)

            with ThreadPoolExecutor(max_workers=3) as executor:
                results = list(executor.map(prepare_coin_payload, opportunities))
                for res in results:
                    if res:
                        ready_orders.append(res)

            if not ready_orders:
                add_ui_log("❌ ABORT: Rates shifted during pre-check.")
                time.sleep(2)
                continue

            # ==========================================
            # ENTRY: T - 20 ms (HTTP BUY)
            # ==========================================
            ultra_precision_wait(t_entry)
            entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
            add_ui_log("🚀 T-20ms: Executing HTTP BUY orders...")

            entry_results = {}
            def execute_buy(item):
                symbol, rate, qty_str = item
                try:
                    resp = http_client.place_order(
                        category="linear",
                        symbol=symbol,
                        side="Buy",
                        orderType="Market",
                        qty=qty_str,
                        reduceOnly=False,
                        positionIdx=0
                    )
                    if resp.get("retCode") == 0:
                        add_ui_log(f"✅ HTTP BUY SUCCESS: {symbol} (Qty: {qty_str})")
                        return symbol, True
                    else:
                        add_ui_log(f"❌ HTTP BUY REJECTED {symbol}: {resp.get('retMsg')}")
                        return symbol, False
                except Exception as e:
                    add_ui_log(f"⚠ HTTP BUY EXCEPTION {symbol}: {e}")
                    return symbol, False

            with ThreadPoolExecutor(max_workers=3) as executor:
                buy_res = list(executor.map(execute_buy, ready_orders))
                for sym, success in buy_res:
                    entry_results[sym] = success

            # ==========================================
            # EXIT: T + 10 ms (HTTP SELL / CLOSE)
            # ==========================================
            ultra_precision_wait(t_exit)
            exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
            add_ui_log("⚡ T+10ms: Executing HTTP SELL (Exit) orders...")

            def execute_exit(item):
                symbol, rate, qty_str = item
                closed_successfully = False
                pos_qty = 0.0
                
                for _ in range(3):
                    try:
                        pos_resp = http_client.get_positions(category="linear", symbol=symbol)
                        pos_list = pos_resp.get("result", {}).get("list", [])
                        if pos_list:
                            pos_qty = float(pos_list[0].get("size", 0))
                            if pos_qty > 0:
                                break
                    except Exception:
                        pass
                    time.sleep(0.005)

                if pos_qty > 0:
                    try:
                        resp = http_client.place_order(
                            category="linear",
                            symbol=symbol,
                            side="Sell",
                            orderType="Market",
                            qty=str(pos_qty),
                            reduceOnly=True,
                            positionIdx=0
                        )
                        if resp.get("retCode") == 0:
                            add_ui_log(f"✅ HTTP SELL SUCCESS: {symbol} (Qty: {pos_qty})")
                            closed_successfully = True
                        else:
                            add_ui_log(f"❌ HTTP SELL REJECTED {symbol}: {resp.get('retMsg')}")
                    except Exception as e:
                        add_ui_log(f"⚠ HTTP SELL EXCEPTION {symbol}: {e}")
                else:
                    closed_successfully = not entry_results.get(symbol, True)

                status_text = "CLOSED (T+10ms)" if closed_successfully else "EXIT PENDING (Safeties Active)"
                status_class = "status-profit" if closed_successfully else "status-error"

                add_ledger_entry({
                    "coin": f"{symbol} (HTTP HFT)",
                    "rate": f"{rate*100:+.2f}%",
                    "entry_time": entry_time_str,
                    "exit_time": exit_time_str,
                    "status_text": status_text,
                    "status_class": status_class
                })

            with ThreadPoolExecutor(max_workers=3) as executor:
                list(executor.map(execute_exit, ready_orders))

            def force_close_check(target_label, target_time_ms):
                ultra_precision_wait(target_time_ms)
                check_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
                
                def check_and_close(item):
                    symbol, rate, _ = item
                    try:
                        pos_resp = http_client.get_positions(category="linear", symbol=symbol)
                        pos_list = pos_resp.get("result", {}).get("list", [])
                        if pos_list:
                            pos_qty = float(pos_list[0].get("size", 0))
                            if pos_qty > 0:
                                add_ui_log(f"🚨 EMERGENCY ({target_label}): Position still open for {symbol} ({pos_qty}). Force closing via HTTP!")
                                http_client.place_order(
                                    category="linear", symbol=symbol, side="Sell", orderType="Market",
                                    qty=str(pos_qty), reduceOnly=True, positionIdx=0
                                )
                                add_ledger_entry({
                                    "coin": f"{symbol} ({target_label} SAFETY EXIT)",
                                    "rate": f"{rate*100:+.2f}%",
                                    "entry_time": entry_time_str,
                                    "exit_time": check_time_str,
                                    "status_text": f"CLOSED ({target_label})",
                                    "status_class": "status-error"
                                })
                    except Exception as e:
                        add_ui_log(f"⚠ Error during {target_label} for {symbol}: {e}")

                with ThreadPoolExecutor(max_workers=3) as executor:
                    executor.map(check_and_close, ready_orders)

            # ==========================================
            # EMERGENCY LAYERS
            # ==========================================
            force_close_check("T+300ms", t_emergency_300ms)
            force_close_check("T+10s", t_emergency_10s)
            force_close_check("T+2m", t_emergency_2m)

            add_ui_log("✨ Trade cycle completed successfully. Resetting for next rescan...")
            time.sleep(10)

        except Exception as main_loop_err:
            add_ui_log(f"⚠ Main Loop Exception: {main_loop_err}")
            time.sleep(3)

if __name__ == '__main__':
    run_production_engine()
