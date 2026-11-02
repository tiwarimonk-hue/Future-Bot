import os
import json
import threading
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

IST = timezone(timedelta(hours=5, minutes=30))
data_lock = threading.Lock()

# Unified Central Dashboard State
dashboard_data = {
    "bybit": {
        "status": "Initializing...",
        "symbols": "Scanning...",
        "rates": "--",
        "settlement": "--",
        "offset": 0.0
    },
    "binance": {
        "status": "Initializing...",
        "symbols": "Scanning...",
        "rates": "--",
        "settlement": "--",
        "offset": 0.0
    },
    "dual": {
        "status": "Scanning Spread...",
        "coin": "--",
        "bybit_rate": "--",
        "binance_rate": "--",
        "spread": "--",
        "settlement": "--",
        "legs": "Ready"
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
# SINGLE PORT HTTP SERVER (PORT 10000)
# ==========================================
class MasterDashboardHandler(BaseHTTPRequestHandler):
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
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

        # Load external dashboard.html from disk
        html_file_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
        if os.path.exists(html_file_path):
            with open(html_file_path, "r", encoding="utf-8") as f:
                content = f.read().encode("utf-8")
        else:
            content = b"<h1>dashboard.html not found! Place it in the same directory.</h1>"
        
        self.wfile.write(content)

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), MasterDashboardHandler)
    print(f"🚀 Unified Web Cockpit Live on http://0.0.0.0:{port}")
    server.serve_forever()

if __name__ == '__main__':
    # 1. Start Single Port Web Server
    threading.Thread(target=run_web_server, daemon=True).start()

    # 2. Modules ko thread me launch karne ka setup:
    # try:
    #     import bybit_engine
    #     threading.Thread(target=bybit_engine.start_bybit_loop, args=(dashboard_data, data_lock, add_ui_log, add_ledger_entry), daemon=True).start()
    # except Exception as e:
    #     add_ui_log(f"Bybit Engine Init Error: {e}")

    # try:
    #     import binance_engine
    #     threading.Thread(target=binance_engine.start_binance_loop, args=(dashboard_data, data_lock, add_ui_log, add_ledger_entry), daemon=True).start()
    # except Exception as e:
    #     add_ui_log(f"Binance Engine Init Error: {e}")

    # Keep Master Thread Alive
    threading.Event().wait()
