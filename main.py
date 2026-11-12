from __future__ import annotations
import time
import threading
from datetime import datetime, timedelta
import config
from binance_ws import BinanceFastLaneWS
from dashboard import (
    IST, data_lock, dashboard_data, add_ui_log,
    set_dashboard_status, run_web_server, load_blocked_coins
)
from scanner import (
    sync_binance_clock, get_synced_time_ms, format_duration,
    find_top_negative_funding_candidates, calculate_order_specs
)
import executor
from cycle_engine import (
    get_strategy_label, update_dashboard_armed_state, run_settlement_cycle
)


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


def run_dump_dip_engine() -> None:
    threading.Thread(target=run_web_server, daemon=True).start()
    ws_pipeline = BinanceFastLaneWS(
        api_key=config.API_KEY,
        api_secret=config.API_SECRET,
        log_fn=add_ui_log,
        status_fn=set_dashboard_status
    )
    ws_pipeline.connect()
    add_ui_log(f"🚀 Binance Tokyo Sniper Active: T-{config.ENTRY_BEFORE_SETTLE_MS}ms Dynamic Lead-Compensated Limit Burst Armed.")
    executor.startup_safety_sweep()

    while not ws_pipeline.ws_ready:
        time.sleep(0.1)

    while True:
        sync_binance_clock()
        with data_lock:
            is_manual_mode = dashboard_data.get("manual_mode", False)
            selected_symbols = list(dashboard_data.get("manual_selected_symbols", []))

        chosen_candidates, top_10 = find_top_negative_funding_candidates(
            preferred_symbols=selected_symbols if is_manual_mode else None,
            manual_mode=is_manual_mode
        )

        with data_lock:
            dashboard_data["top_5_candidates"] = top_10

        # ZERO COIN SAFETY SHIELD: Agar Manual mode me 0 coin select hain to koi trade nahi lagegi
        if is_manual_mode and len(chosen_candidates) == 0:
            with data_lock:
                dashboard_data["target_symbol"] = "MANUAL: 0 COINS SELECTED (IDLE PASS)"
                dashboard_data["funding_rate"] = "0.00%"
                dashboard_data["active_tier"] = "IDLE (FUNDS 100% SAFE)"
                dashboard_data["status"] = "MANUAL MODE: 0 Coins Selected. Click Radar rows to select up to 3 coins."
                dashboard_data["target_settlement_ms"] = 0
                dashboard_data["target_settlement_time"] = "--:--:-- IST"
                dashboard_data["selection_locked"] = False

            add_ui_log("🛡️ [ZERO COIN SHIELD] Manual Mode active with 0 coins selected. Funds 100% idle. Waiting for selection...")
            for _ in range(15):
                with data_lock:
                    if dashboard_data.get("force_rescan", False):
                        dashboard_data["force_rescan"] = False
                        add_ui_log("⚡ [IDLE BREAK] User triggered action. Applying 2s grace period before scan...")
                        time.sleep(2.0)
                        break
                time.sleep(1.0)
            continue

        # IDLE Mode: Jab koi coin qualify na kare (Safe range -0.35% to -1.60% me koi coin na ho)
        if not chosen_candidates or chosen_candidates[0].get("settle_ms", 0) == 0:
            idle_wait_sec, next_scan_time_str = get_seconds_until_next_scheduled_scan()

            with data_lock:
                dashboard_data["target_symbol"] = "Scanning 1h/2h/4h/8h..."
                dashboard_data["funding_rate"] = "0.00%"
                dashboard_data["active_tier"] = "IDLE"
                dashboard_data["status"] = f"IDLE: Next scheduled scan at {next_scan_time_str} ({idle_wait_sec}s)..."
                dashboard_data["target_settlement_ms"] = 0
                dashboard_data["target_settlement_time"] = "--:--:-- IST"
                dashboard_data["selection_locked"] = False

            add_ui_log(f"[IDLE SYNC] No qualified coins found (<= {config.MIN_FUNDING_RATE * 100:.2f}%). Next scheduled scan at {next_scan_time_str} (in {idle_wait_sec}s)...")

            for _ in range(idle_wait_sec):
                with data_lock:
                    if dashboard_data.get("force_rescan", False):
                        dashboard_data["force_rescan"] = False
                        add_ui_log("⚡ [IDLE BREAK] User triggered action. Applying 2s grace period before scan...")
                        time.sleep(2.0)
                        break
                time.sleep(1.0)
            continue

        active_target_syms = [c["symbol"] for c in chosen_candidates]
        settle_epoch = chosen_candidates[0]["settle_ms"]
        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
        t_rescan_20s = settle_epoch - config.RESCAN_BEFORE_SETTLE_MS

        for sym in active_target_syms:
            ws_pipeline.reset_cycle_state(sym)
        ws_pipeline.subscribe_tickers(active_target_syms)

        symbols_title, rates_summary = update_dashboard_armed_state(chosen_candidates, is_manual_mode, settle_epoch, prefix="ARMED")
        first_qty_str, first_lev, _, _, _ = calculate_order_specs(chosen_candidates[0]["symbol"], chosen_candidates[0]["price"])

        print("\n" + "=" * 70, flush=True)
        print(f"🎯 TARGET LOCKED ({'MANUAL' if is_manual_mode else 'AUTO'}): {symbols_title} | Rates: {rates_summary} | Settle: {settle_dt.strftime('%H:%M:%S IST')}", flush=True)
        print(f"Strategy: {get_strategy_label(chosen_candidates[0]['rate'])} | Lev: {first_lev}x", flush=True)
        print("=" * 70, flush=True)

        last_logged_sec = -1
        last_clock_scan_min = -1
        t120_scan_done = False
        abort_cycle = False
        abort_to_idle_wait = False

        while True:
            now_ms = get_synced_time_ms()
            remaining_ms = t_rescan_20s - now_ms
            current_sec_left = max(0, int((settle_epoch - now_ms) / 1000))

            is_locked = (current_sec_left <= 60)
            with data_lock:
                dashboard_data["selection_locked"] = is_locked
                dashboard_data["target_settlement"] = f"{settle_dt.strftime('%H:%M:%S')} IST ({format_duration(current_sec_left)})"
                need_instant_scan = dashboard_data.get("force_rescan", False)
                if need_instant_scan:
                    dashboard_data["force_rescan"] = False
                is_manual_mode = dashboard_data.get("manual_mode", False)
                user_selected_list = list(dashboard_data.get("manual_selected_symbols", []))

            # Check if any currently armed coin got blocked in real-time
            blocked_now = load_blocked_coins()
            blocked_in_targets = [s for s in active_target_syms if s in blocked_now.get("temporary", []) or s in blocked_now.get("permanent", [])]
            if blocked_in_targets:
                add_ui_log(f"🚫 [ARMED COIN BLOCKED] {blocked_in_targets} was blocked! Gracefully unarming, waiting 2s for clean re-scan...")
                for s in active_target_syms:
                    ws_pipeline.reset_cycle_state(s)
                active_target_syms = []
                time.sleep(2.0)
                abort_cycle = True
                break

            # A. T-2 MINUTE (120s) PRE-LOCK AUDIT (Accurate REST API Refresh)
            if current_sec_left <= 120 and current_sec_left > 65 and not t120_scan_done:
                t120_scan_done = True
                add_ui_log("🔍 [T-2m PRE-LOCK AUDIT SCAN] Pulling fresh market data via REST API before 1-min lock window...")
                sync_binance_clock()
                with data_lock:
                    cur_man = dashboard_data.get("manual_mode", False)
                    cur_sel = list(dashboard_data.get("manual_selected_symbols", []))

                chk_targets, chk_top10 = find_top_negative_funding_candidates(
                    preferred_symbols=cur_sel if cur_man else None,
                    manual_mode=cur_man
                )
                with data_lock:
                    dashboard_data["top_5_candidates"] = chk_top10

                if chk_targets:
                    chk_ms = chk_targets[0]["settle_ms"]
                    is_earlier_settlement = (chk_ms < (settle_epoch - 60000))
                    if is_earlier_settlement:
                        add_ui_log(f"⚡ [EARLIER SETTLEMENT DETECTED AT T-2m] {chk_targets[0]['symbol']} ({chk_targets[0]['rate'] * 100:+.4f}%) settles sooner! Auto-switching...")
                        with data_lock:
                            dashboard_data["manual_mode"] = False
                            dashboard_data["manual_selected_symbols"] = []
                        for s in active_target_syms:
                            ws_pipeline.reset_cycle_state(s)
                        chosen_candidates = [chk_targets[0]]
                        active_target_syms = [chk_targets[0]["symbol"]]
                        ws_pipeline.reset_cycle_state(active_target_syms[0])
                        ws_pipeline.subscribe_tickers(active_target_syms)
                        settle_epoch = chk_ms
                        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
                        t_rescan_20s = settle_epoch - config.RESCAN_BEFORE_SETTLE_MS
                        symbols_title, rates_summary = update_dashboard_armed_state(chosen_candidates, False, settle_epoch, prefix="ARMED")
                    elif cur_man:
                        for s in active_target_syms:
                            if s not in [c["symbol"] for c in chk_targets]:
                                ws_pipeline.reset_cycle_state(s)
                        chosen_candidates = chk_targets
                        active_target_syms = [c["symbol"] for c in chosen_candidates]
                        ws_pipeline.subscribe_tickers(active_target_syms)
                        symbols_title, rates_summary = update_dashboard_armed_state(chosen_candidates, True, settle_epoch, prefix="ARMED")
                        add_ui_log(f"🔒 [MANUAL LOCK AT T-2m] Verified {symbols_title} ({rates_summary}).")
                    else:
                        if [c["symbol"] for c in chk_targets] != active_target_syms:
                            add_ui_log(f"⚡ [AUTO SWITCH AT T-2m] Switched target to Rank #1: {chk_targets[0]['symbol']} ({chk_targets[0]['rate'] * 100:+.4f}%)")
                            for s in active_target_syms:
                                ws_pipeline.reset_cycle_state(s)
                            chosen_candidates = chk_targets
                            active_target_syms = [c["symbol"] for c in chosen_candidates]
                            for s in active_target_syms:
                                ws_pipeline.reset_cycle_state(s)
                            ws_pipeline.subscribe_tickers(active_target_syms)
                            settle_epoch = chk_targets[0]["settle_ms"]
                            settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
                            t_rescan_20s = settle_epoch - config.RESCAN_BEFORE_SETTLE_MS
                        else:
                            chosen_candidates = chk_targets

                        symbols_title, rates_summary = update_dashboard_armed_state(chosen_candidates, False, settle_epoch, prefix="ARMED")
                        add_ui_log(f"🔄 [T-2m REFRESH COMPLETE] {symbols_title} rate & specs refreshed: {rates_summary} | Price: {chosen_candidates[0]['price']} USDT")

                    add_ui_log("✅ [T-2m PRE-LOCK AUDIT COMPLETE] Radar refreshed. 60 seconds left for manual selection!")
                elif cur_man and len(cur_sel) > 0:
                    add_ui_log("⚠️ [MANUAL COINS WEAKENED AT T-2m] Selected manual coins failed validation. Reverting to Automatic Rank #1...")
                    with data_lock:
                        dashboard_data["manual_mode"] = False
                        dashboard_data["manual_selected_symbols"] = []
                    auto_chk, _ = find_top_negative_funding_candidates(preferred_symbols=None, manual_mode=False)
                    if auto_chk:
                        for s in active_target_syms:
                            ws_pipeline.reset_cycle_state(s)
                        chosen_candidates = auto_chk
                        active_target_syms = [c["symbol"] for c in chosen_candidates]
                        ws_pipeline.subscribe_tickers(active_target_syms)
                        settle_epoch = chosen_candidates[0]["settle_ms"]
                        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
                        t_rescan_20s = settle_epoch - config.RESCAN_BEFORE_SETTLE_MS
                        symbols_title, rates_summary = update_dashboard_armed_state(chosen_candidates, False, settle_epoch, prefix="ARMED")

            # B. INSTANT EVENT-DRIVEN RE-SCAN
            if need_instant_scan:
                add_ui_log("⚡ [INSTANT EVENT SCAN TRIGGERED] Waiting 2s grace period before fresh market audit...")
                time.sleep(2.0)
                sync_binance_clock()
                with data_lock:
                    cur_man = dashboard_data.get("manual_mode", False)
                    cur_sel = list(dashboard_data.get("manual_selected_symbols", []))

                chk_targets, chk_top10 = find_top_negative_funding_candidates(
                    preferred_symbols=cur_sel if (cur_man and not is_locked) else (active_target_syms if is_locked else None),
                    manual_mode=cur_man if not is_locked else is_manual_mode
                )
                with data_lock:
                    dashboard_data["top_5_candidates"] = chk_top10

                if not is_locked:
                    if cur_man and len(cur_sel) == 0:
                        for s in active_target_syms:
                            ws_pipeline.reset_cycle_state(s)
                        abort_cycle = True
                        break
                    elif chk_targets:
                        new_syms = [c["symbol"] for c in chk_targets]
                        for s in active_target_syms:
                            if s not in new_syms:
                                ws_pipeline.reset_cycle_state(s)
                        chosen_candidates = chk_targets
                        active_target_syms = new_syms
                        settle_epoch = chosen_candidates[0]["settle_ms"]
                        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
                        t_rescan_20s = settle_epoch - config.RESCAN_BEFORE_SETTLE_MS
                        for s in active_target_syms:
                            ws_pipeline.reset_cycle_state(s)
                        ws_pipeline.subscribe_tickers(active_target_syms)
                        symbols_title, rates_summary = update_dashboard_armed_state(chosen_candidates, cur_man, settle_epoch, prefix="ARMED")
                        add_ui_log(f"✅ [INSTANT SCAN COMPLETE] Target updated to {symbols_title} ({rates_summary})")
                    else:
                        add_ui_log("ℹ️ [AUTOMATIC RESET] No coin qualifies in safe range. Unarming target...")
                        for s in active_target_syms:
                            ws_pipeline.reset_cycle_state(s)
                        active_target_syms = []
                        abort_cycle = True
                        abort_to_idle_wait = True
                        break

            # C. PERIODIC SCAN (Minutes 5, 20, 35, 50)
            current_dt = datetime.now(IST)
            current_minute = current_dt.minute
            if current_minute in config.SCHEDULED_SCAN_MINUTES and current_minute != last_clock_scan_min and remaining_ms > 120000:
                last_clock_scan_min = current_minute
                add_ui_log(f"[PERIODIC SCAN at {current_minute:02d}] Auditing market candidates...")
                sync_binance_clock()

                with data_lock:
                    cur_man = dashboard_data.get("manual_mode", False)
                    cur_sel = list(dashboard_data.get("manual_selected_symbols", []))

                chk_targets, chk_top10 = find_top_negative_funding_candidates(
                    preferred_symbols=cur_sel if cur_man else None,
                    manual_mode=cur_man
                )
                with data_lock:
                    dashboard_data["top_5_candidates"] = chk_top10

                if chk_targets:
                    chk_ms = chk_targets[0]["settle_ms"]
                    is_earlier_settlement = (chk_ms < (settle_epoch - 60000))
                    if is_earlier_settlement:
                        add_ui_log(f"⚡ [EARLIER SETTLEMENT DETECTED] {chk_targets[0]['symbol']} ({chk_targets[0]['rate'] * 100:+.4f}%) settles sooner! Auto-switching...")
                        with data_lock:
                            dashboard_data["manual_mode"] = False
                            dashboard_data["manual_selected_symbols"] = []
                        for s in active_target_syms:
                            ws_pipeline.reset_cycle_state(s)
                        chosen_candidates = [chk_targets[0]]
                        active_target_syms = [chk_targets[0]["symbol"]]
                        ws_pipeline.reset_cycle_state(active_target_syms[0])
                        ws_pipeline.subscribe_tickers(active_target_syms)
                        settle_epoch = chk_ms
                        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
                        t_rescan_20s = settle_epoch - config.RESCAN_BEFORE_SETTLE_MS
                        symbols_title, rates_summary = update_dashboard_armed_state(chosen_candidates, False, settle_epoch, prefix="ARMED")
                    elif cur_man:
                        chosen_candidates = chk_targets
                        active_target_syms = [c["symbol"] for c in chosen_candidates]
                        ws_pipeline.subscribe_tickers(active_target_syms)
                        symbols_title, rates_summary = update_dashboard_armed_state(chosen_candidates, True, settle_epoch, prefix="ARMED")
                        add_ui_log(f"🔒 [MANUAL LOCK SECURE] User coin(s) {symbols_title} verified ({rates_summary}). Keeping selection untouched.")
                    else:
                        if [c["symbol"] for c in chk_targets] != active_target_syms:
                            add_ui_log(f"⚡ [AUTO SWITCH] {symbols_title} -> {chk_targets[0]['symbol']} ({chk_targets[0]['rate'] * 100:+.4f}%)")
                            for s in active_target_syms:
                                ws_pipeline.reset_cycle_state(s)
                            chosen_candidates = chk_targets
                            active_target_syms = [c["symbol"] for c in chosen_candidates]
                            for s in active_target_syms:
                                ws_pipeline.reset_cycle_state(s)
                            ws_pipeline.subscribe_tickers(active_target_syms)
                            settle_epoch = chk_targets[0]["settle_ms"]
                            settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
                            t_rescan_20s = settle_epoch - config.RESCAN_BEFORE_SETTLE_MS
                        else:
                            chosen_candidates = chk_targets
                        symbols_title, rates_summary = update_dashboard_armed_state(chosen_candidates, False, settle_epoch, prefix="ARMED")
                else:
                    add_ui_log("[PERIODIC SCAN] No candidates qualified. Aborting cycle to fresh search.")
                    abort_cycle = True
                    abort_to_idle_wait = True
                    break

            if remaining_ms <= 100:
                break

            remaining_sec = int(remaining_ms / 1000)
            if remaining_sec != last_logged_sec and (remaining_sec % 15 == 0 or remaining_sec <= 10):
                lock_status_text = "LOCKED" if is_locked else "OPEN"
                add_ui_log(f"⏰ ARMED: {symbols_title} | Settle in: {current_sec_left}s | Rate: {rates_summary} | Selection: {lock_status_text}")
                last_logged_sec = remaining_sec
            time.sleep(0.5 if remaining_ms > 10000 else 0.1)

        if abort_cycle:
            for s in active_target_syms:
                ws_pipeline.reset_cycle_state(s)
            active_target_syms = []
            if abort_to_idle_wait:
                idle_wait_sec, next_scan_time_str = get_seconds_until_next_scheduled_scan()
                with data_lock:
                    dashboard_data["target_symbol"] = "Scanning 1h/2h/4h/8h..."
                    dashboard_data["funding_rate"] = "0.00%"
                    dashboard_data["active_tier"] = "IDLE"
                    dashboard_data["status"] = f"IDLE: Next scheduled scan at {next_scan_time_str} ({idle_wait_sec}s)..."
                    dashboard_data["target_settlement_ms"] = 0
                    dashboard_data["target_settlement_time"] = "--:--:-- IST"
                    dashboard_data["manual_mode"] = False
                    dashboard_data["manual_selected_symbols"] = []
                    dashboard_data["selection_locked"] = False
                    dashboard_data["force_rescan"] = False

                add_ui_log(f"[IDLE RE-ARM SYNC] Waiting until next scheduled scan at {next_scan_time_str} (in {idle_wait_sec}s)...")
                for _ in range(idle_wait_sec):
                    with data_lock:
                        if dashboard_data.get("force_rescan", False):
                            dashboard_data["force_rescan"] = False
                            add_ui_log("⚡ [IDLE BREAK] User triggered action. Applying 2s grace period before scan...")
                            time.sleep(2.0)
                            break
                    time.sleep(1.0)
            continue

        # Hand off live settlement window (T-20s to T+15s) to dedicated Cycle Execution Engine
        run_settlement_cycle(ws_pipeline, active_target_syms, settle_epoch)


if __name__ == "__main__":
    run_dump_dip_engine()
