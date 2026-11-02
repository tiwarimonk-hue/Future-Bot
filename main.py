import os
import json
import threading
import socket
import sys
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

IST = timezone(timedelta(hours=5, minutes=30))
data_lock = threading.Lock()

# ==========================================
# CENTRAL DASHBOARD TELEMETRY STATE
# ==========================================
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
# ROBUST HTTP SERVER (Suppress ConnectionResetError)
# ==========================================
class RobustHTTPServer(HTTPServer):
    """Custom HTTP Server to suppress ConnectionResetError (Errno 104) and Broken Pipe (Errno 32)"""
    def handle_error(self, request, client_address):
        exc_type, exc_value, _ = sys.exc_info()
        if isinstance(exc_value, (socket.error, OSError)) and getattr(exc_value, 'errno', 0) in (104, 32):
            return  # Client connection drop / reset ko silently ignore karein
        super().handle_error(request, client_address)

# ==========================================
# SINGLE PORT HTTP HANDLER (PORT 10000)
# ==========================================
class MasterDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        # 1. API Status Endpoint
        if self.path == '/api/status':
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with data_lock:
                payload = json.dumps(dashboard_data).encode('utf-8')
            try:
                self.wfile.write(payload)
            except (socket.error, OSError):
                pass
            return

        # 2. Web UI Frontend (Reads dashboard.html from disk)
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

        html_file_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
        if os.path.exists(html_file_path):
            with open(html_file_path, "r", encoding="utf-8") as f:
                content = f.read().encode("utf-8")
        else:
            content = b"""
            <body style='background:#0b0e11;color:#f6465d;font-family:sans-serif;padding:30px;text-align:center;'>
                <h2>dashboard.html not found!</h2>
                <p style='color:#eaecef;'>Please place <b>dashboard.html</b> in the same directory as main.py.</p>
            </body>
            """
        
        try:
            self.wfile.write(content)
        except (socket.error, OSError):
            pass

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = RobustHTTPServer(('0.0.0.0', port), MasterDashboardHandler)
    add_ui_log(f"🚀 Unified Web Dashboard running on Port {port}")
    server.serve_forever()

# ==========================================
# MASTER ENTRY POINT
# ==========================================
if __name__ == '__main__':
    add_ui_log("⚡ Master Controller Starting...")

    # 1. Start Web Server in background thread
    threading.Thread(target=run_web_server, daemon=True).start()

    # 2. Launch Bybit Engine Thread
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

    # 3. Launch Binance Engine Thread
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

    # 4. Launch Dual Arbitrage Engine Thread
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

    # Keep Master Process Alive
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        add_ui_log("🛑 Engine Stopped by User.")
