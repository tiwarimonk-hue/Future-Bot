import os
import time
import math
import threading
from datetime import datetime, timezone, timedelta
from pybit.unified_trading import HTTP
from binance.um_futures import UMFutures

IST = timezone(timedelta(hours=5, minutes=30))

BYBIT_API_KEY = os.environ.get('BYBIT_API_KEY', 'YOUR_BYBIT_API_KEY')
BYBIT_API_SECRET = os.environ.get('BYBIT_API_SECRET', 'YOUR_BYBIT_API_SECRET')
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', 'YOUR_BINANCE_API_KEY')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', 'YOUR_BINANCE_API_SECRET')

MIN_FUNDING_RATE_THRESHOLD = -0.0025  # -0.25% Threshold
ENTRY_MARGIN_USD = 1.0                # $1 Margin
MAX_ALLOWED_LEVERAGE = 10.0           # 10x Max Cap

bybit_clock_offset = 0.0
binance_clock_offset = 0.0

def sync_exchange_clocks(bybit_client, binance_client, dashboard_data, data_lock):
    global bybit_clock_offset, binance_clock_offset
    try:
        t_send = time.time() * 1000
        bybit_resp = bybit_client.get_server_time()
        t_recv = time.time() * 1000
        bybit_server = float(bybit_resp['result']['timeNano']) / 1e6
        bybit_clock_offset = (bybit_server + (t_recv - t_send) / 2.0) - t_recv

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

def scan_cross_arb_opportunities(bybit_client, binance_client, add_ui_log):
    now_ms = int(get_synced_time_ms())

    try:
        bybit_tickers = bybit_client.get_tickers(category="linear")["result"]["list"]
        binance_premiums = binance_client.mark_price()
    except Exception:
        return None

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

        # Bybit settles 1H cycle, Binance settles at least 30m later
        if bybit_time_diff > 35000 and (binance_next - bybit_next >= 1800000):
            if bybit_rate <= MIN_FUNDING_RATE_THRESHOLD:
                candidates.append({
                    "symbol": sym, "long_exchange": "Bybit", "short_exchange": "Binance",
                    "settle_epoch": bybit_next, "bybit_rate": bybit_rate, "binance_rate": binance_rate,
                    "target_rate": bybit_rate, "bybit_price": bybit_price, "binance_price": binance_price,
                    "spread_abs": abs(bybit_rate - binance_rate)
                })

        # Binance settles 1H cycle, Bybit settles at least 30m later
        elif binance_time_diff > 35000 and (bybit_next - binance_next >= 1800000):
            if binance_rate <= MIN_FUNDING_RATE_THRESHOLD:
                candidates.append({
                    "symbol": sym, "long_exchange": "Binance", "short_exchange": "Bybit",
                    "settle_epoch": binance_next, "bybit_rate": bybit_rate, "binance_rate": binance_rate,
                    "target_rate": binance_rate, "bybit_price": bybit_price, "binance_price": binance_price,
                    "spread_abs": abs(binance_rate - bybit_rate)
                })

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["target_rate"])
    return candidates[0]

def calculate_exchange_lots(bybit_client, binance_client, symbol, bybit_price, binance_price):
    try:
        bb_inst = bybit_client.get_instruments_info(category="linear", symbol=symbol)["result"]["list"][0]
        bb_qty_step = float(bb_inst["lotSizeFilter"]["qtyStep"])
        bb_min_qty = float(bb_inst["lotSizeFilter"]["minOrderQty"])
        bb_min_notional = float(bb_inst["lotSizeFilter"].get("minNotionalValue", 5.0))
    except Exception:
        bb_qty_step, bb_min_qty, bb_min_notional = 1.0, 1.0, 5.0

    try:
        bybit_client.set_leverage(category="linear", symbol=symbol, buyLeverage=str(MAX_ALLOWED_LEVERAGE), sellLeverage=str(MAX_ALLOWED_LEVERAGE))
    except Exception:
        pass

    bb_step_str = f"{bb_qty_step:.8f}".rstrip("0")
    bb_decimals = len(bb_step_str.split(".")[1]) if "." in bb_step_str else 0

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

    try:
        binance_client.change_leverage(symbol=symbol, leverage=int(MAX_ALLOWED_LEVERAGE))
    except Exception:
        pass

    bn_step_str = f"{bn_qty_step:.8f}".rstrip("0")
    bn_decimals = len(bn_step_str.split(".")[1]) if "." in bn_step_str else 0

    target_notional = ENTRY_MARGIN_USD * MAX_ALLOWED_LEVERAGE

    bb_calc_qty = math.floor((target_notional / bybit_price) / bb_qty_step) * bb_qty_step
    if bb_calc_qty < bb_min_qty: bb_calc_qty = bb_min_qty
    if (bb_calc_qty * bybit_price) < bb_min_notional:
        bb_calc_qty = math.ceil((bb_min_notional / bybit_price) / bb_qty_step) * bb_qty_step

    bn_calc_qty = math.floor((target_notional / binance_price) / bn_qty_step) * bn_qty_step
    if bn_calc_qty < bn_min_qty: bn_calc_qty = bn_min_qty
    if (bn_calc_qty * binance_price) < bn_min_notional:
        bn_calc_qty = math.ceil((bn_min_notional / binance_price) / bn_qty_step) * bn_qty_step

    return {
        "bybit_qty": f"{bb_calc_qty:.{bb_decimals}f}" if bb_decimals > 0 else str(int(bb_calc_qty)),
        "binance_qty": f"{bn_calc_qty:.{bn_decimals}f}" if bn_decimals > 0 else str(int(bn_calc_qty)),
        "bybit_decimals": bb_decimals,
        "binance_decimals": bn_decimals,
        "leverage": MAX_ALLOWED_LEVERAGE
    }

def fire_bybit_order(bybit_client, symbol, side, qty):
    try:
        bybit_client.place_order(category="linear", symbol=symbol, side=side, orderType="Market", qty=qty, reduceOnly=(side == "Sell"), positionIdx=0)
    except Exception:
        pass

def fire_binance_order(binance_client, symbol, side, qty):
    try:
        binance_client.new_order(symbol=symbol, side=side, type="MARKET", quantity=qty, reduceOnly=(side == "BUY"))
    except Exception:
        pass

def execute_dual_arbitrage_cycle(bybit_client, binance_client, arb_plan, lots, add_ui_log, add_ledger_entry):
    symbol = arb_plan["symbol"]
    long_ex = arb_plan["long_exchange"]
    short_ex = arb_plan["short_exchange"]
    settle_epoch = arb_plan["settle_epoch"]

    # Updated: T - 30s Entry & T + 90s Hard Exit
    t_entry = settle_epoch - 30000
    t_settle = settle_epoch
    t_hard_exit = settle_epoch + 90000

    precision_wait_until(t_entry)
    entry_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]

    th1 = threading.Thread(target=fire_bybit_order, args=(bybit_client, symbol, "Buy" if long_ex == "Bybit" else "Sell", lots["bybit_qty"]), daemon=True)
    th2 = threading.Thread(target=fire_binance_order, args=(binance_client, symbol, "BUY" if long_ex == "Binance" else "SELL", lots["binance_qty"]), daemon=True)
    th1.start(); th2.start()
    th1.join(); th2.join()

    try:
        p1 = float(bybit_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]["lastPrice"])
        p2 = float(binance_client.ticker_price(symbol=symbol)["price"])
    except Exception:
        p1, p2 = arb_plan["bybit_price"], arb_plan["binance_price"]

    entry_spread = (p1 / p2) if long_ex == "Bybit" else (p2 / p1)
    add_ui_log(f"🚀 [Dual-Arb] DISPATCHED AT T-30s | Long: {long_ex} | Short: {short_ex} | Entry Spread: {entry_spread:.6f}")

    precision_wait_until(t_settle + 1000)
    add_ui_log(f"💰 [Dual-Arb] SETTLEMENT HIT. Funding captured on {long_ex}!")

    spread_reverted = False
    exit_reason = "Hard Exit (T+90s Timeout)"

    # Dynamic spread reversion check between T+1s and T+90s
    while True:
        if get_synced_time_ms() >= t_hard_exit:
            break
        try:
            c1 = float(bybit_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]["lastPrice"])
            c2 = float(binance_client.ticker_price(symbol=symbol)["price"])
            cur_sp = (c1 / c2) if long_ex == "Bybit" else (c2 / c1)

            # Exit immediately if gap reverts
            if cur_sp >= entry_spread:
                spread_reverted = True
                exit_reason = f"Spread Reverted Early ({cur_sp:.6f} >= {entry_spread:.6f})"
                add_ui_log(f"🎯 [Dual-Arb] GAP REVERSION MET BEFORE T+90s: Closing immediately!")
                break
        except Exception:
            pass
        time.sleep(0.15)

    exit_time_str = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
    th3 = threading.Thread(target=fire_bybit_order, args=(bybit_client, symbol, "Sell" if long_ex == "Bybit" else "Buy", lots["bybit_qty"]), daemon=True)
    th4 = threading.Thread(target=fire_binance_order, args=(binance_client, symbol, "SELL" if long_ex == "Binance" else "BUY", lots["binance_qty"]), daemon=True)
    th3.start(); th4.start()
    th3.join(); th4.join()

    # Emergency checks at T+105s & T+120s
    for stage_ms in [settle_epoch + 105000, settle_epoch + 120000]:
        precision_wait_until(stage_ms)
        try:
            bb_pos = bybit_client.get_positions(category="linear", symbol=symbol)["result"]["list"]
            if bb_pos and float(bb_pos[0].get("size", 0)) > 0:
                fire_bybit_order(bybit_client, symbol, "Sell" if long_ex == "Bybit" else "Buy", lots["bybit_qty"])
            bn_pos = binance_client.get_position_risk(symbol=symbol)
            for p in bn_pos:
                if abs(float(p.get("positionAmt", 0))) > 0:
                    fire_binance_order(binance_client, symbol, "SELL" if long_ex == "Binance" else "BUY", lots["binance_qty"])
                    break
        except Exception:
            pass

    add_ledger_entry({
        "exchange": "Bybit ⇋ Binance",
        "coin": symbol,
        "rate": f"Long: {long_ex} ({arb_plan['target_rate']*100:.2f}%)",
        "entry_time": entry_time_str,
        "exit_time": exit_time_str,
        "detail": f"{exit_reason} | Spread: {entry_spread:.5f} | ~$10 (10x)",
        "badge_text": "ARB CAPTURED" if spread_reverted else "HARD CLOSED",
        "badge_class": "status-success" if spread_reverted else "status-warn"
    })

def start_dual_loop(dashboard_data, data_lock, add_ui_log, add_ledger_entry):
    bybit_client = HTTP(testnet=False, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)
    binance_client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)
    add_ui_log("🚀 Dual Arbitrage Engine Active (Thresh <= -0.25% | T-30s Entry | T+90s Hard Exit)")

    while True:
        try:
            sync_exchange_clocks(bybit_client, binance_client, dashboard_data, data_lock)
            arb_opp = scan_cross_arb_opportunities(bybit_client, binance_client, add_ui_log)

            if not arb_opp:
                with data_lock:
                    dashboard_data['dual']['status'] = "SCANNING: No 1H vs 4H/8H opportunity"
                    dashboard_data['dual']['coin'] = "--"
                    dashboard_data['dual']['bybit_rate'] = "--"
                    dashboard_data['dual']['binance_rate'] = "--"
                    dashboard_data['dual']['spread'] = "--"
                    dashboard_data['dual']['settlement'] = "--"
                time.sleep(2)
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

            add_ui_log(f"🎯 [Dual-Arb] ARMED {symbol}: Long {arb_opp['long_exchange']} vs Short {arb_opp['short_exchange']} | Settle: {dashboard_data['dual']['settlement']}")

            # Pre-calculation at T - 35s (5s before T - 30s entry)
            t_precalc = settle_epoch - 35000
            interrupted = False

            while True:
                now_ms = get_synced_time_ms()
                if now_ms >= t_precalc:
                    break
                time.sleep(1.5)

                try:
                    bb_tick = bybit_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
                    bn_prem = binance_client.mark_price(symbol=symbol)
                    c_bb_r = float(bb_tick.get("fundingRate", arb_opp["bybit_rate"]))
                    c_bn_r = float(bn_prem.get("lastFundingRate", arb_opp["binance_rate"]))

                    arb_opp["bybit_rate"] = c_bb_r
                    arb_opp["binance_rate"] = c_bn_r
                    arb_opp["spread_abs"] = abs(c_bb_r - c_bn_r)

                    target_cur = c_bb_r if arb_opp["long_exchange"] == "Bybit" else c_bn_r
                    if target_cur > MIN_FUNDING_RATE_THRESHOLD:
                        add_ui_log(f"⚠️ [Dual-Arb] {symbol} rate weakened above -0.25%. Re-scanning!")
                        interrupted = True
                        break

                    with data_lock:
                        dashboard_data['dual']['bybit_rate'] = f"{c_bb_r*100:+.4f}%"
                        dashboard_data['dual']['binance_rate'] = f"{c_bn_r*100:+.4f}%"
                        dashboard_data['dual']['spread'] = f"{arb_opp['spread_abs']*100:.4f}%"
                except Exception:
                    pass

            if interrupted:
                continue

            precision_wait_until(t_precalc)
            sync_exchange_clocks(bybit_client, binance_client, dashboard_data, data_lock)

            try:
                c_bb_p = float(bybit_client.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]["lastPrice"])
                c_bn_p = float(binance_client.ticker_price(symbol=symbol)["price"])
            except Exception:
                c_bb_p, c_bn_p = arb_plan["bybit_price"], arb_plan["binance_price"]

            lots = calculate_exchange_lots(bybit_client, binance_client, symbol, c_bb_p, c_bn_p)
            add_ui_log(f"⚙️ [Dual-Arb] PRE-FLIGHT ({symbol}): Bybit Qty: {lots['bybit_qty']} | Binance Qty: {lots['binance_qty']}")

            execute_dual_arbitrage_cycle(bybit_client, binance_client, arb_opp, lots, add_ui_log, add_ledger_entry)
            time.sleep(5)

        except Exception as e:
            add_ui_log(f"⚠️ [Dual-Arb] Outer Loop Recovered: {e}")
            time.sleep(2)
