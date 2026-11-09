import os
import time
import math
import threading
from datetime import datetime, timezone, timedelta
from binance.um_futures import UMFutures

IST = timezone(timedelta(hours=5, minutes=30))

BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', 'YOUR_BINANCE_API_KEY')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', 'YOUR_BINANCE_API_SECRET')

MIN_FUNDING_RATE_THRESHOLD = -0.004   # -0.4% Auto Top 3 Threshold
INITIAL_ENTRY_MARGIN_USD = 0.5        # $0.5 Margin
MAX_TARGET_COINS = 3
MAX_ALLOWED_LEVERAGE = 20
TP_PROFIT_PCT = 0.001                 # +0.10% above entry price
HARD_EXIT_SECONDS = 600               # 10 Minutes hard exit

binance_offset_ms = 0.0
active_executors = set()
executors_lock = threading.Lock()
funding_intervals_map = {}

def sync_binance_clock(client, dashboard_data, data_lock):
    global binance_offset_ms
    try:
        t_send = time.time() * 1000
        server_time_resp = client.time()
        t_recv = time.time() * 1000
        server_time = float(server_time_resp['serverTime'])
        binance_offset_ms = (server_time + ((t_recv - t_send) / 2.0)) - t_recv
        with data_lock:
            dashboard_data['binance']['offset'] = round(binance_offset_ms, 2)
    except Exception:
        pass

def refresh_funding_intervals(client):
    global funding_intervals_map
    try:
        info_list = client.funding_info()
        for item in info_list:
            sym = item.get("symbol", "")
            hrs = item.get("fundingIntervalHours")
            if sym and hrs:
                funding_intervals_map[sym] = f"{hrs}H"
    except Exception:
        pass

def get_synced_time_ms():
    return (time.time() * 1000) + binance_offset_ms

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

def scan_market_and_sync_state(client, dashboard_data, data_lock):
    try:
        premiums = client.mark_price()
    except Exception:
        return []

    now_ms = int(get_synced_time_ms())
    all_negative = []

    with data_lock:
        m_armed = list(dashboard_data['binance'].get('manual_armed', []))
        m_disarmed = list(dashboard_data['binance'].get('manual_disarmed', []))

    ticker_map = {}
    for item in premiums:
        sym = item.get("symbol", "")
        if not sym.endswith("USDT"):
            continue

        raw_rate = item.get("lastFundingRate", "")
        raw_next = item.get("nextFundingTime", "")
        if not raw_rate or not raw_next:
            continue
        try:
            rate = float(raw_rate)
            next_time_ms = int(raw_next)
            mark_price = float(item.get("markPrice", 0))
        except ValueError:
            continue

        time_diff = next_time_ms - now_ms
        if time_diff <= 1500:
            continue

        cycle_str = funding_intervals_map.get(sym)
        if not cycle_str:
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
            "last_price": mark_price
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
        dashboard_data['binance']['manual_armed'] = valid_m_armed
        dashboard_data['binance']['manual_disarmed'] = valid_m_disarmed
        dashboard_data['binance']['top20'] = top20_list
        if effective_armed:
            dashboard_data['binance']['status'] = f"ARMED ({len(effective_armed)} Coins)"
            dashboard_data['binance']['symbols'] = ", ".join([f"{c['symbol']} ({c['arm_mode']})" for c in effective_armed])
            dashboard_data['binance']['rates'] = ", ".join([c['rate_str'] for c in effective_armed])
            dashboard_data['binance']['settlement'] = effective_armed[0]['settle_str']
        else:
            dashboard_data['binance']['status'] = "SCANNING (Top 20 Live)"
            dashboard_data['binance']['symbols'] = "Scanning..."
            dashboard_data['binance']['rates'] = "--"
            dashboard_data['binance']['settlement'] = "--"

    return effective_armed

def prepare_coin_parameters(client, symbol, price, exchange_info, margin_usd=0.5):
    sym_info = next((s for s in exchange_info.get('symbols', []) if s['symbol'] == symbol), None)
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

    applied_leverage = MAX_ALLOWED_LEVERAGE
    try:
        client.change_leverage(symbol=symbol, leverage=int(applied_leverage))
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

def parallel_coin_executor(client, coin_meta, dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    symbol = coin_meta['symbol']
    settle_epoch = coin_meta['next_funding_time']
    arm_mode = coin_meta.get('arm_mode', 'AUTO')
    exec_key = (symbol, settle_epoch)

    try:
        precision_wait_until(settle_epoch - 1500)

        with data_lock:
            if symbol in dashboard_data['binance'].get('manual_disarmed', []):
                add_ui_log(f"🛑 [Binance] [{symbol}] Trade aborted before entry (Manually DISARMED).")
                return

        try:
            exchange_info = client.exchange_info()
        except Exception:
            exchange_info = {'symbols': []}

        try:
            mp = client.mark_price(symbol=symbol)
            live_price = float(mp.get("markPrice", coin_meta['last_price']))
            rate = float(mp.get("lastFundingRate", coin_meta['funding_rate']))
        except Exception:
            live_price = coin_meta['last_price']
            rate = coin_meta['funding_rate']

        if rate >= 0:
            add_ui_log(f"⚠️ [Binance] [{symbol}] Funding turned non-negative ({rate*100:+.4f}%). Aborting entry.")
            return

        coin_plan = prepare_coin_parameters(client, symbol, live_price, exchange_info, INITIAL_ENTRY_MARGIN_USD)
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
        actual_entry_price = live_price
        filled_qty_float = float(qty)

        try:
            entry_resp = client.new_order(symbol=symbol, side="BUY", type="MARKET", quantity=qty)
            entry_success = True
            if float(entry_resp.get("avgPrice", 0)) > 0:
                actual_entry_price = float(entry_resp["avgPrice"])
            if float(entry_resp.get("executedQty", 0)) > 0:
                filled_qty_float = float(entry_resp["executedQty"])
        except Exception as e:
            entry_err = str(e)

        if not entry_success:
            add_ui_log(f"❌ [Binance] [{symbol}] Market Entry Failed: {entry_err}")
            add_ledger_entry({
                "exchange": "Binance", "coin": f"{symbol} [{arm_mode}]", "rate": rate_str,
                "entry_time": entry_time_str, "exit_time": "--",
                "detail": f"Margin: $0.5 | Qty: {qty} ({leverage}x) | Entry Failed: {entry_err}",
                "badge_text": "ENTRY FAILED", "badge_class": "status-warn"
            })
            return

        if actual_entry_price == live_price:
            try:
                positions = client.get_position_risk(symbol=symbol)
                for p in positions:
                    if float(p.get("positionAmt", 0)) > 0:
                        filled_qty_float = float(p["positionAmt"])
                        if float(p.get("entryPrice", 0)) > 0:
                            actual_entry_price = float(p["entryPrice"])
                        break
            except Exception:
                pass

        filled_qty_str = f"{filled_qty_float:.{qty_decimals}f}" if qty_decimals > 0 else str(int(filled_qty_float))
        entry_price_str = f"{actual_entry_price:.{price_decimals}f}"

        # 2. PLACE STOP LOSS AT 200% OF FUNDING RATE BELOW ENTRY
        sl_discount = 2.0 * abs(rate)
        raw_sl_price = actual_entry_price * (1.0 - sl_discount)
        sl_price = max(tick_size, round(math.floor(raw_sl_price / tick_size) * tick_size, price_decimals))
        sl_price_str = f"{sl_price:.{price_decimals}f}"

        try:
            client.new_order(
                symbol=symbol, side="SELL", type="STOP_MARKET",
                stopPrice=sl_price_str, closePosition=True, workingType="CONTRACT_PRICE"
            )
        except Exception as e:
            add_ui_log(f"⚠️ [Binance] [{symbol}] SL Set Warning: {e}")

        # 3. PLACE +0.10% TAKE PROFIT SELL LIMIT ABOVE ENTRY
        raw_tp_price = actual_entry_price * (1.0 + TP_PROFIT_PCT)
        tp_price = round(math.ceil(raw_tp_price / tick_size) * tick_size, price_decimals)
        if tp_price <= actual_entry_price:
            tp_price = round(actual_entry_price + tick_size, price_decimals)
        tp_price_str = f"{tp_price:.{price_decimals}f}"

        tp_order_id = None
        t_tp_sent_ms = time.time() * 1000
        try:
            tp_resp = client.new_order(
                symbol=symbol, side="SELL", type="LIMIT",
                quantity=filled_qty_str, price=tp_price_str,
                timeInForce="GTC", reduceOnly=True
            )
            tp_order_id = tp_resp.get("orderId")
        except Exception as e:
            add_ui_log(f"⚠️ [Binance] [{symbol}] +0.10% TP Limit Exception: {e}")

        add_ui_log(f"🚀 [Binance] [{symbol}] MKT BUY @ {entry_price_str} | SL (-200% Fund): {sl_price_str} | TP Lim (+0.10%): {tp_price_str} | 10m Window")

        # 4. MONITOR TP LIMIT / SL FOR UP TO 10 MINUTES
        exit_outcome = "HARD EXIT (10m)"
        exit_time_str = "--"
        badge_class = "status-warn"
        position_closed = False

        while get_synced_time_ms() < t_hard_exit:
            try:
                positions = client.get_position_risk(symbol=symbol)
                pos_size = 0.0
                for p in positions:
                    if float(p.get("positionAmt", 0)) > 0:
                        pos_size = float(p.get("positionAmt"))
                        break

                if pos_size <= 0:
                    position_closed = True
                    exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
                    tp_filled = False
                    if tp_order_id:
                        q_resp = client.query_order(symbol=symbol, orderId=tp_order_id)
                        if q_resp.get("status") == "FILLED":
                            tp_filled = True
                            c_t = int(q_resp.get("time", 0))
                            u_t = int(q_resp.get("updateTime", 0))
                            sec_taken = round(max(0.05, (u_t - c_t) / 1000.0), 2) if u_t > c_t else round((time.time() * 1000 - t_tp_sent_ms) / 1000.0, 2)
                            exit_outcome = f"TP +0.10% FILLED ({sec_taken}s)"
                            badge_class = "status-success"
                            add_ui_log(f"🎯 [Binance] [{symbol}] +0.10% SELL LIMIT HIT @ {tp_price_str} in {sec_taken}s!")
                    if not tp_filled:
                        exit_outcome = "SL HIT (-200% Fund)"
                        badge_class = "status-warn"
                        add_ui_log(f"🛑 [Binance] [{symbol}] STOP LOSS HIT @ {sl_price_str}")
                    break
            except Exception:
                pass

            sleep_sec = 0.25 if abs(get_synced_time_ms() - settle_epoch) < 10000 else 1.0
            time.sleep(sleep_sec)

        # 5. IF 10 MINUTES EXPIRE & POSITION STILL OPEN -> CANCEL & HARD EXIT
        try:
            client.cancel_open_orders(symbol=symbol)
        except Exception:
            pass

        if not position_closed:
            exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
            try:
                positions = client.get_position_risk(symbol=symbol)
                pos_size = 0.0
                for p in positions:
                    if float(p.get("positionAmt", 0)) > 0:
                        pos_size = float(p.get("positionAmt"))
                        break
                if pos_size > 0:
                    rem_q = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
                    client.new_order(symbol=symbol, side="SELL", type="MARKET", quantity=rem_q, reduceOnly=True)
                    exit_outcome = "HARD EXIT (10m Timeout)"
                    add_ui_log(f"⏱ [Binance] [{symbol}] 10m Timeout -> Hard Market Exit Executed: {rem_q}")
                else:
                    exit_outcome = "CLOSED"
                    badge_class = "status-success"
            except Exception:
                exit_outcome = "EXIT FAILED"

        with data_lock:
            if symbol in dashboard_data['binance'].get('manual_armed', []):
                dashboard_data['binance']['manual_armed'].remove(symbol)

        add_ledger_entry({
            "exchange": "Binance",
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

def start_binance_loop(dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)
    add_ui_log("🚀 Binance Engine Active ($0.5 Margin | T-1s Mkt | -200% Fund SL | +0.10% TP | 10m Hard Exit)")
    refresh_funding_intervals(client)
    last_clock_sync = 0.0
    last_interval_sync = time.time()

    while True:
        try:
            if time.time() - last_clock_sync >= 15.0:
                sync_binance_clock(client, dashboard_data, data_lock)
                last_clock_sync = time.time()

            if time.time() - last_interval_sync >= 300.0:
                refresh_funding_intervals(client)
                last_interval_sync = time.time()

            armed_coins = scan_market_and_sync_state(client, dashboard_data, data_lock)
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
                                args=(client, c, dashboard_data, data_lock, add_ui_log, add_ledger_entry),
                                daemon=True
                            ).start()
                            add_ui_log(f"⚡ [Binance] Launching Execution Thread for {sym} ({c['arm_mode']})...")

            time.sleep(1.0)
        except Exception as e:
            add_ui_log(f"⚠️ [Binance] Loop Recovered: {e}")
            time.sleep(2)
