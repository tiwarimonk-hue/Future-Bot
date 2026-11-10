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

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json"
})

_CLOCK_OFFSET_MS = 0.0

def sign_params(params: dict) -> str:
    query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    return query_string + "&signature=" + hmac.new(
        config.API_KEY.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

def sync_binance_clock() -> None:
    global _CLOCK_OFFSET_MS
    try:
        start_t = time.perf_counter()
        r = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/time", timeout=2.0)
        end_t = time.perf_counter()
        if r.status_code == 200:
            server_time = r.json().get("serverTime")
            if server_time:
                local_ms = int(time.time() * 1000)
                latency = ((end_t - start_t) * 1000) / 2.0
                _CLOCK_OFFSET_MS = server_time - local_ms - latency
                with data_lock:
                    dashboard_data["clock_offset_ms"] = round(_CLOCK_OFFSET_MS, 2)
    except Exception:
        pass

def get_synced_time_ms() -> float:
    return (time.time() * 1000) + _CLOCK_OFFSET_MS

def format_duration(seconds: int) -> str:
    if seconds < 0:
        return "0s"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"

def format_step(value: float, step: float) -> str:
    dec_val = Decimal(str(value))
    dec_step = Decimal(str(step))
    result = (dec_val / dec_step).to_integral_value(rounding=ROUND_HALF_UP) * dec_step
    # Quantize using dec_step
    result = result.quantize(dec_step)
    
    res_str = f"{result:f}"
    if '.' in res_str:
        res_str = res_str.rstrip('0').rstrip('.')
    return res_str

def format_tick(value: float, tick: float) -> str:
    dec_val = Decimal(str(value))
    dec_tick = Decimal(str(tick))
    result = (dec_val / dec_tick).to_integral_value(rounding=ROUND_HALF_UP) * dec_tick
    result = result.quantize(dec_tick)
    
    res_str = f"{result:f}"
    if '.' in res_str:
        res_str = res_str.rstrip('0').rstrip('.')
    return res_str

def fetch_top_volume_coins(limit: int = 50) -> list[str]:
    try:
        r = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/ticker/24hr", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            usdt_pairs = [x for x in data if x.get("symbol", "").endswith("USDT")]
            usdt_pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
            return [x["symbol"] for x in usdt_pairs[:limit]]
        return []
    except Exception:
        return []

def calculate_order_specs(symbol: str, target_price: float) -> tuple[str, int, float, float, float]:
    """
    API call karke coin ka Exchange Info (Tick Size, Qty Step Size, Min Notional) laata hai.
    Aur base margin ($4.00) ke mutabik required leverage nikalta hai.
    Return: (qty_str, applied_leverage, tick_size, qty_step, min_qty)
    """
    applied_leverage = 20
    tick_size = 0.001
    qty_step = 0.001
    min_qty = 0.0
    qty_str = "0"
    
    try:
        r = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/exchangeInfo", timeout=3.0)
        if r.status_code == 200:
            symbols_info = r.json().get("symbols", [])
            s_info = next((x for x in symbols_info if x.get("symbol") == symbol), None)
            if s_info:
                filters = s_info.get("filters", [])
                for f in filters:
                    if f.get("filterType") == "PRICE_FILTER":
                        tick_size = float(f.get("tickSize", 0.001))
                    elif f.get("filterType") == "LOT_SIZE":
                        qty_step = float(f.get("stepSize", 0.001))
                        min_qty = float(f.get("minQty", 0.0))
        
        # Calculate optimal leverage
        # required_notional = max(5.5, (Base Margin * Lev)) => default margin is $4.00 now (Cond 1 shallow limits & DCA1)
        # For Cond 2 (DCA 1 & 2), margin is $2.00, so 2.00 * 20 = $40 notional which is safely > $5 binance min.
        # Fixed 20x is sufficient for $4.00 base.
        if config.TRADE_MARGIN_USD * config.MAX_LEVERAGE_CAP < config.BINANCE_MIN_NOTIONAL * 1.1:
            req_lev = (config.BINANCE_MIN_NOTIONAL * 1.1) / config.TRADE_MARGIN_USD
            applied_leverage = min(int(req_lev) + 1, int(config.MAX_LEVERAGE_CAP))
        else:
            applied_leverage = int(config.MAX_LEVERAGE_CAP)
            
        applied_notional = config.TRADE_MARGIN_USD * applied_leverage
        
        if target_price > 0:
            raw_qty = applied_notional / target_price
            if raw_qty < min_qty:
                raw_qty = min_qty
            qty_str = format_step(raw_qty, qty_step)
            
    except Exception as e:
        add_ui_log(f"⚠️ [EXCHANGE INFO WARN] Could not fetch specs for {symbol}: {e}")
        
    return qty_str, applied_leverage, tick_size, qty_step, min_qty

def find_top_negative_funding_candidates(
    target_settle_ms: int = 0,
    preferred_symbol: str = "",
    allow_all: bool = False
) -> tuple[str, float, float, int, list[dict]]:
    """
    Market scan karke Top-10 Negative Funding Coins filter karta hai.
    Blocklist check lagata hai aur sweet spot/danger zones exclude karta hai.
    Target manual override bhi support karta hai.
    """
    try:
        r = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/premiumIndex", timeout=3.0)
        if r.status_code != 200:
            return "", 0.0, 0.0, 0, []
        data = r.json()
        
        now_ms = get_synced_time_ms()
        valid_coins = []
        blocked_data = load_blocked_coins()
        temp_blocks = blocked_data.get("temporary", [])
        perm_blocks = blocked_data.get("permanent", [])
        
        # 1. Base Filtering
        for item in data:
            sym = item.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
                
            is_manual_target = (sym == preferred_symbol)
            is_blocked = (sym in temp_blocks) or (sym in perm_blocks)
            
            # Agar auto-scan hai aur coin blocklist me hai toh skip kardo
            if not is_manual_target and is_blocked and not allow_all:
                continue
                
            rate = float(item.get("lastFundingRate", 0.0))
            mark_p = float(item.get("markPrice", 0.0))
            nxt_ms = int(item.get("nextFundingTime", 0))
            
            # Agar next funding 6 minute (360,000ms) se door hai toh skip
            if nxt_ms > 0 and (nxt_ms - now_ms) > 360000 and not is_manual_target and not allow_all:
                continue
                
            if target_settle_ms > 0 and abs(nxt_ms - target_settle_ms) > 60000 and not allow_all:
                continue

            valid_coins.append({
                "symbol": sym,
                "rate": rate,
                "last_price": mark_p,
                "settle_ms": nxt_ms
            })
            
        # 2. Add 24h Volume
        try:
            vol_res = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/ticker/24hr", timeout=3.0)
            if vol_res.status_code == 200:
                vol_map = {x["symbol"]: float(x.get("quoteVolume", 0.0)) for x in vol_res.json()}
                for c in valid_coins:
                    raw_vol = vol_map.get(c["symbol"], 0.0)
                    c["volume"] = raw_vol
                    # Format Volume Str
                    if raw_vol >= 1_000_000_000:
                        c["vol_str"] = f"{raw_vol/1_000_000_000:.2f}B"
                    elif raw_vol >= 1_000_000:
                        c["vol_str"] = f"{raw_vol/1_000_000:.2f}M"
                    elif raw_vol >= 1_000:
                        c["vol_str"] = f"{raw_vol/1_000:.2f}K"
                    else:
                        c["vol_str"] = f"{raw_vol:.0f}"
            else:
                for c in valid_coins:
                    c["volume"] = 0.0
                    c["vol_str"] = "--"
        except Exception:
            for c in valid_coins:
                c["volume"] = 0.0
                c["vol_str"] = "--"

        # Filter strictly negative rates and sort
        negative_coins = [c for c in valid_coins if c["rate"] < 0.0]
        negative_coins.sort(key=lambda x: x["rate"])

        for c in negative_coins:
            diff_s = max(0, int((c["settle_ms"] - now_ms) / 1000))
            c["settle_str"] = datetime.fromtimestamp(c["settle_ms"] / 1000, tz=IST).strftime("%H:%M:%S") + f" ({format_duration(diff_s)})"

        top_10 = negative_coins[:10]

        # 3. Decision Logic (Find exact target to arm)
        target_sym = ""
        target_rate = 0.0
        target_price = 0.0
        target_ms = 0
        
        # Scenario A: Manual Mode Override
        if preferred_symbol:
            for c in negative_coins:
                if c["symbol"] == preferred_symbol:
                    target_sym = c["symbol"]
                    target_rate = c["rate"]
                    target_price = c["last_price"]
                    target_ms = c["settle_ms"]
                    break
            if target_sym:
                return target_sym, target_rate, target_price, target_ms, top_10
                
        # Scenario B: Automatic AI Radar
        for c in negative_coins:
            sym = c["symbol"]
            r = c["rate"]
            
            # Skip specific danger sweet-spots
            if r <= config.SWEET_SPOT_MAX and r >= config.SWEET_SPOT_MIN:
                continue
                
            # Must meet minimum threshold and not exceed absolute danger hard cap
            if r <= config.MIN_FUNDING_RATE and r >= config.MAX_DANGER_FUNDING_RATE:
                target_sym = sym
                target_rate = r
                target_price = c["last_price"]
                target_ms = c["settle_ms"]
                break
                
        return target_sym, target_rate, target_price, target_ms, top_10

    except Exception as e:
        add_ui_log(f"⚠️ [RADAR ERROR] Scanner exception: {e}")
        return "", 0.0, 0.0, 0, []

def precision_wait_until(target_ms: int) -> None:
    """Microsecond precision wait matching NTP clock offset"""
    while True:
        now = get_synced_time_ms()
        diff = target_ms - now
        if diff <= 0:
            break
        elif diff > 5000:
            time.sleep(1.0)
        elif diff > 1000:
            time.sleep(0.5)
        elif diff > 100:
            time.sleep(0.05)
        elif diff > 20:
            time.sleep(0.01)
        else:
            # Busy wait for last 20ms to hit exact window
            pass

class AdvancedScanner:
    """Legacy backward compatibility class"""
    def get_all_funding_rates(self):
        try:
            r = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/premiumIndex", timeout=3.0)
            if r.status_code == 200:
                data = r.json()
                valid = []
                now_ms = int(time.time() * 1000)
                for item in data:
                    if not item.get("symbol", "").endswith("USDT"):
                        continue
                    rt = float(item.get("lastFundingRate", 0.0))
                    nxt = int(item.get("nextFundingTime", 0))
                    if nxt > 0 and (nxt - now_ms) > 360000:
                        continue
                    valid.append((item["symbol"], rt, nxt))
                return valid
            return []
        except:
            return []
