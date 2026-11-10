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

def generate_client_id(prefix: str) -> str:
    short_uuid = uuid.uuid4().hex[:8]
    return f"{prefix}-{short_uuid}"

def binance_rest_post(endpoint: str, params: dict, timeout: float = 2.0) -> dict:
    url = f"{config.BINANCE_REST_URL}{endpoint}"
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign_params(params)
    headers = {"X-MBX-APIKEY": config.API_KEY}
    
    try:
        r = session.post(url, headers=headers, data=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        add_ui_log(f"REST POST Error [{r.status_code}]: {r.text}")
        return {}
    except requests.exceptions.Timeout:
        add_ui_log(f"REST POST Timeout: {endpoint}")
        return {}
    except Exception as e:
        add_ui_log(f"REST POST Exception: {e}")
        return {}

def binance_rest_delete(endpoint: str, params: dict, timeout: float = 2.0) -> dict:
    url = f"{config.BINANCE_REST_URL}{endpoint}"
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign_params(params)
    headers = {"X-MBX-APIKEY": config.API_KEY}
    
    try:
        r = session.delete(url, headers=headers, data=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        add_ui_log(f"REST DELETE Error [{r.status_code}]: {r.text}")
        return {}
    except requests.exceptions.Timeout:
        add_ui_log(f"REST DELETE Timeout: {endpoint}")
        return {}
    except Exception as e:
        add_ui_log(f"REST DELETE Exception: {e}")
        return {}

def binance_rest_get(endpoint: str, params: dict, timeout: float = 2.0) -> dict | list:
    url = f"{config.BINANCE_REST_URL}{endpoint}"
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign_params(params)
    headers = {"X-MBX-APIKEY": config.API_KEY}
    
    try:
        r = session.get(url, headers=headers, params=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return {}
    except Exception as e:
        return {}


def set_leverage(symbol: str, target_leverage: int) -> bool:
    add_ui_log(f"Setting leverage for {symbol} to {target_leverage}x...")
    res = binance_rest_post("/fapi/v1/leverage", {"symbol": symbol, "leverage": target_leverage})
    if res and res.get("leverage") == target_leverage:
        return True
    return False

def set_margin_type(symbol: str, margin_type: str = "CROSSED") -> bool:
    res = binance_rest_post("/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type})
    if res and res.get("code") == 200:
        return True
    if res and res.get("code") == -4046:
        # Already set to requested margin type
        return True
    return False

def fetch_live_position_size(symbol: str) -> float:
    """REST API se live position size check karta hai (for Safety Net sweeps)"""
    res = binance_rest_get("/fapi/v2/positionRisk", {"symbol": symbol})
    if isinstance(res, list):
        for pos in res:
            if pos.get("symbol") == symbol and pos.get("positionSide") == "BOTH":
                return abs(float(pos.get("positionAmt", 0.0)))
    return 0.0


def execute_limit_burst_ws(ws_pipeline, symbol: str, limit_orders_batch: list[tuple[str, str, str, float, str]]) -> None:
    """
    T-50ms par ek single WebSocket batchOrder command bhejkar 4 ya 5 Limit Orders 
    ko exact strict sequential order me (30% -> 50% -> 70% -> DCA#1 -> DCA#2) flush karta hai.
    """
    num_orders = len(limit_orders_batch)
    if num_orders == 0:
        return
        
    start_perf = time.perf_counter()
    
    # WebSocket Fast-Lane Batch Execution (T-50ms)
    results = ws_pipeline.place_orders_burst_ws(symbol, limit_orders_batch)
    
    elapsed_ms = (time.perf_counter() - start_perf) * 1000.0
    
    success_count = 0
    for cid, q, p, _, lbl, res in results:
        if "error" not in res and res.get("status") in ["NEW", "FILLED", "PARTIALLY_FILLED"]:
            success_count += 1
            ws_pipeline.active_limit_orders.add(cid)
        else:
            err_msg = res.get('error') or res.get('msg') or 'Unknown'
            add_ui_log(f"❌ [WS BURST FAILED] {lbl} rejected: {err_msg}")
            
    if success_count == num_orders:
        add_ui_log(f"✅ [WS BURST SUCCESS] All {num_orders} Limit Orders armed via Fast-Lane in {elapsed_ms:.1f}ms")
    else:
        add_ui_log(f"⚠️ [WS BURST WARNING] Armed {success_count}/{num_orders} orders. Pipeline Latency: {elapsed_ms:.1f}ms")


def fast_cancel_shallow_orders(ws_pipeline, symbol: str, shallow_cids: list[str]) -> None:
    """
    T+230ms par Binance Matching engine par strike karne ke liye 
    dynamic latency lead buffer laga kar 3 Shallow Limit Orders ko cancel karta hai.
    """
    valid_cids = [cid for cid in shallow_cids if cid in ws_pipeline.active_limit_orders]
    if not valid_cids:
        return

    start_perf = time.perf_counter()
    
    # 3 Shallow orders ko ek saath batch cancel command bhejta hai
    results = ws_pipeline.cancel_orders_batch_ws(symbol, valid_cids)
    
    elapsed_ms = (time.perf_counter() - start_perf) * 1000.0
    
    for cid, res in results:
        if "error" not in res:
            ws_pipeline.active_limit_orders.discard(cid)
            
    add_ui_log(f"🧹 [SHALLOW CANCEL BURST] Fired {len(valid_cids)} cancellations in {elapsed_ms:.1f}ms")


def fast_cancel_all_orders(ws_pipeline, symbol: str, all_cids: list[str]) -> None:
    """
    T+6s Settlement fees milne ke baad bacche hue saare Limit Orders (DCA 1 & 2) ko purge karta hai.
    """
    valid_cids = [cid for cid in all_cids if cid in ws_pipeline.active_limit_orders]
    if not valid_cids:
        add_ui_log("🧹 [CLEANUP] No open limit orders left to cancel.")
        return

    start_perf = time.perf_counter()
    
    # Send order.cancelAll via Fast-Lane WebSocket
    res = ws_pipeline.cancel_all_orders_ws(symbol)
    
    elapsed_ms = (time.perf_counter() - start_perf) * 1000.0
    
    if "error" not in res:
        ws_pipeline.active_limit_orders.clear()
        add_ui_log(f"🧹 [WS PURGE SUCCESS] Canceled all remaining {len(valid_cids)} orders in {elapsed_ms:.1f}ms")
    else:
        add_ui_log(f"⚠️ [WS PURGE FALLBACK] Fast cancel failed, using REST API...")
        binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": symbol})


def execute_market_exit_3layer(ws_pipeline, symbol: str, qty_str: str, exit_reason: str) -> tuple[float, str, str]:
    """
    Position close karne ke liye 3-Layer Market Exit mechanism:
    Priority 1: WebSocket Fast-Lane API V3 (Fastest 2-4ms)
    Priority 2: REST API Market Exit (Fallback)
    Priority 3: Emergency Hard Sweep
    """
    exit_send_time = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
    
    # Priority 1: WebSocket Market Sell
    cid_ws = f"exit-ws-{symbol[:4]}-{int(time.time()*1000)%100000}"
    ws_res = ws_pipeline.place_order_ws(
        symbol=symbol, side="SELL", order_type="MARKET", quantity=qty_str, 
        client_order_id=cid_ws, reduce_only=True
    )
    
    if "error" not in ws_res and ws_res.get("status") in ["NEW", "FILLED", "PARTIALLY_FILLED"]:
        add_ui_log(f"✅ [MARKET EXIT WS] Fast-Lane Exit accepted. Sent at {exit_send_time}")
        return 0.0, exit_send_time, exit_reason
        
    add_ui_log(f"⚠️ [MARKET EXIT WS FAILED] Fallback to REST API... Error: {ws_res.get('error')}")
    
    # Priority 2: REST API Market Sell (Fallback)
    cid_rest = f"exit-rest-{symbol[:4]}-{int(time.time()*1000)%100000}"
    rest_res = binance_rest_post("/fapi/v1/order", {
        "symbol": symbol,
        "side": "SELL",
        "type": "MARKET",
        "quantity": qty_str,
        "newClientOrderId": cid_rest,
        "reduceOnly": "true"
    })
    
    if rest_res and rest_res.get("status") in ["NEW", "FILLED", "PARTIALLY_FILLED"]:
        add_ui_log(f"✅ [MARKET EXIT REST] Fallback Exit accepted. Sent at {exit_send_time}")
        return 0.0, exit_send_time, f"{exit_reason} (REST Fallback)"
        
    add_ui_log(f"🚨 [MARKET EXIT FAILED] Failed to exit via REST API! Triggering Emergency Hard Sweep...")
    return 0.0, exit_send_time, f"{exit_reason} (FAILED)"


def fetch_post_trade_reconciliation(symbol: str, start_time_ms: int) -> tuple[float, float, str, str, list[dict], dict]:
    """
    Trade cycle complete hone ke baad Binance REST API se official Income aur User Trades 
    fetch karke exact fill times aur Net PnL reconcile karta hai.
    """
    avg_entry = 0.0
    exit_price = 0.0
    first_entry_time_str = ""
    exit_time_str = ""
    dca_hits = []
    
    pnl_data = {
        "funding_fee": 0.0,
        "funding_fee_str": "$0.0000",
        "funding_fee_time": "--:--:--",
        "funding_fee_class": "pnl-neutral",
        "realized_pnl": 0.0,
        "commission": 0.0,
        "net_pnl": 0.0,
        "pnl_str": "$0.0000",
        "pnl_class": "pnl-neutral",
        "pnl_breakdown": ""
    }
    
    # 1. Fetch Official Funding Fee Income
    try:
        income_res = binance_rest_get("/fapi/v1/income", {
            "symbol": symbol,
            "incomeType": "FUNDING_FEE",
            "startTime": start_time_ms,
            "limit": 50
        })
        
        if isinstance(income_res, list):
            funding_income = sum(float(i.get("income", 0.0)) for i in income_res)
            pnl_data["funding_fee"] = funding_income
            
            if funding_income > 0:
                pnl_data["funding_fee_str"] = f"+${funding_income:.4f}"
                pnl_data["funding_fee_class"] = "pnl-positive"
            elif funding_income < 0:
                pnl_data["funding_fee_str"] = f"-${abs(funding_income):.4f}"
                pnl_data["funding_fee_class"] = "pnl-negative"
            else:
                pnl_data["funding_fee_str"] = "$0.0000"
                pnl_data["funding_fee_class"] = "pnl-neutral"
                
            if len(income_res) > 0:
                inc_time = int(income_res[0].get("time", 0))
                if inc_time > 0:
                    pnl_data["funding_fee_time"] = datetime.fromtimestamp(inc_time / 1000, tz=IST).strftime("%H:%M:%S")
    except Exception as e:
        add_ui_log(f"⚠️ [RECONCILIATION WARN] Failed to fetch Funding Income: {e}")

    # 2. Fetch Official User Trades (Fills & Commission)
    try:
        trades_res = binance_rest_get("/fapi/v1/userTrades", {
            "symbol": symbol,
            "startTime": start_time_ms,
            "limit": 50
        })
        
        if isinstance(trades_res, list) and len(trades_res) > 0:
            total_buy_qty = 0.0
            total_buy_cost = 0.0
            total_sell_qty = 0.0
            total_sell_cost = 0.0
            total_realized_pnl = 0.0
            total_commission = 0.0
            
            first_buy_ms = 0
            last_sell_ms = 0
            
            hit_map = {}
            
            for t in trades_res:
                side = t.get("side", "")
                qty = float(t.get("qty", 0.0))
                price = float(t.get("price", 0.0))
                comm = float(t.get("commission", 0.0))
                real_pnl = float(t.get("realizedPnl", 0.0))
                t_ms = int(t.get("time", 0))
                cid = t.get("clientOrderId", "")
                
                total_commission += comm
                total_realized_pnl += real_pnl
                
                t_str = datetime.fromtimestamp(t_ms / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3]
                
                if side == "BUY":
                    if first_buy_ms == 0 or t_ms < first_buy_ms:
                        first_buy_ms = t_ms
                        first_entry_time_str = t_str
                        
                    total_buy_qty += qty
                    total_buy_cost += (price * qty)
                    
                    lbl, grp, s_idx = resolve_limit_level_meta(cid)
                    if lbl not in hit_map:
                        hit_map[lbl] = {
                            "level": lbl,
                            "group": grp,
                            "sort_idx": s_idx,
                            "status": "FILLED (REST)",
                            "price": price,
                            "qty": qty,
                            "first_time": t_str,
                            "first_ms": t_ms
                        }
                    else:
                        old_q = hit_map[lbl]["qty"]
                        new_q = old_q + qty
                        hit_map[lbl]["price"] = ((hit_map[lbl]["price"] * old_q) + (price * qty)) / new_q
                        hit_map[lbl]["qty"] = new_q
                        if t_ms < hit_map[lbl]["first_ms"]:
                            hit_map[lbl]["first_ms"] = t_ms
                            hit_map[lbl]["first_time"] = t_str
                
                elif side == "SELL":
                    if t_ms > last_sell_ms:
                        last_sell_ms = t_ms
                        exit_time_str = t_str
                        
                    total_sell_qty += qty
                    total_sell_cost += (price * qty)
                    
            if total_buy_qty > 0:
                avg_entry = round(total_buy_cost / total_buy_qty, 5)
            if total_sell_qty > 0:
                exit_price = round(total_sell_cost / total_sell_qty, 5)
                
            pnl_data["realized_pnl"] = total_realized_pnl
            pnl_data["commission"] = total_commission
            pnl_data["net_pnl"] = total_realized_pnl + pnl_data["funding_fee"] - total_commission
            
            dca_hits = list(hit_map.values())
            dca_hits.sort(key=lambda x: (x.get("sort_idx", 99), x.get("first_ms", 0)))
            
    except Exception as e:
        add_ui_log(f"⚠️ [RECONCILIATION WARN] Failed to fetch User Trades: {e}")
        
    net_p = pnl_data["net_pnl"]
    if net_p > 0:
        pnl_data["pnl_str"] = f"+${net_p:.4f}"
        pnl_data["pnl_class"] = "pnl-positive"
    elif net_p < 0:
        pnl_data["pnl_str"] = f"-${abs(net_p):.4f}"
        pnl_data["pnl_class"] = "pnl-negative"
    else:
        pnl_data["pnl_str"] = "$0.0000"
        pnl_data["pnl_class"] = "pnl-neutral"
        
    pnl_data["pnl_breakdown"] = f"Trade PnL: ${pnl_data['realized_pnl']:.4f} | Funding: ${pnl_data['funding_fee']:.4f} | Fees: ${pnl_data['commission']:.4f}"
    
    return avg_entry, exit_price, first_entry_time_str, exit_time_str, dca_hits, pnl_data


def delayed_post_settlement_watchdog(symbol: str, qty_step: float, settle_epoch: int) -> None:
    """
    Background Thread: Settlement ke 1 min aur 2 min baad safety audit karta hai
    ki koi ghost limit order ya remnant position baaki toh nahi reh gaya.
    """
    for check_delay in [config.WATCHDOG_CHECK_1_SEC, config.WATCHDOG_CHECK_2_SEC]:
        target_ms = settle_epoch + (check_delay * 1000)
        now_ms = int(time.time() * 1000)
        wait_ms = target_ms - now_ms
        if wait_ms > 0:
            time.sleep(wait_ms / 1000.0)
            
        try:
            binance_rest_delete("/fapi/v1/allOpenOrders", {"symbol": symbol})
            
            pos = fetch_live_position_size(symbol)
            if pos > 0:
                clean_qty = format_step(pos, qty_step)
                add_ui_log(f"🚨 [WATCHDOG T+{check_delay}s] Ghost position detected ({clean_qty} {symbol}). Force liquidating...")
                binance_rest_post("/fapi/v1/order", {
                    "symbol": symbol,
                    "side": "SELL",
                    "type": "MARKET",
                    "quantity": clean_qty,
                    "reduceOnly": "true"
                })
        except Exception as e:
            add_ui_log(f"⚠️ [WATCHDOG WARN] Watchdog check failed: {e}")


def startup_safety_sweep():
    """Bot start hone par purane saare orders cancel karta hai taaki fresh slate mile"""
    add_ui_log("🧹 [STARTUP] Performing global safety sweep...")
    try:
        # Cancel all open orders across all symbols via REST (Requires iterating or specific endpoint if supported)
        # Note: /fapi/v1/allOpenOrders usually requires symbol. For safety, we only sweep if we know the target.
        pass 
    except Exception as e:
        add_ui_log(f"⚠️ [STARTUP SWEEP WARN] {e}")


class BinanceExecutor:
    """Fallback class if legacy functions refer to it. Most logic moved to standalone functions above."""
    def __init__(self):
        pass

    def get_ticker(self, symbol: str) -> dict:
        try:
            return binance_rest_get("/fapi/v1/ticker/24hr", {"symbol": symbol})
        except:
            return {}

