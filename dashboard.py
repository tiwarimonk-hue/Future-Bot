from __future__ import annotations
import json
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import config
from dashboard_template import get_dashboard_html

IST = timezone(timedelta(hours=5, minutes=30))

data_lock = threading.Lock()
trade_history: list[dict] = []
explicitly_disarmed_symbols: set[str] = set()

BLOCKED_COINS_FILE = Path(__file__).resolve().parent / "blocked_coins.json"


def load_blocked_coins() -> dict[str, list[str]]:
    default_data = {"temporary": [], "permanent": []}
    if not BLOCKED_COINS_FILE.exists():
        try:
            with open(BLOCKED_COINS_FILE, "w", encoding="utf-8") as f:
                json.dump(default_data, f, indent=2)
        except Exception:
            pass
        return default_data
    try:
        with open(BLOCKED_COINS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                data = default_data
            if "temporary" not in data or not isinstance(data["temporary"], list):
                data["temporary"] = []
            if "permanent" not in data or not isinstance(data["permanent"], list):
                data["permanent"] = []
            return data
    except Exception as e:
        return default_data


def save_blocked_coins(data: dict[str, list[str]]) -> bool:
    try:
        with open(BLOCKED_COINS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        return False


def block_coin(symbol: str, block_type: str) -> tuple[bool, str]:
    symbol = symbol.strip().upper()
    if not symbol:
        return False, "Invalid symbol"
    with data_lock:
        data = load_blocked_coins()
        if block_type == "temporary":
            if symbol in data["permanent"]:
                data["permanent"].remove(symbol)
            if symbol not in data["temporary"]:
                data["temporary"].append(symbol)
        else:
            if symbol in data["temporary"]:
                data["temporary"].remove(symbol)
            if symbol not in data["permanent"]:
                data["permanent"].append(symbol)

        save_blocked_coins(data)
        dashboard_data["blocked_coins"] = data
        dashboard_data["force_rescan"] = True

        if symbol in dashboard_data.get("manual_selected_symbols", []):
            dashboard_data["manual_selected_symbols"].remove(symbol)

    add_ui_log(f"🚫 [COIN BLOCKED] {symbol} moved to {block_type.upper()} block list.")
    return True, f"{symbol} blocked"


def unblock_coin(symbol: str, block_type: str = "") -> tuple[bool, str]:
    symbol = symbol.strip().upper()
    with data_lock:
        data = load_blocked_coins()
        removed = False
        if block_type in data and symbol in data[block_type]:
            data[block_type].remove(symbol)
            removed = True
        else:
            if symbol in data.get("temporary", []):
                data["temporary"].remove(symbol)
                removed = True
            if symbol in data.get("permanent", []):
                data["permanent"].remove(symbol)
                removed = True

        if removed:
            save_blocked_coins(data)
            dashboard_data["blocked_coins"] = data
            dashboard_data["force_rescan"] = True
            add_ui_log(f"✅ [COIN UNBLOCKED] {symbol} unblocked.")
            return True, f"{symbol} unblocked"
        return False, f"{symbol} not found"


dashboard_data = {
    "target_symbol": "Scanning...",
    "funding_rate": "0.00%",
    "active_tier": "IDLE",
    "status": f"Initializing Binance Funding Sniper (T-{config.ENTRY_BEFORE_SETTLE_MS}ms)...",
    "target_settlement_time": "--:--:-- IST",
    "target_settlement_ms": 0,
    "target_settlement": "--:--:-- IST",
    "clock_offset_ms": 0.0,
    "margin_usd": f"${config.TRADE_MARGIN_USD} USDT",
    "trades": trade_history,
    "trading_enabled": True,
    "manual_selected_symbols": [],
    "auto_armed_symbols": [],
    "selection_locked": False,
    "force_rescan": False,
    "top_5_candidates": [],
    "blocked_coins": load_blocked_coins()
}


def add_ui_log(message: str) -> None:
    timestamp = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] {message}", flush=True)


def set_dashboard_status(status_msg: str) -> None:
    with data_lock:
        dashboard_data["status"] = status_msg


def record_completed_trade(trade_record: dict) -> None:
    with data_lock:
        exists = any(
            t.get("symbol") == trade_record.get("symbol") and 
            t.get("entry_sent") == trade_record.get("entry_sent") 
            for t in trade_history
        )
        if not exists:
            trade_history.insert(0, trade_record)
            if len(trade_history) > 20:
                trade_history.pop()
            dashboard_data["trades"] = list(trade_history)


class FundingDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

    def do_POST(self) -> None:
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else ""

        if self.path == "/api/toggle-trading":
            with data_lock:
                dashboard_data["trading_enabled"] = not dashboard_data["trading_enabled"]
                current_state = dashboard_data["trading_enabled"]
                if current_state:
                    dashboard_data["force_rescan"] = True
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "trading_enabled": current_state}).encode("utf-8"))
            return

        elif self.path == "/api/reset-auto":
            try:
                with data_lock:
                    if dashboard_data["selection_locked"]:
                        self.send_response(403)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"success": False, "message": "Selection locked."}).encode("utf-8"))
                        return
                    dashboard_data["manual_selected_symbols"] = []
                    explicitly_disarmed_symbols.clear()
                    dashboard_data["force_rescan"] = True

                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "mode": "AUTOMATIC"}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        elif self.path == "/api/select-coin":
            try:
                payload = json.loads(body)
                selected_sym = payload.get("symbol", "").strip().upper()
                with data_lock:
                    if dashboard_data["selection_locked"]:
                        self.send_response(403)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"success": False, "message": "Selection locked."}).encode("utf-8"))
                        return

                    manual_list = dashboard_data.setdefault("manual_selected_symbols", [])
                    if selected_sym in manual_list:
                        manual_list.remove(selected_sym)
                        explicitly_disarmed_symbols.add(selected_sym)  # STRICT DISARM
                        action_msg = f"Removed {selected_sym} from manual list & added to disarm blacklist."
                    else:
                        manual_list.append(selected_sym)
                        if selected_sym in explicitly_disarmed_symbols:
                            explicitly_disarmed_symbols.remove(selected_sym)
                        action_msg = f"Added {selected_sym} to manual list."

                    dashboard_data["force_rescan"] = True

                add_ui_log(f"🎯 [RADAR SELECTION] {action_msg}")
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "manual_selected_symbols": dashboard_data["manual_selected_symbols"]}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        elif self.path == "/api/block-coin":
            try:
                payload = json.loads(body)
                sym = payload.get("symbol", "").strip().upper()
                b_type = payload.get("type", "temporary").strip().lower()
                ok, msg = block_coin(sym, b_type)
                self.send_response(200 if ok else 400)
                self.send_header("Content-type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": ok, "message": msg}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        elif self.path == "/api/unblock-coin":
            try:
                payload = json.loads(body)
                sym = payload.get("symbol", "").strip().upper()
                b_type = payload.get("type", "").strip().lower()
                ok, msg = unblock_coin(sym, b_type)
                self.send_response(200 if ok else 400)
                self.send_header("Content-type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": ok, "message": msg}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

    def do_GET(self) -> None:
        if self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with data_lock:
                dashboard_data["blocked_coins"] = load_blocked_coins()
                payload = json.dumps(dashboard_data).encode("utf-8")
            self.wfile.write(payload)
            return

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        html_content = get_dashboard_html()
        self.wfile.write(html_content.encode("utf-8"))


def run_web_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", config.DASHBOARD_PORT), FundingDashboardHandler)
    server.serve_forever()
