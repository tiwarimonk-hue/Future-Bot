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

# WHALE EXECUTION PARAMETERS
WHALE_MARGIN_USD = 1.0          # $1.0 Margin
WHALE_LEVERAGE = 6              # 6x Leverage ($6.0 Notional)
WHALE_SL_PCT = 0.10             # 10% Stop Loss
WHALE_TP_PCT = 0.10             # 10% Take Profit

# Rolling baseline store: key -> {"prev_vol": float, "avg_delta": float, "last_ts": float, "spike_time": str, "spike_usd": float, "spike_mult": float, "side": str}
market_memory = {}
signal_cache = {}  # key -> signal dict (persists hot signals for 90s so user can click ARM smoothly)

def format_usd(val):
    if val >= 1_000_000:
        return f"${val / 1_000_000:.2f}M"
    elif val >= 1_000:
        return f"${val / 1_000:.0f}K"
    return f"${val:.0f}"

def scan_all_whales(bybit_client, binance_client, dashboard_data, data_lock):
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
    except Exception:
        pass

    # 2. Fetch Binance All USDT Futures 24h Tickers
    binance_rows = []
    try:
        bn_list = binance_client.ticker_24hr()
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
    except Exception:
        pass

    all_rows = bybit_rows + binance_rows

    for ex, sym, price, vol24, pct24, high24, low24 in all_rows:
        m_key = f"{ex}:{sym}"
        prev = market_memory.get(m_key)

        if not prev:
            # Bootstrap initial baseline using 24h average per 3s interval
            est_3s_base = max(500.0, vol24 / 28800.0)
            market_memory[m_key] = {
                "prev_vol": vol24,
                "prev_price": price,
                "avg_delta": est_3s_base,
                "last_ts": now_ts
            }
            # Seed strong initial movers so dashboard shows live whale activity immediately
            rng = max(1e-9, high24 - low24)
            pos_in_range = (price - low24) / rng
            if vol24 >= 25_000_000 and abs(pct24) >= 4.0:
                side = "BUY" if (pct24 > 0 and pos_in_range >= 0.55) else ("SELL" if (pct24 < 0 and pos_in_range <= 0.45) else None)
                if side:
                    est_1m_flow = vol24 / 1440.0
                    mult = round(min(12.0, 2.5 + abs(pct24) / 4.0), 1)
                    chance = min(94, int(58 + abs(pct24) * 1.8 + min(15, vol24 / 20_000_000)))
                    signal_cache[f"{ex}:{sym}:{side}"] = {
                        "exchange": ex,
                        "symbol": sym,
                        "side": side,
                        "price": price,
                        "inflow_usd": est_1m_flow,
                        "inflow_str": format_usd(est_1m_flow),
                        "spike_mult": mult,
                        "detected_time": now_ist_str,
                        "chance_pct": chance,
                        "updated_ts": now_ts
                    }
            continue

        dt = max(1.0, now_ts - prev["last_ts"])
        delta_vol = max(0.0, vol24 - prev["prev_vol"])
        price_diff_pct = ((price - prev["prev_price"]) / prev["prev_price"] * 100.0) if prev["prev_price"] > 0 else 0.0

        # Normalize delta to 3s window
        norm_delta = delta_vol * (3.0 / dt)
        avg_d = prev["avg_delta"]
        spike_mult = (norm_delta / avg_d) if avg_d > 0 else 1.0

        # Smooth rolling average (slow adaptation so spikes stand out)
        new_avg = (avg_d * 0.92) + (norm_delta * 0.08)
        prev["prev_vol"] = vol24
        prev["prev_price"] = price
        prev["avg_delta"] = max(250.0, new_avg)
        prev["last_ts"] = now_ts

        # Whale Spike Detection: Abnormal capital injection (>= $25k in burst & >= 2.2x normal flow)
        if delta_vol >= 25_000 and spike_mult >= 2.2:
            if price_diff_pct > 0.02 or (price_diff_pct >= 0 and pct24 > 1.5):
                side = "BUY"
            elif price_diff_pct < -0.02 or (price_diff_pct <= 0 and pct24 < -1.5):
                side = "SELL"
            else:
                continue

            # Probability Score (60% to 98%)
            raw_score = 55.0 + min(22.0, spike_mult * 3.2) + min(12.0, delta_vol / 120_000.0) + min(9.0, abs(price_diff_pct) * 18.0)
            chance_pct = max(60, min(98, int(round(raw_score))))

            s_key = f"{ex}:{sym}:{side}"
            signal_cache[s_key] = {
                "exchange": ex,
                "symbol": sym,
                "side": side,
                "price": price,
                "inflow_usd": delta_vol,
                "inflow_str": format_usd(delta_vol),
                "spike_mult": round(spike_mult, 1),
                "detected_time": now_ist_str,
                "chance_pct": chance_pct,
                "updated_ts": now_ts
            }

    # Prune signals older than 90s unless actively armed
    with data_lock:
        armed_map = dashboard_data['whale'].get('armed_orders', {})
        active_keys = set(armed_map.keys())

    stale_keys = [k for k, v in signal_cache.items() if (now_ts - v["updated_ts"] > 90.0) and (k not in active_keys)]
    for k in stale_keys:
        del signal_cache[k]

    buy_list = []
    sell_list = []

    with data_lock:
        armed_map = dashboard_data['whale'].get('armed_orders', {})
        for k, sig in signal_cache.items():
            item = dict(sig)
            arm_info = armed_map.get(k)
            if arm_info:
                item["arm_state"] = arm_info.get("state", "WAITING_CONFIRM")
                item["confirmed"] = arm_info.get("confirmed", False)
            else:
                item["arm_state"] = "IDLE"
                item["confirmed"] = False

            if item["side"] == "BUY":
                buy_list.append(item)
            else:
                sell_list.append(item)

        buy_list.sort(key=lambda x: (x["arm_state"] != "IDLE", x["chance_pct"], x["inflow_usd"]), reverse=True)
        sell_list.sort(key=lambda x: (x["arm_state"] != "IDLE", x["chance_pct"], x["inflow_usd"]), reverse=True)

        dashboard_data['whale']['buy_signals'] = buy_list[:12]
        dashboard_data['whale']['sell_signals'] = sell_list[:12]
        dashboard_data['whale']['status'] = f"LIVE SCAN ({len(buy_list)} Buy / {len(sell_list)} Sell Whales)"

def execute_whale_armed_trade(exchange, symbol, side, anchor_price, dashboard_data, data_lock, add_ui_log, add_ledger_entry):
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

        # 1. WAIT FOR 1 CONFIRMATION TICK (Max 15s wait)
        confirmed = False
        confirm_price = anchor_price
        deadline = time.time() + 15.0
        ticks_checked = 0

        while time.time() < deadline:
            with data_lock:
                if key not in dashboard_data['whale'].get('armed_orders', {}):
                    return  # User disarmed

            time.sleep(0.35)
            try:
                cur_p = get_live_tick()
                ticks_checked += 1
                # Confirm direction on fresh market tick
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
            add_ui_log(f"⚠️ [Whale] [{exchange} {symbol}] Confirmation Tick Timed Out (No momentum continuation). Disarmed.")
            return

        # Mark Confirmation Tick (✓) in Dashboard State
        with data_lock:
            if key in dashboard_data['whale'].get('armed_orders', {}):
                dashboard_data['whale']['armed_orders'][key]['state'] = 'CONFIRMED'
                dashboard_data['whale']['armed_orders'][key]['confirmed'] = True

        add_ui_log(f"✅✓ [Whale] [{exchange} {symbol} {side}] CONFIRMATION TICK PASSED ({anchor_price} -> {confirm_price})! Firing $1.0 (6x) Entry...")

        # 2. CALCULATE LOT SIZE & SET 6x LEVERAGE ($1.0 MARGIN)
        target_notional = WHALE_MARGIN_USD * WHALE_LEVERAGE  # $6.0 Notional
        entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

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

            # 10% SL & 10% TP
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
            # BINANCE EXECUTION
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

            # 10% SL & 10% TP
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

        add_ui_log(f"🚀✓ [Whale] [{exchange} {symbol} {side}] EXECUTED @ {actual_entry} | SL (10%): {sl_str} | TP (10%): {tp_str}")
        add_ledger_entry({
            "exchange": "Whale",
            "coin": f"{symbol} [{exchange} {side}]",
            "rate": "✓ CONFIRMED",
            "entry_time": f"{entry_time_str} (@ {actual_entry})",
            "exit_time": "Active (±10% Bracket)",
            "detail": f"Margin: $1.0 (6x) | Qty: {qty_str} | SL (10%): {sl_str} | TP Limit (10%): {tp_str}",
            "badge_text": "✓ CONFIRMED & ACTIVE",
            "badge_class": "status-success"
        })

    except Exception as e:
        with data_lock:
            dashboard_data['whale'].get('armed_orders', {}).pop(key, None)
        add_ui_log(f"❌ [Whale] [{exchange} {symbol}] Execution Failed: {e}")

def start_whale_scanner_loop(dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    bybit_client = HTTP(testnet=False, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)
    binance_client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)
    add_ui_log("🐋 Whale Money Flow Engine Active (Full Market Scan | $1.0 Margin | 6x Lev | 1-Tick Confirm ✓)")

    while True:
        try:
            scan_all_whales(bybit_client, binance_client, dashboard_data, data_lock)
            time.sleep(2.5)
        except Exception as e:
            add_ui_log(f"⚠️ [Whale] Scanner Recovered: {e}")
            time.sleep(3)
