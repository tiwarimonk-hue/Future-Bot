from __future__ import annotations
import hmac
import hashlib
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple, List, Dict, Any, Sequence
from datetime import datetime, timezone, timedelta
import requests
import config
from dashboard import (
    dashboard_data, data_lock, add_ui_log,
    load_blocked_coins, get_active_base_margin_usd
)

IST = timezone(timedelta(hours=5, minutes=30))
clock_offset_ms = 0.0

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json"
})

BLACKLISTED_PATTERNS = (
    "DEFIUSDT", "BTCDOMUSDT", "FOOTBALLUSDT", "BLUEBIRDUSDT",
    "ZHONGJIUSDT", "BYDUSDT", "TENCENTUSDT", "BABAUSDT", "XIAOMIUSDT",
    "MEITUANUSDT", "CATLUSDT", "MOUTAIUSDT", "BWETUSDT", "CRMLUSDT",
    "ACNUSDT", "MPUSDT", "SECZUSDT", "UNHUSDT", "NKEUSDT", "NVDAUSDT",
    "TSLAUSDT", "AAPLUSDT", "AMZNUSDT", "MSFTUSDT", "GOOGLUSDT", "METAUSDT",
    "UNITREEUSDT", "JNJUSDT"
)

cached_exchange_rules: dict[str, dict] = {}
cached_leverage_brackets: dict[str, int] = {}
configured_symbols: set[str] = set()
last_exchange_info_time: float = 0.0

cached_24h_volumes: dict[str, float] = {}
last_24h_volume_time: float = 0.0


def is_standard_settlement_time(settle_ms: int) -> bool:
    """
    Checks whether settle_ms corresponds to one of the 6 standard 4-hour settlement
    windows in IST (01:30, 05:30, 09:30, 13:30, 17:30, 21:30 IST) for UI tagging.
    """
    if not settle_ms or settle_ms <= 0:
        return False
    dt_ist = datetime.fromtimestamp(settle_ms / 1000, tz=IST)
    return (dt_ist.minute == config.STANDARD_SETTLEMENT_MINUTE_IST and
            dt_ist.hour in config.STANDARD_SETTLEMENT_HOURS_IST)


def refresh_exchange_rules_if_needed() -> None:
    global cached_exchange_rules, last_exchange_info_time
    now = time.time()
    if cached_exchange_rules and (now - last_exchange_info_time) < 3600:
        return
    try:
        res = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/exchangeInfo", timeout=5).json()
        if not isinstance(res, dict) or "symbols" not in res:
            return
        new_rules = {}
        for s in res.get("symbols", []):
            sym = s.get("symbol", "")
            if s.get("status") != "TRADING" or s.get("contractType") != "PERPETUAL":
                continue
            min_qty = 1.0
            qty_step = 1.0
            tick_size = 0.001
            for f in s.get("filters", []):
                if f.get("filterType") == "LOT_SIZE":
                    min_qty = float(f.get("minQty", 1.0))
                    qty_step = float(f.get("stepSize", 1.0))
                elif f.get("filterType") == "PRICE_FILTER":
                    tick_size = float(f.get("tickSize", 0.001))
            new_rules[sym] = {
                "min_qty": min_qty,
                "qty_step": qty_step,
                "tick_size": tick_size,
                "status": s.get("status", ""),
                "contract_type": s.get("contractType", "")
            }
        if new_rules:
            cached_exchange_rules = new_rules
            last_exchange_info_time = now
            add_ui_log(f"⚡ [RULES CACHED] Loaded Binance rules for {len(new_rules)} perpetual pairs.")
    except Exception as e:
        add_ui_log(f"Exchange info refresh warning: {e}")


def refresh_24h_volumes_if_needed() -> None:
    global cached_24h_volumes, last_24h_volume_time
    now = time.time()
    if cached_24h_volumes and (now - last_24h_volume_time) < 900:
        return
    try:
        res = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/ticker/24hr", timeout=5).json()
        if isinstance(res, list):
            new_vols = {}
            for item in res:
                if isinstance(item, dict):
                    sym = item.get("symbol", "")
                    q_vol = float(item.get("quoteVolume", 0.0))
                    new_vols[sym] = q_vol
            cached_24h_volumes = new_vols
            last_24h_volume_time = now
    except Exception as e:
        add_ui_log(f"24h volume refresh warning: {e}")


def format_volume(vol: float) -> str:
    if vol >= 1_000_000_000:
        return f"${vol / 1_000_000_000:.2f}B"
    elif vol >= 1_000_000:
        return f"${vol / 1_000_000:.2f}M"
    elif vol >= 1_000:
        return f"${vol / 1_000:.2f}K"
    return f"${vol:.2f}"


def sync_binance_clock() -> None:
    """
    Cold TCP/TLS handshake inflation (+17ms drift) ko khatm karne ke liye pehle 1 warm-up call karta hai,
    aur phir warm keep-alive socket par 3 rapid samples lekar sabse kam RTT wale sample se exact offset nikalta hai.
    """
    global clock_offset_ms
    time_url = f"{config.BINANCE_REST_URL}/fapi/v1/time"
    try:
        session.get(time_url, timeout=2.5)

        best_rtt = float("inf")
        best_offset = clock_offset_ms

        for _ in range(3):
            t_send = time.time() * 1000.0
            res = session.get(time_url, timeout=2.0)
            t_recv = time.time() * 1000.0
            data = res.json()
            if isinstance(data, dict) and "serverTime" in data:
                server_time = float(data["serverTime"])
                rtt = t_recv - t_send
                if 0.0 < rtt < best_rtt:
                    best_rtt = rtt
                    estimated_server_time = server_time + (rtt / 2.0)
                    best_offset = estimated_server_time - t_recv

        if best_rtt < float("inf"):
            clock_offset_ms = best_offset
            with data_lock:
                dashboard_data["clock_offset_ms"] = round(clock_offset_ms, 2)
        else:
            add_ui_log("⚠️ Binance clock sync non-standard response.")
    except Exception as e:
        add_ui_log(f"Binance clock sync warning: {e}")


def get_synced_time_ms() -> float:
    return (time.time() * 1000.0) + clock_offset_ms


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


def get_symbol_max_leverage(symbol: str) -> int:
    if symbol in cached_leverage_brackets:
        return cached_leverage_brackets[symbol]
    try:
        time.sleep(0.1)
        ts = int(get_synced_time_ms())
        p_bracket = {"symbol": symbol, "timestamp": ts}
        signed_bracket = sign_params(p_bracket)
        res = session.get(
            f"{config.BINANCE_REST_URL}/fapi/v1/leverageBracket?{signed_bracket}",
            headers={"X-MBX-APIKEY": config.API_KEY},
            timeout=3
        ).json()
        if isinstance(res, list) and len(res) > 0:
            brackets = res[0].get("brackets", [])
            if brackets:
                max_lev = max(int(b.get("initialLeverage", 20)) for b in brackets)
                cached_leverage_brackets[symbol] = max_lev
                return max_lev
    except Exception:
        pass
    return int(config.MAX_LEVERAGE_CAP)


def verify_binance_symbol_preflight(symbol: str, mark_price: float) -> tuple[bool, str]:
    rules = cached_exchange_rules.get(symbol)
    if not rules:
        return False, "Rules not cached"

    if rules.get("status") != "TRADING" or rules.get("contract_type") != "PERPETUAL":
        return False, f"Not trading perpetual ({rules.get('status')}/{rules.get('contract_type')})"

    active_margin = get_active_base_margin_usd()
    coin_max_lev = get_symbol_max_leverage(symbol)
    effective_lev = min(coin_max_lev, int(config.MAX_LEVERAGE_CAP))
    purchasing_power = active_margin * effective_lev

    if purchasing_power < config.BINANCE_MIN_NOTIONAL:
        return False, f"Purchasing power ({purchasing_power:.2f} USDT @ ${active_margin:.2f} x {effective_lev}x) < Min Notional ({config.BINANCE_MIN_NOTIONAL} USDT)"

    min_qty = rules["min_qty"]
    min_lot_cost = min_qty * mark_price
    if min_lot_cost > purchasing_power:
        return False, f"1 Lot cost ({min_lot_cost:.2f} USDT) > max purchasing power ({purchasing_power:.2f} USDT)"

    if symbol in configured_symbols:
        return True, "Pre-flight verified (cached)"

    try:
        time.sleep(0.15)
        ts = int(get_synced_time_ms())
        p_margin = {"symbol": symbol, "marginType": "ISOLATED", "timestamp": ts}
        signed_margin = sign_params(p_margin)
        res_margin = session.post(
            f"{config.BINANCE_REST_URL}/fapi/v1/marginType?{signed_margin}",
            headers={"X-MBX-APIKEY": config.API_KEY},
            timeout=2.5
        ).json()
        if res_margin.get("code") not in [None, 200, -4046]:
            return False, f"Margin type rejected: {res_margin.get('msg')}"

        time.sleep(0.15)
        ts = int(get_synced_time_ms())
        p_lev = {"symbol": symbol, "leverage": int(effective_lev), "timestamp": ts}
        signed_lev = sign_params(p_lev)
        res_lev = session.post(
            f"{config.BINANCE_REST_URL}/fapi/v1/leverage?{signed_lev}",
            headers={"X-MBX-APIKEY": config.API_KEY},
            timeout=2.5
        ).json()
        if "leverage" not in res_lev:
            return False, f"Leverage set failed: {res_lev.get('msg', 'Unknown error')}"

        configured_symbols.add(symbol)
    except Exception as e:
        return False, f"Exchange API handshake exception: {e}"

    return True, "Pre-flight verified"


def find_top_negative_funding_candidates(
    target_settle_ms: Optional[int] = None,
    preferred_symbols: Optional[Sequence[str]] = None,
    manual_mode: bool = False
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Returns (chosen_targets_list, top_10_radar_list).
    In Automatic Mode: Automatically trades both Standard 4h and Off-Time (1h/2h) cycles
                       as long as Funding Rate is within the safe bound (-1.60% to -0.35%).
    In Manual Mode: User can select up to 3 candidates from any cycle.
    Radar Table: Displays top 10 candidates with (1h Off-Time) indicator for informational clarity.
    """
    try:
        refresh_exchange_rules_if_needed()
        refresh_24h_volumes_if_needed()

        blocked_data = load_blocked_coins()
        temp_blocked_set = set(blocked_data.get("temporary", []))
        perm_blocked_set = set(blocked_data.get("permanent", []))

        res = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/premiumIndex", timeout=5)
        tickers = res.json()

        if not isinstance(tickers, list):
            add_ui_log(f"⚠️ [SCANNER BACKOFF] Binance returned non-list response: {tickers}")
            return [], []

        current_time_ms = get_synced_time_ms()
        eligible_candidates = []

        for t in tickers:
            if not isinstance(t, dict):
                continue

            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT") or symbol in BLACKLISTED_PATTERNS or symbol in perm_blocked_set:
                continue

            try:
                rate = float(t.get("lastFundingRate", 0.0))
            except (ValueError, TypeError):
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

            settle_match = False
            if target_settle_ms is not None:
                if abs(next_funding - target_settle_ms) < 60000:
                    settle_match = True
            else:
                if next_funding > (current_time_ms + config.RESCAN_BEFORE_SETTLE_MS + 5000):
                    settle_match = True

            if settle_match:
                vol_24h = cached_24h_volumes.get(symbol, 0.0)
                is_standard = is_standard_settlement_time(next_funding)
                raw_time_str = datetime.fromtimestamp(next_funding / 1000, tz=IST).strftime("%H:%M:%S IST")
                settle_display_str = raw_time_str if is_standard else f"{raw_time_str} (1h Off-Time)"

                eligible_candidates.append({
                    "symbol": symbol,
                    "rate": rate,
                    "price": mark_price,
                    "settle_ms": next_funding,
                    "settle_str": settle_display_str,
                    "raw_settle_str": raw_time_str,
                    "is_standard_settle": is_standard,
                    "volume": vol_24h,
                    "vol_str": format_volume(vol_24h),
                    "is_temp_blocked": symbol in temp_blocked_set
                })

        if not eligible_candidates:
            return [], []

        soonest_settle_time = min(c["settle_ms"] for c in eligible_candidates)
        soonest_batch = [c for c in eligible_candidates if abs(c["settle_ms"] - soonest_settle_time) < 60000]

        # Top 10 Radar: Sorted ascending by funding rate
        soonest_batch.sort(key=lambda x: x["rate"])
        top_10 = soonest_batch[:10]

        # 1. MANUAL OVERRIDE MODE (User can select any coin, standard or off-time)
        if manual_mode:
            clean_preferred = [s.strip().upper() for s in (preferred_symbols or []) if s.strip()]
            if not clean_preferred:
                # ZERO COIN SAFETY SHIELD: 0 coins selected in Manual mode!
                return [], top_10

            chosen_list = []
            for sym in clean_preferred[:3]:
                if sym in temp_blocked_set or sym in perm_blocked_set:
                    add_ui_log(f"⚠️ [PREFERRED SYMBOL IGNORED] {sym} is in block list. Skipping.")
                    continue

                match = next((c for c in soonest_batch if c["symbol"] == sym), None)
                if match and match["rate"] < 0.0 and not match.get("is_temp_blocked", False):
                    ok, msg = verify_binance_symbol_preflight(match["symbol"], match["price"])
                    if ok:
                        chosen_list.append(match)
                        if not any(c["symbol"] == sym for c in top_10):
                            top_10.insert(0, match)
                            top_10 = top_10[:10]
                    else:
                        add_ui_log(f"⚠️ [MANUAL PREFLIGHT REJECT] {sym}: {msg}")
                else:
                    # Fallback check if coin is in tickers and matches settlement window
                    fallback_t = next((t for t in tickers if t.get("symbol") == sym), None)
                    if fallback_t:
                        f_rate = float(fallback_t.get("lastFundingRate", 0.0))
                        f_price = float(fallback_t.get("markPrice", 0.0))
                        f_settle = int(fallback_t.get("nextFundingTime", 0))
                        ref_settle = target_settle_ms if target_settle_ms is not None else soonest_settle_time
                        if f_rate < 0.0 and f_price > 0 and abs(f_settle - ref_settle) < 60000:
                            ok, msg = verify_binance_symbol_preflight(sym, f_price)
                            if ok:
                                is_std = is_standard_settlement_time(f_settle)
                                raw_t = datetime.fromtimestamp(f_settle / 1000, tz=IST).strftime("%H:%M:%S IST")
                                item_obj = {
                                    "symbol": sym,
                                    "rate": f_rate,
                                    "price": f_price,
                                    "settle_ms": f_settle,
                                    "settle_str": raw_t if is_std else f"{raw_t} (1h Off-Time)",
                                    "raw_settle_str": raw_t,
                                    "is_standard_settle": is_std,
                                    "volume": cached_24h_volumes.get(sym, 0.0),
                                    "vol_str": format_volume(cached_24h_volumes.get(sym, 0.0)),
                                    "is_temp_blocked": False
                                }
                                chosen_list.append(item_obj)
                                if not any(c["symbol"] == sym for c in top_10):
                                    top_10.insert(0, item_obj)
                                    top_10 = top_10[:10]
                            else:
                                add_ui_log(f"⚠️ [MANUAL PREFLIGHT REJECT] {sym}: {msg}")

            return chosen_list, top_10

        # 2. AUTOMATIC MODE: Uniform Safe Bound Selection (Trades both 4h and 1h cycles within -1.60% to -0.35%)
        safe_batch = [
            c for c in soonest_batch
            if config.MAX_DANGER_FUNDING_RATE <= c["rate"] <= config.MIN_FUNDING_RATE
            and not c.get("is_temp_blocked", False)
        ]

        sweet_spot = [
            c for c in safe_batch
            if config.SWEET_SPOT_MIN <= c["rate"] <= config.SWEET_SPOT_MAX
        ]

        chosen = None
        candidates_to_filter = sweet_spot if sweet_spot else safe_batch
        for c in candidates_to_filter:
            ok, _ = verify_binance_symbol_preflight(c["symbol"], c["price"])
            if ok:
                chosen = c
                break
            time.sleep(0.15)

        # Fallback to remaining safe coins if top sweet-spot coin fails preflight
        if not chosen and sweet_spot and safe_batch:
            for c in safe_batch:
                if c in candidates_to_filter:
                    continue
                ok, _ = verify_binance_symbol_preflight(c["symbol"], c["price"])
                if ok:
                    chosen = c
                    break
                time.sleep(0.15)

        return ([chosen] if chosen else []), top_10

    except Exception as e:
        add_ui_log(f"Scanner error: {e}")
        return [], []


def calculate_order_specs(symbol: str, price: float):
    max_leverage_cap = int(config.MAX_LEVERAGE_CAP)
    coin_max_lev = get_symbol_max_leverage(symbol)
    applied_leverage = min(max_leverage_cap, coin_max_lev)

    refresh_exchange_rules_if_needed()
    rules = cached_exchange_rules.get(symbol, {})
    tick_size = rules.get("tick_size", 0.001)
    qty_step = rules.get("qty_step", 1.0)
    min_qty = rules.get("min_qty", 1.0)

    active_margin = get_active_base_margin_usd()
    total_notional_usd = max(config.BINANCE_MIN_NOTIONAL, active_margin * applied_leverage)
    raw_qty = max(min_qty, (total_notional_usd / price)) if price > 0 else min_qty
    qty_str = format_step(raw_qty, qty_step)
    if float(qty_str) < min_qty:
        qty_str = format_step(min_qty, qty_step)

    return qty_str, applied_leverage, tick_size, qty_step, min_qty
