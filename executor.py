from __future__ import annotations
import time
import uuid
from datetime import datetime
from typing import Sequence
import requests
import config
from dashboard import IST, add_ui_log
from binance_ws import resolve_limit_level_meta
from scanner import get_synced_time_ms, sign_params, format_step

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json"
})


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


def fast_cancel_shallow_orders(
    ws_pipeline,
    symbol: str,
    shallow_cids: Sequence[str],
    strike_tag: str = "c1",
    strike_label: str = "STRIKE #1",
    use_rest_fallback: bool = False
) -> None:
    now_cancel_dt = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
    add_ui_log(f"⚡ [SHALLOW CANCEL {strike_label} at {now_cancel_dt}] Firing WS cancel for {len(shallow_cids)} shallow orders on {symbol}...")
    results = ws_pipeline.cancel_orders_batch_ws(symbol, list(shallow_cids), timeout=0.08, strike_tag=strike_tag)
    ws_transport_failed = False

    for cid, res in results:
        telem = ws_pipeline.get_shallow_cancel_telemetry(cid)
        f_ms = telem.get("fired_local_ms", 0)
        b_ms = telem.get("binance_engine_ms", 0)
        lat = max(0, b_ms - f_ms) if (f_ms > 0 and b_ms > 0) else 0
        f_str = datetime.fromtimestamp(f_ms / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3] if f_ms > 0 else "--"
        b_str = datetime.fromtimestamp(b_ms / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3] if b_ms > 0 else "--"

        err_raw = res.get("error") if isinstance(res, dict) else None
        err_code = err_raw.get("code") if isinstance(err_raw, dict) else None

        if isinstance(err_raw, str) and err_raw:
            ws_transport_failed = True

        if isinstance(res, dict) and res.get("status") == 200 and not err_raw:
            add_ui_log(f"✅ [{strike_label} CONFIRMED] {cid} | Fired: {f_str} | Engine: {b_str} (RTT: {lat}ms)")
        elif err_code == -2011:
            add_ui_log(f"ℹ️ [{strike_label} ALREADY FILLED/CLOSED] {cid} ignored safely.")
        else:
            add_ui_log(f"⚠️ [{strike_label} RESPONSE] {cid}: {res}")

    if use_rest_fallback and (ws_transport_failed or not ws_pipeline.order_ws_ready):
        add_ui_log(f"🚨 [{strike_label} REST FALLBACK] WS issue detected on {symbol}! Firing HTTP REST allOpenOrders cancel...")
        binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": symbol})


def fast_cancel_all_orders(ws_pipeline, symbol: str, cids: Sequence[str] = ()) -> bool:
    now_cancel_dt = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
    add_ui_log(f"⚡ [FAST WS CANCEL] Purging open limits on {symbol} via Fast WS...")

    ws_success = False
    res_all = ws_pipeline.cancel_all_orders_ws(symbol, timeout=0.15)
    if isinstance(res_all, dict) and res_all.get("status") == 200 and not res_all.get("error"):
        ws_success = True

    for cid in cids:
        if cid:
            res_c = ws_pipeline.cancel_order_ws(symbol, cid, timeout=0.08)
            if isinstance(res_c, dict) and res_c.get("status") == 200 and not res_c.get("error"):
                ws_success = True

    if ws_success:
        return True

    r = binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": symbol})
    return "code" not in r


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


def fetch_post_trade_reconciliation(
    symbol: str,
    start_time_ms: int,
    settle_epoch_ms: int = 0,
    funding_rate: float = 0.0,
    unit_notional_usd: float = 140.0
) -> tuple[float, float, str, str, list[dict], dict]:
    """
    Trade khatm hone ke baad (T+15s par) Binance REST API (/allOrders, /userTrades, /income) se official
    millisecond-precision timestamps, First/Last fill times, aggregated limit boxes,
    dedicated Funding Fee with exact millisecond credit timestamp, funded orders breakdown aur Net PnL fetch karta hai.
    Agar Binance cache lag se realizedPnl 0.0 aaye to mathematical fallback calculate karta hai.
    """
    try:
        ts = int(get_synced_time_ms())

        # 1. Fetch Orders to map orderId -> clientOrderId
        p_orders = {"symbol": symbol, "startTime": int(start_time_ms), "timestamp": ts}
        signed_orders = sign_params(p_orders)
        res_orders = session.get(
            f"{config.BINANCE_REST_URL}/fapi/v1/allOrders?{signed_orders}",
            headers={"X-MBX-APIKEY": config.API_KEY},
            timeout=3.0
        ).json()

        order_map = {}
        if isinstance(res_orders, list):
            for ord_obj in res_orders:
                order_map[ord_obj.get("orderId")] = ord_obj.get("clientOrderId", "")

        # 2. Fetch User Trades
        p_trades = {"symbol": symbol, "startTime": int(start_time_ms), "timestamp": ts}
        signed_trades = sign_params(p_trades)
        res_trades = session.get(
            f"{config.BINANCE_REST_URL}/fapi/v1/userTrades?{signed_trades}",
            headers={"X-MBX-APIKEY": config.API_KEY},
            timeout=3.0
        ).json()

        # 3. Fetch Income Ledger (Funding Fee, Commissions, Realized PnL)
        p_income = {"symbol": symbol, "startTime": int(start_time_ms), "timestamp": ts}
        signed_income = sign_params(p_income)
        res_income = session.get(
            f"{config.BINANCE_REST_URL}/fapi/v1/income?{signed_income}",
            headers={"X-MBX-APIKEY": config.API_KEY},
            timeout=3.0
        ).json()

        funding_fee = 0.0
        funding_fee_time_ms = 0
        income_realized_pnl = 0.0
        commission_paid = 0.0

        if isinstance(res_income, list):
            for inc in res_income:
                itype = inc.get("incomeType", "")
                val = float(inc.get("income", 0.0))
                if itype == "FUNDING_FEE":
                    funding_fee += val
                    t_inc = int(inc.get("time", 0))
                    if t_inc > funding_fee_time_ms:
                        funding_fee_time_ms = t_inc
                elif itype == "REALIZED_PNL":
                    income_realized_pnl += val
                elif itype == "COMMISSION":
                    commission_paid += abs(val)

        default_pnl_info = {
            "net_pnl": 0.0,
            "trade_pnl": 0.0,
            "funding_fee": 0.0,
            "funding_fee_str": "$0.0000",
            "funding_fee_class": "pnl-neutral",
            "funding_fee_time": "--:--:--",
            "funding_orders_summary": "0 Orders ($0.00) | No Fill",
            "commission": 0.0,
            "pnl_str": "$0.0000",
            "pnl_class": "pnl-neutral",
            "pnl_breakdown": "None (No Trades Executed)"
        }

        if not isinstance(res_trades, list) or len(res_trades) == 0:
            return 0.0, 0.0, "", "", [], default_pnl_info

        buy_trades = []
        sell_trades = []
        for tr in res_trades:
            side = tr.get("side", "").upper()
            is_buyer = tr.get("buyer") is True or side == "BUY"
            if is_buyer:
                buy_trades.append(tr)
            else:
                sell_trades.append(tr)

        avg_entry_price = 0.0
        entry_fill_time = ""
        dca_hits = []
        total_buy_qty = 0.0
        total_sell_qty = 0.0

        # Track orders filled on or before settlement snapshot (T+0ms) for Funding Fee attribution
        short_level_labels = {
            1: "S1(20%)",
            2: "S2(40%)",
            3: "S3(60%)",
            4: "S4(80%)"
        }
        pre_settle_levels: dict[int, str] = {}
        pre_settle_notional = 0.0

        if buy_trades:
            total_buy_qty = sum(float(t.get("qty", 0.0)) for t in buy_trades)
            total_buy_cost = sum(float(t.get("price", 0.0)) * float(t.get("qty", 0.0)) for t in buy_trades)
            avg_entry_price = round(total_buy_cost / total_buy_qty, 8) if total_buy_qty > 0 else 0.0

            earliest_buy_ts = min(int(t.get("time", 0)) for t in buy_trades)
            entry_fill_time = datetime.fromtimestamp(earliest_buy_ts / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3]

            grouped_by_level: dict[str, dict] = {}
            for t in buy_trades:
                p = float(t.get("price", 0.0))
                q = float(t.get("qty", 0.0))
                t_ms = int(t.get("time", 0))
                oid = t.get("orderId")
                cid = order_map.get(oid, "")
                lvl_name, grp_type, s_idx = resolve_limit_level_meta(cid, oid)

                # Check if this fill occurred on or before settlement snapshot timestamp
                if settle_epoch_ms > 0 and t_ms <= settle_epoch_ms:
                    pre_settle_notional += (p * q)
                    if s_idx in short_level_labels:
                        pre_settle_levels[s_idx] = short_level_labels[s_idx]
                    else:
                        pre_settle_levels[100 + int(oid or 0)] = f"Order#{oid}"

                if lvl_name not in grouped_by_level:
                    grouped_by_level[lvl_name] = {
                        "level": lvl_name,
                        "group": grp_type,
                        "sort_idx": s_idx,
                        "total_qty": 0.0,
                        "total_cost": 0.0,
                        "first_ms": t_ms,
                        "last_ms": t_ms,
                        "fills_count": 0
                    }

                item = grouped_by_level[lvl_name]
                item["total_qty"] += q
                item["total_cost"] += (p * q)
                if t_ms < item["first_ms"]:
                    item["first_ms"] = t_ms
                if t_ms > item["last_ms"]:
                    item["last_ms"] = t_ms
                item["fills_count"] += 1

            sorted_groups = sorted(
                grouped_by_level.values(),
                key=lambda x: (x["sort_idx"], x["first_ms"])
            )

            for g in sorted_groups:
                tot_q = g["total_qty"]
                lvl_avg_p = round(g["total_cost"] / tot_q, 8) if tot_q > 0 else 0.0
                clean_q = int(tot_q) if float(round(tot_q, 6)).is_integer() else round(tot_q, 6)
                first_t_str = datetime.fromtimestamp(g["first_ms"] / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3]
                last_t_str = datetime.fromtimestamp(g["last_ms"] / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3]
                span_ms = max(0, g["last_ms"] - g["first_ms"])

                dca_hits.append({
                    "level": g["level"],
                    "group": g["group"],
                    "sort_idx": g["sort_idx"],
                    "price": lvl_avg_p,
                    "qty": clean_q,
                    "time": first_t_str,
                    "first_time": first_t_str,
                    "last_time": last_t_str,
                    "span_ms": span_ms,
                    "fills_count": g["fills_count"]
                })

        avg_exit_price = 0.0
        exit_fill_time = ""
        trades_realized_pnl = 0.0
        trades_commission = 0.0

        if sell_trades:
            total_sell_qty = sum(float(t.get("qty", 0.0)) for t in sell_trades)
            total_sell_cost = sum(float(t.get("price", 0.0)) * float(t.get("qty", 0.0)) for t in sell_trades)
            avg_exit_price = round(total_sell_cost / total_sell_qty, 8) if total_sell_qty > 0 else 0.0

            latest_sell_ts = max(int(t.get("time", 0)) for t in sell_trades)
            exit_fill_time = datetime.fromtimestamp(latest_sell_ts / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3]

            trades_realized_pnl = sum(float(t.get("realizedPnl", 0.0)) for t in sell_trades)

        if isinstance(res_trades, list):
            trades_commission = sum(float(t.get("commission", 0.0)) for t in res_trades)

        realized_trade_pnl = income_realized_pnl if abs(income_realized_pnl) > 1e-8 else trades_realized_pnl
        total_comm = commission_paid if commission_paid > 0 else trades_commission

        # MATHEMATICAL PNL FALLBACK (Eliminates 0.0000 PnL bug when Binance API cache delays)
        matched_qty = min(total_buy_qty, total_sell_qty) if (total_buy_qty > 0 and total_sell_qty > 0) else 0.0
        if abs(realized_trade_pnl) < 1e-6 and matched_qty > 0 and avg_entry_price > 0 and avg_exit_price > 0:
            math_pnl = (avg_exit_price - avg_entry_price) * matched_qty
            if abs(math_pnl) > 1e-6:
                realized_trade_pnl = math_pnl

        net_pnl = (realized_trade_pnl + funding_fee) - total_comm

        if funding_fee > 0.00001:
            funding_fee_class = "pnl-positive"
            funding_fee_str = f"+${funding_fee:.4f} USDT"
        elif funding_fee < -0.00001:
            funding_fee_class = "pnl-negative"
            funding_fee_str = f"-${abs(funding_fee):.4f} USDT"
        else:
            funding_fee_class = "pnl-neutral"
            funding_fee_str = "$0.0000 USDT"

        funding_fee_time_str = (
            f"{datetime.fromtimestamp(funding_fee_time_ms / 1000, tz=IST).strftime('%H:%M:%S.%f')[:-3]} IST"
            if funding_fee_time_ms > 0 else "--:--:--"
        )

        # Calculate exact funded orders count & position USDT value
        abs_fr = abs(float(funding_rate))
        math_funded_usd = (abs(funding_fee) / abs_fr) if (abs(funding_fee) > 1e-6 and abs_fr > 1e-6) else 0.0

        if abs(funding_fee) > 1e-6:
            if pre_settle_levels:
                ordered_tags = [pre_settle_levels[k] for k in sorted(pre_settle_levels.keys())]
                ord_cnt = len(ordered_tags)
                disp_usd = pre_settle_notional if pre_settle_notional > 0 else math_funded_usd
                tags_joined = ", ".join(ordered_tags)
                funding_orders_summary = f"{ord_cnt} Order{'s' if ord_cnt > 1 else ''} (~${disp_usd:.2f}) | {tags_joined}"
            elif math_funded_usd > 0:
                est_cnt = max(1, min(4, round(math_funded_usd / max(1.0, unit_notional_usd))))
                funding_orders_summary = f"~{est_cnt} Order{'s' if est_cnt > 1 else ''} (~${math_funded_usd:.2f}) | Snapshot Fill"
            else:
                funding_orders_summary = "1+ Order | Credited"
        else:
            if buy_trades:
                funding_orders_summary = "0 Orders ($0.00) | Filled After Snapshot"
            else:
                funding_orders_summary = "0 Orders ($0.00) | No Fill"

        if net_pnl > 0.00001:
            pnl_class = "pnl-positive"
            pnl_str = f"+${net_pnl:.4f} USDT"
        elif net_pnl < -0.00001:
            pnl_class = "pnl-negative"
            pnl_str = f"-${abs(net_pnl):.4f} USDT"
        else:
            pnl_class = "pnl-neutral"
            pnl_str = "$0.0000 USDT"

        pnl_breakdown = (
            f"Net: {pnl_str} | Trade PnL: {realized_trade_pnl:+.4f} | "
            f"Funding Fee: {funding_fee:+.4f} ({funding_fee_time_str}) | Fee: -{total_comm:.4f}"
        )

        pnl_info = {
            "net_pnl": round(net_pnl, 4),
            "trade_pnl": round(realized_trade_pnl, 4),
            "funding_fee": round(funding_fee, 4),
            "funding_fee_str": funding_fee_str,
            "funding_fee_class": funding_fee_class,
            "funding_fee_time": funding_fee_time_str,
            "funding_orders_summary": funding_orders_summary,
            "commission": round(total_comm, 4),
            "pnl_str": pnl_str,
            "pnl_class": pnl_class,
            "pnl_breakdown": pnl_breakdown
        }

        return avg_entry_price, avg_exit_price, entry_fill_time, exit_fill_time, dca_hits, pnl_info

    except Exception as e:
        add_ui_log(f"⚠️ [RECONCILIATION ERROR]: {e}")
        return 0.0, 0.0, "", "", [], {
            "net_pnl": 0.0,
            "trade_pnl": 0.0,
            "funding_fee": 0.0,
            "funding_fee_str": "$0.0000",
            "funding_fee_class": "pnl-neutral",
            "funding_fee_time": "--:--:--",
            "funding_orders_summary": "0 Orders ($0.00)",
            "commission": 0.0,
            "pnl_str": "$0.0000",
            "pnl_class": "pnl-neutral",
            "pnl_breakdown": f"Error: {e}"
        }


def startup_safety_sweep() -> None:
    add_ui_log("[STARTUP SAFETY SWEEP] Step 1: Purging all lingering open orders from previous sessions...")
    try:
        params = {"timestamp": int(get_synced_time_ms())}
        signed = sign_params(params)
        res = session.get(f"{config.BINANCE_REST_URL}/fapi/v2/positionRisk?{signed}", headers={"X-MBX-APIKEY": config.API_KEY}, timeout=3)
        pos_list = res.json()
        if isinstance(pos_list, list):
            for p in pos_list:
                sym = p.get("symbol", "")
                binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": sym})
        add_ui_log("[STARTUP SAFETY SWEEP] Orders cancel command sent. Waiting 1.0s for sync...")
    except Exception as e:
        add_ui_log(f"[STARTUP SWEEP CANCEL WARNING]: {e}")
    time.sleep(1.0)

    add_ui_log("[STARTUP SAFETY SWEEP] Step 2: Checking for any leftover orphan positions...")
    try:
        params = {"timestamp": int(get_synced_time_ms())}
        signed = sign_params(params)
        res = session.get(f"{config.BINANCE_REST_URL}/fapi/v2/positionRisk?{signed}", headers={"X-MBX-APIKEY": config.API_KEY}, timeout=3)
        pos_list = res.json()
        if isinstance(pos_list, list):
            open_positions = [p for p in pos_list if abs(float(p.get("positionAmt", 0.0))) > 0.0]
            if not open_positions:
                add_ui_log("[STARTUP SAFETY SWEEP] Account verified clean: 0 open positions. Ready to scan.")
                return

            for p in open_positions:
                sym = p.get("symbol", "")
                amt = float(p.get("positionAmt", 0.0))
                size = abs(amt)
                close_side = "SELL" if amt > 0 else "BUY"
                add_ui_log(f"[ORPHAN POSITION DETECTED ON STARTUP] {sym} | Size: {size} | Side: {close_side}")
                binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": sym})
                binance_rest_post("/fapi/v1/order", {
                    "symbol": sym,
                    "side": close_side,
                    "type": "MARKET",
                    "quantity": size,
                    "reduceOnly": "true"
                })
                add_ui_log(f"[STARTUP SWEEP EXECUTED] Closed orphan {sym} position successfully.")
    except Exception as e:
        add_ui_log(f"[STARTUP SWEEP WARNING]: {e}")


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
                    add_ui_log(f"[{label}] Found open position ({clean_qty} {symbol})! Executing hard market sweep...")
                    binance_rest_post("/fapi/v1/order", {
                        "symbol": symbol,
                        "side": "SELL",
                        "type": "MARKET",
                        "quantity": clean_qty,
                        "reduceOnly": "true"
                    })
                    add_ui_log(f"[{label}] Successfully flattened {symbol}!")
                else:
                    add_ui_log(f"[{label}] Verified: 0 open position on {symbol} (Clean).")
            except Exception as e:
                add_ui_log(f"[{label} Exception]: {e}")


def execute_limit_order_ws(
    ws_pipeline,
    symbol: str,
    qty_str: str,
    price_str: str,
    client_order_id: str,
    ratio: float,
    label: str = "LIMIT ORDER"
) -> bool:
    res = ws_pipeline.place_order_ws(
        symbol=symbol,
        side="BUY",
        order_type="LIMIT",
        quantity=qty_str,
        price=price_str,
        time_in_force="GTC",
        client_order_id=client_order_id,
        timeout=0.09
    )
    err_code = res.get("error", {}).get("code") if isinstance(res.get("error"), dict) else None
    is_active = res.get("status") == 200 or "orderId" in res.get("result", {}) or err_code == -4116

    if is_active:
        add_ui_log(f"🎯 [{label} (WS CONFIRMED)] Set @ {price_str} USDT (-{ratio * 100:.3f}%) | Qty: {qty_str}")
        return True

    add_ui_log(f"⚠️ [{label} WS REJECT/UNCONFIRMED]: {res}")
    return False


def execute_limit_burst_ws(
    ws_pipeline,
    symbol: str,
    limit_orders_batch: list[tuple[str, str, str, float, str]]
) -> None:
    burst_results = ws_pipeline.place_orders_burst_ws(symbol, limit_orders_batch, timeout=0.09)
    for _, q_str, p_str, ratio, label, res in burst_results:
        err_code = res.get("error", {}).get("code") if isinstance(res.get("error"), dict) else None
        is_active = res.get("status") == 200 or "orderId" in res.get("result", {}) or err_code == -4116
        if is_active:
            add_ui_log(f"🎯 [{label} (WS CONFIRMED)] Set @ {p_str} USDT (-{ratio * 100:.3f}%) | Qty: {q_str}")
        else:
            add_ui_log(f"⚠️ [{label} WS REJECT/UNCONFIRMED]: {res}")


def execute_multi_symbol_limit_burst_ws(
    ws_pipeline,
    multi_orders_batch: list[tuple[str, str, str, str, float, str]]
) -> None:
    burst_results = ws_pipeline.place_multi_symbol_burst_ws(multi_orders_batch, timeout=0.09)
    for sym, cid, q_str, p_str, ratio, label, res in burst_results:
        err_code = res.get("error", {}).get("code") if isinstance(res.get("error"), dict) else None
        is_active = res.get("status") == 200 or "orderId" in res.get("result", {}) or err_code == -4116
        if is_active:
            add_ui_log(f"🎯 [{sym} | {label} (WS CONFIRMED)] Set @ {p_str} USDT (-{ratio * 100:.3f}%) | Qty: {q_str}")
        else:
            add_ui_log(f"⚠️ [{sym} | {label} WS REJECT/UNCONFIRMED]: {res}")


def execute_market_exit_3layer(
    ws_pipeline,
    symbol: str,
    qty_str: str,
    reason_label: str
) -> tuple[bool, str, str]:
    now_exit_dt = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]

    req_id_1 = f"exit-ws1-{uuid.uuid4().hex[:6]}"
    add_ui_log(f"[{reason_label} | ATTEMPT #1 (WS)] Fired at {now_exit_dt} IST | Qty: {qty_str}")
    res1 = ws_pipeline.place_order_ws(
        symbol=symbol,
        side="SELL",
        order_type="MARKET",
        quantity=qty_str,
        client_order_id=req_id_1,
        timeout=0.5,
        reduce_only=True
    )
    if res1.get("status") == 200 or "orderId" in res1.get("result", {}):
        add_ui_log(f"[{reason_label} | ATTEMPT #1 CONFIRMED] Position closed on {symbol} via WS API!")
        return True, now_exit_dt, "WS_ATTEMPT_1"

    req_id_2 = f"exit-ws2-{uuid.uuid4().hex[:6]}"
    add_ui_log(f"[{reason_label} | ATTEMPT #2 (WS RETRY)] Retrying on {symbol} via WS API...")
    res2 = ws_pipeline.place_order_ws(
        symbol=symbol,
        side="SELL",
        order_type="MARKET",
        quantity=qty_str,
        client_order_id=req_id_2,
        timeout=0.5,
        reduce_only=True
    )
    if res2.get("status") == 200 or "orderId" in res2.get("result", {}):
        add_ui_log(f"[{reason_label} | ATTEMPT #2 CONFIRMED] Position closed on {symbol} on 2nd WS attempt!")
        return True, now_exit_dt, "WS_ATTEMPT_2"

    add_ui_log(f"[{reason_label} | ATTEMPT #3 (REST API)] Both WS attempts rejected on {symbol}! Firing instant REST Market Sell...")
    try:
        r = binance_rest_post("/fapi/v1/order", {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": qty_str,
            "reduceOnly": "true"
        })
        if "orderId" in r:
            add_ui_log(f"[{reason_label} | REST CONFIRMED] Position on {symbol} 100% closed via REST API!")
            return True, now_exit_dt, "REST_FALLBACK"
    except Exception as e:
        add_ui_log(f"REST Market Sell Exception on {symbol}: {e}")
    return False, now_exit_dt, "FAILED"
