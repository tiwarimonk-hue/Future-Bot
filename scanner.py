from __future__ import annotations
import hmac
import hashlib
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime, timezone, timedelta
import requests
import config
from dashboard import dashboard_data, data_lock, add_ui_log, load_blocked_coins

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
    global clock_offset_ms
    try:
        t_send = time.time() * 1000
        res = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/time", timeout=3)
        t_recv = time.time() * 1000
        data = res.json()
        if isinstance(data, dict) and "serverTime" in data:
            server_time = float(data["serverTime"])
            rtt = t_recv - t_send
            estimated_server_time = server_time + (rtt / 2.0)
            clock_offset_ms = estimated_server_time - t_recv
            with data_lock:
                dashboard_data["clock_offset_ms"] = round(clock_offset_ms, 2)
        else:
            add_ui_log(f"⚠️ Binance clock sync non-standard response: {data}")
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

    coin_max_lev = get_symbol_max_leverage(symbol)
    effective_lev = min(coin_max_lev, int(config.MAX_LEVERAGE_CAP))
    purchasing_power = config.TRADE_MARGIN_USD * effective_lev

    if purchasing_power < config.BINANCE_MIN_NOTIONAL:
        return False, f"Purchasing power ({purchasing_power:.2f} USDT) < Min Notional ({config.BINANCE_MIN_NOTIONAL} USDT)"

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
    preferred_symbols: Optional[List[str]] = None
) -> Tuple[List[Dict[str, Any]], Optional[int], List[Dict[str, Any]]]:
    """
    Scans Binance perpetuals and returns up to MAX_AUTO_ARM_COINS (2) verified candidates,
    respecting manual arm/disarm lists and block lists.
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
            return [], None, []

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
                eligible_candidates.append({
                    "symbol": symbol,
                    "rate": rate,
                    "price": mark_price,
                    "settle_ms": next_funding,
                    "settle_str": datetime.fromtimestamp(next_funding / 1000, tz=IST).strftime("%H:%M:%S IST"),
                    "volume": vol_24h,
                    "vol_str": format_volume(vol_24h),
                    "is_temp_blocked": symbol in temp_blocked_set
                })

        if not eligible_candidates:
            return [], None, []

        soonest_settle_time = min(c["settle_ms"] for c in eligible_candidates)
        soonest_batch = [c for c in eligible_candidates if abs(c["settle_ms"] - soonest_settle_time) < 60000]

        soonest_batch.sort(key=lambda x: x["rate"])
        top_10 = soonest_batch[:10]

        safe_batch = [
            c for c in soonest_batch
            if config.MAX_DANGER_FUNDING_RATE <= c["rate"] <= config.MIN_FUNDING_RATE
            and not c.get("is_temp_blocked", False)
        ]

        sweet_spot = [
            c for c in safe_batch
            if config.SWEET_SPOT_MIN <= c["rate"] <= config.SWEET_SPOT_MAX
        ]

        chosen_list = []
        disarmed_set = set(dashboard_data.get("disarmed_symbols", []))

        # 1. Handle Preferred / Manually Armed Symbols first
        if preferred_symbols:
            for sym in preferred_symbols:
                sym = sym.strip().upper()
                if not sym or sym in disarmed_set or sym in temp_blocked_set or sym in perm_blocked_set:
                    continue
                match = next((c for c in soonest_batch if c["symbol"] == sym), None)
                if match and match["rate"] < 0.0:
                    ok, _ = verify_binance_symbol_preflight(match["symbol"], match["price"])
                    if ok and match not in chosen_list:
                        chosen_list.append(match)
                        if len(chosen_list) >= config.MAX_AUTO_ARM_COINS:
                            break

        # 2. Auto Mode: Select up to MAX_AUTO_ARM_COINS (2) verified coins from sweet spot / safe batch
        if len(chosen_list) < config.MAX_AUTO_ARM_COINS:
            candidates_to_filter = sweet_spot if sweet_spot else safe_batch
            for c in candidates_to_filter:
                if c["symbol"] in disarmed_set or c in chosen_list:
                    continue
                ok, _ = verify_binance_symbol_preflight(c["symbol"], c["price"])
                if ok:
                    chosen_list.append(c)
                    if len(chosen_list) >= config.MAX_AUTO_ARM_COINS:
                        break
                time.sleep(0.15)

            # Fallback to remaining safe batch if needed
            if len(chosen_list) < config.MAX_AUTO_ARM_COINS and sweet_spot and safe_batch:
                for c in safe_batch:
                    if c in candidates_to_filter or c["symbol"] in disarmed_set or c in chosen_list:
                        continue
                    ok, _ = verify_binance_symbol_preflight(c["symbol"], c["price"])
                    if ok:
                        chosen_list.append(c)
                        if len(chosen_list) >= config.MAX_AUTO_ARM_COINS:
                            break
                    time.sleep(0.15)

        return chosen_list, soonest_settle_time, top_10

    except Exception as e:
        add_ui_log(f"Scanner error: {e}")
        return [], None, []


def calculate_order_specs(symbol: str, price: float):
    max_leverage_cap = int(config.MAX_LEVERAGE_CAP)
    coin_max_lev = get_symbol_max_leverage(symbol)
    applied_leverage = min(max_leverage_cap, coin_max_lev)

    refresh_exchange_rules_if_needed()
    rules = cached_exchange_rules.get(symbol, {})
    tick_size = rules.get("tick_size", 0.001)
    qty_step = rules.get("qty_step", 1.0)
    min_qty = rules.get("min_qty", 1.0)

    total_notional_usd = max(config.BINANCE_MIN_NOTIONAL, config.TRADE_MARGIN_USD * applied_leverage)
    raw_qty = max(min_qty, (total_notional_usd / price)) if price > 0 else min_qty
    qty_str = format_step(raw_qty, qty_step)
    if float(qty_str) < min_qty:
        qty_str = format_step(min_qty, qty_step)

    return qty_str, applied_leverage, tick_size, qty_step, min_qty
