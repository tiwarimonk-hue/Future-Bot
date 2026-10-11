from __future__ import annotations
import hmac
import hashlib
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple
import requests
import config
from dashboard import dashboard_data, data_lock, add_ui_log

clock_offset_ms = 0.0
session = requests.Session()

BLACKLISTED_PATTERNS = (
    "DEFIUSDT", "BTCDOMUSDT", "FOOTBALLUSDT", "BLUEBIRDUSDT",
    "ZHONGJIUSDT", "BYDUSDT", "TENCENTUSDT", "BABAUSDT",
    "XIAOMIUSDT", "MEITUANUSDT", "CATLUSDT", "MOUTAIUSDT"
)

def sync_binance_clock() -> None:
    global clock_offset_ms
    try:
        t_send = time.time() * 1000
        res = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/time", timeout=3)
        t_recv = time.time() * 1000
        server_time = float(res.json()["serverTime"])
        rtt = t_recv - t_send
        estimated_server_time = server_time + (rtt / 2.0)
        clock_offset_ms = estimated_server_time - t_recv
        with data_lock:
            dashboard_data["clock_offset_ms"] = round(clock_offset_ms, 2)
    except Exception as e:
        add_ui_log(f"Binance clock sync warning: {e}")

def get_synced_time_ms() -> float:
    return (time.time() * 1000) + clock_offset_ms

def precision_wait_until(target_time_ms: float) -> None:
    while True:
        now = get_synced_time_ms()
        diff = target_time_ms - now
        if diff <= 0:
            break
        elif diff > 10:
            time.sleep(0.002)
        elif diff > 2:
            time.sleep(0.0005)

def format_duration(seconds: int) -> str:
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}h {mins}m {secs}s"
    elif mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"

def format_step(value: float, step: float) -> str:
    d_step = Decimal(str(step))
    d_val = Decimal(str(value))
    precision = d_step.normalize().as_tuple().exponent
    quantized = (d_val / d_step).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * d_step
    if precision < 0:
        places = -precision
        return f"{quantized:.{places}f}"
    return str(int(quantized))

def format_tick(value: float, tick: float) -> str:
    d_tick = Decimal(str(tick))
    d_val = Decimal(str(value))
    precision = d_tick.normalize().as_tuple().exponent
    quantized = (d_val / d_tick).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * d_tick
    if precision < 0:
        places = -precision
        return f"{quantized:.{places}f}"
    return str(int(quantized))

def sign_params(params: dict) -> str:
    query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    sig = hmac.new(config.API_SECRET.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{query_string}&signature={sig}"

def find_soonest_highest_negative_funding_symbol(target_settle_ms: Optional[int] = None) -> Tuple[Optional[str], Optional[float], Optional[float], Optional[int]]:
    try:
        res = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/premiumIndex", timeout=5)
        tickers = res.json()
        current_time_ms = get_synced_time_ms()
        eligible_candidates = []
        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT") or symbol in BLACKLISTED_PATTERNS:
                continue
            try:
                rate = float(t.get("lastFundingRate", 0.0))
            except (ValueError, TypeError):
                continue
            if rate > 0.0 or rate > config.MIN_FUNDING_RATE:
                continue
            try:
                next_funding = int(t.get("nextFundingTime", 0))
            except (ValueError, TypeError):
                next_funding = 0
            try:
                mark_price = float(t.get("markPrice", 0.0))
            except (ValueError, TypeError):
                mark_price = 0.0
            if mark_price <= 0:
                continue
            if target_settle_ms is not None:
                if abs(next_funding - target_settle_ms) < 60000:
                    eligible_candidates.append({
                        "symbol": symbol,
                        "rate": rate,
                        "price": mark_price,
                        "settle_ms": next_funding
                    })
            else:
                if next_funding > (current_time_ms + config.RESCAN_BEFORE_SETTLE_MS + 5000):
                    eligible_candidates.append({
                        "symbol": symbol,
                        "rate": rate,
                        "price": mark_price,
                        "settle_ms": next_funding
                    })
        if eligible_candidates:
            if target_settle_ms is not None:
                eligible_candidates.sort(key=lambda x: x["rate"])
                best = eligible_candidates[0]
                return best["symbol"], best["rate"], best["price"], best["settle_ms"]
            else:
                soonest_settle_time = min(c["settle_ms"] for c in eligible_candidates)
                soonest_batch = [c for c in eligible_candidates if abs(c["settle_ms"] - soonest_settle_time) < 60000]
                soonest_batch.sort(key=lambda x: x["rate"])
                best = soonest_batch[0]
                return best["symbol"], best["rate"], best["price"], best["settle_ms"]
        return None, None, None, None
    except Exception as e:
        add_ui_log(f"Scanner error: {e}")
        return None, None, None, None

def calculate_order_specs(symbol: str, price: float):
    max_leverage_cap = int(config.MAX_LEVERAGE_CAP)
    applied_leverage = max_leverage_cap
    tick_size = 0.001
    qty_step = 1.0
    min_qty = 1.0
    try:
        res = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/exchangeInfo", timeout=5)
        symbols_info = res.json().get("symbols", [])
        for s in symbols_info:
            if s.get("symbol") == symbol:
                for f in s.get("filters", []):
                    if f.get("filterType") == "LOT_SIZE":
                        qty_step = float(f.get("stepSize", 1.0))
                        min_qty = float(f.get("minQty", 1.0))
                    elif f.get("filterType") == "PRICE_FILTER":
                        tick_size = float(f.get("tickSize", 0.001))
                break
    except Exception as e:
        add_ui_log(f"Exchange info error: {e}")

    try:
        ts = int(get_synced_time_ms())
        p_margin = {"symbol": symbol, "marginType": "ISOLATED", "timestamp": ts}
        signed_margin = sign_params(p_margin)
        res_margin = session.post(f"{config.BINANCE_REST_URL}/fapi/v1/marginType?{signed_margin}", headers={"X-MBX-APIKEY": config.API_KEY}, timeout=3).json()
        if res_margin.get("code") not in [None, 200, -4046]:
            add_ui_log(f"[MARGIN TYPE WARNING] {symbol}: {res_margin.get('msg', '')}")
        else:
            add_ui_log(f"[MARGIN TYPE] {symbol} locked in ISOLATED mode.")
    except Exception as e:
        add_ui_log(f"[MARGIN TYPE EXCEPTION] {e}")

    try:
        ts = int(get_synced_time_ms())
        p_bracket = {"symbol": symbol, "timestamp": ts}
        signed_bracket = sign_params(p_bracket)
        res_bracket = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/leverageBracket?{signed_bracket}", headers={"X-MBX-APIKEY": config.API_KEY}, timeout=3).json()
        coin_max_lev = max_leverage_cap
        if isinstance(res_bracket, list) and len(res_bracket) > 0:
            brackets = res_bracket[0].get("brackets", [])
            if brackets:
                coin_max_lev = max(int(b.get("initialLeverage", max_leverage_cap)) for b in brackets)
                applied_leverage = min(max_leverage_cap, coin_max_lev)
    except Exception as e:
        add_ui_log(f"[LEVERAGE BRACKET WARNING]: {e}. Fallback to {max_leverage_cap}x")
        applied_leverage = max_leverage_cap

    try:
        ts = int(get_synced_time_ms())
        p_lev = {"symbol": symbol, "leverage": int(applied_leverage), "timestamp": ts}
        signed_lev = sign_params(p_lev)
        res_lev = session.post(f"{config.BINANCE_REST_URL}/fapi/v1/leverage?{signed_lev}", headers={"X-MBX-APIKEY": config.API_KEY}, timeout=3).json()
        if "leverage" in res_lev:
            applied_leverage = int(res_lev["leverage"])
            add_ui_log(f"[LEVERAGE APPLIED] {symbol} leverage set to {applied_leverage}x (Max Limit: {max_leverage_cap}x)")
    except Exception as e:
        add_ui_log(f"[LEVERAGE SET EXCEPTION]: {e}")

    total_notional_usd = max(config.BINANCE_MIN_NOTIONAL, config.TRADE_MARGIN_USD * applied_leverage)
    raw_qty = max(min_qty, (total_notional_usd / price)) if price > 0 else min_qty
    qty_str = format_step(raw_qty, qty_step)
    if float(qty_str) < min_qty:
        qty_str = format_step(min_qty, qty_step)
    return qty_str, applied_leverage, tick_size, qty_step, min_qty
