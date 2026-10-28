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
    "status": "Initializing Multi-Coin Priority Scanner...",
    "target_settlement": "-- IST",
    "entry_target": "-- IST (T + 15ms Short)",
    "exit_target": "-- IST (T + 700ms Hard Exit)",
    "clock_offset_ms": 0.0,
    "active_targets": [],
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
<title>Bybit Multi-Coin Funding Engine</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }
body { background-color: #0b0e11; color: #eaecef; padding: 20px; display: flex; justify-content: center; }
.container { width: 100%; max-width: 1200px; display: flex; flex-direction: column; gap: 16px; }
.header { background: #1e2329; padding: 18px 24px; border-radius: 12px; border: 1px solid #2b313a; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 18px; color: #f7a600; }
.badge { background: rgba(14, 203, 129, 0.15); color: #0ecb81; padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 600; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; }
.card { background: #1e2329; padding: 16px; border-radius: 12px; border: 1px solid #2b313a; }
.card-title { font-size: 11px; color: #848e9c; text-transform: uppercase; margin-bottom: 6px; }
.card-value { font-size: 18px; font-weight: 700; color: #f7a600; }
.section-card { background: #1e2329; padding: 18px; border-radius: 12px; border: 1px solid #2b313a; }
#log-box { background: #0b0e11; border: 1px solid #2b313a; border-radius: 8px; padding: 14px; height: 200px; overflow-y: auto; font-family: 'Courier New', monospace; font-size: 12px; color: #0ecb81; line-height: 1.5; }
.ledger-container { max-height: 300px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; margin-top: 10px; }
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
    <h1>⚡ Bybit Short Arbitrage ($1 Margin | T+15ms Sell | T+700ms Hard Exit)</h1>
    <div class="badge" id="ws-status">INITIALIZING</div>
  </div>
  <div class="grid">
    <div class="card"><div class="card-title">Top Active Targets</div><div class="card-value" id="coins" style="font-size: 14px;">-</div></div>
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
    document.getElementById('coins').innerText = data.target_symbol;
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
    server = HTTPServer(('0.0.0.0', port), FundingDashboardHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()


# ==========================================
# 4. WEBSOCKET PIPELINE
# ==========================================
API_KEY = "5OGqzNHMLZ5uXDRqZg"
API_SECRET = "8yTYSmulJW2MSEoJI7eHm4Zre4V2EObz4YhO"
MIN_FUNDING_RATE_THRESHOLD = -0.003
INITIAL_ENTRY_MARGIN_USD = 1.0

ws_ready = False

def get_ws_auth_payload():
    expires = int(time.time() * 1000) + 10000
    val = f'GET/realtime{expires}'
    signature = hmac.new(API_SECRET.encode('utf-8'), val.encode('utf-8'), hashlib.sha256).hexdigest()
    return {"op": "auth", "args": [API_KEY, expires, signature]}

def on_ws_message(ws, message):
    global ws_ready
    try:
        data = json.loads(message)
        if data.get("op") == "auth" and (data.get("success") is True or data.get("retCode") == 0):
            ws_ready = True
            with data_lock:
                dashboard_data['status'] = "WS AUTHENTICATED (READY)"
            add_ui_log("WebSocket Trade Pipeline Authenticated & LIVE")
    except Exception:
        pass

def on_ws_open(ws):
    add_ui_log("WebSocket Connection Opened. Authenticating...")
    ws.send(json.dumps(get_ws_auth_payload()))

def connect_websocket():
    while True:
        try:
            ws_client = websocket.WebSocketApp(
                "wss://stream.bybit.com/v5/trade",
                on_open=on_ws_open,
                on_message=on_ws_message
            )
            ws_client.run_forever()
        except Exception:
            pass
        time.sleep(2)

threading.Thread(target=connect_websocket, daemon=True).start()


# ==========================================
# 5. MARKET SCANNER (TOP 3 WITH PRIORITY)
# ==========================================
def scan_top_funding_opportunities(http_client):
    try:
        tickers = http_client.get_tickers(category="linear")["result"]["list"]
    except Exception:
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
        if time_diff > 60000 and rate <= MIN_FUNDING_RATE_THRESHOLD:
            diff_hours = round(time_diff / (3600 * 1000), 1)
            
            if diff_hours <= 1.5:
                priority_tier = 1  # 1H Priority
            elif diff_hours <= 4.5:
                priority_tier = 2  # 4H Priority
            else:
                priority_tier = 3  # 8H / Other Priority

            valid_candidates.append({
                "symbol": sym,
                "funding_rate": rate,
                "next_funding_time": next_time_ms,
                "last_price": float(t.get("lastPrice", 0)),
                "window_type": f"{diff_hours}H",
                "priority_tier": priority_tier
            })

    if not valid_candidates:
        return []

    valid_candidates.sort(key=lambda x: (x['priority_tier'], x['funding_rate']))
    
    seen_symbols = set()
    top_3 = []
    for c in valid_candidates:
        if c['symbol'] not in seen_symbols:
            seen_symbols.add(c['symbol'])
            top_3.append(c)
        if len(top_3) == 3:
            break

    return top_3


def calculate_qty_for_price(http_client, symbol, price, margin_usd=1.0):
    try:
        inst_info = http_client.get_instruments_info(category="linear", symbol=symbol)["result"]["list"][0]
        max_leverage = float(inst_info.get("leverageFilter", {}).get("maxLeverage", 10))
        price_filter = inst_info.get("priceFilter", {})
        tick_size = float(price_filter.get("tickSize", "0.0001"))
        lot_filter = inst_info['lotSizeFilter']
        qty_step = float(lot_filter["qtyStep"])
        min_qty = float(lot_filter['minOrderQty'])
    except Exception:
        max_leverage = 10.0
        tick_size = 0.0001
        qty_step = 1.0
        min_qty = 1.0

    applied_leverage = min(20.0, max_leverage)
    try:
        http_client.set_leverage(category="linear", symbol=symbol, buyLeverage=str(applied_leverage), sellLeverage=str(applied_leverage))
    except Exception:
        pass

    qty_str = f"{qty_step:.8f}".rstrip("0")
    qty_decimals = len(qty_str.split(".")[1]) if "." in qty_str else 0
    tick_str = f"{tick_size:.8f}".rstrip("0")
    price_decimals = len(tick_str.split(".")[1]) if "." in tick_str else 2

    if price < 1.0: price_decimals = max(price_decimals, 5)
    elif price < 10.0: price_decimals = max(price_decimals, 4)

    notional = margin_usd * applied_leverage
    calc_qty = max(min_qty, math.floor((notional / price) / qty_step) * qty_step)
    qty_formatted = f"{calc_qty:.{qty_decimals}f}" if qty_decimals > 0 else str(int(calc_qty))

    return qty_formatted, applied_leverage, price_decimals, qty_decimals


# ==========================================
# 6. INDEPENDENT COIN EXECUTION WORKER
# ==========================================
def execute_coin_trade_pipeline(opportunity, active_set):
    http_client = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
    symbol = opportunity['symbol']
    rate = opportunity['funding_rate']
    settle_epoch = opportunity['next_funding_time']
    window_type = opportunity['window_type']
    sim_key = (symbol, settle_epoch)

    entry_time_str = "--"
    exit_time_str = "--"
    current_rate = rate

    try:
        t_pre_calc = settle_epoch - 1000       # T - 1s Pre-Calculation
        t_entry = settle_epoch + 15            # T + 15ms Short (Sell) Entry
        t_hard_exit = settle_epoch + 700       # T + 700ms Hard Exit (Agar TP hit na ho)

        add_ui_log(f"🧵 Worker Spawned for {symbol} [{window_type}] | Settle: {datetime.fromtimestamp(settle_epoch/1000, tz=IST).strftime('%H:%M:%S')}")

        precision_wait_until(t_pre_calc)
        try:
            ticker = http_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
            base_price = float(ticker.get("lastPrice", opportunity['last_price']))
            current_rate = float(ticker.get("fundingRate", rate))
        except Exception as e:
            base_price = opportunity['last_price']

        if current_rate > MIN_FUNDING_RATE_THRESHOLD:
            msg = f"SKIPPED (THRESHOLD: Rate {current_rate*100:+.4f}%)"
            add_ui_log(f"❌ Aborting {symbol}: {msg}")
            add_ledger_entry({
                "coin": f"{symbol} ({window_type})",
                "rate": f"{current_rate*100:+.4f}%",
                "entry_time": "--",
                "exit_time": "--",
                "status_text": msg,
                "status_class": "status-cancel"
            })
            return

        expected_funding_profit = base_price * abs(current_rate)
        target_profit_90 = expected_funding_profit * 0.90
        tp_price = base_price - target_profit_90  # Short position ke liye TP price kam hoga (Buy Limit)
        max_allowed_price_delta = base_price * abs(current_rate) * 0.30

        precision_wait_until(t_entry)
        entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

        try:
            live_ticker = http_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
            live_price = float(live_ticker.get("lastPrice", base_price))
        except Exception:
            live_price = base_price

        price_movement = abs(live_price - base_price)
        if price_movement > max_allowed_price_delta:
            msg = f"SKIPPED (VOLATILITY: Movement {price_movement:.4f})"
            add_ui_log(f"⚠️ VOLATILITY FILTER TRIGGERED FOR {symbol}: {msg}")
            add_ledger_entry({
                "coin": f"{symbol} ({window_type})",
                "rate": f"{current_rate*100:+.4f}%",
                "entry_time": entry_time_str,
                "exit_time": "--",
                "status_text": msg,
                "status_class": "status-cancel"
            })
            return

        initial_qty, leverage, price_decimals, qty_decimals = calculate_qty_for_price(http_client, symbol, live_price, INITIAL_ENTRY_MARGIN_USD)

        entry_success = False
        try:
            # T+15ms Market SELL (Short) Entry
            http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=initial_qty, positionIdx=0)
            add_ui_log(f"🚀 SHORT ENTRY EXECUTED [{symbol}]: Sold {initial_qty} @ {live_price} at {entry_time_str}")
            entry_success = True
        except Exception as e:
            err_msg = f"ERROR (ENTRY): {str(e)[:30]}"
            add_ui_log(f"❌ Short Entry failed for {symbol}: {e}")
            add_ledger_entry({
                "coin": f"{symbol} ({window_type})",
                "rate": f"{current_rate*100:+.4f}%",
                "entry_time": entry_time_str,
                "exit_time": "--",
                "status_text": err_msg,
                "status_class": "status-cancel"
            })
            return

        if entry_success:
            try:
                time.sleep(0.05)  # 50ms buffer to avoid race conditions
                tp_price_str = f"{tp_price:.{price_decimals}f}"
                # Short TP ke liye BUY Limit order
                http_client.place_order(
                    category="linear",
                    symbol=symbol,
                    side="Buy",
                    orderType="Limit",
                    qty=initial_qty,
                    price=tp_price_str,
                    reduceOnly=True,
                    positionIdx=0
                )
                add_ui_log(f"🎯 TP PLACED [{symbol}]: Buy Limit @ {tp_price_str}")
            except Exception as e:
                err_msg = f"ERROR (TP): {str(e)[:30]}"
                add_ui_log(f"⚠ Failed to place TP for {symbol}: {e}")
                add_ledger_entry({
                    "coin": f"{symbol} ({window_type})",
                    "rate": f"{current_rate*100:+.4f}%",
                    "entry_time": entry_time_str,
                    "exit_time": "--",
                    "status_text": err_msg,
                    "status_class": "status-cancel"
                })

        # T + 700ms Hard Exit agar TP hit na hua ho
        precision_wait_until(t_hard_exit)
        exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
        
        status_text = "CLOSED (HARD EXIT T+700ms)"
        status_class = "status-profit"

        if entry_success:
            try:
                http_client.cancel_all_orders(category="linear", symbol=symbol)
                
                pos_resp = http_client.get_positions(category="linear", symbol=symbol)
                pos_list = pos_resp.get("result", {}).get("list", [])
                
                if pos_list:
                    pos_info = pos_list[0]
                    pos_qty = float(pos_info.get("size", 0))
                    if pos_qty > 0:
                        formatted_exit_qty = f"{pos_qty:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_qty))
                        # Hard exit ke liye Market BUY order
                        http_client.place_order(category="linear", symbol=symbol, side="Buy", orderType="Market", qty=formatted_exit_qty, reduceOnly=True, positionIdx=0)
                        add_ui_log(f"⏰ HARD EXIT DISPATCHED [{symbol}]: Bought back {formatted_exit_qty} at {exit_time_str}")
                    else:
                        status_text = "CLOSED (TP HIT ALREADY)"
                else:
                    status_text = "CLOSED (TP HIT ALREADY)"
            except Exception as e:
                status_text = f"ERROR (EXIT): {str(e)[:30]}"
                status_class = "status-cancel"
                add_ui_log(f"Hard exit failed for {symbol}: {e}")

        add_ledger_entry({
            "coin": f"{symbol} ({window_type})",
            "rate": f"{current_rate*100:+.4f}%",
            "entry_time": entry_time_str,
            "exit_time": exit_time_str,
            "status_text": status_text,
            "status_class": status_class
        })

    except Exception as general_err:
        add_ui_log(f"Critical error in pipeline for {symbol}: {general_err}")
        add_ledger_entry({
            "coin": f"{symbol} ({window_type})",
            "rate": f"{current_rate*100:+.4f}%",
            "entry_time": entry_time_str,
            "exit_time": exit_time_str,
            "status_text": f"ERROR (CRITICAL): {str(general_err)[:30]}",
            "status_class": "status-cancel"
        })

    finally:
        if sim_key in active_set:
            active_set.remove(sim_key)


# ==========================================
# 7. MAIN RE-ARMING & SCANNING LOOP (WITH 2-MIN PAUSE)
# ==========================================
def run_funding_engine():
    http_client = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
    add_ui_log("Multi-Coin Priority Funding Engine Active ($1 Margin | Max 20x | T+15ms Sell | T+700ms Hard Exit)...")

    while not ws_ready:
        time.sleep(0.1)

    active_tracking_keys = set()

    while True:
        sync_bybit_clock(http_client)
        
        now_ms = get_synced_time_ms()
        near_settlement = False
        
        with data_lock:
            for _, settle_epoch in active_tracking_keys:
                if 0 < (settle_epoch - now_ms) <= 120000:  # Within 2 minutes of settlement
                    near_settlement = True
                    break

        if near_settlement:
            add_ui_log("⏳ Settlement approaching within 2 minutes. Pausing market scanner to protect execution speed and API limits...")
            time.sleep(15)
            continue

        add_ui_log("🔍 Scanning market for Top 3 Priority Funding Opportunities...")
        top_coins = scan_top_funding_opportunities(http_client)

        if not top_coins:
            with data_lock:
                dashboard_data['target_symbol'] = "Scanning (No coin <= -0.3%)"
                dashboard_data['status'] = "SCANNING: No valid opportunities"
            time.sleep(60)
            continue

        symbol_summary = []
        for coin in top_coins:
            sym = coin['symbol']
            wt = coin['window_type']
            rt = f"{coin['funding_rate']*100:+.4f}%"
            settle_epoch = coin['next_funding_time']
            symbol_summary.append(f"{sym}({wt}:{rt})")

            sim_key = (sym, settle_epoch)
            if sim_key not in active_tracking_keys:
                active_tracking_keys.add(sim_key)
                t_worker = threading.Thread(
                    target=execute_coin_trade_pipeline, 
                    args=(coin, active_tracking_keys), 
                    daemon=True
                )
                t_worker.start()

        with data_lock:
            dashboard_data['target_symbol'] = " | ".join(symbol_summary)
            dashboard_data['status'] = f"ARMED: Tracking {len(top_coins)} Coins"

        time.sleep(60)


if __name__ == '__main__':
    run_funding_engine()
