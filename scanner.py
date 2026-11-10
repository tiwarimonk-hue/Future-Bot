from __future__ import annotations
import time
import hmac
import hashlib
import urllib.parse
import requests
import config
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
_server_time_offset_ms = 0

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json"
})


def sync_binance_clock() -> int:
    global _server_time_offset_ms
    try:
        r = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/time", timeout=2)
        if r.status_code == 200:
            server_ms = int(r.json().get("serverTime", 0))
            local_ms = int(time.time() * 1000)
            _server_time_offset_ms = server_ms - local_ms
            return _server_time_offset_ms
    except Exception:
        pass
    return _server_time_offset_ms


def get_synced_time_ms() -> int:
    return int(time.time() * 1000) + _server_time_offset_ms


def precision_wait_until(target_ms: int) -> None:
    while True:
        now = get_synced_time_ms()
        rem = target_ms - now
        if rem <= 0:
            break
        if rem > 100:
            time.sleep(0.05)
        elif rem > 10:
            time.sleep(0.002)
        else:
            pass


def format_duration(ms: int) -> str:
    sec = max(0, int(ms / 1000))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def format_step(val: float, step: float) -> str:
    if step <= 0:
        return str(val)
    precision = 0
    s_str = f"{step:.10f}".rstrip('0')
    if '.' in s_str:
        precision = len(s_str.split('.')[1])
    val_rounded = round(round(val / step) * step, precision)
    return f"{val_rounded:.{precision}f}"


def format_tick(price: float, tick_size: float) -> str:
    return format_step(price, tick_size)


def sign_params(params: dict, secret: str) -> str:
    query_string = urllib.parse.urlencode(params)
    signature = hmac.new(
        secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return signature


def calculate_order_specs(symbol: str, price: float) -> tuple[str, int, float, float, float]:
    lev = getattr(config, "LEVERAGE", 20)
    tick_size = 0.0001
    qty_step = 1.0
    min_qty = 1.0

    try:
        ex_r = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/exchangeInfo", timeout=2).json()
        for s in ex_r.get("symbols", []):
            if s.get("symbol") == symbol:
                for f in s.get("filters", []):
                    if f.get("filterType") == "PRICE_FILTER":
                        tick_size = float(f.get("tickSize", 0.0001))
                    elif f.get("filterType") == "LOT_SIZE":
                        qty_step = float(f.get("stepSize", 1.0))
                        min_qty = float(f.get("minQty", 1.0))
                break
    except Exception:
        pass

    notional = max(5.5, float(getattr(config, "TRADE_MARGIN_USD", 3.0)) * lev)
    raw_qty = notional / price if price > 0 else min_qty
    qty_str = format_step(raw_qty, qty_step)
    
    if float(qty_str) * price < 5.2:
        qty_str = format_step(float(qty_str) + qty_step, qty_step)
    if float(qty_str) < min_qty:
        qty_str = format_step(min_qty, qty_step)

    return qty_str, lev, tick_size, qty_step, min_qty


def find_top_negative_funding_candidates(
    target_settle_ms: int = 0, 
    preferred_symbols: list[str] = None,
    explicitly_disarmed: list[str] = None
) -> tuple[list[dict], int, list[dict], list[str], list[str]]:
    if preferred_symbols is None:
        preferred_symbols = []
    if explicitly_disarmed is None:
        explicitly_disarmed = []

    try:
        ex_r = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/exchangeInfo", timeout=3).json()
        valid_symbols = {
            s["symbol"] for s in ex_r.get("symbols", [])
            if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING"
        }
    except Exception:
        valid_symbols = set()

    try:
        prem_r = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/premiumIndex", timeout=3).json()
    except Exception:
        prem_r = []

    candidates = []
    next_funding_ms = 0

    for item in prem_r:
        sym = item.get("symbol")
        if valid_symbols and sym not in valid_symbols:
            continue
        try:
            rate = float(item.get("lastFundingRate", 0))
            nxt_time = int(item.get("nextFundingTime", 0))
            mark_p = float(item.get("markPrice", 0))
        except Exception:
            continue

        if nxt_time > 0:
            if next_funding_ms == 0 or nxt_time < next_funding_ms:
                next_funding_ms = nxt_time

        if rate < 0:
            candidates.append({
                "symbol": sym,
                "rate": rate,
                "price": mark_p,
                "nextFundingTime": nxt_time
            })

    candidates.sort(key=lambda x: x["rate"])
    top_10 = candidates[:10]

    qualified = [c for c in candidates if c["rate"] <= -config.FR_THRESHOLD_RATIO]

    auto_armed = []
    manual_armed = []

    for sym in preferred_symbols:
        if sym in explicitly_disarmed:
            continue
        match = next((c for c in candidates if c["symbol"] == sym), None)
        if match:
            manual_armed.append(sym)

    for c in qualified:
        sym = c["symbol"]
        if sym in manual_armed or sym in explicitly_disarmed:
            continue
        if len(auto_armed) < 2:  # AUTO TOP 2 CAPPING
            auto_armed.append(sym)

    final_selected_symbols = list(set(manual_armed + auto_armed))
    final_selected_symbols = final_selected_symbols[:2]

    chosen_candidates = [c for c in candidates if c["symbol"] in final_selected_symbols]
    chosen_candidates.sort(key=lambda x: x["rate"])

    return chosen_candidates, next_funding_ms, top_10, auto_armed, manual_armed
