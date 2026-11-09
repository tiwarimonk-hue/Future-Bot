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

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()

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
    "FR_THRESHOLD_RATIO": 0.0090,
    "SHALLOW_LIMIT_RATIOS": (0.30, 0.50, 0.70),
    "GRID_RATIO_DCA_HIGH": 1.30,
    "SL_BELOW_LIMIT_RATIO_COND_1": 0.0050,
    "MIN_GRID_GAP_DCA_1_LOW": 0.0050,
    "GRID_RATIO_DCA_LOW": 1.30,
    "L2_BELOW_L1_GAP": 0.0050,
    "SL_BELOW_LIMIT_RATIO_COND_2": 0.0050,
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

s1_trade_history: list[dict] = []
s2_trade_history: list[dict] = []

dashboard_data = {
    "target_symbol": "Scanning...",
    "funding_rate": "0.00%",
    "status": "Running...",
    "trades": s1_trade_history,
    "strategy2_trades": s2_trade_history,
    "strategy1_top20": [],
    "strategy1_manual_armed": [],
    "strategy1_manual_disarmed": [],
    "strategy2": {
        "status": "Initializing...",
        "top20": [],
        "manual_armed": [],
        "manual_disarmed": []
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

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        html_file = Path(__file__).resolve().parent / "dashboard.html"
        if html_file.exists():
            with open(html_file, "r", encoding="utf-8") as f:
                self.wfile.write(f.read().encode("utf-8"))
        else:
            self.wfile.write(b"<h1>dashboard.html not found!</h1>")

    def do_POST(self) -> None:
        content_len = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_len).decode("utf-8")) if content_len > 0 else {}

        if self.path == "/api/arm-control":
            strat = body.get("strategy")
            sym = body.get("symbol")
            action = body.get("action")
            with data_lock:
                if strat == 1:
                    armed = dashboard_data["strategy1_manual_armed"]
                    disarmed = dashboard_data["strategy1_manual_disarmed"]
                    if action == "arm":
                        if sym in disarmed: disarmed.remove(sym)
                        if sym not in armed: armed.append(sym)
                    else:
                        if sym in armed: armed.remove(sym)
                        if sym not in disarmed: disarmed.append(sym)
                elif strat == 2:
                    s2 = dashboard_data["strategy2"]
                    armed = s2["manual_armed"]
                    disarmed = s2["manual_disarmed"]
                    if action == "arm":
                        if sym in disarmed: disarmed.remove(sym)
                        if sym not in armed: armed.append(sym)
                    else:
                        if sym in armed: armed.remove(sym)
                        if sym not in disarmed: disarmed.append(sym)

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            return


def run_web_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", CONFIG["DASHBOARD_PORT"]), DashboardHTTPHandler)
    server.serve_forever()


if __name__ == "__main__":
    from strategy1 import BinanceFastLaneWS, run_strategy1_cycle
    from strategy2 import start_strategy2_loop
    from binance.um_futures import UMFutures

    add_ui_log("🚀 Starting Binance Dual-Strategy Engine...")
    threading.Thread(target=run_web_server, daemon=True).start()

    ws_pipeline_s1 = BinanceFastLaneWS(
        api_key=API_KEY, api_secret=API_SECRET,
        rest_url=CONFIG["BINANCE_REST_URL"], ws_url=CONFIG["BINANCE_WS_URL"], ws_api_url=CONFIG["BINANCE_WS_API_URL"],
        log_fn=add_ui_log, status_fn=lambda s: None
    )
    ws_pipeline_s1.connect()

    threading.Thread(
        target=start_strategy2_loop,
        args=(dashboard_data, data_lock, add_ui_log, record_completed_trade_s2, get_strategy1_active_coins),
        daemon=True
    ).start()

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
            res = requests.get(f"{CONFIG['BINANCE_REST_URL']}/fapi/v1/premiumIndex", timeout=5).json()
            if isinstance(res, list):
                now_ms = int(time.time() * 1000)
                all_neg = []
                for c in res:
                    if not c.get("symbol", "").endswith("USDT"): continue
                    try:
                        rate = float(c.get("lastFundingRate", 0))
                        next_t = int(c.get("nextFundingTime", 0))
                        if rate < 0:
                            settle_dt = datetime.fromtimestamp(next_t / 1000, tz=IST)
                            all_neg.append({
                                "symbol": c["symbol"],
                                "rate": rate,
                                "rate_str": f"{rate*100:+.4f}%",
                                "next_funding_time": next_t,
                                "settle_str": settle_dt.strftime('%H:%M:%S IST'),
                                "last_price": float(c.get("markPrice", 0))
                            })
                    except: continue

                all_neg.sort(key=lambda x: x["rate"])
                
                with data_lock:
                    manual_armed = dashboard_data["strategy1_manual_armed"]
                    manual_disarmed = dashboard_data["strategy1_manual_disarmed"]
                    top20_formatted = []
                    for item in all_neg[:20]:
                        sym = item["symbol"]
                        is_armed = sym in manual_armed or (sym not in manual_disarmed and item["rate"] <= CONFIG["MIN_FUNDING_RATE"])
                        top20_formatted.append({
                            "symbol": sym,
                            "rate_str": item["rate_str"],
                            "settle_str": item["settle_str"],
                            "arm_mode": "MANUAL" if sym in manual_armed else "AUTO",
                            "is_armed": is_armed
                        })
                    dashboard_data["strategy1_top20"] = top20_formatted

                neg_coins = [c for c in all_neg if c["rate"] <= CONFIG["MIN_FUNDING_RATE"]]
                if neg_coins:
                    target = neg_coins[0]
                    sym = target["symbol"]
                    rate = target["rate"]
                    price = target["last_price"]
                    settle_ms = target["next_funding_time"]

                    with data_lock:
                        dashboard_data["target_symbol"] = sym
                        dashboard_data["funding_rate"] = target["rate_str"]

                    ws_pipeline_s1.subscribe_ticker(sym)
                    result_s1 = run_strategy1_cycle(
                        ws_pipeline_s1, client_rest, sym, rate, price, settle_ms, CONFIG, state_callbacks_s1
                    )
                    if result_s1:
                        record_completed_trade_s1(result_s1)
            time.sleep(5.0)
        except Exception as e:
            add_ui_log(f"⚠️ Main Loop Error: {e}")
            time.sleep(3.0)
