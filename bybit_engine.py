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
        dual_status = dashboard_data.get('dual', {}).get('status', '')
        dual_coin_armed = dashboard_data.get('dual', {}).get('coin', '--') if "ARMED" in dual_status.upper() else "--"
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
        if time_diff <= 2000:
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

    auto_eligible = [c for c in all_negative if c['funding_rate'] <= MIN_FUNDING_RATE_THRESHOLD and c['symbol'] != dual_coin_armed and c['next_funding_time'] - now_ms > 5000]
    auto_top3_syms = []
    if auto_eligible:
        earliest_time = min(c['next_funding_time'] for c in auto_eligible)
        imminent = [c for c in auto_eligible if abs(c['next_funding_time'] - earliest_time) <= 600000]
        imminent.sort(key=lambda x: x['funding_rate'])
        auto_top3_syms = [c['symbol'] for c in imminent[:MAX_TARGET_COINS]]

    valid_m_armed = [s for s in m_armed if s in ticker_map and ticker_map[s]['funding_rate'] < 0 and s != dual_coin_armed]
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

    # TOP 20 NEGATIVE WATCHLIST
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
        precision_wait_until(settle_epoch - 1150)

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
        t_settle = settle_epoch
        t_limit_cancel = settle_epoch + 900
        t_hard_exit = settle_epoch + 4000

        baseline_tx_time = 0
        try:
            tx_resp = http_client.get_transaction_log(accountType="UNIFIED", category="linear", type="SETTLEMENT", limit=5)
            for item in tx_resp.get("result", {}).get("list", []):
                if item.get("symbol") == symbol:
                    baseline_tx_time = max(baseline_tx_time, int(item.get("transactionTime", 0)))
        except Exception:
            pass

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

        # 2. FETCH ACTUAL FILLED PRICE & PRE-CALCULATE 20%..90% LADDER
        time.sleep(0.06)
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
        abs_rate = abs(rate)

        limit_ladder = {}
        for pct in range(20, 95, 5):
            raw_p = actual_entry_price * (1.0 - (abs_rate * (pct / 100.0)))
            limit_ladder[pct] = round(math.floor(raw_p / tick_size) * tick_size, price_decimals)

        add_ui_log(f"🚀 [Bybit] [{symbol}] MKT BUY @ {entry_price_str} | Ladder 20%-90% Ready ({limit_ladder[20]:.{price_decimals}f}..{limit_ladder[90]:.{price_decimals}f})")

        # 3. WAIT FOR EXACT TIMESTAMP T & POLL FUNDING CREDIT
        precision_wait_until(t_settle)
        funding_credited = False
        funding_fetch_ms = 0.0
        fallback_deadline = t_settle + 750

        while get_synced_time_ms() < fallback_deadline:
            try:
                tx_resp = http_client.get_transaction_log(accountType="UNIFIED", category="linear", type="SETTLEMENT", limit=5)
                for item in tx_resp.get("result", {}).get("list", []):
                    if item.get("symbol") == symbol:
                        tx_t = int(item.get("transactionTime", 0))
                        if tx_t >= (t_settle - 500) and tx_t > baseline_tx_time:
                            funding_credited = True
                            funding_fetch_ms = round(max(1.0, get_synced_time_ms() - t_settle), 1)
                            break
                if funding_credited:
                    break
            except Exception:
                pass
            time.sleep(0.015)

        if not funding_credited:
            funding_fetch_ms = round(get_synced_time_ms() - t_settle, 1)

        # 4. CHECK MARKET DROP % AT CREDIT TIME & FIRE +5% BUFFERED SELL LIMIT IMMEDIATELY
        try:
            cur_p = float(http_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]["lastPrice"])
        except Exception:
            cur_p = actual_entry_price

        raw_price_drop_pct = ((actual_entry_price - cur_p) / actual_entry_price * 100.0) if actual_entry_price > 0 else 0.0
        expected_drop = actual_entry_price * abs_rate
        funding_drop_pct = ((actual_entry_price - cur_p) / expected_drop * 100.0) if expected_drop > 0 else 0.0

        next_tier = math.ceil(funding_drop_pct / 5.0) * 5
        selected_tier = max(20, min(90, next_tier + 5))
        sell_limit_price = limit_ladder[selected_tier]
        limit_price_str = f"{sell_limit_price:.{price_decimals}f}"

        limit_fire_ms = round(get_synced_time_ms() - t_settle, 1)
        limit_fire_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
        t_limit_sent_local = time.time() * 1000
        limit_order_id = None

        try:
            lim_resp = http_client.place_order(
                category="linear", symbol=symbol, side="Sell", orderType="Limit",
                price=limit_price_str, qty=filled_qty_str, timeInForce="GTC",
                reduceOnly=True, positionIdx=0
            )
            if lim_resp.get("retCode") == 0:
                limit_order_id = lim_resp["result"].get("orderId", "")
                add_ui_log(f"🎯 [Bybit] [{symbol}] Fund Fetch +{funding_fetch_ms}ms | Mkt Drop {raw_price_drop_pct:.2f}% ({funding_drop_pct:.1f}% Fund) -> Fired {selected_tier}% Lim @ {limit_price_str} (+{limit_fire_ms}ms)")
            else:
                add_ui_log(f"⚠️ [Bybit] [{symbol}] Sell Limit Rejected: {lim_resp.get('retMsg')}")
        except Exception as e:
            add_ui_log(f"⚠️ [Bybit] [{symbol}] Sell Limit Exception: {e}")

        # 5. MONITOR LIMIT FILL UNTIL T + 900ms, THEN CANCEL IF UNFILLED
        limit_filled = False
        fill_duration_ms = None
        exit_time_str = "--"

        if limit_order_id:
            while get_synced_time_ms() < t_limit_cancel:
                try:
                    open_res = http_client.get_open_orders(category="linear", symbol=symbol, orderId=limit_order_id)
                    if not open_res.get("result", {}).get("list", []):
                        hist_res = http_client.get_order_history(category="linear", symbol=symbol, orderId=limit_order_id)
                        hist_list = hist_res.get("result", {}).get("list", [])
                        if hist_list and hist_list[0].get("orderStatus") == "Filled":
                            c_t = int(hist_list[0].get("createdTime", 0))
                            u_t = int(hist_list[0].get("updatedTime", 0))
                            ex_diff = u_t - c_t
                            fill_duration_ms = ex_diff if ex_diff > 0 else round((time.time() * 1000) - t_limit_sent_local, 1)
                            limit_filled = True
                            exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
                            add_ui_log(f"⚡ [Bybit] [{symbol}] LIMIT ({selected_tier}%) FILLED IN {fill_duration_ms}ms @ {limit_price_str}!")
                            break
                        else:
                            break
                except Exception:
                    pass
                time.sleep(0.025)

        if not limit_filled:
            precision_wait_until(t_limit_cancel)
            try:
                if limit_order_id:
                    http_client.cancel_order(category="linear", symbol=symbol, orderId=limit_order_id)
                else:
                    http_client.cancel_all_orders(category="linear", symbol=symbol)
                add_ui_log(f"⏱ [Bybit] [{symbol}] Limit unfilled at T+900ms -> Cancelled! Waiting for T+4s Hard Exit...")
            except Exception:
                pass

            if limit_order_id:
                try:
                    hist_res = http_client.get_order_history(category="linear", symbol=symbol, orderId=limit_order_id)
                    hist_list = hist_res.get("result", {}).get("list", [])
                    if hist_list and hist_list[0].get("orderStatus") == "Filled":
                        c_t = int(hist_list[0].get("createdTime", 0))
                        u_t = int(hist_list[0].get("updatedTime", 0))
                        ex_diff = u_t - c_t
                        fill_duration_ms = ex_diff if ex_diff > 0 else round((time.time() * 1000) - t_limit_sent_local, 1)
                        limit_filled = True
                        exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
                        add_ui_log(f"⚡ [Bybit] [{symbol}] LIMIT ({selected_tier}%) FILLED JUST BEFORE CANCEL IN {fill_duration_ms}ms!")
                except Exception:
                    pass

        # 6. HARD EXIT AT T + 4s IF LIMIT WAS CANCELLED / UNFILLED
        if not limit_filled:
            precision_wait_until(t_hard_exit)
            exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
            try:
                pos_resp = http_client.get_positions(category="linear", symbol=symbol)
                positions = pos_resp.get("result", {}).get("list", [])
                pos_size = float(positions[0].get("size", 0)) if positions else 0.0
                if pos_size > 0:
                    rem_q = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
                    http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=rem_q, reduceOnly=True, positionIdx=0)
                    add_ui_log(f"🛑 [Bybit] [{symbol}] HARD EXIT EXECUTED AT T+4.0s: {rem_q}")
            except Exception:
                pass

        # 7. EMERGENCY SWEEPS AT T + 15s & T + 30s
        for stage_ms in [settle_epoch + 15000, settle_epoch + 30000]:
            precision_wait_until(stage_ms)
            try:
                pos_resp = http_client.get_positions(category="linear", symbol=symbol)
                positions = pos_resp.get("result", {}).get("list", [])
                pos_size = float(positions[0].get("size", 0)) if positions else 0.0
                if pos_size > 0:
                    rem_q = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
                    http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=rem_q, reduceOnly=True, positionIdx=0)
                    add_ui_log(f"🛡 [Bybit] [{symbol}] EMERGENCY EXIT: {rem_q}")
                else:
                    break
            except Exception:
                pass

        with data_lock:
            if symbol in dashboard_data['bybit'].get('manual_armed', []):
                dashboard_data['bybit']['manual_armed'].remove(symbol)

        fetch_tag = f"+{funding_fetch_ms}ms" if funding_credited else f"+{funding_fetch_ms}ms(FB)"
        drop_tag = f"{raw_price_drop_pct:.2f}% ({funding_drop_pct:.1f}% of Fund)"
        exit_status_tag = f"Fill: {fill_duration_ms}ms" if limit_filled else "Cancelled @ T+900ms -> Hard Exit @ T+4s"
        badge_txt = f"FILLED ({fill_duration_ms}ms)" if limit_filled else "HARD EXIT (T+4s)"

        add_ledger_entry({
            "exchange": "Bybit",
            "coin": f"{symbol} [{arm_mode}]",
            "rate": rate_str,
            "entry_time": f"{entry_time_str} (@ {entry_price_str})",
            "exit_time": exit_time_str,
            "detail": f"Fund Fetch: {fetch_tag} | Mkt Drop @ Credit: {drop_tag} | {selected_tier}% Lim @ {limit_price_str} (+{limit_fire_ms}ms @ {limit_fire_time_str}) | {exit_status_tag} | Qty: {filled_qty_str} ({leverage}x)",
            "badge_text": badge_txt,
            "badge_class": "status-success" if limit_filled else "status-warn"
        })

    finally:
        with executors_lock:
            active_executors.discard(exec_key)

def start_bybit_loop(dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    http_client = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
    add_ui_log("🚀 Bybit Engine Active ($0.5 Margin | T+900ms Lim Cancel | T+4s Hard Exit | Top 20 Live)")
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

                if 0 < time_left <= 4500:
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

            time.sleep(1.2)
        except Exception as e:
            add_ui_log(f"⚠️ [Bybit] Loop Recovered: {e}")
            time.sleep(2)
