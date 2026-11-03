import os
import json
import socket
import threading
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
        "offset": 0.0
    },
    "binance": {
        "status": "Initializing Engine...",
        "symbols": "Scanning Market...",
        "rates": "--",
        "settlement": "--",
        "offset": 0.0
    },
    "dual": {
        "status": "Initializing Engine...",
        "coin": "--",
        "bybit_rate": "--",
        "binance_rate": "--",
        "spread": "--",
        "settlement": "--",
        "legs": "Synced"
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
        if len(dashboard_data['ledger']) > 40:
            dashboard_data['ledger'].pop()

# ==========================================
# THREADED HTTP SERVER (ZERO FREEZE / ZERO STALL)
# ==========================================
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

class MasterDashboardHandler(BaseHTTPRequestHandler):
    timeout = 4  # Drop hanging mobile connections after 4s

    def log_message(self, format, *args):
        return

    def do_GET(self):
        try:
            # 1. API Telemetry Endpoint
            if self.path == '/api/status':
                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Connection", "close")
                self.end_headers()

                with data_lock:
                    payload = json.dumps(dashboard_data).encode('utf-8')

                self.wfile.write(payload)
                self.wfile.flush()
                return

            # 2. Web UI HTML File
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
        except Exception as e:
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

# ==========================================
# MASTER ENTRY POINT
# ==========================================
if __name__ == '__main__':
    add_ui_log("⚡ Master Controller Starting...")

    # 1. Launch Multi-Threaded Web Server
    threading.Thread(target=run_web_server, daemon=True).start()

    # 2. Launch Bybit Solo Engine Thread
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

    # 3. Launch Binance Solo Engine Thread
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

    # 4. Launch Dual Cross-Arbitrage Engine Thread
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

    # Keep Master Thread Active
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        add_ui_log("🛑 Engine Stopped by User.")
