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


def fast_cancel_shallow_orders(ws_pipeline, symbol: str, shallow_cids: Sequence[str]) -> None:
    """
    T+230ms par keval upar ke 3 Shallow Limit Orders (30%, 50%, 70%) ko ek hi millisecond
    me simultaneous WS cancel frames bhejkar cancel karta hai.
    Niche ke DCA orders aur already filled shallow positions 100% surakshit rehte hain.
    """
    now_cancel_dt = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
    add_ui_log(
        f"⚡ [T+{config.SHALLOW_CANCEL_AFTER_SETTLE_MS}ms SHALLOW CANCEL BURST ({now_cancel_dt})] "
        f"Firing simultaneous WS cancel for 3 shallow orders on {symbol}..."
    )
    results = ws_pipeline.cancel_orders_batch_ws(symbol, list(shallow_cids), timeout=0.08)
    for cid, res in results:
        err_obj = res.get("error", {}) if isinstance(res, dict) else {}
        err_code = err_obj.get("code") if isinstance(err_obj, dict) else None
        if isinstance(res, dict) and res.get("status") == 200 and not res.get("error"):
            add_ui_log(f"✅ [SHALLOW CANCEL CONFIRMED] {cid} canceled via WS.")
        elif err_code == -2011:
            add_ui_log(f"ℹ️ [SHALLOW ALREADY FILLED/CLOSED] {cid} ignored safely by exchange.")
        else:
            add_ui_log(f"⚠️ [SHALLOW CANCEL RESPONSE] {cid}: {res}")


def fast_cancel_all_orders(ws_pipeline, symbol: str, cids: Sequence[str] = ()) -> bool:
    """
    Ultra-Fast WebSocket API se sabhi open limit orders ko 2-4ms me cancel karta hai.
    Pehle cancelAll aur specific CIDs par WS bhejta hai; agar WS fail ho to REST fallback leta hai.
    """
    now_cancel_dt = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
    add_ui_log(f"⚡ [FAST WS CANCEL TRIGGERED at {now_cancel_dt}] Purging open limit orders on {symbol} via Fast-Lane WS API...")

    ws_success = False
    res_all = ws_pipeline.cancel_all_orders_ws(symbol, timeout=0.15)

    if isinstance(res_all, dict) and res_all.get("status") == 200 and not res_all.get("error"):
        ws_success = True
        add_ui_log("✅ [FAST WS CANCEL ALL CONFIRMED] Purged via WebSocket API in 2-4ms!")

    for cid in cids:
        if cid:
            res_c = ws_pipeline.cancel_order_ws(symbol, cid, timeout=0.08)
            if isinstance(res_c, dict) and res_c.get("status") == 200 and not res_c.get("error"):
                ws_success = True

    if ws_success:
        return True

    add_ui_log(f"⚠️ [WS CANCEL TIMEOUT / FALLBACK] WS unconfirmed ({res_all}). Purging via REST API...")
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


def fetch_post_trade_reconciliation(symbol: str, start_time_ms: int) -> tuple[float, float, str, str, list[dict], dict]:
    """
    Trade khatm hone ke baad Binance REST API (/allOrders, /userTrades, /income) se official
    millisecond-precision timestamps, First/Last fill times, aggregated limit boxes,
    dedicated Funding Fee with exact credit timestamp aur Net PnL fetch karta hai.
    """
    try:
        time.sleep(0.5)
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
        if buy_trades:
            total_buy_qty = sum(float(t.get("qty", 0.0)) for t in buy_trades)
            total_buy_cost = sum(float(t.get("price", 0.0)) * float(t.get("qty", 0.0)) for t in buy_trades)
            avg_entry_price = round(total_buy_cost / total_buy_qty, 8) if total_buy_qty > 0 else 0.0

            earliest_buy_ts = min(int(t.get("time", 0)) for t in buy_trades)
            entry_fill_time = datetime.fromtimestamp(earliest_buy_ts / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3]

            # Har Limit Order ke saare partial fills ko level ke hisaab se group karein
            grouped_by_level: dict[str, dict] = {}
            for t in buy_trades:
                p = float(t.get("price", 0.0))
                q = float(t.get("qty", 0.0))
                t_ms = int(t.get("time", 0))
                oid = t.get("orderId")
                cid = order_map.get(oid, "")
                lvl_name, grp_type, s_idx = resolve_limit_level_meta(cid, oid)

                if lvl_name not in grouped_by_level:
                    grouped_by_level[lvl_name] = {
                        "level": lvl_name,
                        "group": grp_type,
                        "sort_idx": s_idx,
                        "total_qty": 0.0,
                        "total
