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

def scan_top_funding_opportunities(http_client, add_ui_log, dashboard_data, data_lock):
    try:
        tickers = http_client.get_tickers(category="linear")["result"]["list"]
    except Exception:
        return []

    now_ms = int(get_synced_time_ms())
    valid_candidates = []

    dual_coin_armed = "--"
    with data_lock:
        dual_status = dashboard_data.get('dual', {}).get('status', '')
        if "ARMED" in dual_status.upper():
            dual_coin_armed = dashboard_data.get('dual', {}).get('coin', '--')

    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT") or sym == dual_coin_armed:
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
                "last_price": float(t.get("lastPrice", 0))
            })

    if not valid_candidates:
        return []

    earliest_time = min(c['next_funding_time'] for c in valid_candidates)
    imminent = [c for c in valid_candidates if abs(c['next_funding_time'] - earliest_time) <= 600000]
    imminent.sort(key=lambda x: x['funding_rate'])
    return imminent[:MAX_TARGET_COINS]

def prepare_coin_parameters(http_client, symbol, price, margin_usd=1.0):
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
    if calc_qty < min_qty: calc_qty = min_qty
    if (calc_qty * price) < min_notional:
        calc_qty = math.ceil((min_notional / price) / qty_step) * qty_step

    formatted_qty = f"{calc_qty:.{qty_decimals}f}" if qty_decimals > 0 else str(int(calc_qty))
    return {
        "symbol": symbol, "qty": formatted_qty, "leverage": applied_leverage,
        "qty_decimals": qty_decimals, "price_decimals": price_decimals,
        "tick_size": tick_size, "last_price": price
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

    entry_result_str, exit_result_str = "--", "--"
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
            live_price, live_rate = coin_plan['last_price'], rate

        # 110% LIMIT ORDER MULTIPLIER & 0.6% MIN FLOOR
        multiplier = 1.10
        effective_discount = max(0.006, abs(live_rate) * multiplier)
        raw_limit_price = live_price * (1.0 - effective_discount)

        limit_price = round(math.floor(raw_limit_price / tick_size) * tick_size, price_decimals)
        limit_price_str = f"{limit_price:.{price_decimals}f}"

        # 0.7% STOP LOSS (0.993)
        raw_sl_price = limit_price * (1.0 - 0.007)
        sl_price = round(math.floor(raw_sl_price / tick_size) * tick_size, price_decimals)
        sl_price_str = f"{sl_price:.{price_decimals}f}"

        entry_resp = http_client.place_order(
            category="linear", symbol=symbol, side="Buy", orderType="Limit",
            price=limit_price_str, qty=qty, timeInForce="GTC", positionIdx=0,
            stopLoss=sl_price_str, slTriggerBy="LastPrice"
        )
        if entry_resp.get("retCode") == 0:
            limit_order_id = entry_resp["result"].get("orderId", "")
            entry_result_str = "LIMIT PLACED"
            add_ui_log(f"🎯 [Bybit] [{symbol}] BUY LIMIT at {limit_price_str} (Disc 110%: {effective_discount*100:.2f}% | SL -0.7%: {sl_price_str})")
        else:
            entry_result_str = "REJECTED"
    except Exception:
        entry_result_str = "FAILED"

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
            rem_q = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
            http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=rem_q, reduceOnly=True, positionIdx=0)
            exit_result_str = "CLOSED (T+7s)"
            add_ui_log(f"⚡ [Bybit] [{symbol}] HARD EXIT CLOSED: {rem_q}")
        else:
            exit_result_str = "FLAT / UNFILLED"
    except Exception:
        exit_result_str = "EXCEPTION FAILED"

    for stage_ms in [settle_epoch + 50000, settle_epoch + 60000]:
        precision_wait_until(stage_ms)
        try:
            pos_resp = http_client.get_positions(category="linear", symbol=symbol)
            positions = pos_resp.get("result", {}).get("list", [])
            pos_size = float(positions[0].get("size", 0)) if positions else 0.0
            if pos_size > 0:
                rem_q = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
                http_client.place_order(category="linear", symbol=symbol, side="Sell", orderType="Market", qty=rem_q, reduceOnly=True, positionIdx=0)
            else:
                break
        except Exception:
            pass

    add_ledger_entry({
        "exchange": "Bybit", "coin": symbol, "rate": rate_str,
        "entry_time": entry_time_str, "exit_time": exit_time_str,
        "detail": f"Limit (110%): {limit_price_str} | SL (-0.7%): {sl_price_str} | Qty: {qty} ({leverage}x) | {entry_result_str} / {exit_result_str}",
        "badge_text": "CAPTURED" if pos_filled else "UNFILLED",
        "badge_class": "status-success" if pos_filled else "status-warn"
    })

def start_bybit_loop(dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    http_client = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
    add_ui_log("🚀 Bybit Engine Initialized (Limit 110% | SL -0.7% | Dynamic Re-Scan Enabled)")

    while True:
        sync_bybit_clock(http_client, dashboard_data, data_lock)
        targets = scan_top_funding_opportunities(http_client, add_ui_log, dashboard_data, data_lock)

        if not targets:
            with data_lock:
                dashboard_data['bybit']['status'] = "SCANNING (&le; -0.4%)"
                dashboard_data['bybit']['symbols'] = "Scanning..."
                dashboard_data['bybit']['rates'] = "--"
            time.sleep(3)
            continue

        settle_epoch = targets[0]['next_funding_time']
        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)

        with data_lock:
            dashboard_data['bybit']['status'] = f"ARMED ({len(targets)} Coins)"
            dashboard_data['bybit']['symbols'] = ", ".join([c['symbol'] for c in targets])
            dashboard_data['bybit']['rates'] = ", ".join([f"{c['funding_rate']*100:+.4f}%" for c in targets])
            dashboard_data['bybit']['settlement'] = settle_dt.strftime('%H:%M:%S IST')

        t_precalc = settle_epoch - 2000
        last_rescan_time = time.time()

        while True:
            now_ms = get_synced_time_ms()
            if now_ms >= t_precalc:
                break
            time.sleep(1.0)

            if time.time() - last_rescan_time >= 15.0:
                last_rescan_time = time.time()
                fresh_targets = scan_top_funding_opportunities(http_client, add_ui_log, dashboard_data, data_lock)
                if fresh_targets and fresh_targets[0]['next_funding_time'] == settle_epoch:
                    current_symbols = [t['symbol'] for t in targets]
                    fresh_symbols = [t['symbol'] for t in fresh_targets]
                    if current_symbols != fresh_symbols:
                        targets = fresh_targets
                        with data_lock:
                            dashboard_data['bybit']['status'] = f"ARMED ({len(targets)} Coins)"
                            dashboard_data['bybit']['symbols'] = ", ".join([c['symbol'] for c in targets])
                            dashboard_data['bybit']['rates'] = ", ".join([f"{c['funding_rate']*100:+.4f}%" for c in targets])
                        add_ui_log(f"🔄 [Bybit] Targets updated dynamically: {dashboard_data['bybit']['symbols']}")

        precision_wait_until(t_precalc)
        sync_bybit_clock(http_client, dashboard_data, data_lock)

        prepared_plans = []
        for t in targets:
            sym = t['symbol']
            try:
                live_price = float(http_client.get_tickers(category="linear", symbol=sym)["result"]["list"][0]["lastPrice"])
            except Exception:
                live_price = t['last_price']

            calc = prepare_coin_parameters(http_client, sym, live_price, INITIAL_ENTRY_MARGIN_USD)
            calc['rate'] = t['funding_rate']
            calc['rate_str'] = f"{t['funding_rate']*100:+.4f}%"
            prepared_plans.append(calc)

        threads = [threading.Thread(target=parallel_coin_executor, args=(http_client, p, settle_epoch, add_ui_log, add_ledger_entry), daemon=True) for p in prepared_plans]
        for th in threads: th.start()
        for th in threads: th.join()

        time.sleep(5)
