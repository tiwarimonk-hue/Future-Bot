import os
import time
import math
import threading
from datetime import datetime, timezone, timedelta
from pybit.unified_trading import HTTP
from binance.um_futures import UMFutures

IST = timezone(timedelta(hours=5, minutes=30))

BYBIT_API_KEY = os.environ.get('BYBIT_API_KEY', 'YOUR_BYBIT_API_KEY')
BYBIT_API_SECRET = os.environ.get('BYBIT_API_SECRET', 'YOUR_BYBIT_API_SECRET')
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', 'YOUR_BINANCE_API_KEY')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', 'YOUR_BINANCE_API_SECRET')

# WHALE EXECUTION & LOCK PARAMETERS
WHALE_MARGIN_USD = 1.0          # $1.0 Margin
WHALE_LEVERAGE = 6              # 6x Leverage ($6.0 Notional)
WHALE_SL_PCT = 0.05             # 5% Stop Loss
WHALE_TP_PCT = 0.05             # 5% Take Profit
LOCK_WINDOW_SEC = 120.0         # 2 Minutes lock window if not traded
SCAN_INTERVAL_SEC = 5.0         # 5 Seconds live fetch & update cycle
TOP_N_LIMIT = 10                # Strictly Top 10 per list

market_memory = {}
locked_signals = {}
expired_history = {}

last_bn_err_log = 0.0
last_bb_err_log = 0.0

def format_usd(val):
    abs_v = abs(val)
    if abs_v >= 1_000_000:
        return f"${abs_v / 1_000_000:.2f}M"
    elif abs_v >= 1_000:
        return f"${abs_v / 1_000:.1f}K"
    return f"${abs_v:.0f}"

def scan_all_whales(bybit_client, binance_client, dashboard_data, data_lock, add_ui_log):
    global last_bn_err_log, last_bb_err_log
    now_ts = time.time()
    now_ist_str = datetime.now(IST).strftime('%H:%M:%S IST')

    # 1. Fetch Bybit All USDT Linear Tickers
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
                high24 = float(t.get("highPrice24h", price))
                low24 = float(t.get("lowPrice24h", price))
                if price > 0 and vol24 > 500_000:
                    bybit_rows.append(("BYBIT", sym, price, vol24, pct24, high24, low24))
            except ValueError:
                continue
    except Exception as e:
        if now_ts - last_bb_err_log > 30:
            last_bb_err_log = now_ts
            add_ui_log(f"⚠️ [Whale] Bybit Scan Warning: {e}")

    # 2. Fetch Binance All USDT Futures 24h Tickers
    binance_rows = []
    try:
        bn_list = binance_client.ticker_24hr_price_change()
        for t in bn_list:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            try:
                price = float(t.get("lastPrice", 0))
                vol24 = float(t.get("quoteVolume", 0))
                pct24 = float(t.get("priceChangePercent", 0))
                high24 = float(t.get("highPrice", price))
                low24 = float(t.get("lowPrice", price))
                if price > 0 and vol24 > 500_000:
                    binance_rows.append(("BINANCE", sym, price, vol24, pct24, high24, low24))
            except ValueError:
                continue
    except Exception as e:
        if now_ts - last_bn_err_log > 30:
            last_bn_err_log = now_ts
            add_ui_log(f"⚠️ [Whale] Binance Scan Warning: {e}")

    with data_lock:
        armed_map = dict(dashboard_data['whale'].get('armed_orders', {}))

    live_market_snapshot = {}

    for ex, sym, price, vol24, pct24, high24, low24 in (bybit_rows + binance_rows):
        m_key = f"{ex}:{sym}"
        prev = market_memory.get(m_key)

        if not prev:
            est_5s_base = max(800.0, vol24 / 17280.0)
            market_memory[m_key] = {
                "prev_vol": vol24,
                "prev_price": price,
                "avg_delta_5s": est_5s_base,
                "last_ts": now_ts
            }
            live_market_snapshot[m_key] = {
                "price": price, "delta_vol_5s": est_5s_base, "spike_mult": 1.0,
                "price_diff_5s_pct": 0.0, "pct24": pct24, "vol24": vol24
            }

            rng = max(1e-9, high24 - low24)
            pos_in_range = (price - low24) / rng
            if vol24 >= 20_000_000 and abs(pct24) >= 3.5:
                seed_side = "BUY" if (pct24 > 0 and pos_in_range >= 0.55) else ("SELL" if (pct24 < 0 and pos_in_range <= 0.45) else None)
                if seed_side:
                    seed_flow = vol24 / 2880.0
                    seed_mult = round(min(10.0, 2.4 + abs(pct24) / 4.5), 1)
                    seed_chance = min(92, max(62, int(58 + abs(pct24) * 1.7 + min(12, vol24 / 25_000_000))))
                    s_key = f"{ex}:{sym}:{seed_side}"
                    locked_signals[s_key] = {
                        "exchange": ex,
                        "symbol": sym,
                        "side": seed_side,
                        "anchor_price": price,
                        "price": price,
                        "price_move_pct": 0.0,
                        "locked_ts": now_ts,
                        "locked_time": now_ist_str,
                        "last_update_time": now_ist_str,
                        "last_5s_inflow": seed_flow,
                        "total_inflow": seed_flow,
                        "money_delta_usd": 0.0,
                        "spike_mult": seed_mult,
                        "chance_pct": seed_chance,
                        "chance_delta": 0,
                        "peak_chance": seed_chance,
                        "peak_inflow": seed_flow,
                        "is_resurge": False
                    }
            continue

        dt = max(1.0, now_ts - prev["last_ts"])
        raw_delta_vol = max(0.0, vol24 - prev["prev_vol"])
        delta_vol_5s = raw_delta_vol * (SCAN_INTERVAL_SEC / dt)
        price_diff_5s_pct = ((price - prev["prev_price"]) / prev["prev_price"] * 100.0) if prev["prev_price"] > 0 else 0.0

        avg_d = prev["avg_delta_5s"]
        spike_mult = (delta_vol_5s / avg_d) if avg_d > 0 else 1.0

        prev["prev_vol"] = vol24
        prev["prev_price"] = price
        prev["avg_delta_5s"] = max(400.0, (avg_d * 0.90) + (delta_vol_5s * 0.10))
        prev["last_ts"] = now_ts

        live_market_snapshot[m_key] = {
            "price": price,
            "delta_vol_5s": delta_vol_5s,
            "spike_mult": spike_mult,
            "price_diff_5s_pct": price_diff_5s_pct,
            "pct24": pct24,
            "vol24": vol24
        }

        if delta_vol_5s >= 20_000 and spike_mult >= 2.1:
            if price_diff_5s_pct > 0.02 or (price_diff_5s_pct >= 0 and pct24 > 1.8):
                det_side = "BUY"
            elif price_diff_5s_pct < -0.02 or (price_diff_5s_pct <= 0 and pct24 < -1.8):
                det_side = "SELL"
            else:
                det_side = None

            if det_side:
                s_key = f"{ex}:{sym}:{det_side}"
                raw_score = 56.0 + min(22.0, spike_mult * 3.0) + min(12.0, delta_vol_5s / 150_000.0) + min(9.0, abs(price_diff_5s_pct) * 16.0)
                new_chance = max(60, min(98, int(round(raw_score))))

                if s_key not in locked_signals:
                    prev_exp = expired_history.get(s_key)
                    is_resurge = False
                    if prev_exp:
                        if new_chance > prev_exp["peak_chance"] or delta_vol_5s > prev_exp["peak_inflow"]:
                            is_resurge = True
                        elif (now_ts - prev_exp["expired_ts"]) < 90.0:
                            continue

                    locked_signals[s_key] = {
                        "exchange": ex,
                        "symbol": sym,
                        "side": det_side,
                        "anchor_price": price,
                        "price": price,
                        "price_move_pct": 0.0,
                        "locked_ts": now_ts,
                        "locked_time": now_ist_str,
                        "last_update_time": now_ist_str,
                        "last_5s_inflow": delta_vol_5s,
                        "total_inflow": delta_vol_5s,
                        "money_delta_usd": delta_vol_5s,
                        "spike_mult": round(spike_mult, 1),
                        "chance_pct": new_chance,
                        "chance_delta": 0,
                        "peak_chance": new_chance,
                        "peak_inflow": delta_vol_5s,
                        "is_resurge": is_resurge
                    }

    keys_to_remove = []
    for s_key, sig in list(locked_signals.items()):
        m_key = f"{sig['exchange']}:{sig['symbol']}"
        snap = live_market_snapshot.get(m_key)
        arm_info = armed_map.get(s_key)
        is_traded_or_armed = arm_info is not None and arm_info.get("state") in ("WAITING_CONFIRM", "CONFIRMED", "EXECUTED")

        age_sec = now_ts - sig["locked_ts"]

        if age_sec >= LOCK_WINDOW_SEC and not is_traded_or_armed:
            expired_history[s_key] = {
                "peak_chance": sig["peak_chance"],
                "peak_inflow": sig["peak_inflow"],
                "expired_ts": now_ts
            }
            keys_to_remove.append(s_key)
            continue

        if snap:
            cur_p = snap["price"]
            cur_5s_flow = snap["delta_vol_5s"]
            cur_mult = snap["spike_mult"]
            p_diff_5s = snap["price_diff_5s_pct"]

            money_delta = cur_5s_flow - sig["last_5s_inflow"]
            sig["total_inflow"] += cur_5s_flow
            sig["last_5s_inflow"] = cur_5s_flow
            sig["money_delta_usd"] = money_delta
            sig["price"] = cur_p
            sig["last_update_time"] = now_ist_str
            sig["spike_mult"] = round(cur_mult, 1)

            raw_move = ((cur_p - sig["anchor_price"]) / sig["anchor_price"] * 100.0) if sig["anchor_price"] > 0 else 0.0
            sig["price_move_pct"] = round(raw_move, 2)

            favorable_move = raw_move if sig["side"] == "BUY" else -raw_move
            favorable_tick = p_diff_5s if sig["side"] == "BUY" else -p_diff_5s

            accel_bonus = 6.0 if money_delta > 0 else -5.0
            updated_score = (
                50.0
                + min(22.0, cur_mult * 2.8)
                + min(12.0, cur_5s_flow / 140_000.0)
                + max(-12.0, min(14.0, favorable_move * 8.0))
                + max(-6.0, min(8.0, favorable_tick * 15.0))
                + accel_bonus
            )
            updated_chance = max(25, min(99, int(round(updated_score))))

            sig["chance_delta"] = updated_chance - sig["chance_pct"]
            sig["chance_pct"] = updated_chance
            if updated_chance > sig["peak_chance"]:
                sig["peak_chance"] = updated_chance
            if cur_5s_flow > sig["peak_inflow"]:
                sig["peak_inflow"] = cur_5s_flow

    for k in keys_to_remove:
        locked_signals.pop(k, None)

    buckets = {
        ("BYBIT", "BUY"): [],
        ("BYBIT", "SELL"): [],
        ("BINANCE", "BUY"): [],
        ("BINANCE", "SELL"): []
    }

    for s_key, sig in locked_signals.items():
        arm_info = armed_map.get(s_key)
        rem_sec = max(0, int(LOCK_WINDOW_SEC - (now_ts - sig["locked_ts"])))

        m_delta = sig["money_delta_usd"]
        if m_delta >= 0:
            m_delta_str = f"▲ +{format_usd(m_delta)}"
            m_delta_up = True
        else:
            m_delta_str = f"▼ -{format_usd(m_delta)}"
            m_delta_up = False

        item = {
            "key": s_key,
            "exchange": sig["exchange"],
            "symbol": sig["symbol"],
            "side": sig["side"],
            "price": sig["price"],
            "price_move_pct": sig["price_move_pct"],
            "locked_time": sig["locked_time"],
            "last_update_time": sig["last_update_time"],
            "remaining_sec": rem_sec,
            "current_inflow_usd": sig["last_5s_inflow"],
            "current_inflow_str": format_usd(sig["last_5s_inflow"]),
            "total_inflow_str": format_usd(sig["total_inflow"]),
            "money_delta_str": m_delta_str,
            "money_delta_up": m_delta_up,
            "spike_mult": sig["spike_mult"],
            "chance_pct": sig["chance_pct"],
            "chance_delta": sig["chance_delta"],
            "is_resurge": sig["is_resurge"],
            "arm_state": arm_info.get("state", "WAITING_CONFIRM") if arm_info else "IDLE",
            "arm_mode_type": arm_info.get("mode", "") if arm_info else "",
            "confirmed": arm_info.get("confirmed", False) if arm_info else False
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
        if s_key not in keep_keys and s_key not in armed_map:
            locked_signals.pop(s_key, None)

    bb_count = len(final_lists[("BYBIT", "BUY")]) + len(final_lists[("BYBIT", "SELL")])
    bn_count = len(final_lists[("BINANCE", "BUY")]) + len(final_lists[("BINANCE", "SELL")])

    with data_lock:
        dashboard_data['whale']['bybit_buy'] = final_lists[("BYBIT", "BUY")]
        dashboard_data['whale']['bybit_sell'] = final_lists[("BYBIT", "SELL")]
        dashboard_data['whale']['binance_buy'] = final_lists[("BINANCE", "BUY")]
        dashboard_data['whale']['binance_sell'] = final_lists[("BINANCE", "SELL")]
        dashboard_data['whale']['status'] = f"5s LIVE LOCK | Bybit: {bb_count} | Binance: {bn_count}"

def run_virtual_test_monitor(test_id, exchange, symbol, side, entry_price, sl_price, tp_price, entry_time_str, bybit_client, binance_client, dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    notional_usd = WHALE_MARGIN_USD * WHALE_LEVERAGE  # $1.0 * 6x = $6.0
    virtual_qty = notional_usd / entry_price if entry_price > 0 else 0.0

    def get_live_tick():
        if exchange == "BYBIT":
            tk = bybit_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
            return float(tk["lastPrice"])
        else:
            return float(binance_client.ticker_price(symbol=symbol)["price"])

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
        "pnl_usd": 0.0,
        "price_pct": 0.0,
        "roi_pct": 0.0,
        "exit_time": "--",
        "status": "LIVE",
        "outcome": "TRACKING LIVE...",
        "force_close": False
    }

    with data_lock:
        t_list = dashboard_data['whale'].setdefault('test_trades', [])
        t_list.insert(0, test_record)
        if len(t_list) > 25:
            t_list.pop()

    add_ui_log(f"🧪 [TEST VIRTUAL] Started Live Feed Tracking: {exchange} {symbol} ({side}) @ {entry_price} | SL(5%): {sl_price} | TP(5%): {tp_price}")

    while True:
        time.sleep(0.5)
        try:
            cur_p = get_live_tick()
        except Exception:
            continue

        if side == "BUY":
            price_pct = ((cur_p - entry_price) / entry_price) * 100.0
            pnl_usd = (cur_p - entry_price) * virtual_qty
        else:
            price_pct = ((entry_price - cur_p) / entry_price) * 100.0
            pnl_usd = (entry_price - cur_p) * virtual_qty

        roi_pct = (pnl_usd / WHALE_MARGIN_USD) * 100.0
        hit_status = None
        exit_p = cur_p

        with data_lock:
            force_c = test_record.get('force_close', False)

        if force_c:
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

        with data_lock:
            test_record["live_price"] = round(exit_p, 8)
            test_record["pnl_usd"] = round(pnl_usd, 4)
            test_record["price_pct"] = round(price_pct, 2)
            test_record["roi_pct"] = round(roi_pct, 1)

            if hit_status:
                exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
                test_record["exit_time"] = exit_time_str
                test_record["status"] = "CLOSED"
                test_record["outcome"] = hit_status

        if hit_status:
            sign = "+" if pnl_usd >= 0 else ""
            pnl_str = f"{sign}${pnl_usd:.3f} ({sign}{roi_pct:.1f}% ROI)"
            add_ui_log(f"🧪🏁 [TEST VIRTUAL] {exchange} {symbol} ({side}) {hit_status} @ {exit_p} | PnL: {pnl_str}")
            add_ledger_entry({
                "exchange": "Whale",
                "coin": f"{symbol} [{exchange} {side} - TEST]",
                "rate": f"🧪 {hit_status}",
                "entry_time": f"{entry_time_str} (@ {entry_price})",
                "exit_time": f"{exit_time_str} (@ {exit_p})",
                "detail": f"[VIRTUAL PAPER] Margin: $1.0 (6x) | Net PnL: {pnl_str} | SL(5%): {sl_price} | TP(5%): {tp_price}",
                "badge_text": f"TEST: {pnl_str}",
                "badge_class": "status-success" if pnl_usd >= 0 else "status-warn"
            })
            break

def execute_whale_armed_trade(exchange, symbol, side, anchor_price, mode, dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    key = f"{exchange}:{symbol}:{side}"
    bybit_client = HTTP(testnet=False, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)
    binance_client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)

    def get_live_tick():
        if exchange == "BYBIT":
            tk = bybit_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
            return float(tk["lastPrice"])
        else:
            return float(binance_client.ticker_price(symbol=symbol)["price"])

    try:
        if anchor_price <= 0:
            anchor_price = get_live_tick()

        # 1. WAIT FOR 1 LIVE CONFIRMATION TICK
        confirmed = False
        confirm_price = anchor_price
        deadline = time.time() + 15.0
        ticks_checked = 0

        while time.time() < deadline:
            with data_lock:
                if key not in dashboard_data['whale'].get('armed_orders', {}):
                    return

            time.sleep(0.35)
            try:
                cur_p = get_live_tick()
                ticks_checked += 1
                if side == "BUY" and (cur_p > anchor_price or (ticks_checked >= 3 and cur_p >= anchor_price)):
                    confirmed = True
                    confirm_price = cur_p
                    break
                elif side == "SELL" and (cur_p < anchor_price or (ticks_checked >= 3 and cur_p <= anchor_price)):
                    confirmed = True
                    confirm_price = cur_p
                    break
            except Exception:
                pass

        if not confirmed:
            with data_lock:
                dashboard_data['whale'].get('armed_orders', {}).pop(key, None)
            add_ui_log(f"⚠️ [Whale] [{exchange} {symbol}] Confirmation Tick Timed Out. Disarmed.")
            return

        with data_lock:
            if key in dashboard_data['whale'].get('armed_orders', {}):
                dashboard_data['whale']['armed_orders'][key]['state'] = 'CONFIRMED'
                dashboard_data['whale']['armed_orders'][key]['confirmed'] = True

        entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
        add_ui_log(f"✅✓ [Whale {mode}] [{exchange} {symbol} {side}] CONFIRMATION TICK PASSED ({anchor_price} -> {confirm_price})!")

        # ==========================================================
        # BRANCH A: TEST MODE (100% LIVE MARKET VIRTUAL PAPER TRADE)
        # ==========================================================
        if mode == "TEST":
            if side == "BUY":
                sl_p = round(confirm_price * (1.0 - WHALE_SL_PCT), 6)
                tp_p = round(confirm_price * (1.0 + WHALE_TP_PCT), 6)
            else:
                sl_p = round(confirm_price * (1.0 + WHALE_SL_PCT), 6)
                tp_p = round(confirm_price * (1.0 - WHALE_TP_PCT), 6)

            with data_lock:
                if key in dashboard_data['whale'].get('armed_orders', {}):
                    dashboard_data['whale']['armed_orders'][key]['state'] = 'EXECUTED'
                    dashboard_data['whale']['armed_orders'][key]['confirmed'] = True

            test_id = f"TEST-{int(time.time() * 1000)}"
            threading.Thread(
                target=run_virtual_test_monitor,
                args=(test_id, exchange, symbol, side, confirm_price, sl_p, tp_p, entry_time_str, bybit_client, binance_client, dashboard_data, data_lock, add_ui_log, add_ledger_entry),
                daemon=True
            ).start()
            return

        # ==========================================================
        # BRANCH B: REAL TRADING MODE (LIVE EXCHANGE ORDER ±5% SL/TP)
        # ==========================================================
        target_notional = WHALE_MARGIN_USD * WHALE_LEVERAGE  # $6.0 Notional

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

            calc_qty = math.floor((target_notional / confirm_price) / qty_step) * qty_step
            if calc_qty < min_qty:
                calc_qty = min_qty
            if (calc_qty * confirm_price) < min_notional:
                calc_qty = math.ceil((min_notional / confirm_price) / qty_step) * qty_step
            qty_str = f"{calc_qty:.{q_dec}f}" if q_dec > 0 else str(int(calc_qty))

            order_side = "Buy" if side == "BUY" else "Sell"
            close_side = "Sell" if side == "BUY" else "Buy"

            resp = bybit_client.place_order(category="linear", symbol=symbol, side=order_side, orderType="Market", qty=qty_str, positionIdx=0)
            if resp.get("retCode") != 0:
                raise RuntimeError(resp.get("retMsg", "Bybit Order Rejected"))

            time.sleep(0.1)
            actual_entry = confirm_price
            pos_list = bybit_client.get_positions(category="linear", symbol=symbol)["result"]["list"]
            if pos_list and float(pos_list[0].get("size", 0)) > 0:
                qty_str = f"{float(pos_list[0]['size']):.{q_dec}f}" if q_dec > 0 else str(int(float(pos_list[0]['size'])))
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

            calc_qty = math.floor((target_notional / confirm_price) / qty_step) * qty_step
            if calc_qty < min_qty:
                calc_qty = min_qty
            if (calc_qty * confirm_price) < min_notional:
                calc_qty = math.ceil((min_notional / confirm_price) / qty_step) * qty_step
            qty_str = f"{calc_qty:.{q_dec}f}" if q_dec > 0 else str(int(calc_qty))

            order_side = "BUY" if side == "BUY" else "SELL"
            close_side = "SELL" if side == "BUY" else "BUY"

            resp = binance_client.new_order(symbol=symbol, side=order_side, type="MARKET", quantity=qty_str)
            actual_entry = float(resp.get("avgPrice", 0)) or confirm_price
            if actual_entry == confirm_price:
                for p in binance_client.get_position_risk(symbol=symbol):
                    if abs(float(p.get("positionAmt", 0))) > 0 and float(p.get("entryPrice", 0)) > 0:
                        actual_entry = float(p["entryPrice"])
                        qty_str = f"{abs(float(p['positionAmt'])):.{q_dec}f}" if q_dec > 0 else str(int(abs(float(p['positionAmt']))))
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

        with data_lock:
            if key in dashboard_data['whale'].get('armed_orders', {}):
                dashboard_data['whale']['armed_orders'][key]['state'] = 'EXECUTED'
                dashboard_data['whale']['armed_orders'][key]['confirmed'] = True

        add_ui_log(f"🚀✓ [Whale REAL] [{exchange} {symbol} {side}] EXECUTED @ {actual_entry} | SL (5%): {sl_str} | TP (5%): {tp_str}")
        add_ledger_entry({
            "exchange": "Whale",
            "coin": f"{symbol} [{exchange} {side} - REAL]",
            "rate": "✓ CONFIRMED",
            "entry_time": f"{entry_time_str} (@ {actual_entry})",
            "exit_time": "Active (±5% Bracket)",
            "detail": f"[REAL ACCOUNT] Margin: $1.0 (6x) | Qty: {qty_str} | SL (5%): {sl_str} | TP Limit (5%): {tp_str}",
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
    add_ui_log("🐋 Whale Scanner Active ($1 Margin | 6x Lev | ±5% SL/TP | Real & Virtual Test Modes)")

    while True:
        try:
            scan_all_whales(bybit_client, binance_client, dashboard_data, data_lock, add_ui_log)
            time.sleep(SCAN_INTERVAL_SEC)
        except Exception as e:
            add_ui_log(f"⚠️ [Whale] Scanner Recovered: {e}")
            time.sleep(SCAN_INTERVAL_SEC)
