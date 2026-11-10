from __future__ import annotations
import time
import requests
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import config
from dashboard import IST, add_ui_log, load_blocked_coins

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json"
})

server_clock_offset_ms: float = 0.0


def sync_binance_clock() -> float:
    """Binance REST API se clock drift audit aur measure karta hai"""
    global server_clock_offset_ms
    try:
        t_before = time.time() * 1000
        res = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/time", timeout=2.0).json()
        t_after = time.time() * 1000
        server_time = float(res.get("serverTime", 0))

        if server_time > 0:
            rtt = t_after - t_before
            estimated_server_time = server_time + (rtt / 2.0)
            server_clock_offset_ms = estimated_server_time - t_after

            with config.data_lock:
                config.dashboard_data["clock_offset_ms"] = round(server_clock_offset_ms, 2)

            add_ui_log(f"⏰ [CLOCK SYNC] Binance Server Time Synced | Offset: {server_clock_offset_ms:+.2f} ms | RTT: {rtt:.2f} ms")
    except Exception as e:
        add_ui_log(f"⚠️ [CLOCK SYNC WARNING]: {e}")
    return server_clock_offset_ms


def get_synced_time_ms() -> float:
    """Local System Clock + Synced Server Offset Return Karta hai"""
    return (time.time() * 1000) + server_clock_offset_ms


def precision_wait_until(target_time_ms: float) -> None:
    """Hyper-precise busy-spin wait loop jo microsecond level accuracy deta hai"""
    while True:
        now_ms = get_synced_time_ms()
        diff_ms = target_time_ms - now_ms
        if diff_ms <= 0:
            break
        if diff_ms > 50:
            time.sleep((diff_ms - 30) / 1000.0)
        elif diff_ms > 2:
            time.sleep(0.001)


def format_duration(sec: int) -> str:
    """Seconds ko Human Readable HH:MM:SS format me translate karta hai"""
    if sec <= 0:
        return "0s (Settling...)"
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def format_step(val: float, step: float) -> str:
    """Binance Step Size / Tick Size Precision Formatting (e.g. Price & Qty String)"""
    if step <= 0:
        return f"{val:.8f}".rstrip('0').rstrip('.')
    d_step = Decimal(str(step))
    d_val = Decimal(str(val))
    precision = max(0, -d_step.as_tuple().exponent)
    quantized = (d_val // d_step) * d_step
    return f"{quantized:.{precision}f}"


def format_tick(val: float, tick: float) -> str:
    """Binance Price Tick Precision Formatting Helper"""
    return format_step(val, tick)


def find_top_negative_funding_candidates(
    target_settle_ms: int = 0,
    preferred_symbol: str | None = None
) -> tuple[str, float, float, int, list[dict]]:
    """
    Binance Futures ki premiumIndex query karke Top Funding Rates find karta hai.
    Supports Manual Selection, Automatic Filtering, Blocked Coins Filtering aur 24h Volume sorting.
    """
    try:
        url = f"{config.BINANCE_REST_URL}/fapi/v1/premiumIndex"
        data = session.get(url, timeout=3.0).json()

        if not isinstance(data, list):
            add_ui_log(f"⚠️ [SCANNER ERROR] Expected list from premiumIndex, got: {type(data)}")
            return "", 0.0, 0.0, 0, []

        blocked = load_blocked_coins()
        temp_blocked = set(blocked.get("temporary", []))
        perm_blocked = set(blocked.get("permanent", []))

        # 24h Ticker Volumes load karna taaki liquidity threshold audit ho sake
        vol_map = {}
        try:
            v_data = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/ticker/24hr", timeout=3.0).json()
            if isinstance(v_data, list):
                for item in v_data:
                    s = item.get("symbol")
                    q_vol = float(item.get("quoteVolume", 0))
                    vol_map[s] = q_vol
        except Exception as ve:
            add_ui_log(f"⚠️ [SCANNER VOL FETCH WARNING]: {ve}")

        now_ms = get_synced_time_ms()
        candidates = []

        for item in data:
            sym = item.get("symbol", "")
            if not sym.endswith("USDT"):
                continue

            rate = float(item.get("lastFundingRate", 0))
            price = float(item.get("markPrice", 0))
            settle_ms = int(item.get("nextFundingTime", 0))

            if settle_ms <= now_ms:
                continue

            # Filtering based on target settlement time if specified
            if target_settle_ms > 0 and abs(settle_ms - target_settle_ms) > 300000:
                continue

            quote_vol = vol_map.get(sym, 0.0)
            vol_str = f"${quote_vol / 1000000:.1f}M" if quote_vol >= 1000000 else f"${quote_vol / 1000:.0f}K"
            settle_dt = datetime.fromtimestamp(settle_ms / 1000, tz=IST)
            settle_str = settle_dt.strftime("%H:%M IST")

            candidates.append({
                "symbol": sym,
                "rate": rate,
                "price": price,
                "settle_ms": settle_ms,
                "settle_str": settle_str,
                "volume": quote_vol,
                "vol_str": vol_str
            })

        # Sort candidates by Funding Rate (Most negative first)
        candidates.sort(key=lambda x: x["rate"])

        top_10 = candidates[:10]

        # Manual Selected Symbol Check
        if preferred_symbol:
            pref_sym = preferred_symbol.strip().upper()
            if pref_sym not in temp_blocked and pref_sym not in perm_blocked:
                for c in candidates:
                    if c["symbol"] == pref_sym:
                        add_ui_log(f"🎯 [RADAR PREFERRED LOCK] User manual coin armed: {pref_sym} ({c['rate'] * 100:+.4f}%)")
                        return c["symbol"], c["rate"], c["price"], c["settle_ms"], top_10

        # Automatic Selection Filter Loop
        for c in candidates:
            sym = c["symbol"]
            r = c["rate"]

            if sym in temp_blocked or sym in perm_blocked:
                continue

            # Apply Funding Rate threshold constraints
            if r <= config.MIN_FUNDING_RATE and r >= config.MAX_DANGER_FUNDING_RATE:
                return c["symbol"], c["rate"], c["price"], c["settle_ms"], top_10

        if candidates:
            c0 = candidates[0]
            return c0["symbol"], c0["rate"], c0["price"], c0["settle_ms"], top_10

        return "", 0.0, 0.0, 0, []

    except Exception as e:
        add_ui_log(f"🚨 [SCANNER EXECUTION ERROR]: {e}")
        return "", 0.0, 0.0, 0, []


def calculate_order_specs(symbol: str, price: float) -> tuple[str, int, float, float, float]:
    """
    Binance exchangeInfo se Quantity Precision, Tick Size, Step Size aur Dynamic Leverage calculate karta hai
    """
    try:
        res = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/exchangeInfo", timeout=3.0).json()
        symbols_info = res.get("symbols", [])

        tick_size = 0.0001
        qty_step = 0.001
        min_qty = 0.001

        for s in symbols_info:
            if s.get("symbol") == symbol:
                for f in s.get("filters", []):
                    if f.get("filterType") == "PRICE_FILTER":
                        tick_size = float(f.get("tickSize", 0.0001))
                    elif f.get("filterType") == "LOT_SIZE":
                        qty_step = float(f.get("stepSize", 0.001))
                        min_qty = float(f.get("minQty", 0.001))
                break

        applied_leverage = config.MAX_LEVERAGE
        notional_usd = config.TRADE_MARGIN_USD * applied_leverage
        raw_qty = max(min_qty, notional_usd / price) if price > 0 else min_qty
        qty_str = format_step(raw_qty, qty_step)

        # Minimum notional safety check ($5.2 USDT buffer)
        if float(qty_str) * price < 5.2:
            qty_str = format_step(float(qty_str) + qty_step, qty_step)

        return qty_str, applied_leverage, tick_size, qty_step, min_qty

    except Exception as e:
        add_ui_log(f"⚠️ [CALCULATE SPECS WARNING]: {e}")
        return "1", config.MAX_LEVERAGE, 0.0001, 0.001, 0.001
