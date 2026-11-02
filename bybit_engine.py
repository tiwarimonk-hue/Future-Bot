import os
import time
import math
import threading
from datetime import datetime, timezone, timedelta
from pybit.unified_trading import HTTP

IST = timezone(timedelta(hours=5, minutes=30))

API_KEY = os.environ.get('BYBIT_API_KEY', 'YOUR_BYBIT_API_KEY')
API_SECRET = os.environ.get('BYBIT_API_SECRET', 'YOUR_BYBIT_API_SECRET')

MIN_FUNDING_RATE_THRESHOLD = -0.004
INITIAL_ENTRY_MARGIN_USD = 1.0
MAX_TARGET_COINS = 3
MAX_ALLOWED_LEVERAGE = 20.0

clock_offset_ms = 0.0

def sync_bybit_clock(http_client, dashboard_data, data_lock):
    global clock_offset_ms
    try:
        t_send = time.time() * 1000
        server_time_resp = http_client.get_server_time()
        t_recv = time.time() * 1000

        server_time = float(server_time_resp['result']['timeNano']) / 1e6
        rtt = t_recv - t_send
        estimated_server_time = server_time + (rtt / 2.0)
        clock_offset_ms = estimated_server_time - t_recv

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

def scan_top_funding_opportunities(http_client, add_ui_log, dashboard_data, data_lock):
    try:
        tickers = http_client.get_tickers(category="linear")["result"]["list"]
    except Exception as e:
        add_ui_log(f"[Bybit] Scan API Error: {e}")
        return []

    now_ms = int(get_synced_time_ms())
    valid_candidates = []

    # Check Dual Engine Priority Lock
    dual_coin_armed = "--"
    with data_lock:
        dual_status = dashboard_data.get('dual', {}).get('status', '')
        if "ARMED" in dual_status.upper():
            dual_coin_armed = dashboard_data.get('dual', {}).get('coin', '--')

    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue

        # PRIORITY LOCK: Dual me use hone wale coin ko Bybit Solo me UNARM rakhein
        if sym == dual_coin_armed:
            continue

        raw_rate = t.get("fundingRate", "")
        raw_next = t.get("nextFundingTime", "")
        if not raw_rate or not raw_next:
            continue
        try:
            rate = float(raw_rate)
            next_time_ms = int(raw_next)
        except ValueError:
            continue

        time_diff = next_time_ms - now_ms
        if time_diff > 30000 and rate <= MIN_FUNDING_RATE_THRESHOLD:
            valid_candidates.append({
                "symbol": sym,
                "funding_rate": rate,
                "next_funding_time": next_time_ms,
                "last_price": float(t.get("lastPrice", 0)),
                "window_type": f"{round(time_diff / (3600 * 1000), 1)}H"
            })

    if not valid_candidates:
        return []

    earliest_time = min(c['next_funding_time'] for c in valid_candidates)
    imminent_candidates = [c for c in valid_candidates if abs(c['next_funding_time'] - earliest_time) <= 600000]
    imminent_candidates.sort(key=lambda x: x['funding_rate'])
    return imminent_candidates[:MAX_TARGET_COINS]

def prepare_coin_parameters(http_client, symbol, price, add_ui_log, margin_usd=1.0):
    try:
        inst_info = http_client.get_instruments_info(category="linear", symbol=symbol)["result"]["list"][0]
        exchange_max_lev = float(inst_info.get("leverageFilter", {}).get("maxLeverage", 20.0))
        lot_filter = inst_info['lotSizeFilter']
        price_filter = inst_info.get('priceFilter', {})
        qty_step = float(lot_filter["qtyStep"])
        min_qty = float(lot_filter['minOrderQty'])
        min_notional = float(lot_filter.get("minNotionalValue", 5.0))
        tick_size = float(price_filter.get("tickSize", "0.0001"))
    except Exception as e:
        add_ui_log(f"[Bybit] {symbol} Filter Error: {e}")
        exchange_max_lev = 20.0
        qty_step = 1.0
        min_qty = 1.0
        min_notional = 5.0
        tick_size = 0.0001

    applied_leverage = min(MAX_ALLOWED_LEVERAGE, exchange_max_lev)

    try:
        http_client.set_leverage(
            category="linear",
            symbol=symbol,
            buyLeverage=str(applied_leverage),
            sellLeverage=str(applied_leverage)
        )
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
        "symbol": symbol,
        "qty": formatted_qty,
        "leverage": applied_leverage,
        "qty_decimals": qty_decimals,
        "price_decimals": price_decimals,
        "tick_size": tick_size,
        "last_price": price
    }

def parallel_coin_executor(http_client, coin_plan, settle_epoch, add_ui_log, add_ledger_entry):
    symbol = coin_plan['symbol']
    rate = coin_plan['rate']
    rate_str = coin_plan['rate_str']
    qty = coin_plan['qty']
    qty_decimals = coin_plan['qty_decimals']
    price_decimals = coin_plan['price_decimals']
    tick_size = coin_plan['tick_size']
    leverage = coin_plan['leverage']

    t_entry = settle_epoch - 250
    t_exit = settle_epoch + 7000

    entry_result_str = "--"
    exit_result_str = "--"
    emergency_action_str = "None (Flat)"
    error_log = []
    entry_confirmed = False
    limit_order_id = None
    limit_price_str = "--"

    precision_wait_until(t_entry)
    entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

    try:
        try:
            ticker_data = http_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
            live_price = float(ticker_data.get("lastPrice", coin_plan['last_price']))
            live_rate = float(ticker_data.get("fundingRate", rate))
        except Exception:
            live_price = coin_plan['last_price']
            live_rate = rate

        multiplier = 1.50 if abs(live_rate) >= 0.02 else 1.20
        calc_discount = abs(live_rate) * multiplier
        effective_discount = max(0.006, calc_discount)
        raw_limit_price = live_price * (1.0 - effective_discount)

        limit_price = round(math.floor(raw_limit_price / tick_size) * tick_size, price_decimals)
        limit_price_str = f"{limit_price:.{price_decimals}f}"

        raw_sl_price = limit_price * 0.99
        sl_price = round(math.floor(raw_sl_price / tick_size) * tick_size, price_decimals)
        sl_price_str = f"{sl_price:.{price_decimals}f}"

        entry_resp = http_client.place_order(
            category="linear",
            symbol=symbol,
            side="Buy",
            orderType="Limit",
            price=limit_price_str,
            qty=qty,
            timeInForce="GTC",
            positionIdx=0,
            stopLoss=sl_price_str,
            slTriggerBy="LastPrice"
        )

        if entry_resp.get("retCode") == 0:
            limit_order_id = entry_resp["result"].get("orderId", "")
            entry_result_str = f"LIMIT PLACED (ID: {limit_order_id[:8]}..)"
            entry_confirmed = True
            add_ui_log(f"🎯 [Bybit] [{symbol}] BUY LIMIT at {limit_price_str} (Disc: {effective_discount*100:.2f}% | SL: {sl_price_str}) at {entry_time_str}")
        else:
            err_msg = f"RetCode {entry_resp.get('retCode')}: {entry_resp.get('retMsg')}"
            entry_result_str = "REJECTED"
            error_log.append(err_msg)
            add_ui_log(f"❌ [Bybit] [{symbol}] Rejected: {err_msg}")
    except Exception as e:
        err_msg = str(e)
        entry_result_str = "FAILED"
        error_log.append(err_msg)
        add_ui_log(f"❌ [Bybit] [{symbol}] Exception: {err_msg}")

    precision_wait_until(t_exit)
    exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

    try:
        if limit_order_id:
            http_client.cancel_order(category="linear", symbol=symbol, orderId=limit_order_id)
        else:
            http_client.cancel_all_orders(category="linear", symbol=symbol)
    except Exception:
        pass

    pos_filled = False
    try:
        pos_resp = http_client.get_positions(category="linear", symbol=symbol)
        positions = pos_resp.get("result", {}).get("list", [])
        pos_size = float(positions[0].get("size", 0)) if positions else 0.0

        if pos_size > 0:
            pos_filled = True
            formatted_exit_qty = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
            exit_resp = http_client.place_order(
                category="linear",
                symbol=symbol,
                side="Sell",
                orderType="Market",
                qty=formatted_exit_qty,
                reduceOnly=True,
                positionIdx=0
            )
            if exit_resp.get("retCode") == 0:
                exit_result_str = "CLOSED (T+7s)"
                add_ui_log(f"⚡ [Bybit] [{symbol}] HARD EXIT CLOSED: {formatted_exit_qty} at {exit_time_str}")
            else:
                exit_result_str = "EXIT REJECTED"
                error_log.append(f"Exit RetCode: {exit_resp.get('retMsg')}")
        else:
            exit_result_str = "FLAT / UNFILLED"
            add_ui_log(f"ℹ [Bybit] [{symbol}] Position Flat at T+7s")
    except Exception as e:
        exit_result_str = "EXCEPTION FAILED"
        error_log.append(str(e))

    for stage_name, target_ms in [("T+50s", settle_epoch + 50000), ("T+60s", settle_epoch + 60000)]:
        precision_wait_until(target_ms)
        try:
            pos_resp = http_client.get_positions(category="linear", symbol=symbol)
            positions = pos_resp.get("result", {}).get("list", [])
            pos_size = float(positions[0].get("size", 0)) if positions else 0.0

            if pos_size > 0:
                rem_qty = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
                http_client.place_order(
                    category="linear",
                    symbol=symbol,
                    side="Sell",
                    orderType="Market",
                    qty=rem_qty,
                    reduceOnly=True,
                    positionIdx=0
                )
                action_msg = f"{stage_name} Closed {rem_qty}"
                add_ui_log(f"🛡 [Bybit] [{symbol}] EMERGENCY EXIT: {action_msg}")
            else:
                break
        except Exception as e:
            error_log.append(str(e))

    add_ledger_entry({
        "exchange": "Bybit",
        "coin": symbol,
        "rate": rate_str,
        "entry_time": entry_time_str,
        "exit_time": exit_time_str,
        "detail": f"Limit: {limit_price_str} | Qty: {qty} ({leverage}x) | {entry_result_str} / {exit_result_str}",
        "badge_text": "CAPTURED" if pos_filled else ("UNFILLED" if entry_confirmed else "REJECTED"),
        "badge_class": "status-success" if pos_filled else "status-warn"
    })

def start_bybit_loop(dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    http_client = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
    add_ui_log("🚀 Bybit Engine Initialized (Threshold <= -0.4% | Dual-Conflict Locked)")

    while True:
        sync_bybit_clock(http_client, dashboard_data, data_lock)
        targets = scan_top_funding_opportunities(http_client, add_ui_log, dashboard_data, data_lock)

        if not targets:
            with data_lock:
                dashboard_data['bybit']['status'] = "SCANNING (&le; -0.4% / Dual Locked)"
                dashboard_data['bybit']['symbols'] = "Scanning..."
                dashboard_data['bybit']['rates'] = "--"
            time.sleep(3)
            continue

        settle_epoch = targets[0]['next_funding_time']
        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
        symbols_str = ", ".join([c['symbol'] for c in targets])
        rates_str = ", ".join([f"{c['funding_rate']*100:+.4f}%" for c in targets])

        with data_lock:
            dashboard_data['bybit']['status'] = f"ARMED ({len(targets)} Coins)"
            dashboard_data['bybit']['symbols'] = symbols_str
            dashboard_data['bybit']['rates'] = rates_str
            dashboard_data['bybit']['settlement'] = settle_dt.strftime('%H:%M:%S IST')

        t_precalc = settle_epoch - 2000
        interrupted = False

        while True:
            now_ms = get_synced_time_ms()
            if now_ms >= t_precalc:
                break
            time.sleep(1.0)

            # Re-check Dual Lock while waiting
            with data_lock:
                dual_status = dashboard_data.get('dual', {}).get('status', '')
                dual_coin = dashboard_data.get('dual', {}).get('coin', '')
                if "ARMED" in dual_status.upper() and any(t['symbol'] == dual_coin for t in targets):
                    add_ui_log(f"⚠️ [Bybit] Coin {dual_coin} was claimed by Dual Engine! Unarming from Bybit Solo.")
                    interrupted = True
                    break

        if interrupted:
            continue

        precision_wait_until(t_precalc)
        sync_bybit_clock(http_client, dashboard_data, data_lock)

        prepared_plans = []
        for t in targets:
            sym = t['symbol']
            try:
                live_price = float(http_client.get_tickers(category="linear", symbol=sym)["result"]["list"][0]["lastPrice"])
            except Exception:
                live_price = t['last_price']

            calc = prepare_coin_parameters(http_client, sym, live_price, add_ui_log, INITIAL_ENTRY_MARGIN_USD)
            calc['rate'] = t['funding_rate']
            calc['rate_str'] = f"{t['funding_rate']*100:+.4f}%"
            prepared_plans.append(calc)

        worker_threads = []
        for plan in prepared_plans:
            th = threading.Thread(
                target=parallel_coin_executor,
                args=(http_client, plan, settle_epoch, add_ui_log, add_ledger_entry),
                daemon=True
            )
            worker_threads.append(th)
            th.start()

        for th in worker_threads:
            th.join()

        time.sleep(5)
