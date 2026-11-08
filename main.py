import os
import json
import time
import threading
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone, timedelta
from socketserver import ThreadingMixIn
from http.server import HTTPServer, BaseHTTPRequestHandler

IST = timezone(timedelta(hours=5, minutes=30))
data_lock = threading.RLock()

# Central Dashboard Telemetry State
dashboard_data = {
    "bybit": {
        "status": "Initializing Engine...",
        "symbols": "Scanning Market...",
        "rates": "--",
        "settlement": "--",
        "offset": 0.0,
        "top10": [],
        "manual_armed": [],
        "manual_disarmed": []
    },
    "binance": {
        "status": "Initializing Engine...",
        "symbols": "Scanning Market...",
        "rates": "--",
        "settlement": "--",
        "offset": 0.0,
        "top10": [],
        "manual_armed": [],
        "manual_disarmed": []
    },
    "dual": {
        "status": "Initializing Engine...",
        "coin": "--",
        "bybit_rate": "--",
        "binance_rate": "--",
        "spread": "--",
        "settlement": "--",
        "legs": "Synced",
        "trade_enabled": True
    },
    "logs": [],
    "ledger": []
}

def add_ui_log(message):
    timestamp = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
    line = f'[{timestamp}] {message}'
    print(line)
    with data_lock:
        dashboard_data['logs'].append(line)
        if len(dashboard_data['logs']) > 150:
            dashboard_data['logs'].pop(0)

def add_ledger_entry(trade_info):
    with data_lock:
        dashboard_data['ledger'].insert(0, trade_info)
        if len(dashboard_data['ledger']) > 50:
            dashboard_data['ledger'].pop()

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

class MasterDashboardHandler(BaseHTTPRequestHandler):
    timeout = 4

    def log_message(self, format, *args):
        return

    def _send_json(self, obj):
        self.send_response(200)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode('utf-8'))
        self.wfile.flush()

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            params = parse_qs(parsed.query)

            # 1. API Telemetry
            if path == '/api/status':
                with data_lock:
                    payload = json.dumps(dashboard_data).encode('utf-8')
                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
                self.wfile.flush()
                return

            # 2. Dual Engine Toggle
            if path == '/api/toggle_dual':
                with data_lock:
                    curr_val = dashboard_data['dual'].get('trade_enabled', True)
                    dashboard_data['dual']['trade_enabled'] = not curr_val
                    new_val = dashboard_data['dual']['trade_enabled']
                status_str = "ENABLED" if new_val else "DISABLED"
                add_ui_log(f"🔘 [User Action] Dual Engine Auto-Trading manually set to: {status_str}")
                self._send_json({"trade_enabled": new_val})
                return

            # 3. Manual Coin ARM / DISARM Toggle (Bybit & Binance)
            if path == '/api/toggle_coin':
                ex = params.get('exchange', [''])[0].lower()
                sym = params.get('symbol', [''])[0].upper()
                if ex in ('bybit', 'binance') and sym:
                    action_taken = ""
                    with data_lock:
                        ex_data = dashboard_data[ex]
                        m_armed = ex_data.setdefault('manual_armed', [])
                        m_disarmed = ex_data.setdefault('manual_disarmed', [])
                        top10 = ex_data.get('top10', [])

                        # Determine current armed state
                        currently_armed = False
                        for item in top10:
                            if item['symbol'] == sym:
                                currently_armed = item.get('is_armed', False)
                                break
                        if not top10:
                            currently_armed = sym in m_armed

                        if currently_armed:
                            if sym in m_armed:
                                m_armed.remove(sym)
                            if sym not in m_disarmed:
                                m_disarmed.append(sym)
                            action_taken = "DISARMED"
                            for item in top10:
                                if item['symbol'] == sym:
                                    item['is_armed'] = False
                                    item['arm_mode'] = "DISARMED"
                        else:
                            if sym in m_disarmed:
                                m_disarmed.remove(sym)
                            if sym not in m_armed:
                                m_armed.append(sym)
                            action_taken = "MANUAL ARMED"
                            for item in top10:
                                if item['symbol'] == sym:
                                    item['is_armed'] = True
                                    item['arm_mode'] = "MANUAL"

                    add_ui_log(f"🔘 [{ex.upper()}] User manually {action_taken}: {sym}")
                    self._send_json({"ok": True, "exchange": ex, "symbol": sym, "action": action_taken})
                    return

            # 4. HTML Delivery
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Connection", "close")
            self.end_headers()

            html_file_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
            if os.path.exists(html_file_path):
                with open(html_file_path, "r", encoding="utf-8") as f:
                    content = f.read().encode("utf-8")
            else:
                content = b"<h1>dashboard.html not found! Place it in the same directory.</h1>"

            self.wfile.write(content)
            self.wfile.flush()

        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            pass
        finally:
            self.close_connection = True

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    while True:
        try:
            server = ThreadedHTTPServer(('0.0.0.0', port), MasterDashboardHandler)
            add_ui_log(f"🚀 Multi-Threaded Cockpit Web Server Running on Port {port}")
            server.serve_forever()
        except Exception as e:
            add_ui_log(f"⚠️ Web server restarted due to: {e}")
            time.sleep(1)

if __name__ == '__main__':
    add_ui_log("⚡ Master Controller Starting ($0.5 Margin | +5% Buffered Sell Limit | Top 10 Manual Control)...")

    # 1. Web Server Thread
    threading.Thread(target=run_web_server, daemon=True).start()

    # 2. Bybit Solo Thread
    try:
        import bybit_engine
        threading.Thread(
            target=bybit_engine.start_bybit_loop,
            args=(dashboard_data, data_lock, add_ui_log, add_ledger_entry),
            daemon=True
        ).start()
        add_ui_log("✔ Bybit Engine Thread Launched")
    except Exception as e:
        add_ui_log(f"❌ Failed to launch Bybit Engine: {e}")

    # 3. Binance Solo Thread
    try:
        import binance_engine
        threading.Thread(
            target=binance_engine.start_binance_loop,
            args=(dashboard_data, data_lock, add_ui_log, add_ledger_entry),
            daemon=True
        ).start()
        add_ui_log("✔ Binance Engine Thread Launched")
    except Exception as e:
        add_ui_log(f"❌ Failed to launch Binance Engine: {e}")

    # 4. Dual Engine Thread
    try:
        import dual_engine
        threading.Thread(
            target=dual_engine.start_dual_loop,
            args=(dashboard_data, data_lock, add_ui_log, add_ledger_entry),
            daemon=True
        ).start()
        add_ui_log("✔ Dual Arbitrage Engine Thread Launched")
    except Exception as e:
        add_ui_log(f"❌ Failed to launch Dual Engine: {e}")

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        add_ui_log("🛑 Engine Stopped by User.")
