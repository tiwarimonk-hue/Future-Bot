from __future__ import annotations
import gc
import time
import threading
from decimal import Decimal
from datetime import datetime
import requests
import config
from binance_ws import BinanceFastLaneWS
from dashboard import (
    IST, data_lock, dashboard_data, add_ui_log,
    record_completed_trade, load_blocked_coins,
    get_active_base_margin_usd
)
from scanner import (
    sync_binance_clock, get_synced_time_ms, precision_wait_until,
    find_top_negative_funding_candidates, format_step, format_tick,
    calculate_order_specs
)
import executor

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json"
})


def get_strategy_label(rate_val: float, margin_usd: float | None = None) -> str:
    m_val = margin_usd if margin_usd is not None else get_active_base_margin_usd()
    return f"4 SHALLOW LIMITS (20/40/60/80% FR @ ${m_val:.2f}) [T-{config.ENTRY_BEFORE_SETTLE_MS}ms]"


def evaluate_sl_specs(current_rate: float, margin_usd: float | None = None) -> tuple[float, str]:
    """Funding rate ke aadhar par Anchor Price se Stop-Loss distance (150% |FR|, min 0.60%) return karta hai"""
    m_val = margin_usd if margin_usd is not None else get_active_base_margin_usd()
    abs_r = abs(float(current_rate))
    sl_r = max(config.MIN_SL_DROP_RATIO, round(abs_r * config.SL_FUNDING_MULTIPLIER, 6))
    lbl = (
        f"4 Shallow (20/40/60/80% FR @ ${m_val:.2f}), "
        f"SL (-{sl_r * 100:.3f}% from Anchor)"
    )
    return sl_r, lbl


def format_limit_qty(notional_usd: float, limit_price_str: str, min_qty: float, qty_step: float) -> str:
    p_val = float(limit_price_str)
    raw_q = max(min_qty, notional_usd / p_val) if p_val > 0 else min_qty
    q_str = format_step(raw_q, qty_step)
    if float(q_str) * p_val < 5.2:
        q_str = format_step(float(q_str) + qty_step, qty_step)
    if float(q_str) < min_qty:
        q_str = format_step(min_qty, qty_step)
    return q_str


def update_dashboard_armed_state(chosen_candidates: list[dict], is_manual_mode: bool, settle_epoch: int, prefix: str = "ARMED") -> tuple[str, str]:
    active_syms = [c["symbol"] for c in chosen_candidates]
    settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
    rates_summary = " | ".join([f"{c['symbol']}: {c['rate'] * 100:+.4f}%" for c in chosen_candidates])
    symbols_title = ", ".join(active_syms)
    if len(chosen_candidates) == 1:
        tier_label = get_strategy_label(chosen_candidates[0]["rate"])
    else:
        tier_label = f"{'MANUAL' if is_manual_mode else 'AUTO'} ({len(active_syms)} Pairs) [T-{config.ENTRY_BEFORE_SETTLE_MS}ms]"

    with data_lock:
        dashboard_data["target_symbol"] = symbols_title
        dashboard_data["funding_rate"] = rates_summary if len(chosen_candidates) > 1 else f"{chosen_candidates[0]['rate'] * 100:+.4f}%"
        dashboard_data["active_tier"] = tier_label
        dashboard_data["target_settlement_time"] = settle_dt.strftime("%H:%M:%S IST")
        dashboard_data["target_settlement_ms"] = settle_epoch
        dashboard_data["status"] = f"{prefix}: {symbols_title} | {tier_label}"

    return symbols_title, rates_summary


def run_settlement_cycle(
    ws_pipeline: BinanceFastLaneWS,
    active_target_syms: list[str],
    settle_epoch: int
) -> None:
    """
    Executes the complete high-precision settlement cycle from T-20s to T+20s:
    Phase 1 (T-20s) -> Phase 2 (T-10s GC / T-8s TLS / T-4s Ping) ->
    Phase 3 (T-2.5s Snapshot / T-500ms 3-Ping Burst) -> Phase 4 (T-50ms 4-Shallow Limit Burst) ->
    Phase 5 (SL Watchdog + 3-Strike Shallow Cancel: T+235ms / T+250ms / T+270ms) ->
    Phase 6 (T+6s Cancel / T+7s Exit) -> Phase 6B (T+9s Safety Sweep #1) ->
    Phase 7 (T+15s Ledger Audit & Smart Merge) -> Phase 8 (T+20s Safety Sweep #2) -> Finally Sweep.
    """
    t_rescan_20s = settle_epoch - config.RESCAN_BEFORE_SETTLE_MS
    targets_meta: dict[str, dict] = {}

    try:
        # PHASE 1: T-20s Fresh Scan
        precision_wait_until(t_rescan_20s)
        add_ui_log("[T-20s FRESH SCAN] Re-evaluating selected coin(s) & rates...")
        sync_binance_clock()
        with data_lock:
            is_manual_mode = dashboard_data.get("manual_mode", False)
            selected_symbols = list(dashboard_data.get("manual_selected_symbols", []))
            dashboard_data["selection_locked"] = True

        active_margin_usd = get_active_base_margin_usd()

        # Safety Check: if any target coin got blocked, filter/abort immediately
        blocked_now = load_blocked_coins()
        if is_manual_mode:
            selected_symbols = [s for s in selected_symbols if s not in blocked_now.get("temporary", []) and s not in blocked_now.get("permanent", [])]
            if not selected_symbols:
                add_ui_log("[T-20s SCAN ABORT] Manually selected coin(s) are empty or in block list. Aborting cycle safely!")
                return
        else:
            if any(s in blocked_now.get("temporary", []) or s in blocked_now.get("permanent", []) for s in active_target_syms):
                add_ui_log(f"[T-20s SCAN ABORT] Target coin ({active_target_syms}) is in block list. Aborting cycle safely!")
                return

        fresh_targets, top_10_fresh = find_top_negative_funding_candidates(
            target_settle_ms=settle_epoch,
            preferred_symbols=selected_symbols if is_manual_mode else None,
            manual_mode=is_manual_mode
        )
        with data_lock:
            dashboard_data["top_5_candidates"] = top_10_fresh

        if is_manual_mode:
            valid_manual = [c for c in fresh_targets if c.get("rate") is not None and c["rate"] < 0.0]
            if not valid_manual:
                add_ui_log(f"[T-20s SCAN ABORT] Manually selected coin(s) ({selected_symbols}) became invalid/positive. Aborting cycle safely!")
                return
            fresh_targets = valid_manual
        else:
            if (not fresh_targets or fresh_targets[0].get("rate") is None or
                fresh_targets[0]["rate"] > config.MIN_FUNDING_RATE or fresh_targets[0]["rate"] < config.MAX_DANGER_FUNDING_RATE):
                add_ui_log(f"[T-20s SCAN ABORT] Auto-selected coin ({active_target_syms}) rate out of safe bounds. Aborting cycle safely.")
                return

        fresh_syms = [c["symbol"] for c in fresh_targets]
        if fresh_syms != active_target_syms:
            add_ui_log(f"[TARGET SWITCHED AT T-20s] {active_target_syms} -> {fresh_syms}")
            for s in active_target_syms:
                if s not in fresh_syms:
                    ws_pipeline.reset_cycle_state(s)
            active_target_syms = fresh_syms
            for s in active_target_syms:
                ws_pipeline.reset_cycle_state(s)
            ws_pipeline.subscribe_tickers(active_target_syms)

        update_dashboard_armed_state(fresh_targets, is_manual_mode, settle_epoch, prefix="LOCKED")

        # Timeline markers
        t_gc_purge = settle_epoch - config.GC_PURGE_BEFORE_SETTLE_MS                    # T-10s
        t_ws_warmup = settle_epoch - config.WS_WARMUP_BEFORE_SETTLE_MS                  # T-8s TLS Handshake
        t_tcp_warmup = settle_epoch - config.TCP_WARMUP_BEFORE_SETTLE_MS                # T-4s Backup Ping Warmup
        t_recheck = settle_epoch - config.RECHECK_BEFORE_SETTLE_MS                      # T-2.5s (2500ms)
        t_multi_ping = settle_epoch - config.MULTI_PING_BEFORE_SETTLE_MS                # T-500ms 3-Ping Rapid Burst

        # Default entry & 3-strike cancel targets before T-500ms multi-ping measurement
        t_limit_burst = settle_epoch - config.ENTRY_BEFORE_SETTLE_MS
        t_cancel_shallow_1 = settle_epoch + config.SHALLOW_CANCEL_AFTER_SETTLE_MS
        t_cancel_shallow_2 = settle_epoch + config.SHALLOW_CANCEL_BACKUP_1_MS
        t_cancel_shallow_3 = settle_epoch + config.SHALLOW_CANCEL_BACKUP_2_MS

        t_cancel_orders = settle_epoch + config.CANCEL_ORDERS_AFTER_SETTLE_MS           # T+6s
        t_scheduled_exit = settle_epoch + config.EXIT_AFTER_SETTLE_MS                   # T+7s
        t_emergency_sweep = settle_epoch + config.EMERGENCY_SWEEP_AFTER_SETTLE_MS       # T+9s Safety Sweep #1
        t_post_trade_audit = settle_epoch + config.POST_TRADE_AUDIT_AFTER_SETTLE_MS     # T+15s Official Audit
        t_second_emergency_sweep = settle_epoch + config.SECOND_EMERGENCY_SWEEP_AFTER_SETTLE_MS # T+20s Safety Sweep #2

        cycle_order_start_ms = 0
        epoch_tag = f"{int(settle_epoch / 1000) % 100000}"

        # Build execution structures & Unique Collision-Free Client Order IDs + Planned Shallow Maps
        for c_idx, c in enumerate(fresh_targets, start=1):
            sym = c["symbol"]
            clean_sym = sym.replace("USDT", "")[:7]
            rate = c["rate"]
            p_val = c["price"]
            qty_str, applied_lev, tick_s, qty_s, min_q = calculate_order_specs(sym, p_val)
            sl_ratio, cond_label = evaluate_sl_specs(rate, active_margin_usd)

            shallow_notional = max(5.5, active_margin_usd * applied_lev)

            # Unique non-colliding order ID format for 4 Shallow Orders (20%, 40%, 60%, 80% FR)
            cid_s1 = f"s1-c{c_idx}-{clean_sym}-{epoch_tag}"
            cid_s2 = f"s2-c{c_idx}-{clean_sym}-{epoch_tag}"
            cid_s3 = f"s3-c{c_idx}-{clean_sym}-{epoch_tag}"
            cid_s4 = f"s4-c{c_idx}-{clean_sym}-{epoch_tag}"

            targets_meta[sym] = {
                "symbol": sym,
                "clean_sym": clean_sym,
                "c_idx": c_idx,
                "rate": rate,
                "rate_str": f"{rate * 100:+.4f}%",
                "price": p_val,
                "t2_snapshot_price": p_val,
                "margin_usd": active_margin_usd,
                "applied_lev": applied_lev,
                "tick_size": tick_s,
                "qty_step": qty_s,
                "min_qty": min_q,
                "qty_str": qty_str,
                "sl_ratio": sl_ratio,
                "cond_label": cond_label,
                "shallow_notional": shallow_notional,
                "shallow_cids": [cid_s1, cid_s2, cid_s3, cid_s4],
                "all_cids": [cid_s1, cid_s2, cid_s3, cid_s4],
                "cids": (cid_s1, cid_s2, cid_s3, cid_s4),
                "planned_shallow_map": {},
                "planned_dca_map": {},
                "sl_price": 0.0,
                "sl_target_str": "--",
                "sl_triggered": False,
                "entry_dispatch_time": "",
                "exit_send_time": "",
                "exit_reason": "",
                "exit_status": "",
                "exit_price_detected": 0.0
            }

        with data_lock:
            trading_active = dashboard_data.get("trading_enabled", True)

        if not trading_active:
            add_ui_log("🛑 [TRADING PAUSED] Master Trading Switch is OFF! Skipping order placements. Funds 100% idle.")
            precision_wait_until(t_scheduled_exit)
            return

        # Phase 2A (T-10s): GC Purge & Pause
        precision_wait_until(t_gc_purge)
        gc.collect()
        gc.disable()
        add_ui_log("[PRE-ENTRY T-10s] GC cleaned and paused. Dedicated CPU locked.")

        # Phase 2B (T-8s): Zero-Clash Order WebSocket Re-Arm & Fresh TLS Handshake
        precision_wait_until(t_ws_warmup)
        add_ui_log("🔌 [PRE-WARM T-8s] Refreshing Order WebSocket TLS session to eliminate dormant disconnects...")
        ws_pipeline.reconnect_order_ws()
        t_wait_end = time.time() + 3.0
        while not ws_pipeline.order_ws_ready and time.time() < t_wait_end:
            time.sleep(0.05)
        if ws_pipeline.order_ws_ready:
            add_ui_log("⚡ [PRE-WARM T-8s CONFIRMED] Fresh Order WebSocket TLS Handshake active and armed.")
        else:
            add_ui_log("⚠️ [PRE-WARM T-8s WARNING] Order WebSocket re-arming in progress...")

        # Phase 2C (T-4s): Order WebSocket Backup Ping Warm-Up & Backup Latency Measurement
        precision_wait_until(t_tcp_warmup)
        if ws_pipeline.ping_order_ws():
            add_ui_log("🔥 [BACKUP PING at T-4s] Order WebSocket backup ping frame sent. Measuring backup RTT...")
        else:
            add_ui_log("⚠️ [BACKUP PING at T-4s WARNING] Backup ping frame skipped (socket not ready).")

        # Phase 3A (T-2.5s / 2500ms): Pure Price Snapshot Anchor Lock (Backup) & Rate Recheck
        precision_wait_until(t_recheck)
        dropped_syms = []
        for sym, meta in targets_meta.items():
            try:
                r_check = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/premiumIndex?symbol={sym}", timeout=1.2).json()
                fresh_r = float(r_check.get("lastFundingRate", meta["rate"]))
                fresh_p = float(r_check.get("markPrice", meta["price"]))
                if fresh_p > 0:
                    meta["price"] = fresh_p
                    meta["t2_snapshot_price"] = fresh_p

                is_invalid = False
                if is_manual_mode:
                    if fresh_r >= 0.0:
                        is_invalid = True
                else:
                    if fresh_r > config.MIN_FUNDING_RATE or fresh_r < config.MAX_DANGER_FUNDING_RATE or fresh_r >= 0.0:
                        is_invalid = True

                if is_invalid:
                    add_ui_log(f"[PRE-ENTRY HARD ABORT at T-2.5s] {sym} rate dropped to {fresh_r * 100:.4f}%. Dropping coin!")
                    dropped_syms.append(sym)
                else:
                    add_ui_log(f"🔍 [T-2.5s BACKUP SNAPSHOT] {sym} Mark Price: {meta['t2_snapshot_price']:.6f} USDT (Backup Anchor Locked)")
            except Exception as t_err:
                add_ui_log(f"⚠️ [T-2.5s RECHECK WARNING ({sym})] REST glitch ({t_err}). Using verified snapshot.")

        for d_sym in dropped_syms:
            targets_meta.pop(d_sym, None)

        if not targets_meta:
            add_ui_log("🛑 [PRE-ENTRY HARD ABORT at T-2.5s] All target coins became invalid. Cancelling cycle!")
            return

        # Phase 3B (T-500ms): Fresh 3-Ping Rapid WS Burst & Dynamic Lead Lock (For Entry & 3-Strike Cancel)
        precision_wait_until(t_multi_ping)
        ws_pipeline.ping_order_ws_burst(count=3, interval_sec=0.025, timeout_per_ping=0.075)

        dynamic_lead = ws_pipeline.get_measured_lead_latency_ms()
        target_entry_ms = settle_epoch - config.ENTRY_BEFORE_SETTLE_MS
        t_limit_burst = target_entry_ms - dynamic_lead

        t_cancel_shallow_1 = (settle_epoch + config.SHALLOW_CANCEL_AFTER_SETTLE_MS) - dynamic_lead
        t_cancel_shallow_2 = (settle_epoch + config.SHALLOW_CANCEL_BACKUP_1_MS) - dynamic_lead
        t_cancel_shallow_3 = (settle_epoch + config.SHALLOW_CANCEL_BACKUP_2_MS) - dynamic_lead

        add_ui_log(
            f"⚡ [DYNAMIC LEAD TIMELINE] Locked 1-Way Lead: {dynamic_lead:.2f}ms | "
            f"Entry Fire: T-{settle_epoch - t_limit_burst:.2f}ms (Target: T-{config.ENTRY_BEFORE_SETTLE_MS}ms) | "
            f"Cancel Strikes: T+{t_cancel_shallow_1 - settle_epoch:.1f}ms / T+{t_cancel_shallow_2 - settle_epoch:.1f}ms / T+{t_cancel_shallow_3 - settle_epoch:.1f}ms"
        )

        # Phase 4 (Lead-Compensated T-50ms): SINGLE-SHOT ORDERED 4-SHALLOW LIMIT BURST (NO RETRIES)
        precision_wait_until(t_limit_burst)
        cycle_order_start_ms = int(get_synced_time_ms()) - 1000
        entry_dispatch_time = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]

        multi_burst_frames: list[tuple[str, str, str, str, float, str]] = []

        for sym, meta in targets_meta.items():
            meta["entry_dispatch_time"] = entry_dispatch_time

            # Anchor Price: Priority 1 = Latest Live WS Ticker, Priority 2 = T-2.5s Snapshot
            live_ws_p = ws_pipeline.get_live_price(sym)
            if live_ws_p and live_ws_p > 0:
                anchor_price = live_ws_p
                anchor_src = "LIVE WS TICKER"
            else:
                anchor_price = meta["t2_snapshot_price"]
                anchor_src = "T-2.5s BACKUP SNAPSHOT"

            dec_anchor = Decimal(str(anchor_price))
            abs_rate_dec = Decimal(str(abs(float(meta["rate"]))))
            cid_s1, cid_s2, cid_s3, cid_s4 = meta["cids"]

            shallow_specs = [
                (cid_s1, config.SHALLOW_LIMIT_RATIOS[0], "Shallow Limit #1 (20% FR)", 1),
                (cid_s2, config.SHALLOW_LIMIT_RATIOS[1], "Shallow Limit #2 (40% FR)", 2),
                (cid_s3, config.SHALLOW_LIMIT_RATIOS[2], "Shallow Limit #3 (60% FR)", 3),
                (cid_s4, config.SHALLOW_LIMIT_RATIOS[3], "Shallow Limit #4 (80% FR)", 4),
            ]

            coin_orders_count = 0
            for cid_s, s_ratio, s_label, s_idx in shallow_specs:
                drop_dec = abs_rate_dec * Decimal(str(s_ratio))
                p_dec = dec_anchor * (Decimal("1") - drop_dec)
                p_str = format_tick(float(p_dec), meta["tick_size"])
                q_str = format_limit_qty(meta["shallow_notional"], p_str, meta["min_qty"], meta["qty_step"])
                multi_burst_frames.append((sym, cid_s, q_str, p_str, float(drop_dec), s_label))
                coin_orders_count += 1
                meta["planned_shallow_map"][cid_s] = {
                    "cid": cid_s,
                    "label": s_label,
                    "price": p_str,
                    "qty": q_str,
                    "sort_idx": s_idx
                }

            # Stop-Loss: 150% of |FR| below Anchor Price, with strict 0.60% minimum floor
            sl_drop_dec = max(
                Decimal(str(config.MIN_SL_DROP_RATIO)),
                abs_rate_dec * Decimal(str(config.SL_FUNDING_MULTIPLIER))
            )
            sl_dec = dec_anchor * (Decimal("1") - sl_drop_dec)
            meta["sl_price"] = float(sl_dec)
            meta["sl_target_str"] = format_tick(meta["sl_price"], meta["tick_size"])

            add_ui_log(
                f"⚡ [T-{config.ENTRY_BEFORE_SETTLE_MS}ms SINGLE-SHOT BURST ({entry_dispatch_time})] Firing {coin_orders_count} Shallow Limit Orders on {sym} "
                f"(${meta['margin_usd']:.2f} Base @ {meta['applied_lev']}x) | Anchor ({anchor_src}): {anchor_price:.6f} | {meta['cond_label']}"
            )
            add_ui_log(
                f"🛡️ [STOP LOSS ARMED ({sym})] Floor: {meta['sl_target_str']} USDT "
                f"(-{float(sl_drop_dec) * 100:.3f}% from Anchor | Pure Trade LTP Trigger)"
            )

        # Flush all Limit Orders across all active coins in 1 single socket lock (<0.5ms)
        executor.execute_multi_symbol_limit_burst_ws(ws_pipeline, multi_burst_frames)

        # Phase 5: Real-Trade (LTP) Stop Loss Watchdog Loop + 3-Strike Shallow Cancel (T+235ms / T+250ms / T+270ms)
        shallow_cancel_1_fired = False
        shallow_cancel_2_fired = False
        shallow_cancel_3_fired = False

        def trigger_sl_sequence(target_symbol: str, meta: dict, real_ltp: float) -> None:
            meta["exit_price_detected"] = real_ltp
            meta["exit_reason"] = f"REAL-TRADE SL HIT (LTP: {real_ltp} <= SL: {meta['sl_target_str']})"
            meta["exit_status"] = "STOP LOSS EXECUTED (MAX LOSS SHIELD)"
            add_ui_log(f"🚨 [{target_symbol} | {meta['exit_reason']}] Triggering Emergency Stop Loss Priority...")

            # STEP 1: CLOSE POSITION FIRST VIA ZERO-DELAY WEBSOCKET COUNTER (2-4ms)
            ws_pos = ws_pipeline.get_filled_qty(target_symbol)
            exit_qty = format_step(ws_pos, meta["qty_step"]) if ws_pos > 0 else meta["qty_str"]

            if ws_pos > 0:
                add_ui_log(f"⚡ [SL PRIORITY #1 ({target_symbol})] Closing OPEN POSITION FIRST via WS! Market Sell for {exit_qty} {target_symbol}...")
                _, meta["exit_send_time"], _ = executor.execute_market_exit_3layer(
                    ws_pipeline, target_symbol, exit_qty, f"SL EMERGENCY EXIT ({target_symbol})"
                )
            else:
                add_ui_log(f"🛡️ [SL PRIORITY #1 ({target_symbol})] Verified: 0 open WS position to close.")

            # STEP 2: CANCEL ALL REMAINING LIMIT ORDERS SECOND (Ultra-Fast WS)
            add_ui_log(f"🧹 [SL PRIORITY #2 ({target_symbol})] Position closed! Purging all open limit orders via Fast-Lane WS...")
            executor.fast_cancel_all_orders(ws_pipeline, target_symbol, meta["all_cids"])

            # STEP 3: FINAL REMNANT SWEEP (REST API Cross-Check)
            rem_pos = executor.fetch_live_position_size(target_symbol)
            if rem_pos > 0:
                rem_qty = format_step(rem_pos, meta["qty_step"])
                add_ui_log(f"⚠️ [SL PRIORITY #3 ({target_symbol})] Remnant fill detected ({rem_qty} {target_symbol}). Hard sweeping to 0...")
                executor.execute_market_exit_3layer(
                    ws_pipeline, target_symbol, rem_qty, f"SL REM-SWEEP EXIT ({target_symbol})"
                )

        while get_synced_time_ms() < t_cancel_orders:
            now_loop_ms = get_synced_time_ms()

            # Strike #1: T+235ms Lead-Compensated Primary WS Shallow Cancel
            if not shallow_cancel_1_fired and now_loop_ms >= t_cancel_shallow_1:
                shallow_cancel_1_fired = True
                for sym, meta in targets_meta.items():
                    threading.Thread(
                        target=executor.fast_cancel_shallow_orders,
                        args=(ws_pipeline, sym, meta["shallow_cids"], "c1", f"STRIKE #1 (T+{int(config.SHALLOW_CANCEL_AFTER_SETTLE_MS)}ms)", False),
                        daemon=True
                    ).start()

            # Strike #2: T+250ms Unconditional Backup #1 WS Shallow Cancel
            if not shallow_cancel_2_fired and now_loop_ms >= t_cancel_shallow_2:
                shallow_cancel_2_fired = True
                for sym, meta in targets_meta.items():
                    threading.Thread(
                        target=executor.fast_cancel_shallow_orders,
                        args=(ws_pipeline, sym, meta["shallow_cids"], "c2", f"BACKUP #1 (T+{int(config.SHALLOW_CANCEL_BACKUP_1_MS)}ms)", False),
                        daemon=True
                    ).start()

            # Strike #3: T+270ms Unconditional Backup #2 WS Shallow Cancel (+ HTTP REST Fallback)
            if not shallow_cancel_3_fired and now_loop_ms >= t_cancel_shallow_3:
                shallow_cancel_3_fired = True
                for sym, meta in targets_meta.items():
                    threading.Thread(
                        target=executor.fast_cancel_shallow_orders,
                        args=(ws_pipeline, sym, meta["shallow_cids"], "c3", f"BACKUP #2 (T+{int(config.SHALLOW_CANCEL_BACKUP_2_MS)}ms)", True),
                        daemon=True
                    ).start()

            for sym, meta in targets_meta.items():
                if meta["sl_triggered"]:
                    continue

                real_market_trade_price = ws_pipeline.get_last_traded_price(sym)
                active_pos_check = ws_pipeline.get_filled_qty(sym)

                if active_pos_check > 0 and (0.0 < real_market_trade_price <= meta["sl_price"] and meta["sl_price"] > 0):
                    meta["sl_triggered"] = True
                    threading.Thread(
                        target=trigger_sl_sequence,
                        args=(sym, meta, real_market_trade_price),
                        daemon=True
                    ).start()

            if all(m["sl_triggered"] for m in targets_meta.values()):
                break

            time.sleep(0.001)

        # Phase 6: Normal Settlement Exit (T+6s Cancel Any Remaining Open Orders -> T+7s Market Exit)
        remaining_active_metas = [m for m in targets_meta.values() if not m["sl_triggered"]]
        if remaining_active_metas:
            precision_wait_until(t_cancel_orders)
            add_ui_log(f"⚡ [T+{(config.CANCEL_ORDERS_AFTER_SETTLE_MS // 1000)}s SETTLEMENT FEE CAPTURED] Purging any remaining open orders via Fast WS...")
            cancel_threads = []
            for meta in remaining_active_metas:
                th_c = threading.Thread(
                    target=executor.fast_cancel_all_orders,
                    args=(ws_pipeline, meta["symbol"], meta["all_cids"]),
                    daemon=True
                )
                th_c.start()
                cancel_threads.append(th_c)

            precision_wait_until(t_scheduled_exit)
            time.sleep(0.02)

            def trigger_harvest_exit(meta: dict) -> None:
                sym = meta["symbol"]
                live_pos = executor.fetch_live_position_size(sym)
                ws_pos = ws_pipeline.get_filled_qty(sym)
                total_pos = max(live_pos, ws_pos)
                meta["exit_price_detected"] = ws_pipeline.get_live_price(sym) or meta["t2_snapshot_price"]

                if total_pos > 0:
                    exit_qty = format_step(total_pos, meta["qty_step"])
                    meta["exit_reason"] = f"POST-SETTLEMENT HARVEST EXIT (T+{(config.EXIT_AFTER_SETTLE_MS // 1000)}s)"
                    meta["exit_status"] = f"SETTLEMENT FEE CAPTURED (EXIT T+{(config.EXIT_AFTER_SETTLE_MS // 1000)}s)"
                    add_ui_log(f"💰 [{sym} | {meta['exit_reason']}] Liquidation triggered! Selling filled position: {exit_qty} {sym}...")
                    _, meta["exit_send_time"], _ = executor.execute_market_exit_3layer(
                        ws_pipeline, sym, exit_qty, f"HARVEST EXIT ({sym})"
                    )
                else:
                    meta["exit_reason"] = "CLEAN PASS (NO LIMIT FILLED)"
                    meta["exit_status"] = "CLEAN PASS (NO LIMIT FILLED)"
                    add_ui_log(f"🛡️ [{sym} CLEAN PASS] 0 limit orders filled during dip window. Skipping exit sell.")

            harvest_threads = []
            for meta in remaining_active_metas:
                th_h = threading.Thread(target=trigger_harvest_exit, args=(meta,), daemon=True)
                th_h.start()
                harvest_threads.append(th_h)

            for th_h in harvest_threads:
                th_h.join(timeout=1.5)

        # Phase 6B: Scheduled Safety Net Check #1 at T+9s (Before T+15s audit)
        precision_wait_until(t_emergency_sweep)
        for sym, meta in targets_meta.items():
            try:
                executor.binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": sym})
                final_pos = executor.fetch_live_position_size(sym)
                if final_pos > 0:
                    clean_qty = format_step(final_pos, meta["qty_step"])
                    add_ui_log(f"🚨 [SAFETY NET #1 T+{(config.EMERGENCY_SWEEP_AFTER_SETTLE_MS // 1000)}s] Remnant position found ({clean_qty} {sym}). Hard market closing...")
                    executor.binance_rest_post("/fapi/v1/order", {
                        "symbol": sym,
                        "side": "SELL",
                        "type": "MARKET",
                        "quantity": clean_qty,
                        "reduceOnly": "true"
                    })
                else:
                    add_ui_log(f"🛡️ [Safety Net #1 T+{(config.EMERGENCY_SWEEP_AFTER_SETTLE_MS // 1000)}s] Position on {sym} verified at 0 (100% Flat).")
            except Exception as e:
                add_ui_log(f"Safety net #1 warning on {sym}: {e}")

        # Phase 7: Post-Trade Official Millisecond Reconciliation & PnL Audit at T+15s
        precision_wait_until(t_post_trade_audit)
        add_ui_log(f"📊 [POST-TRADE AUDIT at T+{(config.POST_TRADE_AUDIT_AFTER_SETTLE_MS // 1000)}s] Reconciling official millisecond timestamps and PnL from Binance...")
        for sym, meta in targets_meta.items():
            rec_entry_p, rec_exit_p, rec_entry_t, rec_exit_t, rec_dca_hits, rec_pnl = executor.fetch_post_trade_reconciliation(
                sym,
                cycle_order_start_ms or (settle_epoch - 5000),
                settle_epoch_ms=settle_epoch,
                funding_rate=meta["rate"],
                unit_notional_usd=meta["shallow_notional"]
            )

            final_avg_entry = rec_entry_p or ws_pipeline.get_avg_fill_price(sym) or 0.0
            final_exit_price = rec_exit_p or ws_pipeline.get_sell_fill_price(sym) or meta["exit_price_detected"] or meta["t2_snapshot_price"]
            entry_fill_time_str = rec_entry_t or ws_pipeline.get_last_fill_time(sym) or meta["entry_dispatch_time"]
            exit_fill_time = rec_exit_t or ws_pipeline.get_last_fill_time(sym) or datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]

            # SMART MERGING: Index-level protection so live WebSocket fills are NEVER erased
            ws_hits = ws_pipeline.get_dca_hits(sym)
            combined_hits_by_idx: dict[int, dict] = {}

            # Step A: Load WebSocket hits first
            for h in ws_hits:
                s_idx = h.get("sort_idx", 99)
                combined_hits_by_idx[s_idx] = dict(h)

            # Step B: Merge REST API hits safely
            for h in rec_dca_hits:
                s_idx = h.get("sort_idx", 99)
                if s_idx in combined_hits_by_idx:
                    existing = combined_hits_by_idx[s_idx]
                    if float(h.get("qty", 0)) >= float(existing.get("qty", 0)):
                        existing["price"] = h.get("price", existing.get("price"))
                        existing["qty"] = h.get("qty", existing.get("qty"))
                        existing["fills_count"] = max(existing.get("fills_count", 1), h.get("fills_count", 1))
                elif s_idx != 99:
                    combined_hits_by_idx[s_idx] = dict(h)

            dca_hits_list = list(combined_hits_by_idx.values())

            # Row 1 Reservation: Ensure all 4 Shallow Order boxes ALWAYS appear in Row 1 (Blue)
            filled_shallow_indices = {h.get("sort_idx") for h in dca_hits_list if h.get("group") == "shallow"}
            for cid_s, p_info in meta["planned_shallow_map"].items():
                s_idx = p_info["sort_idx"]
                if s_idx not in filled_shallow_indices:
                    telem = ws_pipeline.get_shallow_cancel_telemetry(cid_s)
                    f_ms = telem.get("fired_local_ms", 0)
                    b_ms = telem.get("binance_engine_ms", 0)
                    lat = max(0, b_ms - f_ms) if (f_ms > 0 and b_ms > 0) else 0
                    f_str = datetime.fromtimestamp(f_ms / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3] if f_ms > 0 else "--"
                    b_str = datetime.fromtimestamp(b_ms / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3] if b_ms > 0 else "--"

                    dca_hits_list.append({
                        "level": p_info["label"],
                        "group": "shallow",
                        "sort_idx": s_idx,
                        "status": "CANCELED",
                        "target_price": p_info["price"],
                        "target_qty": p_info["qty"],
                        "price": p_info["price"],
                        "qty": p_info["qty"],
                        "cancel_fired": f_str,
                        "binance_canceled": b_str,
                        "cancel_latency_ms": lat
                    })

            # Re-sort dca_hits_list so Shallow #1 -> #2 -> #3 -> #4 are in strict order
            dca_hits_list.sort(key=lambda x: (x.get("sort_idx", 99), x.get("first_ms", 0)))

            if final_avg_entry > 0:
                add_ui_log(
                    f"✅ [RECONCILED STATS ({sym})] Avg Entry: {final_avg_entry} | Exit: {final_exit_price} | "
                    f"Funding: {rec_pnl.get('funding_fee_str')} ({rec_pnl.get('funding_fee_time')}) [{rec_pnl.get('funding_orders_summary')}] | "
                    f"Net PnL: {rec_pnl.get('pnl_str')} | First Fill: {entry_fill_time_str}"
                )
                add_ui_log(f"   💵 [PNL BREAKDOWN ({sym})] {rec_pnl.get('pnl_breakdown')}")

            strategy_lbl = get_strategy_label(meta["rate"], meta["margin_usd"])
            record_completed_trade({
                "symbol": sym,
                "funding_rate": meta["rate_str"],
                "tier": strategy_lbl,
                "entry_sent": meta["entry_dispatch_time"] or "N/A",
                "entry_ack": entry_fill_time_str or "N/A",
                "entry_rtt": "Tokyo Single-Shot WS",
                "entry_fill_time": entry_fill_time_str or "N/A",
                "entry_fill_price": str(final_avg_entry) if final_avg_entry > 0 else "No Fill",
                "exit_sent": meta["exit_send_time"] or "N/A",
                "exit_fill_time": exit_fill_time,
                "exit_price": str(final_exit_price),
                "exit_type": meta["exit_reason"],
                "status": meta["exit_status"],
                "details": f"Avg Entry: {final_avg_entry} | Exit: {final_exit_price} | Lev: {meta['applied_lev']}x | SL: {meta['sl_target_str']} | {meta['cond_label']}",
                "funding_fee_received": rec_pnl.get("funding_fee_str", "$0.0000"),
                "funding_fee_class": rec_pnl.get("funding_fee_class", "pnl-neutral"),
                "funding_fee_time": rec_pnl.get("funding_fee_time", "--:--:--"),
                "funding_orders_summary": rec_pnl.get("funding_orders_summary", "0 Orders ($0.00)"),
                "pnl": rec_pnl.get("pnl_str", "$0.0000"),
                "pnl_class": rec_pnl.get("pnl_class", "pnl-neutral"),
                "pnl_breakdown": rec_pnl.get("pnl_breakdown", ""),
                "dca_hits": dca_hits_list
            })

            threading.Thread(
                target=executor.delayed_post_settlement_watchdog,
                args=(sym, meta["qty_step"], settle_epoch),
                daemon=True
            ).start()

        # Phase 8: Second Hard Emergency Safety Net Check at T+20s (Redundant Fail-Safe Backup)
        precision_wait_until(t_second_emergency_sweep)
        for sym, meta in targets_meta.items():
            try:
                executor.binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": sym})
                final_pos_20s = executor.fetch_live_position_size(sym)
                if final_pos_20s > 0:
                    clean_qty_20s = format_step(final_pos_20s, meta["qty_step"])
                    add_ui_log(f"🚨 [SAFETY NET #2 T+{(config.SECOND_EMERGENCY_SWEEP_AFTER_SETTLE_MS // 1000)}s] Remnant position found ({clean_qty_20s} {sym}). Hard market closing...")
                    executor.binance_rest_post("/fapi/v1/order", {
                        "symbol": sym,
                        "side": "SELL",
                        "type": "MARKET",
                        "quantity": clean_qty_20s,
                        "reduceOnly": "true"
                    })
                else:
                    add_ui_log(f"🛡️ [Safety Net #2 T+{(config.SECOND_EMERGENCY_SWEEP_AFTER_SETTLE_MS // 1000)}s] Position on {sym} verified at 0 (100% Flat).")
            except Exception as e:
                add_ui_log(f"Safety net #2 warning on {sym}: {e}")

    except Exception as cycle_err:
        add_ui_log(f"🚨 [CYCLE EXECUTION ERROR CAUGHT]: {cycle_err}")
    finally:
        # GUARANTEED FAIL-SAFE SWEEP INSIDE FINALLY (Zero orphan position risk)
        for sym, meta in targets_meta.items():
            try:
                executor.binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": sym})
                final_pos = executor.fetch_live_position_size(sym)
                if final_pos > 0:
                    clean_qty = format_step(final_pos, meta["qty_step"])
                    add_ui_log(f"🚨 [FINALLY GUARANTEED SWEEP] Remnant position found on {sym} ({clean_qty}). Hard closing to 0...")
                    executor.binance_rest_post("/fapi/v1/order", {
                        "symbol": sym,
                        "side": "SELL",
                        "type": "MARKET",
                        "quantity": clean_qty,
                        "reduceOnly": "true"
                    })
                else:
                    add_ui_log(f"🛡️ [FINALLY VERIFIED] Position on {sym} is 0 (100% Flat).")
            except Exception as e:
                add_ui_log(f"Finally safety sweep warning on {sym}: {e}")

            ws_pipeline.reset_cycle_state(sym)

        for sym in active_target_syms:
            ws_pipeline.reset_cycle_state(sym)

        ws_pipeline.clear_global_cycle_telemetry()
        with data_lock:
            dashboard_data["manual_mode"] = False
            dashboard_data["manual_selected_symbols"] = []
            dashboard_data["selection_locked"] = False

        gc.enable()
        gc.collect()
        add_ui_log("Garbage Collector restored. Reset to safe AUTOMATIC mode for next scan.")
        time.sleep(1.0)
