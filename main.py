from __future__ import annotations
import gc
import json
import time
import uuid
import threading
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import requests
import config
from binance_ws import BinanceFastLaneWS
from dashboard import (
    IST, data_lock, dashboard_data, add_ui_log,
    set_dashboard_status, record_completed_trade, run_web_server
)
from scanner import (
    sync_binance_clock, get_synced_time_ms, precision_wait_until,
    format_duration, find_soonest_highest_negative_funding_symbol,
    format_step, format_tick, calculate_order_specs, sign_params
)

session = requests.Session()

def binance_rest_post(endpoint: str, params: dict) -> dict:
    params["timestamp"] = int(get_synced_time_ms())
    signed = sign_params(params)
    url = f"{config.BINANCE_REST_URL}{endpoint}?{signed}"
    headers = {"X-MBX-APIKEY": config.API_KEY}
    try:
        r = session.post(url, headers=headers, timeout=2.5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def binance_rest_delete(endpoint: str, params: dict) -> dict:
    params["timestamp"] = int(get_synced_time_ms())
    signed = sign_params(params)
    url = f"{config.BINANCE_REST_URL}{endpoint}?{signed}"
    headers = {"X-MBX-APIKEY": config.API_KEY}
    try:
        r = session.delete(url, headers=headers, timeout=2.5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def fetch_live_position_size(symbol: str) -> float:
    params = {"symbol": symbol, "timestamp": int(get_synced_time_ms())}
    signed = sign_params(params)
    url = f"{config.BINANCE_REST_URL}/fapi/v2/positionRisk?{signed}"
    headers = {"X-MBX-APIKEY": config.API_KEY}
    try:
        res = session.get(url, headers=headers, timeout=2)
        data = res.json()
        if isinstance(data, list) and len(data) > 0:
            return abs(float(data[0].get("positionAmt", 0.0)))
    except Exception:
        pass
    return 0.0

def startup_safety_sweep() -> None:
    add_ui_log("[STARTUP SAFETY SWEEP] Step 1: Purging lingering open orders...")
    try:
        params = {"timestamp": int(get_synced_time_ms())}
        signed = sign_params(params)
        res = session.get(f"{config.BINANCE_REST_URL}/fapi/v2/positionRisk?{signed}", headers={"X-MBX-APIKEY": config.API_KEY}, timeout=3)
        pos_list = res.json()
        if isinstance(pos_list, list):
            for p in pos_list:
                sym = p.get("symbol", "")
                binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": sym})
        add_ui_log("[STARTUP SAFETY SWEEP] Orders cancel command sent.")
    except Exception as e:
        add_ui_log(f"[STARTUP SWEEP WARNING]: {e}")
    time.sleep(1.0)
    
    add_ui_log("[STARTUP SAFETY SWEEP] Step 2: Checking orphan positions...")
    try:
        params = {"timestamp": int(get_synced_time_ms())}
        signed = sign_params(params)
        res = session.get(f"{config.BINANCE_REST_URL}/fapi/v2/positionRisk?{signed}", headers={"X-MBX-APIKEY": config.API_KEY}, timeout=3)
        pos_list = res.json()
        open_positions = [p for p in pos_list if abs(float(p.get("positionAmt", 0.0))) > 0.0]
        if not open_positions:
            add_ui_log("[STARTUP SAFETY SWEEP] Account verified clean.")
            return
        for p in open_positions:
            sym = p.get("symbol", "")
            amt = float(p.get("positionAmt", 0.0))
            size = abs(amt)
            close_side = "SELL" if amt > 0 else "BUY"
            add_ui_log(f"[ORPHAN DETECTED] {sym} | Size: {size}")
            binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": sym})
            binance_rest_post("/fapi/v1/order", {
                "symbol": sym,
                "side": close_side,
                "type": "MARKET",
                "quantity": size,
                "reduceOnly": "true"
            })
    except Exception as e:
        add_ui_log(f"[STARTUP SWEEP EXCEPTION]: {e}")

def delayed_post_settlement_watchdog(symbol: str, qty_step: float, settle_epoch: float) -> None:
    checks = [
        (config.WATCHDOG_CHECK_1_SEC, "1-MINUTE POST-SETTLEMENT WATCHDOG"),
        (config.WATCHDOG_CHECK_2_SEC, "2-MINUTE POST-SETTLEMENT WATCHDOG")
    ]
    for delay_sec, label in checks:
        target_wake_ms = settle_epoch + (delay_sec * 1000)
        now_ms = get_synced_time_ms()
        sleep_dur = (target_wake_ms - now_ms) / 1000.0
        if sleep_dur > 0:
            try:
                time.sleep(sleep_dur)
                binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": symbol})
                active_size = fetch_live_position_size(symbol)
                if active_size > 0:
                    clean_qty = format_step(active_size, qty_step)
                    add_ui_log(f"[{label}] Found open position {clean_qty} {symbol}! Flattening...")
                    binance_rest_post("/fapi/v1/order", {
                        "symbol": symbol,
                        "side": "SELL",
                        "type": "MARKET",
                        "quantity": clean_qty,
                        "reduceOnly": "true"
                    })
                else:
                    add_ui_log(f"[{label}] Verified: 0 open position on {symbol} (Clean).")
            except Exception as e:
                add_ui_log(f"[{label} Exception]: {e}")

def run_dump_dip_engine() -> None:
    threading.Thread(target=run_web_server, daemon=True).start()
    ws_pipeline = BinanceFastLaneWS(
        api_key=config.API_KEY,
        api_secret=config.API_SECRET,
        log_fn=add_ui_log,
        status_fn=set_dashboard_status
    )
    ws_pipeline.connect()
    add_ui_log("Binance Tokyo Sniper Active")
    startup_safety_sweep()
    while not ws_pipeline.ws_ready:
        time.sleep(0.1)

    while True:
        sync_binance_clock()
        target_symbol, rate, last_price, next_funding_ms = find_soonest_highest_negative_funding_symbol()
        if not target_symbol or next_funding_ms == 0:
            with data_lock:
                dashboard_data["target_symbol"] = "Scanning..."
                dashboard_data["funding_rate"] = "0.00%"
            time.sleep(2.5)
            continue

        ws_pipeline.reset_cycle_state(target_symbol)
        ws_pipeline.subscribe_ticker(target_symbol)
        settle_epoch = next_funding_ms
        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)
        rate_percent = f"{rate * 100:+.4f}%"
        qty_str, applied_leverage, tick_size, qty_step, min_qty = calculate_order_specs(target_symbol, last_price)

        t_rescan_15s = settle_epoch - config.RESCAN_BEFORE_SETTLE_MS
        t_gc_purge = settle_epoch - config.GC_PURGE_BEFORE_SETTLE_MS
        t_recheck = settle_epoch - config.RECHECK_BEFORE_SETTLE_MS
        t_entry = settle_epoch + config.ENTRY_AFTER_SETTLE_MS
        t_scheduled_exit = settle_epoch + config.EXIT_AFTER_SETTLE_MS
        t_emergency_sweep = settle_epoch + config.EMERGENCY_SWEEP_AFTER_SETTLE_MS

        entry_ist = datetime.fromtimestamp(t_entry / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]
        
        with data_lock:
            dashboard_data["target_symbol"] = target_symbol
            dashboard_data["funding_rate"] = rate_percent
            dashboard_data["target_settlement_time"] = settle_dt.strftime('%H:%M:%S IST')
            dashboard_data["target_settlement_ms"] = settle_epoch
            dashboard_data["action_direction"] = "LONG CAPTURE"
            dashboard_data["entry_target"] = f"{entry_ist} IST"
            dashboard_data["status"] = f"ARMED: {target_symbol} | Rate: {rate_percent}"

        add_ui_log(f"🎯 TARGET LOCKED: {target_symbol} | Rate: {rate_percent}")
        
        last_logged_sec = -1
        abort_cycle = False
        
        while True:
            now_ms = get_synced_time_ms()
            remaining_ms = t_rescan_15s - now_ms
            current_sec_left = max(0, int((settle_epoch - now_ms) / 1000))
            with data_lock:
                dashboard_data["target_settlement"] = f"{settle_dt.strftime('%H:%M:%S')} IST ({format_duration(current_sec_left)})"
            if remaining_ms < 100:
                break
            remaining_sec = int(remaining_ms / 1000)
            if remaining_sec != last_logged_sec and (remaining_sec % 15 == 0 or remaining_sec < 10):
                last_logged_sec = remaining_sec
            time.sleep(0.5 if remaining_ms > 10000 else 0.1)

        if abort_cycle:
            continue

        precision_wait_until(t_rescan_15s)
        sync_binance_clock()
        fresh_best_sym, fresh_best_rate, fresh_best_price, fresh_best_ms = find_soonest_highest_negative_funding_symbol(target_settle_ms=settle_epoch)
        if fresh_best_sym and fresh_best_sym != target_symbol:
            target_symbol = fresh_best_sym
            rate = fresh_best_rate
            last_price = fresh_best_price or last_price
            rate_percent = f"{rate * 100:+.4f}%"
            ws_pipeline.reset_cycle_state(target_symbol)
            ws_pipeline.subscribe_ticker(target_symbol)
            qty_str, applied_leverage, tick_size, qty_step, min_qty = calculate_order_specs(target_symbol, last_price)

        precision_wait_until(t_gc_purge)
        gc.collect()
        gc.disable()
        add_ui_log("[GC] GC disabled for Tokyo execution.")

        try:
            precision_wait_until(t_recheck)
            fresh_rate_ok = True
            try:
                r_check = session.get(f"{config.BINANCE_REST_URL}/fapi/v1/premiumIndex?symbol={target_symbol}", timeout=2).json()
                fresh_rate = float(r_check.get("lastFundingRate", rate))
                if fresh_rate > config.MIN_FUNDING_RATE:
                    add_ui_log(f"[PRE-ENTRY HARD ABORT] Rate dropped to {fresh_rate * 100:.4f}%")
                    fresh_rate_ok = False
            except Exception:
                pass
            if not fresh_rate_ok:
                gc.enable()
                continue

            precision_wait_until(t_entry)
            order_accepted = False
            entry_dispatch_time = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
            active_req_id_ws = f"fee-buy-{uuid.uuid4().hex[:6]}"
            t_synced_now = int(get_synced_time_ms())

            ws_res = ws_pipeline.place_order_ws(
                symbol=target_symbol,
                side="BUY",
                order_type="MARKET",
                quantity=qty_str,
                client_order_id=active_req_id_ws,
                timestamp_ms=t_synced_now,
                timeout=0.3
            )

            if isinstance(ws_res, dict) and (ws_res.get("status") == 200 or "orderId" in ws_res.get("result", {})):
                order_accepted = True
                add_ui_log(f"🚀 [MARKET BUY WS] Confirmed at {entry_dispatch_time}")
            else:
                add_ui_log("⚠️ [WS BUY FAILED], Falling back to REST...")
                order_res = binance_rest_post("/fapi/v1/order", {
                    "symbol": target_symbol,
                    "side": "BUY",
                    "type": "MARKET",
                    "quantity": qty_str,
                    "newClientOrderId": f"rest-{uuid.uuid4().hex[:6]}"
                })
                if "orderId" in order_res:
                    order_accepted = True
                    add_ui_log("🚀 [MARKET BUY REST] Success!")

            if not order_accepted:
                gc.enable()
                continue

            time.sleep(0.05)
            actual_pos = ws_pipeline.get_filled_qty(target_symbol) or fetch_live_position_size(target_symbol)
            fill_price = ws_pipeline.get_avg_fill_price(target_symbol) or last_price
            fill_time_str = ws_pipeline.get_last_fill_time(target_symbol) or entry_dispatch_time

            precision_wait_until(t_scheduled_exit)
            exit_price = ws_pipeline.get_live_price(target_symbol) or fill_price
            exit_send_time = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
            
            binance_rest_post("/fapi/v1/order", {
                "symbol": target_symbol,
                "side": "SELL",
                "type": "MARKET",
                "quantity": qty_str,
                "reduceOnly": "true"
            })
            add_ui_log(f"⏰ [EXIT FIRED] at {exit_send_time}")

            record_completed_trade({
                "symbol": target_symbol,
                "funding_rate": rate_percent,
                "entry_sent": entry_dispatch_time,
                "entry_ack": fill_time_str,
                "entry_fill_time": fill_time_str,
                "entry_fill_price": str(fill_price),
                "exit_sent": exit_send_time,
                "exit_fill_time": datetime.now(IST).strftime('%H:%M:%S.%f')[:-3],
                "exit_price": str(exit_price),
                "exit_type": "Scheduled Exit",
                "status": "SETTLEMENT FEE CAPTURED",
                "details": f"Entry: {fill_price} | Exit: {exit_price} | Qty: {qty_str}"
            })

            threading.Thread(target=delayed_post_settlement_watchdog, args=(target_symbol, qty_step, settle_epoch), daemon=True).start()

        finally:
            precision_wait_until(t_emergency_sweep)
            try:
                binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": target_symbol})
                final_pos = fetch_live_position_size(target_symbol)
                if final_pos > 0:
                    binance_rest_post("/fapi/v1/order", {
                        "symbol": target_symbol,
                        "side": "SELL",
                        "type": "MARKET",
                        "quantity": str(final_pos),
                        "reduceOnly": "true"
                    })
            except Exception:
                pass
            ws_pipeline.reset_cycle_state(target_symbol)
            gc.enable()
            gc.collect()
            time.sleep(1.0)

if __name__ == '__main__':
    run_dump_dip_engine()
