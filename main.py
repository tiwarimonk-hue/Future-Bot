from __future__ import annotations
import gc
import os
import hmac
import hashlib
import time
import json
import threading
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import requests
from dotenv import load_dotenv

# Load Environment Variables
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()

# Master Configuration Settings
CONFIG = {
    "BINANCE_REST_URL": "https://fapi.binance.com",
    "BINANCE_WS_URL": "wss://fstream.binance.com",
    "BINANCE_WS_API_URL": "wss://ws-fapi.binance.com/ws-fapi/v1",
    "API_KEY": API_KEY,
    "API_SECRET": API_SECRET,
    "TRADE_MARGIN_USD": 1.50,
    "LIMIT_ORDER_MARGIN_USD": 1.50,
    "DCA_SPLIT_MARGIN_USD": 0.75,
    "MAX_LEVERAGE_CAP": 20.0,
    "BINANCE_MIN_NOTIONAL": 5.0,
    "MIN_FUNDING_RATE": -0.0035,
    "MAX_DANGER_FUNDING_RATE": -0.0250,
    "SWEET_SPOT_MIN": -0.0130,
    "SWEET_SPOT_MAX": -0.0080,
    "FR_THRESHOLD_RATIO": 0.0090,
    "SHALLOW_LIMIT_RATIOS": (0.30, 0.50, 0.70),
    "GRID_RATIO_DCA_HIGH": 1.30,
    "SL_BELOW_LIMIT_RATIO_COND_1": 0.0050,
    "MIN_GRID_GAP_DCA_1_LOW": 0.0050,
    "GRID_RATIO_DCA_LOW": 1.30,
    "L2_BELOW_L1_GAP": 0.0050,
    "SL_BELOW_LIMIT_RATIO_COND_2": 0.0050,
    "SCHEDULED_SCAN_MINUTES": (5, 20, 35, 50),
    "RESCAN_BEFORE_SETTLE_MS": 20000,
    "GC_PURGE_BEFORE_SETTLE_MS": 10000,
    "WS_WARMUP_BEFORE_SETTLE_MS": 8000,
    "TCP_WARMUP_BEFORE_SETTLE_MS": 4000,
    "RECHECK_BEFORE_SETTLE_MS": 2500,
    "ENTRY_BEFORE_SETTLE_MS": 100,
    "SHALLOW_CANCEL_FLOOR_MS": 228.0,
    "SHALLOW_CANCEL_AFTER_SETTLE_MS": 230.0,
    "CANCEL_ORDERS_AFTER_SETTLE_MS": 6000,
    "EXIT_AFTER_SETTLE_MS": 7000,
    "EMERGENCY_SWEEP_AFTER_SETTLE_MS": 9000,
    "DASHBOARD_PORT": int(os.getenv("PORT", 10000))
}

IST = timezone(timedelta(hours=5, minutes=30))
data_lock = threading.Lock()
clock_offset_ms = 0.0

# Separate Ledgers for Each Strategy
s1_trade_history: list[dict] = []
s2_trade_history: list[dict] = []

dashboard_data = {
    "target_symbol": "Scanning...",
    "funding_rate": "0.00%",
    "active_tier": "IDLE",
    "status": "Initializing Dual Strategy Engine...",
    "target_settlement_time": "--:--:-- IST",
    "target_settlement_ms": 0,
    "target_settlement": "--:--:-- IST",
    "clock_offset_ms": 0.0,
    "trades": s1_trade_history,        # Strategy 1 Ledger
    "strategy2_trades": s2_trade_history,# Strategy 2 Ledger
    "trading_enabled": True,
    "manual_selected_symbol": "",
    "selection_locked": False,
    "force_rescan": False,
    "top_5_candidates": [],
    "strategy2": {
        "status": "Initializing...",
        "symbols": "--",
        "rates": "--",
        "settlement": "--",
        "offset": 0.0,
        "manual_armed": [],
        "manual_disarmed": [],
        "top20": []
    }
}


def add_ui_log(message: str) -> None:
    timestamp = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] {message}", flush=True)


def record_completed_trade_s1(trade_record: dict) -> None:
    with data_lock:
        s1_trade_history.insert(0, trade_record)
        if len(s1_trade_history) > 20: s1_trade_history.pop()
        dashboard_data["trades"] = list(s1_trade_history)


def record_completed_trade_s2(trade_record: dict) -> None:
    with data_lock:
        s2_trade_history.insert(0, trade_record)
        if len(s2_trade_history) > 20: s2_trade_history.pop()
        dashboard_data["strategy2_trades"] = list(s2_trade_history)


def get_synced_time_ms() -> float:
    return (time.time() * 1000) + clock_offset_ms


def precision_wait_until(target_time_ms: float) -> None:
    while True:
        now = get_synced_time_ms()
        diff = target_time_ms - now
        if diff <= 0: break
        elif diff > 10: time.sleep(0.002)
        elif diff > 2: time.sleep(0.0005)


def format_step(value: float, step: float) -> str:
    d_step = Decimal(str(step))
    d_val = Decimal(str(value))
    precision = d_step.normalize().as_tuple().exponent
    quantized = (d_val / d_step).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * d_step
    return f"{quantized:.{-precision}f}" if precision < 0 else str(int(quantized))


def format_tick(value: float, tick: float) -> str:
    d_tick = Decimal(str(tick))
    d_val = Decimal(str(value))
    precision = d_tick.normalize().as_tuple().exponent
    quantized = (d_val / d_tick).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * d_tick
    return f"{quantized:.{-precision}f}" if precision < 0 else str(int(quantized))


def sign_params(params: dict, secret: str = API_SECRET) -> str:
    query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    sig = hmac.new(secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{query_string}&signature={sig}"


# Priority Filtering Helper: Returns set of coins currently locked/traded by Strategy 1
def get_strategy1_active_coins() -> set[str]:
    with data_lock:
        s1_coin = dashboard_data.get("target_symbol", "")
        if s1_coin and not s1_coin.startswith("Scanning") and s1_coin != "--":
            return {s1_coin}
    return set()


class DashboardHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): return

    def do_GET(self) -> None:
        if self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with data_lock:
                payload = json.dumps(dashboard_data).encode("utf-8")
            self.wfile.write(payload)
            return

        # Serve standalone dashboard.html
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        html_file = Path(__file__).resolve().parent / "dashboard.html"
        if html_file.exists():
            with open(html_file, "r", encoding="utf-8") as f:
                self.wfile.write(f.read().encode("utf-8"))
        else:
            self.wfile.write(b"<h1>dashboard.html file not found!</h1>")

    def do_POST(self) -> None:
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else ""

        if self.path == "/api/toggle-trading":
            with data_lock:
                dashboard_data["trading_enabled"] = not dashboard_data["trading_enabled"]
                st = dashboard_data["trading_enabled"]
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "trading_enabled": st}).encode("utf-8"))
            return


def run_web_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", CONFIG["DASHBOARD_PORT"]), DashboardHTTPHandler)
    server.serve_forever()


# Main Launcher
if __name__ == "__main__":
    from strategy1 import BinanceFastLaneWS, run_strategy1_cycle
    from strategy2 import start_strategy2_loop
    from binance.um_futures import UMFutures

    add_ui_log("🚀 Starting Binance Dual-Strategy Engine...")
    threading.Thread(target=run_web_server, daemon=True).start()

    # Initialize Strategy 1 WebSocket Pipeline
    ws_pipeline_s1 = BinanceFastLaneWS(
        api_key=API_KEY, api_secret=API_SECRET,
        rest_url=CONFIG["BINANCE_REST_URL"], ws_url=CONFIG["BINANCE_WS_URL"], ws_api_url=CONFIG["BINANCE_WS_API_URL"],
        log_fn=add_ui_log, status_fn=lambda s: None
    )
    ws_pipeline_s1.connect()

    # Initialize Strategy 2 Loop in Background Thread
    threading.Thread(
        target=start_strategy2_loop,
        args=(dashboard_data, data_lock, add_ui_log, record_completed_trade_s2, get_strategy1_active_coins),
        daemon=True
    ).start()

    # Strategy 1 Master Execution Loop (Priority Strategy)
    client_rest = UMFutures(key=API_KEY, secret=API_SECRET)
    state_callbacks_s1 = {
        "add_ui_log": add_ui_log,
        "get_synced_time_ms": get_synced_time_ms,
        "precision_wait_until": precision_wait_until,
        "format_step": format_step,
        "format_tick": format_tick,
        "sign_params": sign_params
    }

    while True:
        try:
            # Simple scanner example for Strategy 1
            res = requests.get(f"{CONFIG['BINANCE_REST_URL']}/fapi/v1/premiumIndex", timeout=5).json()
            if isinstance(res, list):
                neg_coins = [c for c in res if float(c.get("lastFundingRate", 0)) < CONFIG["MIN_FUNDING_RATE"] and c.get("symbol", "").endswith("USDT")]
                neg_coins.sort(key=lambda x: float(x.get("lastFundingRate", 0)))

                if neg_coins:
                    target = neg_coins[0]
                    sym = target["symbol"]
                    rate = float(target["lastFundingRate"])
                    price = float(target["markPrice"])
                    settle_ms = int(target["nextFundingTime"])

                    with data_lock:
                        dashboard_data["target_symbol"] = sym
                        dashboard_data["funding_rate"] = f"{rate*100:+.4f}%"

                    ws_pipeline_s1.subscribe_ticker(sym)
                    
                    # Run Strategy 1 Execution Cycle
                    result_s1 = run_strategy1_cycle(
                        ws_pipeline_s1, client_rest, sym, rate, price, settle_ms, CONFIG, state_callbacks_s1
                    )
                    if result_s1:
                        record_completed_trade_s1(result_s1)
            time.sleep(5.0)
        except Exception as e:
            add_ui_log(f"⚠️ Main Loop Recovered: {e}")
            time.sleep(3.0)
