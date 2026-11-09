import os
import time
import math
import threading
from collections import deque
from datetime import datetime, timezone, timedelta
from pybit.unified_trading import HTTP
from binance.um_futures import UMFutures

IST = timezone(timedelta(hours=5, minutes=30))

BYBIT_API_KEY = os.environ.get('BYBIT_API_KEY', 'YOUR_BYBIT_API_KEY')
BYBIT_API_SECRET = os.environ.get('BYBIT_API_SECRET', 'YOUR_BYBIT_API_SECRET')
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', 'YOUR_BINANCE_API_KEY')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', 'YOUR_BINANCE_API_SECRET')

# WHALE EXECUTION & PRE-TRACKING PARAMETERS
WHALE_MARGIN_USD = 1.0          # $1.0 Margin
WHALE_LEVERAGE = 6              # 6x Leverage ($6.0 Notional)
WHALE_SL_PCT = 0.05             # 5% Stop Loss
WHALE_TP_PCT = 0.05             # 5% Take Profit

INCUBATION_MIN_SEC = 60.0       # Silent background tracking for 60s before showing
INCUBATION_MAX_SEC = 120.0      # Drop from incubation if it doesn't qualify within 2m
MIN_WIN_CHANCE_PCT = 55         # Gate threshold to enter the Top 10 list
MIN_CONSISTENCY_RATIO = 0.65    # At least 65% of 1s ticks must move in trend direction
MIN_PRE_MOVE_PCT = 0.35         # Must have moved >= 0.35% during 60s tracking
MAX_PRE_MOVE_PCT = 3.50         # Over-extended guard before 5% TP

LOCK_WINDOW_SEC = 120.0         # Guaranteed 2-minute lock once shown on dashboard
SCAN_INTERVAL_SEC = 1.0         # 1 Second live fetch & update cycle
TOP_N_LIMIT = 10                # Strictly Top 10 per list

market_memory = {}      # "EX:SYM" -> baseline volume & price tracking
incubation_pool = {}    # "EX:SYM:SIDE" -> silent 60s background tracker
locked_signals = {}     # "EX:SYM:SIDE" -> promoted signals locked for full 2 minutes
expired_history = {}    # "EX:SYM:SIDE" -> peak stats for re-surge detection
binance_24h_cache = {}  # sym -> {"vol24": float, "pct24": float}
last_bn_24h_fetch = 0.0

realized_pnl_state = {
    "bybit_test_closed": 0.0,
    "binance_test_closed": 0.0,
    "bybit_real_closed": 0.0,
    "binance_real_closed": 0.0,
    "bybit_wins": 0,
    "bybit_losses": 0,
    "binance_wins": 0,
    "binance_losses": 0
}
pnl_lock = threading.Lock()

last_bn_err_log = 0.0
last_bb_err_log = 0.0

def format_usd(val):
    abs_v = abs(val)
    if abs_v >= 1_000_000:
        return f"${abs_v / 1_000_000:.2f}M"
    elif abs_v >= 1_000:
        return f"${abs_v / 1_000:.1f}K"
    return f"${abs_v:.0f}"

def calculate_smoothed_chance(side, mult_window, inflow_window, tick_window, favorable_move_pct):
    avg_mult = sum(mult_window) / len(mult_window) if mult_window else 1.0
    avg_inflow = sum(inflow_window) / len(inflow_window) if inflow_window else 0.0
    avg_tick = sum(tick_window) / len(tick_window) if tick_window else 0.0
    favorable_tick = avg_tick if side == "BUY" else -avg_tick

    accel_bonus = 4.0 if (inflow_window and inflow_window[-1] >= avg_inflow * 0.85) else -3.0

    score = (
        46.0
        + min(22.0, avg_mult * 3.1)
        + min(12.0, avg_inflow / 30_000.0)
        + max(-10.0, min(14.0, favorable_move_pct * 6.5))
        + max(-6.0, min(8.0, favorable_tick * 35.0))
        + accel_bonus
    )
    return max(20, min(98, int(round(score))))

def sync_pnl_summary(dashboard_data, data_lock):
    with data_lock:
        bb_tests = dashboard_data['whale'].get('bybit_test_trades', [])
        bn_tests = dashboard_data['whale'].get('binance_test_trades', [])

        bb_live_unrealized = sum(t.get('pnl_usd', 0.0) for t in bb_tests if t.get('status') == 'LIVE')
        bn_live_unrealized = sum(t.get('pnl_usd', 0.0) for t in bn_tests if t.get('status') == 'LIVE')

        with pnl_lock:
            bb_total_test = round(realized_pnl_state["bybit_test_closed"] + bb_live_unrealized, 3)
            bn_total_test = round(realized_pnl_state["binance_test_closed"] + bn_live_unrealized, 3)
            dashboard_data['whale']['pnl'] = {
                "bybit_test": bb_total_test,
                "binance_test": bn_total_test,
                "bybit_real": round(realized_pnl_state["bybit_real_closed"], 3),
                "binance_real": round(realized_pnl_state["binance_real_closed"], 3),
                "bybit_wins": realized_pnl_state["bybit_wins"],
                "bybit_losses": realized_pnl_state["bybit_losses"],
                "binance_wins": realized_pnl_state["binance_wins"],
                "binance_losses": realized_pnl_state["binance_losses"]
            }

def update_live_test_trades_1s(live_prices_map, dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    """Updates all active Bybit & Binance virtual test trades every 1 second using live market prices."""
    completed_events = []

    with data_lock:
        for exchange, list_key in (("BYBIT", "bybit_test_trades"), ("BINANCE", "binance_test_trades")):
            for t in dashboard_data['whale'].get(list_key, []):
                if t.get("status") != "LIVE":
                    continue

                m_key = f"{exchange}:{t['symbol']}"
                cur_p = live_prices_map.get(m_key)
                if not cur_p or cur_p <= 0:
                    continue

                entry_price = t["entry_price"]
                sl_price = t["sl_price"]
                tp_price = t["tp_price"]
                side = t["side"]
                virtual_qty = t["virtual_qty"]

                if side == "BUY":
                    price_pct = ((cur_p - entry_price) / entry_price) * 100.0
                    pnl_usd = (cur_p - entry_price) * virtual_qty
                else:
                    price_pct = ((entry_price - cur_p) / entry_price) * 100.0
                    pnl_usd = (entry_price - cur_p) * virtual_qty

                roi_pct = (pnl_usd / WHALE_MARGIN_USD) * 100.0
                hit_status = None
                exit_p = cur_p

                if t.get("force_close", False):
                    hit_status = "MANUAL CLOSED"
                elif side == "BUY":
                    if cur_p >= tp_price:
                        hit_status = "TP +5% HIT"
                        exit_p = tp_price
                        pnl_usd = (tp_price - entry_price) * virtual_qty
                        price_pct = 5.0
                        roi_pct = 30.0
                    elif cur_p <= sl_price:
                        hit_status = "SL -5% HIT"
                        exit_p = sl_price
                        pnl_usd = (sl_price - entry_price) * virtual_qty
                        price_pct = -5.0
                        roi_pct = -30.0
                else:
                    if cur_p <= tp_price:
                        hit_status = "TP +5% HIT"
                        exit_p = tp_price
                        pnl_usd = (entry_price - tp_price) * virtual_qty
                        price_pct = 5.0
                        roi_pct = 30.0
                    elif cur_p >= sl_price:
                        hit_status = "SL -5% HIT"
                        exit_p = sl_price
                        pnl_usd = (entry_price - sl_price) * virtual_qty
                        price_pct = -5.0
                        roi_pct = -30.0

                t["live_price"] = round(exit_p, 8)
                t["pnl_usd"] = round(pnl_usd, 4)
                t["price_pct"] = round(price_pct, 2)
                t["roi_pct"] = round(roi_pct, 1)

                if hit_status:
                    exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
                    t["exit_time"] = exit_time_str
                    t["status"] = "CLOSED"
                    t["outcome"] = hit_status
                    completed_events.append((exchange, dict(t), pnl_usd, roi_pct, exit_p, exit_time_str, hit_status))

    for exchange, t_snap, pnl_usd, roi_pct, exit_p, exit_time_str, hit_status in completed_events:
        with pnl_lock:
            if exchange == "BYBIT":
                realized_pnl_state["bybit_test_closed"] += pnl_usd
                if pnl_usd >= 0:
                    realized_pnl_state["bybit_wins"] += 1
                else:
                    realized_pnl_state["bybit_losses"] += 1
            else:
                realized_pnl_state["binance_test_closed"] += pnl_usd
                if pnl_usd >= 0:
                    realized_pnl_state["binance_wins"] += 1
                else:
                    realized_pnl_state["binance_losses"] += 1

        sign = "+" if pnl_usd >= 0 else ""
        pnl_str = f"{sign}${pnl_usd:.3f} ({sign}{roi_pct:.1f}% ROI)"
        add_ui_log(f"🧪🏁 [TEST {exchange}] {t_snap['symbol']} ({t_snap['side']}) {hit_status} @ {exit_p} | PnL: {pnl_str}")
        add_ledger_entry({
            "exchange": f"Whale-{exchange}",
            "coin": f"{t_snap['symbol']} [{t_snap['side']} - TEST]",
            "rate": f"🧪 {hit_status}",
            "entry_time": f"{t_snap['entry_time']} (@ {t_snap['entry_price']})",
            "exit_time": f"{exit_time_str} (@ {exit_p})",
            "detail": f"[{exchange} VIRTUAL] Margin: $1.0 (6x) | Net PnL: {pnl_str} | SL(5%): {t_snap['sl_price']} | TP(5%): {t_snap['tp_price']}",
            "badge_text": f"TEST: {pnl_str}",
            "badge_class": "status-success" if pnl_usd >= 0 else "status-warn"
        })

    sync_pnl_summary(dashboard_data, data_lock)

def scan_all_whales(bybit_client, binance_client, dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    global last_bn_err_log, last_bb_err_log, last_bn_24h_fetch, binance_24h_cache
    now_ts = time.time()
    now_ist_str = datetime.now(IST).strftime('%H:%M:%S IST')

    live_prices_map = {}

    # 1. Fetch Bybit All USDT Linear Tickers (1s Live Price + 24h Volume)
    bybit_rows = []
    try:
        bb_list = bybit_client.get_tickers(category="linear").get("result", {}).get("list", [])
        for t in bb_list:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            try:
                price = float(t.get("lastPrice", 0))
                vol24 = float(t.get("turnover24h", 0))
                pct24 = float(t.get("price24hPcnt", 0)) * 100.0
                if price > 0:
                    live_prices_map[f"BYBIT:{sym}"] = price
                    if vol24 > 500_000:
                        bybit_rows.append(("BYBIT", sym, price, vol24, pct24, True))
            except ValueError:
                continue
    except Exception as e:
        if now_ts - last_bb_err_log > 30:
            last_bb_err_log = now_ts
            add_ui_log(f"⚠️ [Whale] Bybit Scan Warning: {e}")

    # 2. Fetch Binance 24h Volume every 3s (Rate-limit safe) + 1s Live Ticker Prices
    has_fresh_bn_vol = False
    if now_ts - last_bn_24h_fetch >= 3.0 or not binance_24h_cache:
        try:
            bn_24h_list = binance_client.ticker_24hr_price_change()
            new_cache = {}
            for t in bn_24h_list:
                sym = t.get("symbol", "")
                if sym.endswith("USDT"):
                    try:
                        new_cache[sym] = {
                            "vol24": float(t.get("quoteVolume", 0)),
                            "pct24": float(t.get("priceChangePercent", 0))
                        }
                    except ValueError:
                        continue
            if new_cache:
                binance_24h_cache = new_cache
                last_bn_24h_fetch = now_ts
                has_fresh_bn_vol = True
        except Exception as e:
            if now_ts - last_bn_err_log > 30:
                last_bn_err_log = now_ts
                add_ui_log(f"⚠️ [Whale] Binance 24h Vol Warning: {e}")

    binance_rows = []
    try:
        bn_prices = binance_client.ticker_price()
        for t in bn_prices:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            try:
                price = float(t.get("price", 0))
                if price > 0:
                    live_prices_map[f"BINANCE:{sym}"] = price
                    v_info = binance_24h_cache.get(sym)
                    if v_info and v_info["vol24"] > 500_000:
                        binance_rows.append(("BINANCE", sym, price, v_info["vol24"], v_info["pct24"], has_fresh_bn_vol))
            except ValueError:
                continue
    except Exception as e:
        if now_ts - last_bn_err_log > 30:
            last_bn_err_log = now_ts
            add_ui_log(f"⚠️ [Whale] Binance 1s Price Warning: {e}")

    # Immediately update all active virtual test trades & PnL cards every 1 second
    update_live_test_trades_1s(live_prices_map, dashboard_data, data_lock, add_ui_log, add_ledger_entry)

    with data_lock:
        armed_map = dict(dashboard_data['whale'].get('armed_orders', {}))
        active_test_keys = set()
        for lk in ("bybit_test_trades", "binance_test_trades"):
            for t in dashboard_data['whale'].get(lk, []):
                if t.get("status") == "LIVE":
                    active_test_keys.add(f"{t['exchange']}:{t['symbol']}:{t['side']}")

    live_market_snapshot = {}

    # 3. Compute 1-Second Price Velocity & Volume Spike for every coin
    for ex, sym, price, vol24, pct24, vol_updated in (bybit_rows + binance_rows):
        m_key = f"{ex}:{sym}"
        prev = market_memory.get(m_key)

        if not prev:
            est_1s_base = max(200.0, vol24 / 86400.0)
            market_memory[m_key] = {
                "prev_vol": vol24,
                "prev_price": price,
                "avg_delta_1s": est_1s_base,
                "last_vol_ts": now_ts,
                "last_price_ts": now_ts,
                "last_mult": 1.0,
                "last_flow_1s": est_1s_base
            }
            live_market_snapshot[m_key] = {
                "price": price, "delta_vol_1s": est_1s_base, "spike_mult": 1.0,
                "price_diff_1s_pct": 0.0, "pct24": pct24
            }
            continue

        price_diff_1s_pct = ((price - prev["prev_price"]) / prev["prev_price"] * 100.0) if prev["prev_price"] > 0 else 0.0
        prev["prev_price"] = price
        prev["last_price_ts"] = now_ts

        if vol_updated and vol24 > prev["prev_vol"]:
            dt_v = max(1.0, now_ts - prev["last_vol_ts"])
            delta_vol_1s = (vol24 - prev["prev_vol"]) / dt_v
            avg_d = prev["avg_delta_1s"]
            spike_mult = (delta_vol_1s / avg_d) if avg_d > 0 else 1.0
            prev["prev_vol"] = vol24
            prev["last_vol_ts"] = now_ts
            prev["avg_delta_1s"] = max(150.0, (avg_d * 0.90) + (delta_vol_1s * 0.10))
            prev["last_mult"] = spike_mult
            prev["last_flow_1s"] = delta_vol_1s
        else:
            if vol_updated:
                prev["prev_vol"] = vol24
                prev["last_vol_ts"] = now_ts
            delta_vol_1s = prev["last_flow_1s"]
            spike_mult = prev["last_mult"]

        live_market_snapshot[m_key] = {
            "price": price,
            "delta_vol_1s": delta_vol_1s,
            "spike_mult": spike_mult,
            "price_diff_1s_pct": price_diff_1s_pct,
            "pct24": pct24
        }

        # 4. Trigger Silent 60s Background Incubation on Initial Whale Spike
        if delta_vol_1s >= 4_000 and spike_mult >= 2.0:
            if price_diff_1s_pct > 0.01 or (price_diff_1s_pct >= 0 and pct24 > 1.5):
                det_side = "BUY"
            elif price_diff_1s_pct < -0.01 or (price_diff_1s_pct <= 0 and pct24 < -1.5):
                det_side = "SELL"
            else:
                det_side = None

            if det_side:
                s_key = f"{ex}:{sym}:{det_side}"
                if s_key not in incubation_pool and s_key not in locked_signals:
                    prev_exp = expired_history.get(s_key)
                    if prev_exp and (now_ts - prev_exp["expired_ts"]) < 45.0:
                        continue
                    incubation_pool[s_key] = {
                        "exchange": ex,
                        "symbol": sym,
                        "side": det_side,
                        "start_ts": now_ts,
                        "start_price": price,
                        "last_price": price,
                        "ticks_total": 1,
                        "ticks_favorable": 1,
                        "total_inflow": delta_vol_1s,
                        "last_1s_inflow": delta_vol_1s,
                        "mult_window": deque([spike_mult], maxlen=15),
                        "inflow_window": deque([delta_vol_1s], maxlen=15),
                        "tick_window": deque([price_diff_1s_pct], maxlen=15)
                    }

    # 5. Process Silent Incubation Pool (60s Continuous 1s Tracking Before Promotion)
    inc_to_remove = []
    for s_key, inc in list(incubation_pool.items()):
        m_key = f"{inc['exchange']}:{inc['symbol']}"
        snap = live_market_snapshot.get(m_key)
        if not snap:
            continue

        cur_p = snap["price"]
        cur_flow = snap["delta_vol_1s"]
        cur_mult = snap["spike_mult"]
        p_diff = snap["price_diff_1s_pct"]

        inc["ticks_total"] += 1
        inc["total_inflow"] += cur_flow
        inc["last_1s_inflow"] = cur_flow
        inc["mult_window"].append(cur_mult)
        inc["inflow_window"].append(cur_flow)
        inc["tick_window"].append(p_diff)

        if inc["side"] == "BUY" and cur_p >= inc["last_price"]:
            inc["ticks_favorable"] += 1
        elif inc["side"] == "SELL" and cur_p <= inc["last_price"]:
            inc["ticks_favorable"] += 1
        inc["last_price"] = cur_p

        elapsed = now_ts - inc["start_ts"]

        if elapsed > INCUBATION_MAX_SEC:
            inc_to_remove.append(s_key)
            continue

        if elapsed >= INCUBATION_MIN_SEC:
            consistency = inc["ticks_favorable"] / max(1, inc["ticks_total"])
            raw_move = ((cur_p - inc["start_price"]) / inc["start_price"] * 100.0) if inc["start_price"] > 0 else 0.0
            fav_move = raw_move if inc["side"] == "BUY" else -raw_move

            if consistency < 0.50 or fav_move < -0.25 or fav_move > MAX_PRE_MOVE_PCT:
                inc_to_remove.append(s_key)
                continue

            if consistency >= MIN_CONSISTENCY_RATIO and fav_move >= MIN_PRE_MOVE_PCT:
                chance = calculate_smoothed_chance(
                    inc["side"], inc["mult_window"], inc["inflow_window"], inc["tick_window"], fav_move
                )
                # Gate: Only promote into Top 10 list if chance >= 55%
                if chance >= MIN_WIN_CHANCE_PCT:
                    prev_exp = expired_history.get(s_key)
                    is_resurge = False
                    if prev_exp:
                        if chance > prev_exp["peak_chance"] or cur_flow > prev_exp["peak_inflow"]:
                            is_resurge = True
                        else:
                            inc_to_remove.append(s_key)
                            continue

                    locked_signals[s_key] = {
                        "exchange": inc["exchange"],
                        "symbol": inc["symbol"],
                        "side": inc["side"],
                        "anchor_price": cur_p,
                        "price": cur_p,
                        "price_move_pct": round(raw_move, 2),
                        "tracked_sec": int(round(elapsed)),
                        "locked_ts": now_ts,
                        "locked_time": now_ist_str,
                        "last_update_time": now_ist_str,
                        "last_1s_inflow": cur_flow,
                        "total_inflow": inc["total_inflow"],
                        "money_delta_usd": cur_flow - (sum(inc["inflow_window"]) / len(inc["inflow_window"])),
                        "spike_mult": round(sum(inc["mult_window"]) / len(inc["mult_window"]), 1),
                        "chance_pct": chance,
                        "chance_delta": 0,
                        "peak_chance": chance,
                        "peak_inflow": cur_flow,
                        "is_resurge": is_resurge,
                        "tested": False,
                        "real_traded": False,
                        "mult_window": deque(inc["mult_window"], maxlen=15),
                        "inflow_window": deque(inc["inflow_window"], maxlen=15),
                        "tick_window": deque(inc["tick_window"], maxlen=15)
                    }
                    inc_to_remove.append(s_key)

    for k in inc_to_remove:
        incubation_pool.pop(k, None)

    # 6. Update Promoted Locked Signals EVERY 1 SECOND — Guaranteed 2-Minute (120s) Stay!
    locked_to_remove = []
    for s_key, sig in list(locked_signals.items()):
        m_key = f"{sig['exchange']}:{sig['symbol']}"
        snap = live_market_snapshot.get(m_key)
        arm_info = armed_map.get(s_key)
        is_actively_arming = arm_info is not None and arm_info.get("state") == "WAITING_CONFIRM"
        has_live_test = s_key in active_test_keys

        age_sec = now_ts - sig["locked_ts"]

        # Only remove AFTER full 2 minutes (120s) have elapsed
        if age_sec >= LOCK_WINDOW_SEC and not is_actively_arming and not has_live_test:
            expired_history[s_key] = {
                "peak_chance": sig["peak_chance"],
                "peak_inflow": sig["peak_inflow"],
                "expired_ts": now_ts
            }
            locked_to_remove.append(s_key)
            continue

        if snap:
            cur_p = snap["price"]
            cur_1s_flow = snap["delta_vol_1s"]
            cur_mult = snap["spike_mult"]
            p_diff_1s = snap["price_diff_1s_pct"]

            sig["mult_window"].append(cur_mult)
            sig["inflow_window"].append(cur_1s_flow)
            sig["tick_window"].append(p_diff_1s)

            money_delta = cur_1s_flow - sig["last_1s_inflow"]
            sig["total_inflow"] += cur_1s_flow
            sig["last_1s_inflow"] = cur_1s_flow
            sig["money_delta_usd"] = money_delta
            sig["price"] = cur_p
            sig["last_update_time"] = now_ist_str
            sig["spike_mult"] = round(sum(sig["mult_window"]) / len(sig["mult_window"]), 1)

            raw_move = ((cur_p - sig["anchor_price"]) / sig["anchor_price"] * 100.0) if sig["anchor_price"] > 0 else 0.0
            sig["price_move_pct"] = round(raw_move, 2)
            fav_move = raw_move if sig["side"] == "BUY" else -raw_move

            updated_chance = calculate_smoothed_chance(
                sig["side"], sig["mult_window"], sig["inflow_window"], sig["tick_window"], fav_move
            )

            # Update live chance every 1s WITHOUT deleting the coin before 120s!
            sig["chance_delta"] = updated_chance - sig["chance_pct"]
            sig["chance_pct"] = updated_chance
            if updated_chance > sig["peak_chance"]:
                sig["peak_chance"] = updated_chance
            if cur_1s_flow > sig["peak_inflow"]:
                sig["peak_inflow"] = cur_1s_flow

    for k in locked_to_remove:
        locked_signals.pop(k, None)

    # 7. Build 4 Separate Lists (Bybit Buy, Bybit Sell, Binance Buy, Binance Sell) — Strictly Top 10
    buckets = {
        ("BYBIT", "BUY"): [],
        ("BYBIT", "SELL"): [],
        ("BINANCE", "BUY"): [],
        ("BINANCE", "SELL"): []
    }

    for s_key, sig in locked_signals.items():
        arm_info = armed_map.get(s_key)
        is_actively_arming = arm_info is not None and arm_info.get("state") == "WAITING_CONFIRM"
        rem_sec = max(0, int(LOCK_WINDOW_SEC - (now_ts - sig["locked_ts"])))

        m_delta = sig["money_delta_usd"]
        if m_delta >= 0:
            m_delta_str = f"▲ +{format_usd(m_delta)}/s"
            m_delta_up = True
        else:
            m_delta_str = f"▼ -{format_usd(m_delta)}/s"
            m_delta_up = False

        item = {
            "key": s_key,
            "exchange": sig["exchange"],
            "symbol": sig["symbol"],
            "side": sig["side"],
            "price": sig["price"],
            "price_move_pct": sig["price_move_pct"],
            "tracked_sec": sig.get("tracked_sec", 60),
            "locked_time": sig["locked_time"],
            "last_update_time": sig["last_update_time"],
            "remaining_sec": rem_sec,
            "current_inflow_usd": sig["last_1s_inflow"],
            "current_inflow_str": f"{format_usd(sig['last_1s_inflow'])}/s",
            "total_inflow_str": format_usd(sig["total_inflow"]),
            "money_delta_str": m_delta_str,
            "money_delta_up": m_delta_up,
            "spike_mult": sig["spike_mult"],
            "chance_pct": sig["chance_pct"],
            "chance_delta": sig["chance_delta"],
            "is_resurge": sig["is_resurge"],
            "tested": sig.get("tested", False),
            "real_traded": sig.get("real_traded", False),
            "arm_state": arm_info.get("state", "WAITING_CONFIRM") if is_actively_arming else "IDLE",
            "arm_mode_type": arm_info.get("mode", "") if is_actively_arming else ""
        }
        buckets[(sig["exchange"], sig["side"])].append(item)

    final_lists = {}
    keep_keys = set()

    for b_key, items in buckets.items():
        items.sort(key=lambda x: (x["arm_state"] != "IDLE", x["chance_pct"], x["current_inflow_usd"]), reverse=True)
        top10 = items[:TOP_N_LIMIT]
        final_lists[b_key] = top10
        for it in top10:
            keep_keys.add(it["key"])

    for s_key in list(locked_signals.keys()):
        if s_key not in keep_keys and s_key not in armed_map and s_key not in active_test_keys:
            locked_signals.pop(s_key, None)

    bb_count = len(final_lists[("BYBIT", "BUY")]) + len(final_lists[("BYBIT", "SELL")])
    bn_count = len(final_lists[("BINANCE", "BUY")]) + len(final_lists[("BINANCE", "SELL")])
    inc_count = len(incubation_pool)

    with data_lock:
        dashboard_data['whale']['incubating_count'] = inc_count
        dashboard_data['whale']['bybit_buy'] = final_lists[("BYBIT", "BUY")]
        dashboard_data['whale']['bybit_sell'] = final_lists[("BYBIT", "SELL")]
        dashboard_data['whale']['binance_buy'] = final_lists[("BINANCE", "BUY")]
        dashboard_data['whale']['binance_sell'] = final_lists[("BINANCE", "SELL")]
        dashboard_data['whale']['status'] = f"1s LIVE ({inc_count} Watching) | BB: {bb_count} | BN: {bn_count}"

def start_virtual_test_trade(test_id, exchange, symbol, side, entry_price, sl_price, tp_price, entry_time_str, dashboard_data, data_lock, add_ui_log):
    notional_usd = WHALE_MARGIN_USD * WHALE_LEVERAGE  # $1.0 * 6x = $6.0
    virtual_qty = notional_usd / entry_price if entry_price > 0 else 0.0
    list_key = "bybit_test_trades" if exchange == "BYBIT" else "binance_test_trades"

    test_record = {
        "id": test_id,
        "exchange": exchange,
        "symbol": symbol,
        "side": side,
        "entry_time": entry_time_str,
        "entry_price": entry_price,
        "live_price": entry_price,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "virtual_qty": virtual_qty,
        "pnl_usd": 0.0,
        "price_pct": 0.0,
        "roi_pct": 0.0,
        "exit_time": "--",
        "status": "LIVE",
        "outcome": "TRACKING 1s LIVE...",
        "force_close": False
    }

    with data_lock:
        t_list = dashboard_data['whale'].setdefault(list_key, [])
        t_list.insert(0, test_record)
        if len(t_list) > 20:
            t_list.pop()

    add_ui_log(f"🧪⚡ [TEST {exchange}] Instant Virtual Entry: {symbol} ({side}) @ {entry_price} | SL(5%): {sl_price} | TP(5%): {tp_price}")

def monitor_real_whale_position(exchange, symbol, side, actual_entry, qty_float, sl_str, tp_str, entry_time_str, bybit_client, binance_client, dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    deadline = time.time() + (4 * 3600)
    while time.time() < deadline:
        time.sleep(2.5)
        try:
            if exchange == "BYBIT":
                pos_list = bybit_client.get_positions(category="linear", symbol=symbol)["result"]["list"]
                pos_size = float(pos_list[0].get("size", 0)) if pos_list else 0.0
                if pos_size <= 0:
                    try:
                        bybit_client.cancel_all_orders(category="linear", symbol=symbol)
                    except Exception:
                        pass
                    pnl_val = 0.0
                    try:
                        closed = bybit_client.get_closed_pnl(category="linear", symbol=symbol, limit=1)["result"]["list"]
                        if closed:
                            pnl_val = float(closed[0].get("closedPnl", 0))
                    except Exception:
                        pass
                    with pnl_lock:
                        realized_pnl_state["bybit_real_closed"] += pnl_val
                        if pnl_val >= 0:
                            realized_pnl_state["bybit_wins"] += 1
                        else:
                            realized_pnl_state["bybit_losses"] += 1
                    sync_pnl_summary(dashboard_data, data_lock)
                    sign = "+" if pnl_val >= 0 else ""
                    exit_t = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
                    add_ui_log(f"🏁 [REAL BYBIT] {symbol} ({side}) Closed | Realized PnL: {sign}${pnl_val:.3f}")
                    add_ledger_entry({
                        "exchange": "Whale-BYBIT",
                        "coin": f"{symbol} [{side} - REAL CLOSED]",
                        "rate": f"{sign}${pnl_val:.3f}",
                        "entry_time": f"{entry_time_str} (@ {actual_entry})",
                        "exit_time": exit_t,
                        "detail": f"[BYBIT REAL] SL(5%): {sl_str} | TP(5%): {tp_str} | Realized PnL: {sign}${pnl_val:.3f}",
                        "badge_text": f"REAL PNL: {sign}${pnl_val:.3f}",
                        "badge_class": "status-success" if pnl_val >= 0 else "status-warn"
                    })
                    break
            else:
                positions = binance_client.get_position_risk(symbol=symbol)
                pos_size = 0.0
                for p in positions:
                    if abs(float(p.get("positionAmt", 0))) > 0:
                        pos_size = abs(float(p.get("positionAmt")))
                        break
                if pos_size <= 0:
                    try:
                        binance_client.cancel_open_orders(symbol=symbol)
                    except Exception:
                        pass
                    cur_p = float(binance_client.ticker_price(symbol=symbol)["price"])
                    pnl_val = ((cur_p - actual_entry) * qty_float) if side == "BUY" else ((actual_entry - cur_p) * qty_float)
                    with pnl_lock:
                        realized_pnl_state["binance_real_closed"] += pnl_val
                        if pnl_val >= 0:
                            realized_pnl_state["binance_wins"] += 1
                        else:
                            realized_pnl_state["binance_losses"] += 1
                    sync_pnl_summary(dashboard_data, data_lock)
                    sign = "+" if pnl_val >= 0 else ""
                    exit_t = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
                    add_ui_log(f"🏁 [REAL BINANCE] {symbol} ({side}) Closed @ {cur_p} | Realized PnL: {sign}${pnl_val:.3f}")
                    add_ledger_entry({
                        "exchange": "Whale-BINANCE",
                        "coin": f"{symbol} [{side} - REAL CLOSED]",
                        "rate": f"{sign}${pnl_val:.3f}",
                        "entry_time": f"{entry_time_str} (@ {actual_entry})",
                        "exit_time": f"{exit_t} (@ {cur_p})",
                        "detail": f"[BINANCE REAL] SL(5%): {sl_str} | TP(5%): {tp_str} | Realized PnL: {sign}${pnl_val:.3f}",
                        "badge_text": f"REAL PNL: {sign}${pnl_val:.3f}",
                        "badge_class": "status-success" if pnl_val >= 0 else "status-warn"
                    })
                    break
        except Exception:
            pass

def execute_whale_armed_trade(exchange, symbol, side, anchor_price, mode, dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    """Zero-delay instant execution: coin is already verified over 60s, so execute immediately on click."""
    key = f"{exchange}:{symbol}:{side}"
    bybit_client = HTTP(testnet=False, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)
    binance_client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)

    def get_live_tick():
        for attempt in range(2):
            try:
                if exchange == "BYBIT":
                    tk = bybit_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
                    return float(tk["lastPrice"])
                else:
                    return float(binance_client.ticker_price(symbol=symbol)["price"])
            except Exception:
                if attempt == 0:
                    time.sleep(0.08)
        return anchor_price

    try:
        # 1. Fetch instant live market tick (~100ms, no 15s wait loop)
        exec_price = get_live_tick()
        if exec_price <= 0:
            raise RuntimeError("Unable to fetch live market price")

        entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

        # ==========================================================
        # BRANCH A: INSTANT VIRTUAL TEST EXECUTION
        # ==========================================================
        if mode == "TEST":
            if side == "BUY":
                sl_p = round(exec_price * (1.0 - WHALE_SL_PCT), 6)
                tp_p = round(exec_price * (1.0 + WHALE_TP_PCT), 6)
            else:
                sl_p = round(exec_price * (1.0 + WHALE_SL_PCT), 6)
                tp_p = round(exec_price * (1.0 - WHALE_TP_PCT), 6)

            if key in locked_signals:
                locked_signals[key]["tested"] = True
            with data_lock:
                dashboard_data['whale'].get('armed_orders', {}).pop(key, None)

            test_id = f"TEST-{exchange}-{int(time.time() * 1000)}"
            start_virtual_test_trade(
                test_id, exchange, symbol, side, exec_price, sl_p, tp_p, entry_time_str,
                dashboard_data, data_lock, add_ui_log
            )
            return

        # ==========================================================
        # BRANCH B: INSTANT REAL TRADING EXECUTION (±5% SL/TP)
        # ==========================================================
        target_notional = WHALE_MARGIN_USD * WHALE_LEVERAGE

        if exchange == "BYBIT":
            inst = bybit_client.get_instruments_info(category="linear", symbol=symbol)["result"]["list"][0]
            max_lev = float(inst.get("leverageFilter", {}).get("maxLeverage", 6.0))
            applied_lev = min(float(WHALE_LEVERAGE), max_lev)
            qty_step = float(inst["lotSizeFilter"]["qtyStep"])
            min_qty = float(inst["lotSizeFilter"]["minOrderQty"])
            min_notional = float(inst["lotSizeFilter"].get("minNotionalValue", 5.0))
            tick_size = float(inst.get("priceFilter", {}).get("tickSize", "0.0001"))

            try:
                bybit_client.set_leverage(category="linear", symbol=symbol, buyLeverage=str(applied_lev), sellLeverage=str(applied_lev))
            except Exception:
                pass

            q_dec = len(f"{qty_step:.8f}".rstrip("0").split(".")[1]) if "." in f"{qty_step:.8f}".rstrip("0") else 0
            p_dec = len(f"{tick_size:.8f}".rstrip("0").split(".")[1]) if "." in f"{tick_size:.8f}".rstrip("0") else 2

            calc_qty = math.floor((target_notional / exec_price) / qty_step) * qty_step
            if calc_qty < min_qty:
                calc_qty = min_qty
            if (calc_qty * exec_price) < min_notional:
                calc_qty = math.ceil((min_notional / exec_price) / qty_step) * qty_step
            qty_str = f"{calc_qty:.{q_dec}f}" if q_dec > 0 else str(int(calc_qty))

            order_side = "Buy" if side == "BUY" else "Sell"
            close_side = "Sell" if side == "BUY" else "Buy"

            resp = bybit_client.place_order(category="linear", symbol=symbol, side=order_side, orderType="Market", qty=qty_str, positionIdx=0)
            if resp.get("retCode") != 0:
                raise RuntimeError(resp.get("retMsg", "Bybit Order Rejected"))

            time.sleep(0.1)
            actual_entry = exec_price
            qty_float = float(qty_str)
            pos_list = bybit_client.get_positions(category="linear", symbol=symbol)["result"]["list"]
            if pos_list and float(pos_list[0].get("size", 0)) > 0:
                qty_float = float(pos_list[0]['size'])
                qty_str = f"{qty_float:.{q_dec}f}" if q_dec > 0 else str(int(qty_float))
                if float(pos_list[0].get("avgPrice", 0)) > 0:
                    actual_entry = float(pos_list[0]["avgPrice"])

            if side == "BUY":
                sl_p = max(tick_size, round(math.floor((actual_entry * (1.0 - WHALE_SL_PCT)) / tick_size) * tick_size, p_dec))
                tp_p = round(math.ceil((actual_entry * (1.0 + WHALE_TP_PCT)) / tick_size) * tick_size, p_dec)
            else:
                sl_p = round(math.ceil((actual_entry * (1.0 + WHALE_SL_PCT)) / tick_size) * tick_size, p_dec)
                tp_p = max(tick_size, round(math.floor((actual_entry * (1.0 - WHALE_TP_PCT)) / tick_size) * tick_size, p_dec))

            sl_str = f"{sl_p:.{p_dec}f}"
            tp_str = f"{tp_p:.{p_dec}f}"

            bybit_client.set_trading_stop(category="linear", symbol=symbol, stopLoss=sl_str, slTriggerBy="LastPrice", positionIdx=0)
            bybit_client.place_order(category="linear", symbol=symbol, side=close_side, orderType="Limit", price=tp_str, qty=qty_str, timeInForce="GTC", reduceOnly=True, positionIdx=0)

        else:
            ex_info = binance_client.exchange_info()
            sym_info = next((s for s in ex_info.get('symbols', []) if s['symbol'] == symbol), None)
            qty_step, min_qty, tick_size, min_notional = 1.0, 1.0, 0.0001, 5.0
            if sym_info:
                for f in sym_info.get('filters', []):
                    if f['filterType'] == 'LOT_SIZE':
                        qty_step = float(f.get('stepSize', 1.0))
                        min_qty = float(f.get('minQty', 1.0))
                    elif f['filterType'] == 'PRICE_FILTER':
                        tick_size = float(f.get('tickSize', 0.0001))
                    elif f['filterType'] == 'MIN_NOTIONAL':
                        min_notional = float(f.get('notional', 5.0))

            applied_lev = WHALE_LEVERAGE
            try:
                binance_client.change_leverage(symbol=symbol, leverage=int(applied_lev))
            except Exception:
                pass

            q_dec = len(f"{qty_step:.8f}".rstrip("0").split(".")[1]) if "." in f"{qty_step:.8f}".rstrip("0") else 0
            p_dec = len(f"{tick_size:.8f}".rstrip("0").split(".")[1]) if "." in f"{tick_size:.8f}".rstrip("0") else 2

            calc_qty = math.floor((target_notional / exec_price) / qty_step) * qty_step
            if calc_qty < min_qty:
                calc_qty = min_qty
            if (calc_qty * exec_price) < min_notional:
                calc_qty = math.ceil((min_notional / exec_price) / qty_step) * qty_step
            qty_str = f"{calc_qty:.{q_dec}f}" if q_dec > 0 else str(int(calc_qty))
            qty_float = float(qty_str)

            order_side = "BUY" if side == "BUY" else "SELL"
            close_side = "SELL" if side == "BUY" else "BUY"

            resp = binance_client.new_order(symbol=symbol, side=order_side, type="MARKET", quantity=qty_str)
            actual_entry = float(resp.get("avgPrice", 0)) or exec_price
            if actual_entry == exec_price:
                for p in binance_client.get_position_risk(symbol=symbol):
                    if abs(float(p.get("positionAmt", 0))) > 0 and float(p.get("entryPrice", 0)) > 0:
                        actual_entry = float(p["entryPrice"])
                        qty_float = abs(float(p['positionAmt']))
                        qty_str = f"{qty_float:.{q_dec}f}" if q_dec > 0 else str(int(qty_float))
                        break

            if side == "BUY":
                sl_p = max(tick_size, round(math.floor((actual_entry * (1.0 - WHALE_SL_PCT)) / tick_size) * tick_size, p_dec))
                tp_p = round(math.ceil((actual_entry * (1.0 + WHALE_TP_PCT)) / tick_size) * tick_size, p_dec)
            else:
                sl_p = round(math.ceil((actual_entry * (1.0 + WHALE_SL_PCT)) / tick_size) * tick_size, p_dec)
                tp_p = max(tick_size, round(math.floor((actual_entry * (1.0 - WHALE_TP_PCT)) / tick_size) * tick_size, p_dec))

            sl_str = f"{sl_p:.{p_dec}f}"
            tp_str = f"{tp_p:.{p_dec}f}"

            binance_client.new_order(symbol=symbol, side=close_side, type="STOP_MARKET", stopPrice=sl_str, closePosition=True, workingType="CONTRACT_PRICE")
            binance_client.new_order(symbol=symbol, side=close_side, type="LIMIT", quantity=qty_str, price=tp_str, timeInForce="GTC", reduceOnly=True)

        if key in locked_signals:
            locked_signals[key]["real_traded"] = True
        with data_lock:
            dashboard_data['whale'].get('armed_orders', {}).pop(key, None)

        threading.Thread(
            target=monitor_real_whale_position,
            args=(exchange, symbol, side, actual_entry, qty_float, sl_str, tp_str, entry_time_str, bybit_client, binance_client, dashboard_data, data_lock, add_ui_log, add_ledger_entry),
            daemon=True
        ).start()

        add_ui_log(f"🚀⚡ [Whale REAL {exchange}] {symbol} ({side}) INSTANT ENTRY @ {actual_entry} | SL(5%): {sl_str} | TP(5%): {tp_str}")
        add_ledger_entry({
            "exchange": f"Whale-{exchange}",
            "coin": f"{symbol} [{side} - REAL]",
            "rate": "✓ CONFIRMED",
            "entry_time": f"{entry_time_str} (@ {actual_entry})",
            "exit_time": "Active (±5% Bracket)",
            "detail": f"[{exchange} REAL] Margin: $1.0 (6x) | Qty: {qty_str} | SL(5%): {sl_str} | TP Limit(5%): {tp_str}",
            "badge_text": "✓ REAL ACTIVE",
            "badge_class": "status-success"
        })

    except Exception as e:
        with data_lock:
            dashboard_data['whale'].get('armed_orders', {}).pop(key, None)
        add_ui_log(f"❌ [Whale {mode}] [{exchange} {symbol}] Execution Failed: {e}")

def start_whale_scanner_loop(dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    bybit_client = HTTP(testnet=False, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)
    binance_client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)
    add_ui_log("🐋 Whale Scanner Active (60s Pre-Track | 2m Guaranteed Lock | Instant REAL & TEST Entry)")

    while True:
        loop_start = time.time()
        try:
            scan_all_whales(bybit_client, binance_client, dashboard_data, data_lock, add_ui_log, add_ledger_entry)
        except Exception as e:
            add_ui_log(f"⚠️ [Whale] Scanner Recovered: {e}")

        elapsed = time.time() - loop_start
        sleep_time = max(0.15, SCAN_INTERVAL_SEC - elapsed)
        time.sleep(sleep_time)
