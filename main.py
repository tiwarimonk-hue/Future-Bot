from __future__ import annotations
import gc
import time
import threading
from decimal import Decimal
from datetime import datetime, timedelta
import requests
import config
from binance_ws import BinanceFastLaneWS
from dashboard import (
    IST, data_lock, dashboard_data, add_ui_log,
    set_dashboard_status, record_completed_trade, run_web_server,
    load_blocked_coins
)
from scanner import (
    sync_binance_clock, get_synced_time_ms, precision_wait_until,
    format_duration, find_top_negative_funding_candidates,
    format_step, format_tick, calculate_order_specs
)
import executor
import strategy_2_engine

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
})

def get_seconds_until_next_scheduled_scan() -> tuple[int, str]:
    now = datetime.now(IST)
    for m in config.SCHEDULED_SCAN_MINUTES:
        if now.minute < m:
            target_dt = now.replace(minute=m, second=0, microsecond=0)
            sec = max(1, int((target_dt - now).total_seconds()))
            return sec, target_dt.strftime("%H:%M:%S IST")
    next_hour = now + timedelta(hours=1)
    target_dt = next_hour.replace(minute=config.SCHEDULED_SCAN_MINUTES[0], second=0, microsecond=0)
    sec = max(1, int((target_dt - now).total_seconds()))
    return sec, target_dt.strftime("%H:%M:%S IST")

def get_strategy_label(rate_val: float) -> str:
    if abs(float(rate_val)) >= config.FR_THRESHOLD_RATIO:
        return f"COND 1 (>=0.9%): 4 LIMITS (30/50/70/130% FR @ ${config.LIMIT_ORDER_MARGIN_USD}) [T-{config.ENTRY_BEFORE_SETTLE_MS}ms]"
    return f"COND 2 (<0.9%): 5 LIMITS (30/50/70% @ ${config.LIMIT_ORDER_MARGIN_USD} + 2 DCA @ ${config.DCA_SPLIT_MARGIN_USD}) [T-{config.ENTRY_BEFORE_SETTLE_MS}ms]"

def evaluate_dca_condition_specs(current_rate: float) -> tuple[float, float, float, str, bool]:
    abs_r = abs(float(current_rate))
    if abs_r >= config.FR_THRESHOLD_RATIO:
        g1 = round(abs_r * config.GRID_RATIO_DCA_HIGH, 5)
        g2 = 0.0
        sl_r = config.SL_BELOW_LIMIT_RATIO_COND_1
        lbl = f"COND 1 (|FR| >= 0.90%): 3 Shallow (30/50/70% FR @ ${config.LIMIT_ORDER_MARGIN_USD}) + DCA #1 (-{g1 * 100:.3f}% @ ${config.LIMIT_ORDER_MARGIN_USD}), SL (-{sl_r * 100:.2f}%)"
        return g1, g2, sl_r, lbl, True
    else:
        g1 = max(config.MIN_GRID_GAP_DCA_1_LOW, round(abs_r * config.GRID_RATIO_DCA_LOW, 5))
        g2 = 0.0
        sl_r = config.SL_BELOW_LIMIT_RATIO_COND_2
        lbl = f"COND 2 (|FR| < 0.90%): 3 Shallow (30/50/70% FR @ ${config.LIMIT_ORDER_MARGIN_USD}) + L1 (-{g1 * 100:.3f}% @ ${config.DCA_SPLIT_MARGIN_USD}) + L2 (L1 - 0.50% @ ${config.DCA_SPLIT_MARGIN_USD}), SL (-{sl_r * 100:.2f}%)"
        return g1, g2, sl_r, lbl, False

def format_limit_qty(notional_usd: float, limit_price_str: str, min_qty: float, qty_step: float) -> str:
    p_val = float(limit_price_str)
    raw_q = max(min_qty, notional_usd / p_val) if p_val > 0 else min_qty
    q_str = format_step(raw_q, qty_step)
    if float(q_str) * p_val < 5.2: q_str = format_step(float(q_str) + qty_step, qty_step)
    if float(q_str) < min_qty: q_str = format_step(min_qty, qty_step)
    return q_str

def run_dump_dip_engine() -> None:
    threading.Thread(target=run_web_server, daemon=True).start()

    # Strat 2 Integration Thread
    threading.Thread(
        target=strategy_2_engine.start_binance_loop,
        args=(dashboard_data, data_lock, add_ui_log, record_completed_trade),
        daemon=True
    ).start()

    ws_pipeline = BinanceFastLaneWS(
        api_key=config.API_KEY,
        api_secret=config.API_SECRET,
        log_fn=add_ui_log,
        status_fn=set_dashboard_status
    )
    ws_pipeline.connect()
    add_ui_log(f"🚀 Binance Tokyo Sniper Active: T-{config.ENTRY_BEFORE_SETTLE_MS}ms Single-Shot Limit Burst Armed.")
    executor.startup_safety_sweep()
    while not ws_pipeline.ws_ready: time.sleep(0.1)

    while True:
        sync_binance_clock()
        with data_lock: user_pref = dashboard_data.get("manual_selected_symbol") or None
        target_symbol, rate, last_price, next_funding_ms, top_10 = find_top_negative_funding_candidates(preferred_symbol=user_pref)
        with data_lock: dashboard_data["top_5_candidates"] = top_10

        if not target_symbol or next_funding_ms == 0:
            idle_wait_sec, next_scan_time_str = get_seconds_until_next_scheduled_scan()
            with data_lock:
                dashboard_data["target_symbol"] = "Scanning 1h/2h/4h/8h..."
                dashboard_data["funding_rate"] = "0.00%"
                dashboard_data["active_tier"] = "IDLE"
                dashboard_data["status"] = f"IDLE: Next scheduled scan at {next_scan_time_str} ({idle_wait_sec}s)..."
                dashboard_data["target_settlement_ms"] = 0
                dashboard_data["target_settlement_time"] = "--:--:-- IST"
                dashboard_data["selection_locked"] = False
            add_ui_log(f"[IDLE SYNC] No qualified coins found. Next scheduled market scan at {next_scan_time_str} (in {idle_wait_sec}s)...")
            for _ in range(idle_wait_sec):
                with data_lock:
                    if dashboard_data.get("force_rescan", False):
                        dashboard_data["force_rescan"] = False
                        time.sleep(2.0)
                        break
                time.sleep(1.0)
            continue

        ws_pipeline.reset_cycle_state(target_symbol)
        ws_pipeline.subscribe_ticker(target_symbol)
        settle_epoch = next_funding_ms
        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
        rate_percent = f"{rate * 100:+.4f}%"
        qty_str, applied_leverage, tick_size, qty_step, min_qty = calculate_order_specs(target_symbol, last_price)
        t_rescan_20s = settle_epoch - config.RESCAN_BEFORE_SETTLE_MS
        strategy_label = get_strategy_label(rate)
        with data_lock:
            dashboard_data["target_symbol"] = target_symbol
            dashboard_data["funding_rate"] = rate_percent
            dashboard_data["active_tier"] = strategy_label
            dashboard_data["target_settlement_time"] = settle_dt.strftime("%H:%M:%S IST")
            dashboard_data["target_settlement_ms"] = settle_epoch
            dashboard_data["status"] = f"ARMED: {target_symbol} | {strategy_label}"

        print(f"🎯 TARGET LOCKED: {target_symbol} | Rate: {rate_percent} | Settle: {settle_dt.strftime('%H:%M:%S IST')}")
        last_logged_sec, last_clock_scan_min, t120_scan_done, abort_cycle = -1, -1, False, False

        while True:
            now_ms = get_synced_time_ms()
            remaining_ms = t_rescan_20s - now_ms
            current_sec_left = max(0, int((settle_epoch - now_ms) / 1000))
            is_locked = (current_sec_left <= 60)
            with data_lock:
                dashboard_data["selection_locked"] = is_locked
                dashboard_data["target_settlement"] = f"{settle_dt.strftime('%H:%M:%S')} IST ({format_duration(current_sec_left)})"
                need_instant_scan = dashboard_data.get("force_rescan", False)
                if need_instant_scan: dashboard_data["force_rescan"] = False
                user_selected = dashboard_data.get("manual_selected_symbol")
            
            blocked_now = load_blocked_coins()
            if target_symbol in blocked_now.get("temporary", []) or target_symbol in blocked_now.get("permanent", []):
                ws_pipeline.reset_cycle_state(target_symbol)
                target_symbol = ""
                time.sleep(2.0)
                abort_cycle = True
                break

            if current_sec_left <= 120 and current_sec_left > 65 and not t120_scan_done:
                t120_scan_done = True
                sync_binance_clock()
                with data_lock: active_user_pref = dashboard_data.get("manual_selected_symbol")
                chk_sym, chk_r, chk_p, chk_ms, chk_top10 = find_top_negative_funding_candidates(preferred_symbol=active_user_pref)
                with data_lock: dashboard_data["top_5_candidates"] = chk_top10
                if chk_sym:
                    if chk_ms < (settle_epoch - 60000):
                        with data_lock: dashboard_data["manual_selected_symbol"] = ""
                        target_symbol = chk_sym
                        rate, last_price, settle_epoch = chk_r, chk_p or last_price, chk_ms
                        ws_pipeline.reset_cycle_state(target_symbol)
                        ws_pipeline.subscribe_ticker(target_symbol)
                    elif active_user_pref:
                        if chk_sym == active_user_pref:
                            rate = chk_r
                            if chk_p: last_price = chk_p
                        else:
                            target_symbol = chk_sym
                            rate, last_price, settle_epoch = chk_r, chk_p or last_price, chk_ms
                            ws_pipeline.reset_cycle_state(target_symbol)
                            ws_pipeline.subscribe_ticker(target_symbol)
                            with data_lock: dashboard_data["manual_selected_symbol"] = ""
                    else:
                        if chk_sym != target_symbol:
                            target_symbol = chk_sym
                            settle_epoch = chk_ms
                            ws_pipeline.reset_cycle_state(target_symbol)
                            ws_pipeline.subscribe_ticker(target_symbol)
                        rate, last_price = chk_r, chk_p or last_price
                    settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
                    rate_percent, t_rescan_20s = f"{rate * 100:+.4f}%", settle_epoch - config.RESCAN_BEFORE_SETTLE_MS
                    qty_str, applied_leverage, tick_size, qty_step, min_qty = calculate_order_specs(target_symbol, last_price)
                    strategy_label = get_strategy_label(rate)
                    with data_lock:
                        dashboard_data["target_symbol"] = target_symbol
                        dashboard_data["funding_rate"] = rate_percent
                        dashboard_data["active_tier"] = strategy_label
                        dashboard_data["target_settlement_time"] = settle_dt.strftime("%H:%M:%S IST")
                        dashboard_data["target_settlement_ms"] = settle_epoch
                        dashboard_data["status"] = f"ARMED: {target_symbol} | {strategy_label}"

            if need_instant_scan:
                time.sleep(2.0)
                sync_binance_clock()
                chk_sym, chk_r, chk_p, chk_ms, chk_top10 = find_top_negative_funding_candidates(preferred_symbol=user_selected if not is_locked else target_symbol)
                with data_lock: dashboard_data["top_5_candidates"] = chk_top10
                if not is_locked:
                    if chk_sym:
                        if user_selected and chk_sym != user_selected:
                            with data_lock: dashboard_data["manual_selected_symbol"] = ""
                        if chk_sym != target_symbol: ws_pipeline.reset_cycle_state(target_symbol)
                        target_symbol, rate, last_price, settle_epoch = chk_sym, chk_r, chk_p or last_price, chk_ms
                        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
                        rate_percent, strategy_label = f"{rate * 100:+.4f}%", get_strategy_label(rate)
                        with data_lock:
                            dashboard_data["target_symbol"] = target_symbol
                            dashboard_data["funding_rate"] = rate_percent
                            dashboard_data["active_tier"] = strategy_label
                            dashboard_data["target_settlement_time"] = settle_dt.strftime("%H:%M:%S IST")
                            dashboard_data["target_settlement_ms"] = settle_epoch
                            dashboard_data["status"] = f"ARMED: {target_symbol} | {strategy_label}"
                        ws_pipeline.reset_cycle_state(target_symbol)
                        ws_pipeline.subscribe_ticker(target_symbol)
                        qty_str, applied_leverage, tick_size, qty_step, min_qty = calculate_order_specs(target_symbol, last_price)
                        t_rescan_20s = settle_epoch - config.RESCAN_BEFORE_SETTLE_MS
                    else:
                        ws_pipeline.reset_cycle_state(target_symbol)
                        target_symbol, abort_cycle = "", True
                        break

            current_dt = datetime.now(IST)
            current_minute = current_dt.minute
            if current_minute in config.SCHEDULED_SCAN_MINUTES and current_minute != last_clock_scan_min and remaining_ms > 120000:
                last_clock_scan_min = current_minute
                sync_binance_clock()
                with data_lock: active_user_pref = dashboard_data.get("manual_selected_symbol")
                chk_sym, chk_rate, chk_price, chk_ms, chk_top10 = find_top_negative_funding_candidates(preferred_symbol=active_user_pref)
                with data_lock: dashboard_data["top_5_candidates"] = chk_top10
                if chk_sym:
                    if chk_ms < (settle_epoch - 60000):
                        with data_lock: dashboard_data["manual_selected_symbol"] = ""
                        target_symbol, rate, last_price, settle_epoch = chk_sym, chk_rate, chk_price or last_price, chk_ms
                        ws_pipeline.reset_cycle_state(target_symbol)
                        ws_pipeline.subscribe_ticker(target_symbol)
                    elif active_user_pref:
                        if chk_sym == active_user_pref: rate = chk_rate
                        else:
                            with data_lock: dashboard_data["manual_selected_symbol"] = ""
                            target_symbol, rate, last_price, settle_epoch = chk_sym, chk_rate, chk_price or last_price, chk_ms
                            ws_pipeline.reset_cycle_state(target_symbol)
                            ws_pipeline.subscribe_ticker(target_symbol)
                    else:
                        if chk_sym != target_symbol:
                            target_symbol, settle_epoch = chk_sym, chk_ms
                            ws_pipeline.reset_cycle_state(target_symbol)
                            ws_pipeline.subscribe_ticker(target_symbol)
                        rate, last_price = chk_rate, chk_price or last_price
                    settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
                    rate_percent, t_rescan_20s = f"{rate * 100:+.4f}%", settle_epoch - config.RESCAN_BEFORE_SETTLE_MS
                    qty_str, applied_leverage, tick_size, qty_step, min_qty = calculate_order_specs(target_symbol, last_price)
                    strategy_label = get_strategy_label(rate)
                    with data_lock:
                        dashboard_data["target_symbol"] = target_symbol
                        dashboard_data["funding_rate"] = rate_percent
                        dashboard_data["active_tier"] = strategy_label
                        dashboard_data["target_settlement_time"] = settle_dt.strftime("%H:%M:%S IST")
                        dashboard_data["target_settlement_ms"] = settle_epoch
                else:
                    abort_cycle = True
                    break

            if remaining_ms <= 100: break
            remaining_sec = int(remaining_ms / 1000)
            if remaining_sec != last_logged_sec and (remaining_sec % 15 == 0 or remaining_sec <= 10):
                last_logged_sec = remaining_sec
            time.sleep(0.5 if remaining_ms > 10000 else 0.1)

        if abort_cycle:
            ws_pipeline.reset_cycle_state(target_symbol)
            target_symbol = ""
            idle_wait_sec, next_scan_time_str = get_seconds_until_next_scheduled_scan()
            with data_lock:
                dashboard_data["target_symbol"] = "Scanning 1h/2h/4h/8h..."
                dashboard_data["funding_rate"] = "0.00%"
                dashboard_data["active_tier"] = "IDLE"
                dashboard_data["status"] = f"IDLE: Next scheduled scan at {next_scan_time_str} ({idle_wait_sec}s)..."
                dashboard_data["target_settlement_ms"] = 0
                dashboard_data["target_settlement_time"] = "--:--:-- IST"
                dashboard_data["manual_selected_symbol"] = ""
                dashboard_data["selection_locked"] = False
                dashboard_data["force_rescan"] = False
            for _ in range(idle_wait_sec):
                with data_lock:
                    if dashboard_data.get("force_rescan", False):
                        dashboard_data["force_rescan"] = False
                        time.sleep(2.0)
                        break
                time.sleep(1.0)
            continue

        precision_wait_until(t_rescan_20s)
        sync_binance_clock()
        with data_lock:
            manual_coin = dashboard_data.get("manual_selected_symbol")
            active_target = manual_coin or target_symbol
            dashboard_data["selection_locked"] = True

        blocked_now = load_blocked_coins()
        if active_target in blocked_now.get("temporary", []) or active_target in blocked_now.get("permanent", []):
            time.sleep(1.0)
            continue

        fresh_sym, fresh_rate, fresh_price, fresh_ms, top_10_fresh = find_top_negative_funding_candidates(target_settle_ms=settle_epoch, preferred_symbol=active_target)
        with data_lock: dashboard_data["top_5_candidates"] = top_10_fresh

        if manual_coin:
            if fresh_sym != manual_coin or fresh_rate is None or fresh_rate >= 0.0:
                time.sleep(1.0); continue
        else:
            if (not fresh_sym or fresh_rate is None or fresh_rate > config.MIN_FUNDING_RATE or fresh_rate < config.MAX_DANGER_FUNDING_RATE):
                time.sleep(1.0); continue

        if fresh_sym != target_symbol:
            ws_pipeline.reset_cycle_state(target_symbol)
            target_symbol = fresh_sym
            ws_pipeline.reset_cycle_state(target_symbol)
            ws_pipeline.subscribe_ticker(target_symbol)

        rate, last_price = fresh_rate, fresh_price or last_price
        rate_percent = f"{rate * 100:+.4f}%"
        qty_str, applied_leverage, tick_size, qty_step, min_qty = calculate_order_specs(target_symbol, last_price)
        strategy_label = get_strategy_label(rate)

        with data_lock:
            dashboard_data["target_symbol"] = target_symbol
            dashboard_data["funding_rate"] = rate_percent
            dashboard_data["active_tier"] = strategy_label
            dashboard_data["status"] = f"LOCKED: {target_symbol} | {strategy_label}"

        t_gc_purge = settle_epoch - config.GC_PURGE_BEFORE_SETTLE_MS
        t_ws_warmup = settle_epoch - config.WS_WARMUP_BEFORE_SETTLE_MS
        t_tcp_warmup = settle_epoch - config.TCP_WARMUP_BEFORE_SETTLE_MS
        t_recheck = settle_epoch - config.RECHECK_BEFORE_SETTLE_MS
        t_limit_burst = settle_epoch - config.ENTRY_BEFORE_SETTLE_MS
        t_cancel_shallow = settle_epoch + config.SHALLOW_CANCEL_FLOOR_MS
        t_cancel_orders = settle_epoch + config.CANCEL_ORDERS_AFTER_SETTLE_MS
        t_scheduled_exit = settle_epoch + config.EXIT_AFTER_SETTLE_MS
        t_emergency_sweep = settle_epoch + config.EMERGENCY_SWEEP_AFTER_SETTLE_MS

        entry_dispatch_time, t2_snapshot_price, cycle_order_start_ms = "", last_price, 0
        gap_dca_1, _, sl_ratio, condition_label, is_cond_1 = evaluate_dca_condition_specs(rate)
        shallow_notional_usd = max(5.5, config.LIMIT_ORDER_MARGIN_USD * applied_leverage)
        dca_cond1_notional_usd = max(5.5, config.LIMIT_ORDER_MARGIN_USD * applied_leverage)
        dca_cond2_notional_usd = max(5.5, config.DCA_SPLIT_MARGIN_USD * applied_leverage)
        sl_target_price, sl_target_str = 0.0, "--"
        epoch_tag = f"{int(settle_epoch / 1000) % 100000}"
        cid_shlw1, cid_shlw2, cid_shlw3 = f"shlw1-{target_symbol[:4]}-{epoch_tag}", f"shlw2-{target_symbol[:4]}-{epoch_tag}", f"shlw3-{target_symbol[:4]}-{epoch_tag}"
        cid_dca1, cid_dca2 = f"dca-dip1-{target_symbol[:4]}-{epoch_tag}", f"dca-dip2-{target_symbol[:4]}-{epoch_tag}"
        shallow_cids = [cid_shlw1, cid_shlw2, cid_shlw3]
        all_cycle_cids = [cid_shlw1, cid_shlw2, cid_shlw3, cid_dca1] + ([] if is_cond_1 else [cid_dca2])

        try:
            with data_lock: trading_active = dashboard_data.get("trading_enabled", True)
            if not trading_active:
                precision_wait_until(t_scheduled_exit); continue

            precision_wait_until(t_gc_purge)
            gc.collect(); gc.disable()

            precision_wait_until(t_ws_warmup)
            ws_pipeline.reconnect_order_ws()
            t_wait_end = time.time() + 3.0
            while not ws_pipeline.order_ws_ready and time.time() < t_wait_end: time.sleep(0.05)

            precision_wait_until(t_tcp_warmup)
            ws_pipeline.ping_order_ws()

            precision_wait_until(t_recheck)
            try:
                r_check = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/premiumIndex?symbol={target_symbol}", timeout=1.2).json()
                fresh_r, fresh_p = float(r_check.get("lastFundingRate", rate)), float(r_check.get("markPrice", last_price))
                if fresh_p > 0: last_price, t2_snapshot_price = fresh_p, fresh_p
                is_invalid = (fresh_r >= 0.0) if manual_coin else (fresh_r > config.MIN_FUNDING_RATE or fresh_r < config.MAX_DANGER_FUNDING_RATE or fresh_r >= 0.0)
                if is_invalid: continue
            except Exception: pass

            dynamic_lead = ws_pipeline.get_measured_lead_latency_ms()
            target_strike_ms = settle_epoch + config.SHALLOW_CANCEL_AFTER_SETTLE_MS
            computed_trigger = target_strike_ms - dynamic_lead
            floor_trigger = settle_epoch + config.SHALLOW_CANCEL_FLOOR_MS
            t_cancel_shallow = max(floor_trigger, computed_trigger)

            precision_wait_until(t_limit_burst)
            cycle_order_start_ms = int(get_synced_time_ms()) - 1000
            entry_dispatch_time = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
            live_ws_p = ws_pipeline.get_live_price(target_symbol)
            anchor_price, anchor_src = (live_ws_p, "LIVE WS TICKER") if (live_ws_p and live_ws_p > 0) else (t2_snapshot_price, "T-2.5s BACKUP SNAPSHOT")
            dec_anchor, abs_rate_dec = Decimal(str(anchor_price)), Decimal(str(abs(float(rate))))

            limit_orders_batch, planned_shallow_map = [], {}
            shallow_specs = [(cid_shlw1, config.SHALLOW_LIMIT_RATIOS[0], "Shallow Limit #1 (30% FR)", 1), (cid_shlw2, config.SHALLOW_LIMIT_RATIOS[1], "Shallow Limit #2 (50% FR)", 2), (cid_shlw3, config.SHALLOW_LIMIT_RATIOS[2], "Shallow Limit #3 (70% FR)", 3)]
            for cid_s, s_ratio, s_label, s_idx in shallow_specs:
                drop_dec = abs_rate_dec * Decimal(str(s_ratio))
                p_dec = dec_anchor * (Decimal("1") - drop_dec)
                p_str = format_tick(float(p_dec), tick_size)
                q_str = format_limit_qty(shallow_notional_usd, p_str, min_qty, qty_step)
                limit_orders_batch.append((cid_s, q_str, p_str, float(drop_dec), s_label))
                planned_shallow_map[cid_s] = {"cid": cid_s, "label": s_label, "price": p_str, "qty": q_str, "sort_idx": s_idx}

            if is_cond_1:
                dca1_p_dec = dec_anchor * (Decimal("1") - Decimal(str(gap_dca_1)))
                dca1_price_str = format_tick(float(dca1_p_dec), tick_size)
                dca1_qty = format_limit_qty(dca_cond1_notional_usd, dca1_price_str, min_qty, qty_step)
                limit_orders_batch.append((cid_dca1, dca1_qty, dca1_price_str, gap_dca_1, "DCA Limit #1 (130% FR)"))
                sl_dec = Decimal(dca1_price_str) * (Decimal("1") - Decimal(str(sl_ratio)))
                sl_target_price, sl_target_str = float(sl_dec), format_tick(float(sl_dec), tick_size)
            else:
                dca1_p_dec = dec_anchor * (Decimal("1") - Decimal(str(gap_dca_1)))
                dca1_price_str = format_tick(float(dca1_p_dec), tick_size)
                dca1_qty = format_limit_qty(dca_cond2_notional_usd, dca1_price_str, min_qty, qty_step)
                limit_orders_batch.append((cid_dca1, dca1_qty, dca1_price_str, gap_dca_1, f"DCA Limit #1 (${config.DCA_SPLIT_MARGIN_USD})"))
                dca2_p_dec = Decimal(dca1_price_str) * (Decimal("1") - Decimal(str(config.L2_BELOW_L1_GAP)))
                dca2_price_str = format_tick(float(dca2_p_dec), tick_size)
                gap_dca_2_val = float((dec_anchor - Decimal(dca2_price_str)) / dec_anchor)
                dca2_qty = format_limit_qty(dca_cond2_notional_usd, dca2_price_str, min_qty, qty_step)
                limit_orders_batch.append((cid_dca2, dca2_qty, dca2_price_str, gap_dca_2_val, f"DCA Limit #2 (${config.DCA_SPLIT_MARGIN_USD})"))
                sl_dec = Decimal(dca2_price_str) * (Decimal("1") - Decimal(str(sl_ratio)))
                sl_target_price, sl_target_str = float(sl_dec), format_tick(float(sl_dec), tick_size)

            executor.execute_limit_burst_ws(ws_pipeline, target_symbol, limit_orders_batch)

            exit_triggered, shallow_cancel_fired, exit_reason, exit_status, exit_send_time, exit_price_detected = False, False, "", "", "", 0.0

            while get_synced_time_ms() < t_cancel_orders:
                now_loop_ms = get_synced_time_ms()
                if not shallow_cancel_fired and now_loop_ms >= t_cancel_shallow:
                    shallow_cancel_fired = True
                    threading.Thread(target=executor.fast_cancel_shallow_orders, args=(ws_pipeline, target_symbol, shallow_cids), daemon=True).start()

                real_market_trade_price = ws_pipeline.get_last_traded_price(target_symbol)
                active_pos_check = ws_pipeline.get_filled_qty(target_symbol)
                if active_pos_check > 0 and (0.0 < real_market_trade_price <= sl_target_price and sl_target_price > 0):
                    exit_price_detected, exit_triggered, exit_reason, exit_status = real_market_trade_price, True, f"REAL-TRADE SL HIT (LTP: {real_market_trade_price} <= SL: {sl_target_str})", "STOP LOSS EXECUTED"
                    ws_pos = ws_pipeline.get_filled_qty(target_symbol)
                    exit_qty = format_step(ws_pos, qty_step) if ws_pos > 0 else qty_str
                    if ws_pos > 0: _, exit_send_time, _ = executor.execute_market_exit_3layer(ws_pipeline, target_symbol, exit_qty, "SL EMERGENCY EXIT")
                    executor.fast_cancel_all_orders(ws_pipeline, target_symbol, all_cycle_cids)
                    rem_pos = executor.fetch_live_position_size(target_symbol)
                    if rem_pos > 0: executor.execute_market_exit_3layer(ws_pipeline, target_symbol, format_step(rem_pos, qty_step), "SL REM-SWEEP EXIT")
                    break
                time.sleep(0.001)

            if not exit_triggered:
                precision_wait_until(t_cancel_orders)
                executor.fast_cancel_all_orders(ws_pipeline, target_symbol, all_cycle_cids)
                precision_wait_until(t_scheduled_exit)
                time.sleep(0.02)
                live_pos, ws_pos = executor.fetch_live_position_size(target_symbol), ws_pipeline.get_filled_qty(target_symbol)
                total_pos = max(live_pos, ws_pos)
                exit_price_detected = ws_pipeline.get_live_price(target_symbol) or t2_snapshot_price
                if total_pos > 0:
                    exit_qty, exit_reason, exit_status = format_step(total_pos, qty_step), f"POST-SETTLEMENT HARVEST EXIT", "SETTLEMENT FEE CAPTURED"
                    _, exit_send_time, _ = executor.execute_market_exit_3layer(ws_pipeline, target_symbol, exit_qty, "HARVEST EXIT")
                else:
                    exit_reason, exit_status = "CLEAN PASS (NO LIMIT FILLED)", "CLEAN PASS (NO LIMIT FILLED)"

            rec_entry_p, rec_exit_p, rec_entry_t, rec_exit_t, rec_dca_hits, rec_pnl = executor.fetch_post_trade_reconciliation(target_symbol, cycle_order_start_ms or (settle_epoch - 5000))
            final_avg_entry = rec_entry_p or ws_pipeline.get_avg_fill_price(target_symbol) or 0.0
            final_exit_price = rec_exit_p or ws_pipeline.get_sell_fill_price(target_symbol) or exit_price_detected
            entry_fill_time_str = rec_entry_t or ws_pipeline.get_last_fill_time(target_symbol) or entry_dispatch_time
            exit_fill_time = rec_exit_t or ws_pipeline.get_last_fill_time(target_symbol) or datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
            dca_hits_list = rec_dca_hits or ws_pipeline.get_dca_hits(target_symbol)

            filled_shallow_indices = {h.get("sort_idx") for h in dca_hits_list if h.get("group") == "shallow"}
            for cid_s, p_info in planned_shallow_map.items():
                s_idx = p_info["sort_idx"]
                if s_idx not in filled_shallow_indices:
                    telem = ws_pipeline.get_shallow_cancel_telemetry(cid_s)
                    f_ms, b_ms = telem.get("fired_local_ms", 0), telem.get("binance_engine_ms", 0)
                    lat = max(0, b_ms - f_ms) if (f_ms > 0 and b_ms > 0) else 0
                    f_str = datetime.fromtimestamp(f_ms / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3] if f_ms > 0 else "--"
                    b_str = datetime.fromtimestamp(b_ms / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3] if b_ms > 0 else "--"
                    dca_hits_list.append({"level": p_info["label"], "group": "shallow", "sort_idx": s_idx, "status": "CANCELED", "target_price": p_info["price"], "target_qty": p_info["qty"], "price": p_info["price"], "qty": p_info["qty"], "cancel_fired": f_str, "binance_canceled": b_str, "cancel_latency_ms": lat})
            dca_hits_list.sort(key=lambda x: (x.get("sort_idx", 99), x.get("first_ms", 0)))

            record_completed_trade({
                "strategy": "strategy_1", "symbol": target_symbol, "funding_rate": rate_percent, "tier": strategy_label,
                "entry_sent": entry_dispatch_time or "N/A", "entry_ack": entry_fill_time_str or "N/A", "entry_rtt": "Tokyo Single-Shot WS",
                "entry_fill_time": entry_fill_time_str or "N/A", "entry_fill_price": str(final_avg_entry) if final_avg_entry > 0 else "No Fill",
                "exit_sent": exit_send_time or "N/A", "exit_fill_time": exit_fill_time, "exit_price": str(final_exit_price),
                "exit_type": exit_reason, "status": exit_status, "details": f"Avg Entry: {final_avg_entry} | Exit: {final_exit_price} | Lev: {applied_leverage}x | SL: {sl_target_str}",
                "funding_fee_received": rec_pnl.get("funding_fee_str", "$0.0000"), "funding_fee_class": rec_pnl.get("funding_fee_class", "pnl-neutral"),
                "funding_fee_time": rec_pnl.get("funding_fee_time", "--:--:--"), "pnl": rec_pnl.get("pnl_str", "$0.0000"),
                "pnl_class": rec_pnl.get("pnl_class", "pnl-neutral"), "pnl_breakdown": rec_pnl.get("pnl_breakdown", ""), "dca_hits": dca_hits_list
            })
            threading.Thread(target=executor.delayed_post_settlement_watchdog, args=(target_symbol, qty_step, settle_epoch), daemon=True).start()

        except Exception as cycle_err:
            add_ui_log(f"🚨 [CYCLE EXECUTION ERROR CAUGHT]: {cycle_err}")
        finally:
            precision_wait_until(t_emergency_sweep)
            try:
                executor.binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": target_symbol})
                final_pos = executor.fetch_live_position_size(target_symbol)
                if final_pos > 0: executor.binance_rest_post("/fapi/v1/order", {"symbol": target_symbol, "side": "SELL", "type": "MARKET", "quantity": format_step(final_pos, qty_step), "reduceOnly": "true"})
            except Exception as e: pass
            with data_lock:
                dashboard_data["manual_selected_symbol"] = ""
                dashboard_data["selection_locked"] = False
            ws_pipeline.reset_cycle_state(target_symbol)
            gc.enable()
            gc.collect()
            time.sleep(1.0)

if __name__ == "__main__":
    run_dump_dip_engine()
