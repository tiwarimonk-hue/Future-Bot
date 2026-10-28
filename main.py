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
    "target_symbol": "BLURUSDT (11:50 PM TEST)",
    "funding_rate": "-2.00% (Simulated/Live)",
    "action_direction": "--",
    "status": "Initializing 11:50 PM Test Engine...",
    "target_settlement": "-- IST",
    "entry_target": "-- IST (T + 10ms Short)",
    "exit_target": "-- IST (Hard Exit T + 705ms)",
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
            pass

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
<title>BLURUSDT 11:50 PM Test Engine</title>
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
.status-abort { background: rgba(246, 70, 93, 0.15); color: #f6465d; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>⚡ BLURUSDT 11:50 PM Test ($1 | T+10ms Sell | T+705ms Exit)</h1>
    <div class="badge" id="ws-status">TEST RUNNING</div>
  </div>
  <div class="grid">
    <div class="card"><div class="card-title">Target Coin</div><div class="card-value" id="coin">-</div></div>
    <div class="card"><div class="card-title">Funding Rate</div><div class="card-value" id="rate" style="color:#0ecb81;">-</div></div>
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
# 4. CONFIG & API CREDENTIALS
# ==========================================
API_KEY = os.environ.get('BYBIT_API_KEY', 'YOUR_API_KEY_HERE')
API_SECRET = os.environ.get('BYBIT_API_SECRET', 'YOUR_API_SECRET_HERE')
MARGIN_USD = 1.0  # $1 Fixed Margin

def round_to_tick(price, tick_size):
    return round(round(price / tick_size) * tick_size, 8)

def get_instrument_specs(http_client, symbol):
    try:
        inst_info = http_client.get_instruments_info(category="linear", symbol=symbol)["result"]["list"][0]
        exchange_max_lev = float(inst_info.get("leverageFilter", {}).get("maxLeverage", 20))
        max_leverage = min(exchange_max_lev, 20.0)
        
        price_filter = inst_info.get("priceFilter", {})
        tick_size = float(price_filter.get("tickSize", "0.0001"))
        lot_filter = inst_info['lotSizeFilter']
        qty_step = float(lot_filter["qtyStep"])
        min_qty = float(lot_filter['minOrderQty'])

        try:
            http_client.set_leverage(category="linear", symbol=symbol, buyLeverage=str(max_leverage), sellLeverage=str(max_leverage))
        except Exception:
            pass

        qty_str = f"{qty_step:.8f}".rstrip("0")
        qty_decimals = len(qty_str.split(".")[1]) if "." in qty_str else 0

        tick_str = f"{tick_size:.8f}".rstrip("0")
        price_decimals = len(tick_str.split(".")[1]) if "." in tick_str else 2

        return max_leverage, price_decimals, qty_step, min_qty, qty_decimals, tick_size
    except Exception:
        return 20.0, 4, 1.0, 1.0, 0, 0.0001

# ==========================================
# 5. TEST ENGINE MAIN LOOP FOR 11:50 PM
# ==========================================
def run_blurusdt_1150_test():
    http_client = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
    target_symbol = "BLURUSDT"
    add_ui_log(f"🧪 BLURUSDT 11:50 PM Test Engine Active ($1 Margin)")

    while True:
        try:
            sync_bybit_clock(http_client)

            # --- SET TARGET TIME TO 11:50 PM IST ---
            target_hour = 23    # 23 matlab 11 PM
            target_minute = 50  # 50 minutes (11:50 PM IST)
            
            now_dt = datetime.now(IST)
            settle_dt = now_dt.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
            
            if now_dt >= settle_dt:
                settle_dt = settle_dt + timedelta(days=1)
                
            settle_epoch = int(settle_dt.timestamp() * 1000)
            
            resp = http_client.get_tickers(category="linear", symbol=target_symbol)
            tickers = resp.get("result", {}).get("list", [])
            if not tickers:
                time.sleep(3)
                continue

            t_data = tickers[0]
            raw_rate = t_data.get("fundingRate", "-0.02")
            rate = float(raw_rate) if raw_rate else -0.02
            last_price = float(t_data.get("lastPrice", 0))

            lev, p_dec, q_step, m_qty, q_dec, t_size = get_instrument_specs(http_client, target_symbol)

            with data_lock:
                dashboard_data['target_symbol'] = target_symbol
                dashboard_data['funding_rate'] = f"{rate*100:+.2f}%"
                dashboard_data['target_settlement'] = settle_dt.strftime('%H:%M:%S IST')
                dashboard_data['action_direction'] = f"ARMED 11:50 PM: T+10ms @ {settle_dt.strftime('%H:%M:%S')}"

            t_pre_compute = settle_epoch - 1000  # T - 1000ms
            t_entry = settle_epoch + 10          # T + 10ms (Exact Target)
            t_exit = settle_epoch + 705          # T + 705ms Hard Exit

            entry_ist = datetime.fromtimestamp(t_entry / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]
            exit_ist = datetime.fromtimestamp(t_exit / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]

            with data_lock:
                dashboard_data['entry_target'] = f"{entry_ist} IST (T + 10ms Short)"
                dashboard_data['exit_target'] = f"{exit_ist} IST (Hard Exit T + 705ms)"
                dashboard_data['status'] = f"WAITING FOR 11:50 PM: {settle_dt.strftime('%H:%M:%S')}"

            add_ui_log(f"🎯 11:50 PM TARGET SET: {target_symbol} | Target Time: {settle_dt.strftime('%H:%M:%S IST')} | Rate: {rate*100:+.2f}%")

            # High precision wait for T - 1000ms
            ultra_precision_wait(t_pre_compute)

            add_ui_log("🔍 T-1000ms: Pre-computing order payload...")
            try:
                ticker_resp = http_client.get_tickers(category="linear", symbol=target_symbol)
                live_price = float(ticker_resp.get("result", {}).get("list", [])[0].get("lastPrice", last_price))
            except Exception:
                live_price = last_price

            abs_funding_pct = abs(rate)
            notional = MARGIN_USD * lev
            calc_qty = max(m_qty, math.floor((notional / live_price) / q_step) * q_step)
            qty_str = f"{calc_qty:.{q_dec}f}" if q_dec > 0 else str(int(calc_qty))

            raw_tp_price = live_price * (1.0 - abs_funding_pct * 0.90)
            tp_price = round_to_tick(raw_tp_price, t_size)
            tp_price_str = f"{tp_price:.{p_dec}f}"

            add_ui_log(f"📦 Payload Ready: Qty {qty_str} | TP Buy @ {tp_price_str}. Waiting for T+10ms...")

            # T + 10ms Execution Loop
            ultra_precision_wait(t_entry)
            entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
            add_ui_log(f"🚀 T+10ms: Firing Short Market Sell for {target_symbol}...")

            order_success = False
            status_text = "FAILED"
            status_class = "status-abort"

            try:
                res = http_client.place_order(
                    category="linear",
                    symbol=target_symbol,
                    side="Sell",
                    orderType="Market",
                    qty=qty_str,
                    takeProfit=tp_price_str,
                    tpTriggerBy="LastPrice",
                    positionIdx=0
                )
                add_ui_log(f"🚀 HTTP ORDER RESPONSE: {res}")
                if res.get("retCode") == 0:
                    order_success = True
                else:
                    status_text = f"REJECTED: {res.get('retMsg', 'Unknown')}"
            except Exception as e:
                status_text = f"ERROR: {str(e)}"
                add_ui_log(f"⚠ Entry Exception: {e}")

            # T + 705ms Hard Exit Check
            ultra_precision_wait(t_exit)
            exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

            if order_success:
                try:
                    try:
                        http_client.cancel_all_orders(category="linear", symbol=target_symbol)
                    except Exception:
                        pass

                    pos_resp = http_client.get_positions(category="linear", symbol=target_symbol)
                    pos_list = pos_resp.get("result", {}).get("list", [])
                    if pos_list:
                        pos_qty = float(pos_list[0].get("size", 0))
                        if pos_qty > 0:
                            http_client.place_order(
                                category="linear", symbol=target_symbol, side="Buy", orderType="Market",
                                qty=str(pos_qty), reduceOnly=True, positionIdx=0
                            )
                            add_ui_log(f"⏰ HARD EXIT (T+705ms): Forced Cover of {pos_qty} {target_symbol}")
                            status_text = "HARD EXIT (T+705ms)"
                            status_class = "status-abort"
                        else:
                            status_text = "TP HIT / CLOSED"
                            status_class = "status-profit"
                except Exception as e:
                    add_ui_log(f"⚠ Hard Exit Check Failed: {e}")

            add_ledger_entry({
                "coin": f"{target_symbol} (11:50 PM Test)",
                "rate": f"{rate*100:+.2f}%",
                "entry_time": entry_time_str,
                "exit_time": exit_time_str,
                "status_text": status_text,
                "status_class": status_class
            })

            time.sleep(20)

        except Exception as main_err:
            add_ui_log(f"⚠ Main Loop Exception: {main_err}")
            time.sleep(3)

if __name__ == '__main__':
    run_blurusdt_1150_test()
