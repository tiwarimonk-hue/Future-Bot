import os
import time
import math
import threading
from datetime import datetime, timezone, timedelta
from pybit.unified_trading import HTTP

IST = timezone(timedelta(hours=5, minutes=30))

API_KEY = os.environ.get('BYBIT_API_KEY', 'YOUR_BYBIT_API_KEY')
API_SECRET = os.environ.get('BYBIT_API_SECRET', 'YOUR_BYBIT_API_SECRET')

MIN_FUNDING_RATE_THRESHOLD = -0.004   # -0.4% Auto Top 3 Threshold
INITIAL_ENTRY_MARGIN_USD = 0.5        # $0.5 Margin
MAX_TARGET_COINS = 3
MAX_ALLOWED_LEVERAGE = 20.0
TP_PROFIT_PCT = 0.001                 # +0.10% above entry price
HARD_EXIT_SECONDS = 600               # 10 Minutes hard exit

clock_offset_ms = 0.0
active_executors = set()
executors_lock = threading.Lock()

def sync_bybit_clock(http_client, dashboard_data, data_lock):
    global clock_offset_ms
    try:
        t_send = time.time() * 1000
        server_time_resp = http_client.get_server_time()
        t_recv = time.time() * 1000
        server_time = float(server_time_resp['result']['timeNano']) / 1e6
        clock_offset_ms = (server_time + ((t_recv - t_send) / 2.0)) - t_recv
        with data_lock:
            dashboard_data['bybit']['offset'] = round(clock_offset_ms, 2)
    except Exception:
        pass

def get_synced_time_ms():
    return (time.time() * 1000) + clock_offset_ms

def precision_wait_until(target_time_ms):
    while True:
        now = get_synced_time_ms()
        diff = target_time_ms - now
        if diff <= 0:
            break
        elif diff > 10:
            time.sleep(0.002)
        elif diff > 2:
            time.sleep(0.0005)

def scan_market_and_sync_state(http_client, dashboard_data, data_lock):
    try:
        tickers = http_client.get_tickers(category="linear")["result"]["list"]
    except Exception:
        return []

    now_ms = int(get_synced_time_ms())
    all_negative = []

    with data_lock:
        m_armed = list(dashboard_data['bybit'].get('manual_armed', []))
        m_disarmed = list(dashboard_data['bybit'].get('manual_disarmed', []))

    ticker_map = {}
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue

        raw_rate = t.get("fundingRate", "")
        raw_next = t.get("nextFundingTime", "")
        if not raw_rate or not raw_next:
            continue
        try:
            rate = float(raw_rate)
            next_time_ms = int(raw_next)
            last_price = float(t.get("lastPrice", 0))
        except ValueError:
            continue

        time_diff = next_time_ms - now_ms
        if time_diff <= 1500:
            continue

        raw_interval = t.get("fundingIntervalHour", "")
        if raw_interval:
            cycle_str = f"{raw_interval}H"
        else:
            est_h = max(1, round(time_diff / 3600000))
            cycle_str = f"{est_h}H"

        settle_dt = datetime.fromtimestamp(next_time_ms / 1000, tz=IST)
        coin_obj = {
            "symbol": sym,
            "funding_rate": rate,
            "rate_str": f"{rate*100:+.4f}%",
            "next_funding_time": next_time_ms,
            "settle_str": settle_dt.strftime('%H:%M:%S IST'),
            "cycle": cycle_str,
            "last_price": last_price
        }
        ticker_map[sym] = coin_obj

        if rate < 0:
            all_negative.append(coin_obj)

    all_negative.sort(key=lambda x: x['funding_rate'])

    # Auto Top 3 (<= -0.4%)
    auto_eligible = [
        c for c in all_negative
        if c['funding_rate'] <= MIN_FUNDING_RATE_THRESHOLD
        and (c['next_funding_time'] - now_ms) > 1500
    ]
    auto_top3_syms = []
    if auto_eligible:
        earliest_time = min(c['next_funding_time'] for c in auto_eligible)
        imminent = [c for c in auto_eligible if abs(c['next_funding_time'] - earliest_time) <= 600000]
        imminent.sort(key=lambda x: x['funding_rate'])
        auto_top3_syms = [c['symbol'] for c in imminent[:MAX_TARGET_COINS]]

    valid_m_armed = [s for s in m_armed if s in ticker_map and ticker_map[s]['funding_rate'] < 0]
    valid_m_disarmed = [s for s in m_disarmed if s in ticker_map and ticker_map[s]['funding_rate'] < 0]

    effective_armed = []
    seen = set()
    for sym in auto_top3_syms:
        if sym not in valid_m_disarmed and sym not in seen:
            c = dict(ticker_map[sym])
            c['arm_mode'] = "AUTO"
            effective_armed.append(c)
            seen.add(sym)

    for sym in valid_m_armed:
        if sym not in seen:
            c = dict(ticker_map[sym])
            c['arm_mode'] = "MANUAL"
            effective_armed.append(c)
            seen.add(sym)

    effective_armed.sort(key=lambda x: (x['next_funding_time'], x['funding_rate']))

    top20_list = []
    for c in all_negative[:20]:
        sym = c['symbol']
        if sym in seen:
            mode = "MANUAL" if sym in valid_m_armed else "AUTO"
            armed_flag = True
        else:
            mode = "DISARMED"
            armed_flag = False
        top20_list.append({
            "symbol": sym,
            "rate": c['funding_rate'],
            "rate_str": c['rate_str'],
            "next_funding_time": c['next_funding_time'],
            "settle_str": c['settle_str'],
            "cycle": c['cycle'],
            "arm_mode": mode,
            "is_armed": armed_flag
        })

    with data_lock:
        dashboard_data['bybit']['manual_armed'] = valid_m_armed
        dashboard_data['bybit']['manual_disarmed'] = valid_m_disarmed
        dashboard_data['bybit']['top20'] = top20_list
        if effective_armed:
            dashboard_data['bybit']['status'] = f"ARMED ({len(effective_armed)} Coins)"
            dashboard_data['bybit']['symbols'] = ", ".join([f"{c['symbol']} ({c['arm_mode']})" for c in effective_armed])
            dashboard_data['bybit']['rates'] = ", ".join([c['rate_str'] for c in effective_armed])
            dashboard_data['bybit']['settlement'] = effective_armed[0]['settle_str']
        else:
            dashboard_data['bybit']['status'] = "SCANNING (Top 20 Live)"
            dashboard_data['bybit']['symbols'] = "Scanning..."
            dashboard_data['bybit']['rates'] = "--"
            dashboard_data['bybit']['settlement'] = "--"

    return effective_armed

def prepare_coin_parameters(http_client, symbol, price, margin_usd=0.5):
    try:
        inst_info = http_client.get_instruments_info(category="linear", symbol=symbol)["result"]["list"][0]
        exchange_max_lev = float(inst_info.get("leverageFilter", {}).get("maxLeverage", 20.0))
        lot_filter = inst_info['lotSizeFilter']
        price_filter = inst_info.get('priceFilter', {})
        qty_step = float(lot_filter["qtyStep"])
        min_qty = float(lot_filter['minOrderQty'])
        min_notional = float(lot_filter.get("minNotionalValue", 5.0))
        tick_size = float(price_filter.get("tickSize", "0.0001"))
    except Exception:
        exchange_max_lev, qty_step, min_qty, min_notional, tick_size = 20.0, 1.0, 1.0, 5.0, 0.0001

    applied_leverage = min(MAX_ALLOWED_LEVERAGE, exchange_max_lev)
    try:
        http_client.set_leverage(category="linear", symbol=symbol, buyLeverage=str(applied_leverage), sellLeverage=str(applied_leverage))
    except Exception:
        pass

    qty_str = f"{qty_step:.8f}".rstrip("0")
    qty_decimals = len(qty_str.split(".")[1]) if "." in qty_str else 0
    tick_str = f"{tick_size:.8f}".rstrip("0")
    price_decimals = len(tick_str.split(".")[1]) if "." in tick_str else 2

    notional = margin_usd * applied_leverage
    calc_qty = math.floor((notional / price) / qty_step) * qty_step
    if calc_qty < min_qty:
        calc_qty = min_qty
    if (calc_qty * price) < min_notional:
        calc_qty = math.ceil((min_notional / price) / qty_step) * qty_step

    formatted_qty = f"{calc_qty:.{qty_decimals}f}" if qty_decimals > 0 else str(int(calc_qty))
    return {
        "symbol": symbol, "qty": formatted_qty, "leverage": applied_leverage,
        "qty_decimals": qty_decimals, "price_decimals": price_decimals,
        "tick_size": tick_size, "last_price": price
    }

def parallel_coin_executor(http_client, coin_meta, dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    symbol = coin_meta['symbol']
    settle_epoch = coin_meta['next_funding_time']
    arm_mode = coin_meta.get('arm_mode', 'AUTO')
    exec_key = (symbol, settle_epoch)

    try:
        precision_wait_until(settle_epoch - 1500)

        with data_lock:
            if symbol in dashboard_data['bybit'].get('manual_disarmed', []):
                add_ui_log(f"🛑 [Bybit] [{symbol}] Trade aborted before entry (Manually DISARMED).")
                return

        try:
            tk = http_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
            live_price = float(tk.get("lastPrice", coin_meta['last_price']))
            rate = float(tk.get("fundingRate", coin_meta['funding_rate']))
        except Exception:
            live_price = coin_meta['last_price']
            rate = coin_meta['funding_rate']

        if rate >= 0:
            add_ui_log(f"⚠️ [Bybit] [{symbol}] Funding turned non-negative ({rate*100:+.4f}%). Aborting entry.")
            return

        coin_plan = prepare_coin_parameters(http_client, symbol, live_price, INITIAL_ENTRY_MARGIN_USD)
        qty = coin_plan['qty']
        qty_decimals = coin_plan['qty_decimals']
        price_decimals = coin_plan['price_decimals']
        tick_size = coin_plan['tick_size']
        leverage = coin_plan['leverage']
        rate_str = f"{rate*100:+.4f}%"

        t_entry = settle_epoch - 1000
        t_hard_exit = settle_epoch + (HARD_EXIT_SECONDS * 1000)

        # 1. MARKET BUY ENTRY AT T - 1s
        precision_wait_until(t_entry)
        entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
        entry_success = False
        entry_err = ""

        try:
            entry_resp = http_client.place_order(
                category="linear", symbol=symbol, side="Buy",
                orderType="Market", qty=qty, positionIdx=0
            )
            if entry_resp.get("retCode") == 0:
                entry_success = True
            else:
                entry_err = f"RetCode {entry_resp.get('retCode')}: {entry_resp.get('retMsg')}"
        except Exception as e:
            entry_err = str(e)

        if not entry_success:
            add_ui_log(f"❌ [Bybit] [{symbol}] Market Entry Failed: {entry_err}")
            add_ledger_entry({
                "exchange": "Bybit", "coin": f"{symbol} [{arm_mode}]", "rate": rate_str,
                "entry_time": entry_time_str, "exit_time": "--",
                "detail": f"Margin: $0.5 | Qty: {qty} ({leverage}x) | Entry Failed: {entry_err}",
                "badge_text": "ENTRY FAILED", "badge_class": "status-warn"
            })
            return

        # 2. FETCH ACTUAL FILLED PRICE & POSITION SIZE
        time.sleep(0.08)
        actual_entry_price = live_price
        filled_qty_float = float(qty)

        try:
            pos_list = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"]
            if pos_list and float(pos_list[0].get("size", 0)) > 0:
                filled_qty_float = float(pos_list[0]["size"])
                avg_p = float(pos_list[0].get("avgPrice", 0))
                if avg_p > 0:
                    actual_entry_price = avg_p
        except Exception:
            pass

        filled_qty_str = f"{filled_qty_float:.{qty_decimals}f}" if qty_decimals > 0 else str(int(filled_qty_float))
        entry_price_str = f"{actual_entry_price:.{price_decimals}f}"

        # 3. PLACE STOP LOSS AT 200% OF FUNDING RATE BELOW ENTRY
        sl_discount = 2.0 * abs(rate)
        raw_sl_price = actual_entry_price * (1.0 - sl_discount)
        sl_price = max(tick_size, round(math.floor(raw_sl_price / tick_size) * tick_size, price_decimals))
        sl_price_str = f"{sl_price:.{price_decimals}f}"

        try:
            http_client.set_trading_stop(
                category="linear", symbol=symbol,
                stopLoss=sl_price_str, slTriggerBy="LastPrice", positionIdx=0
            )
        except Exception as e:
            add_ui_log(f"⚠️ [Bybit] [{symbol}] SL Set Warning: {e}")

        # 4. PLACE +0.10% TAKE PROFIT SELL LIMIT ABOVE ENTRY
        raw_tp_price = actual_entry_price * (1.0 + TP_PROFIT_PCT)
        tp_price = round(math.ceil(raw_tp_price / tick_size) * tick_size, price_decimals)
        if tp_price <= actual_entry_price:
            tp_price = round(actual_entry_price + tick_size, price_decimals)
        tp_price_str = f"{tp_price:.{price_decimals}f}"

        tp_order_id = None
        t_tp_sent_ms = time.time() * 1000
        try:
            tp_resp = http_client.place_order(
                category="linear", symbol=symbol, side="Sell",
                orderType="Limit", price=tp_price_str, qty=filled_qty_str,
                timeInForce="GTC", reduceOnly=True, positionIdx=0
            )
            if tp_resp.get("retCode") == 0:
                tp_order_id = tp_resp["result"].get("orderId", "")
            else:
                add_ui_log(f"⚠️ [Bybit] [{symbol}] +0.10% TP Limit Rejected: {tp_resp.get('retMsg')}")
        except Exception as e:
            add_ui_log(f"⚠️ [Bybit] [{symbol}] +0.10% TP Limit Exception: {e}")

        add_ui_log(f"🚀 [Bybit] [{symbol}] MKT BUY @ {entry_price_str} | SL (-200% Fund): {sl_price_str} | TP Lim (+0.10%): {tp_price_str} | 10m Window")

        # 5. MONITOR TP LIMIT / SL FOR UP TO 10 MINUTES
        exit_outcome = "HARD EXIT (10m)"
        exit_time_str = "--"
        badge_class = "status-warn"
        position_closed = False

        while get_synced_time_ms() < t_hard_exit:
            try:
                pos_list = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"]
                pos_size = float(pos_list[0].get("size", 0)) if pos_list else 0.0

                if pos_size <= 0:
                    position_closed = True
                    exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
                    # Check if TP Limit filled or SL hit
                    tp_filled = False
                    if tp_order_id:
                        hist = http_client.get_order_history(category="linear", symbol=symbol, orderId=tp_order_id).get("result", {}).get("list", [])
                        if hist and hist[0].get("orderStatus") == "Filled":
                            tp_filled = True
                            c_t = int(hist[0].get("createdTime", 0))
                            u_t = int(hist[0].get("updatedTime", 0))
                            sec_taken = round(max(0.05, (u_t - c_t) / 1000.0), 2) if u_t > c_t else round((time.time() * 1000 - t_tp_sent_ms) / 1000.0, 2)
                            exit_outcome = f"TP +0.10% FILLED ({sec_taken}s)"
                            badge_class = "status-success"
                            add_ui_log(f"🎯 [Bybit] [{symbol}] +0.10% SELL LIMIT HIT @ {tp_price_str} in {sec_taken}s!")
                    if not tp_filled:
                        exit_outcome = "SL HIT (-200% Fund)"
                        badge_class = "status-warn"
                        add_ui_log(f"🛑 [Bybit] [{symbol}] STOP LOSS HIT @ {sl_price_str}")
                    break
            except Exception:
                pass

            # Poll faster near settlement, 1s thereafter
            sleep_sec = 0.25 if abs(get_synced_time_ms() - settle_epoch) < 10000 else 1.0
            time.sleep(sleep_sec)

        # 6. IF 10 MINUTES EXPIRE & POSITION STILL OPEN -> CANCEL & HARD EXIT
        try:
            http_client.cancel_all_orders(category="linear", symbol=symbol)
        except Exception:
            pass

        if not position_closed:
            exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
            try:
                pos_list = http_client.get_positions(category="linear", symbol=symbol)["result"]["list"]
                pos_size = float(pos_list[0].get("size", 0)) if pos_list else 0.0
                if pos_size > 0:
                    rem_q = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
                    http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=rem_q, reduceOnly=True, positionIdx=0)
                    exit_outcome = "HARD EXIT (10m Timeout)"
                    add_ui_log(f"⏱ [Bybit] [{symbol}] 10m Timeout -> Hard Market Exit Executed: {rem_q}")
                else:
                    exit_outcome = "CLOSED"
                    badge_class = "status-success"
            except Exception:
                exit_outcome = "EXIT FAILED"

        with data_lock:
            if symbol in dashboard_data['bybit'].get('manual_armed', []):
                dashboard_data['bybit']['manual_armed'].remove(symbol)

        add_ledger_entry({
            "exchange": "Bybit",
            "coin": f"{symbol} [{arm_mode}]",
            "rate": rate_str,
            "entry_time": f"{entry_time_str} (@ {entry_price_str})",
            "exit_time": exit_time_str,
            "detail": f"SL (-200% Fund): {sl_price_str} | TP (+0.10%): {tp_price_str} | Outcome: {exit_outcome} | Qty: {filled_qty_str} ({leverage}x)",
            "badge_text": exit_outcome,
            "badge_class": badge_class
        })

    finally:
        with executors_lock:
            active_executors.discard(exec_key)

def start_bybit_loop(dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    http_client = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
    add_ui_log("🚀 Bybit Engine Active ($0.5 Margin | T-1s Mkt | -200% Fund SL | +0.10% TP | 10m Hard Exit)")
    last_clock_sync = 0.0

    while True:
        try:
            if time.time() - last_clock_sync >= 15.0:
                sync_bybit_clock(http_client, dashboard_data, data_lock)
                last_clock_sync = time.time()

            armed_coins = scan_market_and_sync_state(http_client, dashboard_data, data_lock)
            now_ms = get_synced_time_ms()

            for c in armed_coins:
                sym = c['symbol']
                settle_epoch = c['next_funding_time']
                time_left = settle_epoch - now_ms

                if 1500 < time_left <= 10000:
                    exec_key = (sym, settle_epoch)
                    with executors_lock:
                        if exec_key not in active_executors:
                            active_executors.add(exec_key)
                            threading.Thread(
                                target=parallel_coin_executor,
                                args=(http_client, c, dashboard_data, data_lock, add_ui_log, add_ledger_entry),
                                daemon=True
                            ).start()
                            add_ui_log(f"⚡ [Bybit] Launching Execution Thread for {sym} ({c['arm_mode']})...")

            time.sleep(1.0)
        except Exception as e:
            add_ui_log(f"⚠️ [Bybit] Loop Recovered: {e}")
            time.sleep(2)
