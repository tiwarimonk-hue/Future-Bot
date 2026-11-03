import os
import time
import math
import threading
from datetime import datetime, timezone, timedelta
from binance.um_futures import UMFutures

IST = timezone(timedelta(hours=5, minutes=30))

BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', 'YOUR_BINANCE_API_KEY')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', 'YOUR_BINANCE_API_SECRET')

MIN_FUNDING_RATE_THRESHOLD = -0.004
INITIAL_ENTRY_MARGIN_USD = 1.0
MAX_TARGET_COINS = 3
MAX_ALLOWED_LEVERAGE = 20

binance_offset_ms = 0.0

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

def scan_top_funding_opportunities(client, add_ui_log, dashboard_data, data_lock):
    try:
        premiums = client.mark_price()
    except Exception:
        return []

    now_ms = int(get_synced_time_ms())
    valid_candidates = []

    dual_coin_armed = "--"
    with data_lock:
        dual_status = dashboard_data.get('dual', {}).get('status', '')
        if "ARMED" in dual_status.upper():
            dual_coin_armed = dashboard_data.get('dual', {}).get('coin', '--')

    for item in premiums:
        sym = item.get("symbol", "")
        if not sym.endswith("USDT") or sym == dual_coin_armed:
            continue

        raw_rate = item.get("lastFundingRate", "")
        raw_next = item.get("nextFundingTime", "")
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
                "last_price": float(item.get("markPrice", 0))
            })

    if not valid_candidates:
        return []

    earliest_time = min(c['next_funding_time'] for c in valid_candidates)
    imminent = [c for c in valid_candidates if abs(c['next_funding_time'] - earliest_time) <= 600000]
    imminent.sort(key=lambda x: x['funding_rate'])
    return imminent[:MAX_TARGET_COINS]

def prepare_coin_parameters(client, symbol, price, exchange_info, margin_usd=1.0):
    sym_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)
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
    if calc_qty < min_qty: calc_qty = min_qty
    if (calc_qty * price) < min_notional:
        calc_qty = math.ceil((min_notional / price) / qty_step) * qty_step

    formatted_qty = f"{calc_qty:.{qty_decimals}f}" if qty_decimals > 0 else str(int(calc_qty))
    return {
        "symbol": symbol, "qty": formatted_qty, "leverage": applied_leverage,
        "qty_decimals": qty_decimals, "price_decimals": price_decimals,
        "tick_size": tick_size, "last_price": price
    }

def parallel_coin_executor(client, coin_plan, settle_epoch, add_ui_log, add_ledger_entry):
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
    limit_price_str = "--"

    precision_wait_until(t_entry)
    entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

    try:
        try:
            live_price = float(client.ticker_price(symbol=symbol)["price"])
        except Exception:
            live_price = coin_plan['last_price']

        multiplier = 1.50 if abs(rate) >= 0.02 else 1.20
        effective_discount = max(0.006, abs(rate) * multiplier)
        raw_limit_price = live_price * (1.0 - effective_discount)

        limit_price = round(math.floor(raw_limit_price / tick_size) * tick_size, price_decimals)
        limit_price_str = f"{limit_price:.{price_decimals}f}"
        sl_price = round(math.floor((limit_price * 0.99) / tick_size) * tick_size, price_decimals)
        sl_price_str = f"{sl_price:.{price_decimals}f}"

        client.new_order(symbol=symbol, side="BUY", type="LIMIT", quantity=qty, price=limit_price_str, timeInForce="GTC")
        try:
            client.new_order(symbol=symbol, side="SELL", type="STOP_MARKET", stopPrice=sl_price_str, closePosition=True)
        except Exception:
            pass
        entry_result_str = "LIMIT PLACED"
        add_ui_log(f"🎯 [Binance] [{symbol}] BUY LIMIT at {limit_price_str} (Disc: {effective_discount*100:.2f}%)")
    except Exception:
        entry_result_str = "FAILED"

    precision_wait_until(t_exit)
    exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

    try: client.cancel_open_orders(symbol=symbol)
    except Exception: pass

    pos_filled = False
    try:
        positions = client.get_position_risk(symbol=symbol)
        pos_size = 0.0
        for p in positions:
            if float(p.get("positionAmt", 0)) > 0:
                pos_size = float(p.get("positionAmt"))
                break

        if pos_size > 0:
            pos_filled = True
            rem_q = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
            client.new_order(symbol=symbol, side="SELL", type="MARKET", quantity=rem_q, reduceOnly=True)
            exit_result_str = "CLOSED (T+7s)"
            add_ui_log(f"⚡ [Binance] [{symbol}] HARD EXIT CLOSED: {rem_q}")
        else:
            exit_result_str = "FLAT / UNFILLED"
    except Exception:
        exit_result_str = "FAILED"

    add_ledger_entry({
        "exchange": "Binance", "coin": symbol, "rate": rate_str,
        "entry_time": entry_time_str, "exit_time": exit_time_str,
        "detail": f"Limit: {limit_price_str} | Qty: {qty} ({leverage}x) | {entry_result_str} / {exit_result_str}",
        "badge_text": "CAPTURED" if pos_filled else "UNFILLED",
        "badge_class": "status-success" if pos_filled else "status-warn"
    })

def start_binance_loop(dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)
    add_ui_log("🚀 Binance Engine Initialized (Threshold <= -0.4% | Dynamic Re-Scan Enabled)")

    while True:
        sync_binance_clock(client, dashboard_data, data_lock)
        targets = scan_top_funding_opportunities(client, add_ui_log, dashboard_data, data_lock)

        if not targets:
            with data_lock:
                dashboard_data['binance']['status'] = "SCANNING (&le; -0.4%)"
                dashboard_data['binance']['symbols'] = "Scanning..."
                dashboard_data['binance']['rates'] = "--"
            time.sleep(3)
            continue

        settle_epoch = targets[0]['next_funding_time']
        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)

        with data_lock:
            dashboard_data['binance']['status'] = f"ARMED ({len(targets)} Coins)"
            dashboard_data['binance']['symbols'] = ", ".join([c['symbol'] for c in targets])
            dashboard_data['binance']['rates'] = ", ".join([f"{c['funding_rate']*100:+.4f}%" for c in targets])
            dashboard_data['binance']['settlement'] = settle_dt.strftime('%H:%M:%S IST')

        t_precalc = settle_epoch - 2000
        last_rescan_time = time.time()

        # Dynamic ARMED loop: Refreshes every 15s to catch new deeper coins like CATUSDT
        while True:
            now_ms = get_synced_time_ms()
            if now_ms >= t_precalc:
                break
            time.sleep(1.0)

            if time.time() - last_rescan_time >= 15.0:
                last_rescan_time = time.time()
                fresh_targets = scan_top_funding_opportunities(client, add_ui_log, dashboard_data, data_lock)
                if fresh_targets and fresh_targets[0]['next_funding_time'] == settle_epoch:
                    current_symbols = [t['symbol'] for t in targets]
                    fresh_symbols = [t['symbol'] for t in fresh_targets]
                    if current_symbols != fresh_symbols:
                        targets = fresh_targets
                        with data_lock:
                            dashboard_data['binance']['status'] = f"ARMED ({len(targets)} Coins)"
                            dashboard_data['binance']['symbols'] = ", ".join([c['symbol'] for c in targets])
                            dashboard_data['binance']['rates'] = ", ".join([f"{c['funding_rate']*100:+.4f}%" for c in targets])
                        add_ui_log(f"🔄 [Binance] Targets updated dynamically: {dashboard_data['binance']['symbols']}")

        precision_wait_until(t_precalc)
        sync_binance_clock(client, dashboard_data, data_lock)

        try: exchange_info = client.exchange_info()
        except Exception: exchange_info = {'symbols': []}

        prepared_plans = []
        for t in targets:
            sym = t['symbol']
            try: live_price = float(client.ticker_price(symbol=sym)['price'])
            except Exception: live_price = t['last_price']

            calc = prepare_coin_parameters(client, sym, live_price, exchange_info, INITIAL_ENTRY_MARGIN_USD)
            calc['rate'] = t['funding_rate']
            calc['rate_str'] = f"{t['funding_rate']*100:+.4f}%"
            prepared_plans.append(calc)

        threads = [threading.Thread(target=parallel_coin_executor, args=(client, p, settle_epoch, add_ui_log, add_ledger_entry), daemon=True) for p in prepared_plans]
        for th in threads: th.start()
        for th in threads: th.join()

        time.sleep(5)
