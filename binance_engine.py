import os
import time
import math
import threading
from datetime import datetime, timezone, timedelta
from binance.um_futures import UMFutures

IST = timezone(timedelta(hours=5, minutes=30))

# ==========================================
# CONFIGURATION & SETTINGS
# ==========================================
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', 'YOUR_BINANCE_API_KEY')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', 'YOUR_BINANCE_API_SECRET')

MIN_FUNDING_RATE_THRESHOLD = -0.004  # -0.4% Threshold
INITIAL_ENTRY_MARGIN_USD = 1.0       # $1 Margin strictly kept
MAX_TARGET_COINS = 3                 # Top 3 Coins Parallel
MAX_ALLOWED_LEVERAGE = 20            # Strictly capped at 20x

binance_offset_ms = 0.0

def sync_binance_clock(client, dashboard_data, data_lock):
    global binance_offset_ms
    try:
        t_send = time.time() * 1000
        server_time_resp = client.time()
        t_recv = time.time() * 1000

        server_time = float(server_time_resp['serverTime'])
        rtt = t_recv - t_send
        estimated_server_time = server_time + (rtt / 2.0)
        binance_offset_ms = estimated_server_time - t_recv

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

def scan_top_funding_opportunities(client, add_ui_log):
    try:
        premiums = client.mark_price()
    except Exception as e:
        add_ui_log(f"[Binance] Scan API Error: {e}")
        return []

    now_ms = int(get_synced_time_ms())
    valid_candidates = []

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
        except ValueError:
            continue

        time_diff = next_time_ms - now_ms
        if time_diff > 30000 and rate <= MIN_FUNDING_RATE_THRESHOLD:
            valid_candidates.append({
                "symbol": sym,
                "funding_rate": rate,
                "next_funding_time": next_time_ms,
                "last_price": float(item.get("markPrice", 0)),
                "window_type": f"{round(time_diff / (3600 * 1000), 1)}H"
            })

    if not valid_candidates:
        return []

    earliest_time = min(c['next_funding_time'] for c in valid_candidates)
    imminent_candidates = [c for c in valid_candidates if abs(c['next_funding_time'] - earliest_time) <= 600000]
    imminent_candidates.sort(key=lambda x: x['funding_rate'])
    return imminent_candidates[:MAX_TARGET_COINS]

def prepare_coin_parameters(client, symbol, price, exchange_info, add_ui_log, margin_usd=1.0):
    sym_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)
    
    qty_step = 1.0
    min_qty = 1.0
    tick_size = 0.0001
    min_notional = 5.0

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
    except Exception as e:
        # Agar coin ka maximum leverage 20x se kam hai, toh permitted max use karein
        try:
            brackets = client.leverage_brackets(symbol=symbol)
            if brackets:
                max_allowed = int(brackets[0]['brackets'][0]['initialLeverage'])
                applied_leverage = min(MAX_ALLOWED_LEVERAGE, max_allowed)
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
        "symbol": symbol,
        "qty": formatted_qty,
        "leverage": applied_leverage,
        "qty_decimals": qty_decimals,
        "price_decimals": price_decimals,
        "tick_size": tick_size,
        "last_price": price
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

    t_entry = settle_epoch - 250      # T - 250ms Limit Order
    t_exit = settle_epoch + 7000      # T + 7s Hard Exit

    entry_result_str = "--"
    exit_result_str = "--"
    emergency_action_str = "None (Flat)"
    error_log = []
    entry_confirmed = False
    limit_order_id = None
    limit_price_str = "--"

    # 1. PRECISE LIMIT ENTRY AT T - 250ms
    precision_wait_until(t_entry)
    entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

    try:
        try:
            ticker_data = client.ticker_price(symbol=symbol)
            live_price = float(ticker_data.get("price", coin_plan['last_price']))
        except Exception:
            live_price = coin_plan['last_price']

        multiplier = 1.50 if abs(rate) >= 0.02 else 1.20
        calc_discount = abs(rate) * multiplier
        effective_discount = max(0.006, calc_discount)
        raw_limit_price = live_price * (1.0 - effective_discount)

        limit_price = round(math.floor(raw_limit_price / tick_size) * tick_size, price_decimals)
        limit_price_str = f"{limit_price:.{price_decimals}f}"

        raw_sl_price = limit_price * 0.99
        sl_price = round(math.floor(raw_sl_price / tick_size) * tick_size, price_decimals)
        sl_price_str = f"{sl_price:.{price_decimals}f}"

        # Place Buy Limit Order on Binance
        entry_resp = client.new_order(
            symbol=symbol,
            side="BUY",
            type="LIMIT",
            quantity=qty,
            price=limit_price_str,
            timeInForce="GTC"
        )
        limit_order_id = entry_resp.get("orderId")
        entry_confirmed = True
        entry_result_str = f"LIMIT PLACED ({str(limit_order_id)[:8]}..)"
        add_ui_log(f"🎯 [Binance] [{symbol}] BUY LIMIT at {limit_price_str} (Disc: {effective_discount*100:.2f}% | SL: {sl_price_str}) at {entry_time_str}")

        # Place Native 1% Stop Loss (STOP_MARKET with closePosition=True)
        try:
            client.new_order(
                symbol=symbol,
                side="SELL",
                type="STOP_MARKET",
                stopPrice=sl_price_str,
                closePosition=True
            )
        except Exception as e:
            add_ui_log(f"⚠️ [Binance] [{symbol}] Native SL Error: {e}")

    except Exception as e:
        err_msg = str(e)
        entry_result_str = "FAILED"
        error_log.append(err_msg)
        add_ui_log(f"❌ [Binance] [{symbol}] Order Exception: {err_msg}")

    # 2. HARD EXIT AT T + 7s
    precision_wait_until(t_exit)
    exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

    try:
        client.cancel_open_orders(symbol=symbol)
    except Exception:
        pass

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
            formatted_exit_qty = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
            client.new_order(
                symbol=symbol,
                side="SELL",
                type="MARKET",
                quantity=formatted_exit_qty,
                reduceOnly=True
            )
            exit_result_str = "CLOSED (T+7s)"
            add_ui_log(f"⚡ [Binance] [{symbol}] HARD EXIT CLOSED: {formatted_exit_qty} at {exit_time_str}")
        else:
            exit_result_str = "FLAT / UNFILLED"
            add_ui_log(f"ℹ [Binance] [{symbol}] Position Flat at T+7s")
    except Exception as e:
        exit_result_str = "EXCEPTION FAILED"
        error_log.append(str(e))

    # 3. EMERGENCY EXITS (T + 50s and T + 60s)
    emergency_times = [("T+50s", settle_epoch + 50000), ("T+60s", settle_epoch + 60000)]
    position_cleared = False
    triggered_stages = []

    for stage_name, target_ms in emergency_times:
        precision_wait_until(target_ms)
        try:
            positions = client.get_position_risk(symbol=symbol)
            pos_size = 0.0
            for p in positions:
                if float(p.get("positionAmt", 0)) > 0:
                    pos_size = float(p.get("positionAmt"))
                    break

            if pos_size > 0:
                rem_qty = f"{pos_size:.{qty_decimals}f}" if qty_decimals > 0 else str(int(pos_size))
                client.new_order(
                    symbol=symbol,
                    side="SELL",
                    type="MARKET",
                    quantity=rem_qty,
                    reduceOnly=True
                )
                action_msg = f"{stage_name} Closed {rem_qty}"
                triggered_stages.append(action_msg)
                add_ui_log(f"🛡 [Binance] [{symbol}] EMERGENCY EXIT: {action_msg}")
            else:
                position_cleared = True
                break
        except Exception as e:
            error_log.append(str(e))

    if not entry_confirmed:
        badge_text, badge_class = "REJECTED", "status-error"
    elif triggered_stages:
        emergency_action_str = " | ".join(triggered_stages)
        badge_text, badge_class = "EMERGENCY CLOSED", "status-warn"
    elif pos_filled and position_cleared:
        emergency_action_str = "Clean (T+7s)"
        badge_text, badge_class = "CAPTURED", "status-success"
    else:
        badge_text, badge_class = "UNFILLED", "status-warn"

    add_ledger_entry({
        "exchange": "Binance",
        "coin": symbol,
        "rate": rate_str,
        "entry_time": entry_time_str,
        "exit_time": exit_time_str,
        "detail": f"Limit: {limit_price_str} | Qty: {qty} ({leverage}x) | {entry_result_str} / {exit_result_str}",
        "badge_text": badge_text,
        "badge_class": badge_class
    })

# ==========================================
# MASTER ENTRY POINT (Called from main.py)
# ==========================================
def start_binance_loop(dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)
    add_ui_log("🚀 Binance Engine Initialized (Threshold <= -0.4% | $1 Margin | Capped 20x | T-250ms Entry)")

    while True:
        sync_binance_clock(client, dashboard_data, data_lock)
        targets = scan_top_funding_opportunities(client, add_ui_log)

        if not targets:
            with data_lock:
                dashboard_data['binance']['status'] = "SCANNING (No coin <= -0.4%)"
                dashboard_data['binance']['symbols'] = "Scanning..."
                dashboard_data['binance']['rates'] = "--"
            time.sleep(3)
            continue

        settle_epoch = targets[0]['next_funding_time']
        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
        symbols_str = ", ".join([c['symbol'] for c in targets])
        rates_str = ", ".join([f"{c['funding_rate']*100:+.4f}%" for c in targets])

        with data_lock:
            dashboard_data['binance']['status'] = f"ARMED ({len(targets)} Coins)"
            dashboard_data['binance']['symbols'] = symbols_str
            dashboard_data['binance']['rates'] = rates_str
            dashboard_data['binance']['settlement'] = settle_dt.strftime('%H:%M:%S IST')

        add_ui_log(f"🎯 [Binance] ARMED: {symbols_str} | Settlement: {settle_dt.strftime('%H:%M:%S IST')}")

        t_precalc = settle_epoch - 2000
        last_5min_scan = time.time()
        last_rate_update = time.time()
        interrupted = False

        while True:
            now_ms = get_synced_time_ms()
            if now_ms >= t_precalc:
                break

            time.sleep(1.0)

            # Live Rate Update (Every 3s)
            if time.time() - last_rate_update >= 3.0:
                last_rate_update = time.time()
                try:
                    premiums = client.mark_price()
                    prem_dict = {p['symbol']: float(p['lastFundingRate']) for p in premiums}
                    active_rates = []
                    threshold_breached = False

                    for t in targets:
                        cur_rate = prem_dict.get(t['symbol'], t['funding_rate'])
                        t['funding_rate'] = cur_rate
                        active_rates.append(f"{cur_rate * 100:+.4f}%")

                        if cur_rate > MIN_FUNDING_RATE_THRESHOLD:
                            add_ui_log(f"⚠️ [Binance] {t['symbol']} weakened above -0.4%. Re-arming!")
                            threshold_breached = True

                    with data_lock:
                        dashboard_data['binance']['rates'] = ", ".join(active_rates)

                    if threshold_breached:
                        interrupted = True
                        break
                except Exception:
                    pass

            # 5-Minute Full Rescan
            if time.time() - last_5min_scan >= 300:
                last_5min_scan = time.time()
                add_ui_log("🔄 [Binance] 5-Minute Rescan running...")
                sync_binance_clock(client, dashboard_data, data_lock)
                fresh_targets = scan_top_funding_opportunities(client, add_ui_log)
                if fresh_targets:
                    fresh_symbols = [c['symbol'] for c in fresh_targets]
                    current_symbols = [c['symbol'] for c in targets]
                    if fresh_symbols != current_symbols or fresh_targets[0]['next_funding_time'] < settle_epoch:
                        add_ui_log(f"⚡ [Binance] Target coins switched to {', '.join(fresh_symbols)}")
                        interrupted = True
                        break

        if interrupted:
            continue

        # T - 2s Pre-Calculation
        precision_wait_until(t_precalc)
        sync_binance_clock(client, dashboard_data, data_lock)

        try:
            exchange_info = client.exchange_info()
        except Exception:
            exchange_info = {'symbols': []}

        prepared_plans = []
        for t in targets:
            sym = t['symbol']
            try:
                live_price = float(client.ticker_price(symbol=sym)['price'])
            except Exception:
                live_price = t['last_price']

            calc = prepare_coin_parameters(client, sym, live_price, exchange_info, add_ui_log, INITIAL_ENTRY_MARGIN_USD)
            calc['rate'] = t['funding_rate']
            calc['rate_str'] = f"{t['funding_rate']*100:+.4f}%"
            prepared_plans.append(calc)

        # Parallel Launch
        worker_threads = []
        for plan in prepared_plans:
            th = threading.Thread(
                target=parallel_coin_executor,
                args=(client, plan, settle_epoch, add_ui_log, add_ledger_entry),
                daemon=True
            )
            worker_threads.append(th)
            th.start()

        for th in worker_threads:
            th.join()

        add_ui_log("✅ [Binance] Cycle complete. Searching next targets...")
        time.sleep(5)
