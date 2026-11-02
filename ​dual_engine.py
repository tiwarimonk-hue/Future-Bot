import os
import time
import math
import threading
from datetime import datetime, timezone, timedelta
from pybit.unified_trading import HTTP
from binance.um_futures import UMFutures

IST = timezone(timedelta(hours=5, minutes=30))

# ==========================================
# CONFIGURATION & PARAMETERS
# ==========================================
BYBIT_API_KEY = os.environ.get('BYBIT_API_KEY', 'YOUR_BYBIT_API_KEY')
BYBIT_API_SECRET = os.environ.get('BYBIT_API_SECRET', 'YOUR_BYBIT_API_SECRET')
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', 'YOUR_BINANCE_API_KEY')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', 'YOUR_BINANCE_API_SECRET')

MIN_FUNDING_RATE_THRESHOLD = -0.0025  # Updated: -0.25% Threshold
ENTRY_MARGIN_USD = 1.0                # $1 Margin strictly kept
MAX_ALLOWED_LEVERAGE = 10.0           # Strictly capped at 10x

bybit_clock_offset = 0.0
binance_clock_offset = 0.0

# ==========================================
# CLOCK SYNCHRONIZATION
# ==========================================
def sync_exchange_clocks(bybit_client, binance_client, dashboard_data, data_lock):
    global bybit_clock_offset, binance_clock_offset
    try:
        # Bybit clock sync
        t_send = time.time() * 1000
        bybit_resp = bybit_client.get_server_time()
        t_recv = time.time() * 1000
        bybit_server = float(bybit_resp['result']['timeNano']) / 1e6
        bybit_clock_offset = (bybit_server + (t_recv - t_send) / 2.0) - t_recv

        # Binance clock sync
        t_send_b = time.time() * 1000
        binance_resp = binance_client.time()
        t_recv_b = time.time() * 1000
        binance_server = float(binance_resp['serverTime'])
        binance_clock_offset = (binance_server + (t_recv_b - t_send_b) / 2.0) - t_recv_b
    except Exception:
        pass

def get_synced_time_ms():
    return (time.time() * 1000) + ((bybit_clock_offset + binance_clock_offset) / 2.0)

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

# ==========================================
# CROSS-EXCHANGE OPPORTUNITY SCANNER
# ==========================================
def scan_cross_arb_opportunities(bybit_client, binance_client, add_ui_log):
    now_ms = int(get_synced_time_ms())

    try:
        bybit_tickers = bybit_client.get_tickers(category="linear")["result"]["list"]
        binance_premiums = binance_client.mark_price()
    except Exception as e:
        add_ui_log(f"[Dual-Arb] Scan API Fetch Error: {e}")
        return None

    # Map Binance items: {symbol: {rate, next_time, price}}
    binance_map = {}
    for bp in binance_premiums:
        sym = bp.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        try:
            binance_map[sym] = {
                "rate": float(bp.get("lastFundingRate", 0)),
                "next_time": int(bp.get("nextFundingTime", 0)),
                "price": float(bp.get("markPrice", 0))
            }
        except ValueError:
            continue

    candidates = []

    for bt in bybit_tickers:
        sym = bt.get("symbol", "")
        if not sym.endswith("USDT") or sym not in binance_map:
            continue

        raw_bybit_rate = bt.get("fundingRate", "")
        raw_bybit_next = bt.get("nextFundingTime", "")
        if not raw_bybit_rate or not raw_bybit_next:
            continue

        try:
            bybit_rate = float(raw_bybit_rate)
            bybit_next = int(raw_bybit_next)
            bybit_price = float(bt.get("lastPrice", 0))
        except ValueError:
            continue

        binance_info = binance_map[sym]
        binance_rate = binance_info["rate"]
        binance_next = binance_info["next_time"]
        binance_price = binance_info["price"]

        bybit_time_diff = bybit_next - now_ms
        binance_time_diff = binance_next - now_ms

        # Opportunity Check 1: Bybit is 1H cycle (settling in < 15 mins), Binance is 4H/8H (settling > 30 mins)
        if bybit_rate <= MIN_FUNDING_RATE_THRESHOLD and (30000 < bybit_time_diff <= 900000) and (binance_time_diff > 1800000):
            candidates.append({
                "symbol": sym,
                "long_exchange": "Bybit",
                "short_exchange": "Binance",
                "settle_epoch": bybit_next,
                "bybit_rate": bybit_rate,
                "binance_rate": binance_rate,
                "target_rate": bybit_rate,
                "bybit_price": bybit_price,
                "binance_price": binance_price,
                "spread_abs": abs(bybit_rate - binance_rate)
            })

        # Opportunity Check 2: Binance is 1H cycle (settling in < 15 mins), Bybit is 4H/8H (settling > 30 mins)
        elif binance_rate <= MIN_FUNDING_RATE_THRESHOLD and (30000 < binance_time_diff <= 900000) and (bybit_time_diff > 1800000):
            candidates.append({
                "symbol": sym,
                "long_exchange": "Binance",
                "short_exchange": "Bybit",
                "settle_epoch": binance_next,
                "bybit_rate": bybit_rate,
                "binance_rate": binance_rate,
                "target_rate": binance_rate,
                "bybit_price": bybit_price,
                "binance_price": binance_price,
                "spread_abs": abs(binance_rate - bybit_rate)
            })

    if not candidates:
        return None

    # Deepest negative funding rate first
    candidates.sort(key=lambda x: x["target_rate"])
    return candidates[0]

# ==========================================
# PRECISION & NOTIONAL ALIGNMENT
# ==========================================
def calculate_exchange_lots(bybit_client, binance_client, symbol, bybit_price, binance_price, add_ui_log):
    # 1. Bybit Specs
    try:
        bb_inst = bybit_client.get_instruments_info(category="linear", symbol=symbol)["result"]["list"][0]
        bb_max_lev = float(bb_inst.get("leverageFilter", {}).get("maxLeverage", 10.0))
        bb_qty_step = float(bb_inst["lotSizeFilter"]["qtyStep"])
        bb_min_qty = float(bb_inst["lotSizeFilter"]["minOrderQty"])
        bb_min_notional = float(bb_inst["lotSizeFilter"].get("minNotionalValue", 5.0))
    except Exception:
        bb_max_lev, bb_qty_step, bb_min_qty, bb_min_notional = 10.0, 1.0, 1.0, 5.0

    bb_lev = min(MAX_ALLOWED_LEVERAGE, bb_max_lev)
    try:
        bybit_client.set_leverage(category="linear", symbol=symbol, buyLeverage=str(bb_lev), sellLeverage=str(bb_lev))
    except Exception:
        pass

    bb_step_str = f"{bb_qty_step:.8f}".rstrip("0")
    bb_decimals = len(bb_step_str.split(".")[1]) if "." in bb_step_str else 0

    # 2. Binance Specs
    try:
        bn_info = binance_client.exchange_info()
        bn_sym = next((s for s in bn_info['symbols'] if s['symbol'] == symbol), None)
        bn_qty_step, bn_min_qty, bn_min_notional = 1.0, 1.0, 5.0
        if bn_sym:
            for f in bn_sym.get('filters', []):
                if f['filterType'] == 'LOT_SIZE':
                    bn_qty_step = float(f.get('stepSize', 1.0))
                    bn_min_qty = float(f.get('minQty', 1.0))
                elif f['filterType'] == 'MIN_NOTIONAL':
                    bn_min_notional = float(f.get('notional', 5.0))
    except Exception:
        bn_qty_step, bn_min_qty, bn_min_notional = 1.0, 1.0, 5.0

    bn_lev = int(MAX_ALLOWED_LEVERAGE)
    try:
        binance_client.change_leverage(symbol=symbol, leverage=bn_lev)
    except Exception:
        pass

    bn_step_str = f"{bn_qty_step:.8f}".rstrip("0")
    bn_decimals = len(bn_step_str.split(".")[1]) if "." in bn_step_str else 0

    # Calculate Balanced $10 Notional ($1 Margin @ 10x Leverage)
    target_notional = ENTRY_MARGIN_USD * MAX_ALLOWED_LEVERAGE

    bb_calc_qty = math.floor((target_notional / bybit_price) / bb_qty_step) * bb_qty_step
    if bb_calc_qty < bb_min_qty:
        bb_calc_qty = bb_min_qty
    if (bb_calc_qty * bybit_price) < bb_min_notional:
        bb_calc_qty = math.ceil((min_notional := bb_min_notional) / bybit_price / bb_qty_step) * bb_qty_step

    bn_calc_qty = math.floor((target_notional / binance_price) / bn_qty_step) * bn_qty_step
    if bn_calc_qty < bn_min_qty:
        bn_calc_qty = bn_min_qty
    if (bn_calc_qty * binance_price) < bn_min_notional:
        bn_calc_qty = math.ceil(bn_min_notional / binance_price / bn_qty_step) * bn_qty_step

    return {
        "bybit_qty": f"{bb_calc_qty:.{bb_decimals}f}" if bb_decimals > 0 else str(int(bb_calc_qty)),
        "binance_qty": f"{bn_calc_qty:.{bn_decimals}f}" if bn_decimals > 0 else str(int(bn_calc_qty)),
        "bybit_decimals": bb_decimals,
        "binance_decimals": bn_decimals,
        "leverage": MAX_ALLOWED_LEVERAGE
    }

# ==========================================
# SIMULTANEOUS LEG EXECUTION WORKERS
# ==========================================
def fire_bybit_order(bybit_client, symbol, side, qty, result_dict, key):
    try:
        resp = bybit_client.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=qty,
            reduceOnly=(side == "Sell"),
            positionIdx=0
        )
        if resp.get("retCode") == 0:
            result_dict[key] = {"status": "SUCCESS", "orderId": resp["result"].get("orderId")}
        else:
            result_dict[key] = {"status": "REJECTED", "error": resp.get("retMsg")}
    except Exception as e:
        result_dict[key] = {"status": "FAILED", "error": str(e)}

def fire_binance_order(binance_client, symbol, side, qty, result_dict, key):
    try:
        resp = binance_client.new_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=qty,
            reduceOnly=(side == "BUY")
        )
        result_dict[key] = {"status": "SUCCESS", "orderId": resp.get("orderId")}
    except Exception as e:
        result_dict[key] = {"status": "FAILED", "error": str(e)}

# ==========================================
# MASTER DUAL ARBITRAGE EXECUTOR
# ==========================================
def execute_dual_arbitrage_cycle(bybit_client, binance_client, arb_plan, lots, add_ui_log, add_ledger_entry):
    symbol = arb_plan["symbol"]
    long_ex = arb_plan["long_exchange"]
    short_ex = arb_plan["short_exchange"]
    settle_epoch = arb_plan["settle_epoch"]

    t_entry = settle_epoch - 15000       # Exact Entry at T - 15s
    t_settle = settle_epoch              # Settlement at T
    t_hard_exit = settle_epoch + 30000   # Hard Exit at T + 30s

    entry_results = {}
    exit_results = {}

    # ----------------------------------------------------
    # PHASE 1: DUAL ENTRY AT T - 15s
    # ----------------------------------------------------
    precision_wait_until(t_entry)
    entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

    th_bybit = threading.Thread(
        target=fire_bybit_order,
        args=(bybit_client, symbol, "Buy" if long_ex == "Bybit" else "Sell", lots["bybit_qty"], entry_results, "bybit"),
        daemon=True
    )
    th_binance = threading.Thread(
        target=fire_binance_order,
        args=(binance_client, symbol, "BUY" if long_ex == "Binance" else "SELL", lots["binance_qty"], entry_results, "binance"),
        daemon=True
    )

    th_bybit.start()
    th_binance.start()
    th_bybit.join()
    th_binance.join()

    # Capture Entry Prices & Base Entry Spread
    try:
        p_bybit_entry = float(bybit_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]["lastPrice"])
        p_binance_entry = float(binance_client.ticker_price(symbol=symbol)["price"])
    except Exception:
        p_bybit_entry = arb_plan["bybit_price"]
        p_binance_entry = arb_plan["binance_price"]

    p_long_entry = p_bybit_entry if long_ex == "Bybit" else p_binance_entry
    p_short_entry = p_binance_entry if long_ex == "Bybit" else p_bybit_entry
    entry_spread = p_long_entry / p_short_entry if p_short_entry > 0 else 1.0

    add_ui_log(f"🚀 [Dual-Arb] ENTRY DISPATCHED AT T-15s ({entry_time_str}) | Long: {long_ex} | Short: {short_ex} | Entry Spread: {entry_spread:.6f}")

    # ----------------------------------------------------
    # PHASE 2: WAIT UNTIL SETTLEMENT T
    # ----------------------------------------------------
    precision_wait_until(t_settle + 1000)  # T + 1s to allow funding credit
    add_ui_log(f"💰 [Dual-Arb] SETTLEMENT HIT ({datetime.now(IST).strftime('%H:%M:%S')}). Funding captured on {long_ex}!")

    # ----------------------------------------------------
    # PHASE 3: DYNAMIC SPREAD-REVERSION EXIT (T+1s to T+30s)
    # ----------------------------------------------------
    spread_reverted = False
    exit_reason = "Hard Exit (T+30s Timeout)"

    while True:
        now_ms = get_synced_time_ms()
        if now_ms >= t_hard_exit:
            break

        try:
            cur_bb_p = float(bybit_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]["lastPrice"])
            cur_bn_p = float(binance_client.ticker_price(symbol=symbol)["price"])
            cur_long_p = cur_bb_p if long_ex == "Bybit" else cur_bn_p
            cur_short_p = cur_bn_p if long_ex == "Bybit" else cur_bb_p
            current_spread = cur_long_p / cur_short_p if cur_short_p > 0 else 1.0

            # Exit Trigger: Current Spread matches or improves upon Entry Spread
            if current_spread >= entry_spread:
                spread_reverted = True
                exit_reason = f"Spread Reverted ({current_spread:.6f} >= {entry_spread:.6f})"
                add_ui_log(f"🎯 [Dual-Arb] SPREAD REVERSION MET: {exit_reason}. Closing immediately!")
                break
        except Exception:
            pass

        time.sleep(0.15)  # 150ms live tick check

    # ----------------------------------------------------
    # PHASE 4: CLOSE BOTH LEGS
    # ----------------------------------------------------
    exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

    th_bybit_exit = threading.Thread(
        target=fire_bybit_order,
        args=(bybit_client, symbol, "Sell" if long_ex == "Bybit" else "Buy", lots["bybit_qty"], exit_results, "bybit"),
        daemon=True
    )
    th_binance_exit = threading.Thread(
        target=fire_binance_order,
        args=(binance_client, symbol, "SELL" if long_ex == "Binance" else "BUY", lots["binance_qty"], exit_results, "binance"),
        daemon=True
    )

    th_bybit_exit.start()
    th_binance_exit.start()
    th_bybit_exit.join()
    th_binance_exit.join()

    # ----------------------------------------------------
    # PHASE 5: EMERGENCY SWEEPS (T+50s and T+60s)
    # ----------------------------------------------------
    emergency_times = [("T+50s", settle_epoch + 50000), ("T+60s", settle_epoch + 60000)]
    triggered_stages = []

    for stage_name, target_ms in emergency_times:
        precision_wait_until(target_ms)
        try:
            # Check Bybit residual
            bb_pos = bybit_client.get_positions(category="linear", symbol=symbol)["result"]["list"]
            bb_size = float(bb_pos[0].get("size", 0)) if bb_pos else 0.0
            if bb_size > 0:
                rem_qty = f"{bb_size:.{lots['bybit_decimals']}f}" if lots['bybit_decimals'] > 0 else str(int(bb_size))
                bybit_client.place_order(
                    category="linear", symbol=symbol,
                    side="Sell" if long_ex == "Bybit" else "Buy",
                    orderType="Market", qty=rem_qty, reduceOnly=True, positionIdx=0
                )
                triggered_stages.append(f"{stage_name} Bybit ({rem_qty})")

            # Check Binance residual
            bn_positions = binance_client.get_position_risk(symbol=symbol)
            bn_size = 0.0
            for p in bn_positions:
                if abs(float(p.get("positionAmt", 0))) > 0:
                    bn_size = abs(float(p.get("positionAmt")))
                    break
            if bn_size > 0:
                rem_bn_qty = f"{bn_size:.{lots['binance_decimals']}f}" if lots['binance_decimals'] > 0 else str(int(bn_size))
                binance_client.new_order(
                    symbol=symbol,
                    side="SELL" if long_ex == "Binance" else "BUY",
                    type="MARKET", quantity=rem_bn_qty, reduceOnly=True
                )
                triggered_stages.append(f"{stage_name} Binance ({rem_bn_qty})")
        except Exception as e:
            add_ui_log(f"⚠️ [Dual-Arb] Emergency check error: {e}")

    badge_text = "ARB CAPTURED" if spread_reverted else "HARD CLOSED"
    badge_class = "status-success" if spread_reverted else "status-warn"
    if triggered_stages:
        badge_text = "EMERGENCY CLOSED"
        badge_class = "status-warn"

    add_ledger_entry({
        "exchange": "Bybit ⇋ Binance",
        "coin": symbol,
        "rate": f"Long: {long_ex} ({arb_plan['target_rate']*100:.2f}%)",
        "entry_time": entry_time_str,
        "exit_time": exit_time_str,
        "detail": f"{exit_reason} | Spread: {entry_spread:.5f} | Notional: ~$10 (10x) | Emerg: {', '.join(triggered_stages) if triggered_stages else 'None'}",
        "badge_text": badge_text,
        "badge_class": badge_class
    })

# ==========================================
# MASTER ENTRY POINT (Called from main.py)
# ==========================================
def start_dual_loop(dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    bybit_client = HTTP(testnet=False, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)
    binance_client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)
    add_ui_log("🚀 Dual Arbitrage Engine Active (Thresh <= -0.25% | $1 Margin | 10x Cap | T-15s Entry | T+30s Hard Exit)")

    while True:
        sync_exchange_clocks(bybit_client, binance_client, dashboard_data, data_lock)
        arb_opp = scan_cross_arb_opportunities(bybit_client, binance_client, add_ui_log)

        if not arb_opp:
            with data_lock:
                dashboard_data['dual']['status'] = "SCANNING: No 1H vs 4H/8H opportunity (<= -0.25%)"
                dashboard_data['dual']['coin'] = "--"
                dashboard_data['dual']['bybit_rate'] = "--"
                dashboard_data['dual']['binance_rate'] = "--"
                dashboard_data['dual']['spread'] = "--"
            time.sleep(3)
            continue

        symbol = arb_opp["symbol"]
        settle_epoch = arb_opp["settle_epoch"]
        settle_dt = datetime.fromtimestamp(settle_epoch / 1000, tz=IST)

        with data_lock:
            dashboard_data['dual']['status'] = f"ARMED: Long {arb_opp['long_exchange']} / Short {arb_opp['short_exchange']}"
            dashboard_data['dual']['coin'] = symbol
            dashboard_data['dual']['bybit_rate'] = f"{arb_opp['bybit_rate']*100:+.4f}%"
            dashboard_data['dual']['binance_rate'] = f"{arb_opp['binance_rate']*100:+.4f}%"
            dashboard_data['dual']['spread'] = f"{arb_opp['spread_abs']*100:.4f}%"
            dashboard_data['dual']['settlement'] = settle_dt.strftime('%H:%M:%S IST')

        add_ui_log(f"🎯 [Dual-Arb] ARMED {symbol}: Long {arb_opp['long_exchange']} & Short {arb_opp['short_exchange']} | Settle: {dashboard_data['dual']['settlement']}")

        # Lock precision and leverage at T - 18s (3 seconds before T - 15s entry)
        t_precalc = settle_epoch - 18000

        while True:
            now_ms = get_synced_time_ms()
            if now_ms >= t_precalc:
                break
            time.sleep(1.0)

        # Pre-calculate exact lot steps
        sync_exchange_clocks(bybit_client, binance_client, dashboard_data, data_lock)
        lots = calculate_exchange_lots(
            bybit_client, binance_client, symbol,
            arb_opp["bybit_price"], arb_opp["binance_price"], add_ui_log
        )

        add_ui_log(f"⚙️ [Dual-Arb] PRE-FLIGHT ({symbol}): Bybit Qty: {lots['bybit_qty']} | Binance Qty: {lots['binance_qty']} @ {lots['leverage']}x")

        # Execute cycle with entry at T - 15s
        execute_dual_arbitrage_cycle(bybit_client, binance_client, arb_opp, lots, add_ui_log, add_ledger_entry)

        add_ui_log("✅ [Dual-Arb] Arbitrage cycle finished. Re-scanning cross-exchange markets...")
        time.sleep(5)
