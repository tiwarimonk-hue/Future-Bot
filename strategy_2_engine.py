import os
import time
import math
import threading
from datetime import datetime, timezone, timedelta
from binance.um_futures import UMFutures
import executor  # For post-trade reconciliation
import config

IST = timezone(timedelta(hours=5, minutes=30))

BINANCE_API_KEY = config.API_KEY
BINANCE_API_SECRET = config.API_SECRET

MIN_FUNDING_RATE_THRESHOLD = -0.004   # -0.4% Auto Top 3 Threshold
INITIAL_ENTRY_MARGIN_USD = config.TRADE_MARGIN_USD  # Updated to $0.5 Margin globally
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
            if 'binance' not in dashboard_data:
                dashboard_data['binance'] = {}
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
        if 'binance' not in dashboard_data:
            dashboard_data['binance'] = {}
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

    with data_lock:
        dashboard_data['binance']['manual_armed'] = valid_m_armed
        dashboard_data['binance']['manual_disarmed'] = valid_m_disarmed
        if effective_armed:
            dashboard_data['binance']['status'] = f"ARMED ({len(effective_armed)} Coins) [T+210ms]"
        else:
            dashboard_data['binance']['status'] = "SCANNING (Top 20 Live)"

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
    if calc_qty < min_qty: calc_qty = min_qty
    if (calc_qty * price) < min_notional: calc_qty = math.ceil((min_notional / price) / qty_step) * qty_step
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
    entry_sent_time = ""
    exit_outcome = "HARD EXIT (10m)"
    badge_class = "status-warn"
    try:
        precision_wait_until(settle_epoch - 1000)
        with data_lock:
            if symbol in dashboard_data['binance'].get('manual_disarmed', []):
                add_ui_log(f"🛑 [Strategy 2] [{symbol}] Trade aborted before entry.")
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
            return
        coin_plan = prepare_coin_parameters(client, symbol, live_price, exchange_info, INITIAL_ENTRY_MARGIN_USD)
        qty, qty_decimals, price_decimals = coin_plan['qty'], coin_plan['qty_decimals'], coin_plan['price_decimals']
        tick_size, leverage, rate_str = coin_plan['tick_size'], coin_plan['leverage'], f"{rate*100:+.4f}%"

        # T+210ms Entry
        t_entry = settle_epoch + 210
        t_hard_exit = t_entry + (HARD_EXIT_SECONDS * 1000)
        precision_wait_until(t_entry)
        entry_sent_time = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
        entry_success = False
        actual_entry_price = live_price
        filled_qty_float = float(qty)
        try:
            entry_resp = client.new_order(symbol=symbol, side="BUY", type="MARKET", quantity=qty)
            entry_success = True
            if float(entry_resp.get("avgPrice", 0)) > 0: actual_entry_price = float(entry_resp["avgPrice"])
            if float(entry_resp.get("executedQty", 0)) > 0: filled_qty_float = float(entry_resp["executedQty"])
        except Exception as e:
            entry_err = str(e)
        if not entry_success:
            add_ledger_entry({"strategy": "strategy_2", "symbol": symbol, "funding_rate": rate_str, "tier": f"{arm_mode} ($0.5 Mgn)", "entry_sent": entry_sent_time, "entry_ack": "--", "entry_fill_time": "--", "entry_fill_price": "--", "exit_sent": "--", "exit_fill_time": "--", "exit_price": "--", "exit_type": "ENTRY FAILED", "status": "REJECTED", "details": f"Error: {entry_err}", "pnl_breakdown": "None", "dca_hits": []})
            return
        if actual_entry_price == live_price:
            try:
                for p in client.get_position_risk(symbol=symbol):
                    if float(p.get("positionAmt", 0)) > 0:
                        filled_qty_float = float(p["positionAmt"])
                        if float(p.get("entryPrice", 0)) > 0: actual_entry_price = float(p["entryPrice"])
                        break
            except: pass
        filled_qty_str = f"{filled_qty_float:.{qty_decimals}f}" if qty_decimals > 0 else str(int(filled_qty_float))
        entry_price_str = f"{actual_entry_price:.{price_decimals}f}"

        # SL and TP setup
        sl_discount = 2.0 * abs(rate)
        sl_price = max(tick_size, round(math.floor((actual_entry_price * (1.0 - sl_discount)) / tick_size) * tick_size, price_decimals))
        sl_price_str = f"{sl_price:.{price_decimals}f}"
        try: client.new_order(symbol=symbol, side="SELL", type="STOP_MARKET", stopPrice=sl_price_str, closePosition=True, workingType="CONTRACT_PRICE")
        except: pass

        tp_price = round(math.ceil((actual_entry_price * (1.0 + TP_PROFIT_PCT)) / tick_size) * tick_size, price_decimals)
        if tp_price <= actual_entry_price: tp_price = round(actual_entry_price + tick_size, price_decimals)
        tp_price_str = f"{tp_price:.{price_decimals}f}"
        tp_order_id, t_tp_sent_ms = None, time.time() * 1000
        try:
            tp_resp = client.new_order(symbol=symbol, side="SELL", type="LIMIT", quantity=filled_qty_str, price=tp_price_str, timeInForce="GTC", reduceOnly=True)
            tp_order_id = tp_resp.get("orderId")
        except: pass

        add_ui_log(f"🚀 [Strategy 2] [{symbol}] T+210ms BUY @ {entry_price_str} | SL: {sl_price_str} | TP: {tp_price_str}")

        position_closed = False
        exit_time_str = "--"
        while get_synced_time_ms() < t_hard_exit:
            try:
                pos_size = 0.0
                for p in client.get_position_risk(symbol=symbol):
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
                            exit_outcome, badge_class = f"TP +0.10% HIT", "status-success"
                    if not tp_filled: exit_outcome, badge_class = "SL HIT (-200% Fund)", "status-warn"
                    break
            except: pass
            time.sleep(0.25 if abs(get_synced_time_ms() - settle_epoch) < 10000 else 1.0)

        try: client.cancel_open_orders(symbol=symbol)
        except: pass
        if not position_closed:
            exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
            try:
                pos_size = sum(float(p.get("positionAmt", 0)) for p in client.get_position_risk(symbol=symbol) if float(p.get("positionAmt", 0)) > 0)
                if pos_size > 0:
                    client.new_order(symbol=symbol, side="SELL", type="MARKET", quantity=f"{pos_size:.{qty_decimals}f}" if qty_decimals>0 else str(int(pos_size)), reduceOnly=True)
                    exit_outcome = "HARD EXIT (10m Timeout)"
                else: exit_outcome = "CLOSED (Verified)"
            except: exit_outcome = "EXIT FAILED"

        # Reconciliation
        rec_entry_p, rec_exit_p, rec_entry_t, rec_exit_t, _, rec_pnl = executor.fetch_post_trade_reconciliation(symbol, settle_epoch - 5000)
        final_avg_entry = rec_entry_p or actual_entry_price
        final_exit_price = rec_exit_p or live_price
        entry_time_final = rec_entry_t or entry_sent_time
        exit_time_final = rec_exit_t or exit_time_str

        add_ledger_entry({
            "strategy": "strategy_2", "symbol": symbol, "funding_rate": rate_str, "tier": f"{arm_mode} ($0.5 Mgn @ T+210ms)",
            "entry_sent": entry_sent_time, "entry_ack": entry_time_final, "entry_rtt": "T+210ms Direct",
            "entry_fill_time": entry_time_final, "entry_fill_price": str(final_avg_entry),
            "exit_sent": exit_time_final, "exit_fill_time": exit_time_final, "exit_price": str(final_exit_price),
            "exit_type": exit_outcome, "status": exit_outcome, "details": f"Qty: {filled_qty_str} | SL: {sl_price_str} | TP: {tp_price_str}",
            "funding_fee_received": rec_pnl.get("funding_fee_str", "$0.0000"), "funding_fee_class": rec_pnl.get("funding_fee_class", "pnl-neutral"),
            "funding_fee_time": rec_pnl.get("funding_fee_time", "--:--:--"), "pnl": rec_pnl.get("pnl_str", "$0.0000"),
            "pnl_class": rec_pnl.get("pnl_class", "pnl-neutral"), "pnl_breakdown": rec_pnl.get("pnl_breakdown", ""), "dca_hits": []
        })
    finally:
        with executors_lock: active_executors.discard(exec_key)

def start_binance_loop(dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)
    add_ui_log("🚀 Strategy 2 Active ($0.5 Margin | T+210ms Mkt Buy | -200% Fund SL | +0.10% TP)")
    refresh_funding_intervals(client)
    last_clock_sync, last_interval_sync = 0.0, time.time()
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
                if 1500 < c['next_funding_time'] - now_ms <= 10000:
                    with executors_lock:
                        if (c['symbol'], c['next_funding_time']) not in active_executors:
                            active_executors.add((c['symbol'], c['next_funding_time']))
                            threading.Thread(target=parallel_coin_executor, args=(client, c, dashboard_data, data_lock, add_ui_log, add_ledger_entry), daemon=True).start()
            time.sleep(1.0)
        except Exception as e:
            time.sleep(2)
