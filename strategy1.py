from __future__ import annotations
import gc
import hmac
import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Optional, Sequence, Tuple, List, Dict
import requests
import websocket

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


class BinanceFastLaneWS:
    def __init__(self, api_key: str, api_secret: str, rest_url: str, ws_url: str, ws_api_url: str, log_fn: Callable[[str], None], status_fn: Callable[[str], None]):
        self.api_key = api_key
        self.api_secret = api_secret
        self.rest_url = rest_url
        self.ws_url = ws_url
        self.ws_api_url = ws_api_url
        self.log_fn = log_fn
        self.status_fn = status_fn
        self.ws_lock = threading.Lock()
        self.order_send_lock = threading.Lock()

        self.listen_key: str = ""
        self.user_ws: Optional[websocket.WebSocketApp] = None
        self.order_ws: Optional[websocket.WebSocketApp] = None
        self.public_ws: Optional[websocket.WebSocketApp] = None

        self.user_ws_ready = False
        self.order_ws_ready = False
        self.ws_ready = False

        self.live_prices: dict[str, float] = {}
        self.last_traded_prices: dict[str, float] = {}
        self.current_subscribed_symbol: Optional[str] = None

        self.pending_order_events: dict[str, threading.Event] = {}
        self.pending_order_responses: dict[str, dict[str, Any]] = {}

        self.filled_quantities: dict[str, float] = {}
        self.buy_avg_prices: dict[str, float] = {}
        self.sell_fill_prices: dict[str, float] = {}
        self.last_fill_prices: dict[str, float] = {}
        self.last_fill_times: dict[str, str] = {}
        self.shallow_cancels_telemetry: dict[str, dict[str, Any]] = {}
        self.filled_orders_set: set[str] = set()

        self.ping_send_perf: float = 0.0
        self.last_measured_rtt_ms: float = 2.4

    def reset_cycle_state(self, symbol: str) -> None:
        with self.ws_lock:
            self.last_fill_prices.pop(symbol, None)
            self.buy_avg_prices.pop(symbol, None)
            self.sell_fill_prices.pop(symbol, None)
            self.last_fill_times.pop(symbol, None)
            self.filled_quantities.pop(symbol, None)
            self.last_traded_prices.pop(symbol, None)
            self.pending_order_events.clear()
            self.pending_order_responses.clear()
            self.shallow_cancels_telemetry.clear()
            self.filled_orders_set.clear()

    def acquire_listen_key(self) -> bool:
        if not self.api_key: return False
        url = f"{self.rest_url}/fapi/v1/listenKey"
        headers = {"X-MBX-APIKEY": self.api_key, "User-Agent": DEFAULT_UA, "Accept": "application/json"}
        try:
            res = requests.post(url, headers=headers, timeout=5)
            if res.status_code == 200:
                self.listen_key = res.json().get("listenKey", "")
                return True
        except Exception: pass
        return False

    def keep_alive_listen_key(self) -> None:
        url = f"{self.rest_url}/fapi/v1/listenKey"
        headers = {"X-MBX-APIKEY": self.api_key, "User-Agent": DEFAULT_UA, "Accept": "application/json"}
        while True:
            time.sleep(20 * 60)
            if self.listen_key:
                try: requests.put(url, headers=headers, timeout=5)
                except Exception: self.acquire_listen_key()

    def on_user_message(self, ws: Any, message: str) -> None:
        try:
            data = json.loads(message)
        except Exception: return

        if data.get("e") == "ORDER_TRADE_UPDATE":
            order_data = data.get("o", {})
            sym = order_data.get("s", "")
            side = order_data.get("S", "").lower()
            execution_type = order_data.get("x", "")
            client_order_id = order_data.get("c", "")
            last_filled_qty = float(order_data.get("l", 0.0))
            last_filled_price = float(order_data.get("L", 0.0))
            trade_ts_ms = int(order_data.get("T") or data.get("E") or int(time.time() * 1000))
            exact_trade_time = datetime.fromtimestamp(trade_ts_ms / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3]

            if client_order_id and client_order_id in self.shallow_cancels_telemetry:
                with self.ws_lock:
                    if execution_type == "CANCELED":
                        self.shallow_cancels_telemetry[client_order_id]["binance_canceled_ms"] = trade_ts_ms
                        self.shallow_cancels_telemetry[client_order_id]["binance_canceled_str"] = exact_trade_time
                    elif execution_type == "TRADE":
                        self.filled_orders_set.add(client_order_id)

            if execution_type == "TRADE" and last_filled_qty > 0:
                with self.ws_lock:
                    self.last_fill_times[sym] = exact_trade_time
                    if side == "buy":
                        old_qty = self.filled_quantities.get(sym, 0.0)
                        new_qty = old_qty + last_filled_qty
                        self.filled_quantities[sym] = new_qty
                        self.buy_avg_prices[sym] = last_filled_price
                    elif side == "sell":
                        self.sell_fill_prices[sym] = last_filled_price
                        self.filled_quantities[sym] = max(0.0, self.filled_quantities.get(sym, 0.0) - last_filled_qty)

    def on_order_message(self, ws: Any, message: str) -> None:
        try:
            data = json.loads(message)
            req_id = data.get("id", "")
            if req_id and req_id in self.pending_order_events:
                self.pending_order_responses[req_id] = data
                self.pending_order_events[req_id].set()
        except Exception: pass

    def place_orders_burst_ws(self, symbol: str, orders_batch: list[tuple[str, str, str, float, str]], timeout: float = 0.09) -> None:
        if not self.order_ws: return
        timestamp_ms = int(time.time() * 1000)
        prepared_frames = []
        for cid, q_str, p_str, _, _ in orders_batch:
            sign_dict = {
                "apiKey": self.api_key, "symbol": symbol, "side": "BUY", "type": "LIMIT",
                "quantity": q_str, "price": p_str, "timeInForce": "GTC", "newClientOrderId": cid, "timestamp": timestamp_ms
            }
            query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
            sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
            payload = {"id": cid, "method": "order.place", "params": {**sign_dict, "signature": sig}}
            prepared_frames.append(json.dumps(payload))

        with self.order_send_lock:
            for raw_frame in prepared_frames:
                try: self.order_ws.send(raw_frame)
                except Exception: pass

    def cancel_orders_batch_ws(self, symbol: str, client_order_ids: list[str], timeout: float = 0.08) -> None:
        if not self.order_ws: return
        timestamp_ms = int(time.time() * 1000)
        fired_time_str = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
        fired_ts_ms = int(time.time() * 1000)

        prepared_frames = []
        for cid in client_order_ids:
            with self.ws_lock:
                self.shallow_cancels_telemetry[cid] = {
                    "cancel_fired_str": fired_time_str,
                    "cancel_fired_ms": fired_ts_ms,
                    "binance_canceled_str": "--",
                    "binance_canceled_ms": 0
                }
            sign_dict = {"apiKey": self.api_key, "symbol": symbol, "origClientOrderId": cid, "timestamp": timestamp_ms}
            query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
            sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
            cancel_req_id = f"cancel-{cid}"
            payload = {"id": cancel_req_id, "method": "order.cancel", "params": {**sign_dict, "signature": sig}}
            prepared_frames.append(json.dumps(payload))

        with self.order_send_lock:
            for raw_frame in prepared_frames:
                try: self.order_ws.send(raw_frame)
                except Exception: pass

    def place_order_ws(self, symbol: str, side: str, order_type: str, quantity: str, client_order_id: str, timeout: float = 0.5, reduce_only: bool = False) -> dict:
        if not self.order_ws: return {"error": "WS disconnected"}
        timestamp_ms = int(time.time() * 1000)
        sign_dict = {
            "apiKey": self.api_key, "symbol": symbol, "side": side, "type": order_type,
            "quantity": quantity, "newClientOrderId": client_order_id, "timestamp": timestamp_ms
        }
        if reduce_only: sign_dict["reduceOnly"] = "true"
        query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
        sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
        payload = {"id": client_order_id, "method": "order.place", "params": {**sign_dict, "signature": sig}}
        event = threading.Event()
        self.pending_order_events[client_order_id] = event
        try:
            with self.order_send_lock: self.order_ws.send(json.dumps(payload))
        except Exception as e: return {"error": str(e)}
        confirmed = event.wait(timeout=timeout)
        res = self.pending_order_responses.pop(client_order_id, None)
        self.pending_order_events.pop(client_order_id, None)
        return res if (confirmed and res) else {"error": "Timeout"}

    def cancel_all_orders_ws(self, symbol: str, timeout: float = 0.3) -> dict:
        if not self.order_ws: return {"error": "WS disconnected"}
        timestamp_ms = int(time.time() * 1000)
        sign_dict = {"apiKey": self.api_key, "symbol": symbol, "timestamp": timestamp_ms}
        query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
        sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
        req_id = f"cancel-all-{symbol[:4]}-{timestamp_ms % 100000}"
        payload = {"id": req_id, "method": "order.cancelAll", "params": {**sign_dict, "signature": sig}}
        try:
            with self.order_send_lock: self.order_ws.send(json.dumps(payload))
            return {"status": 200}
        except Exception as e: return {"error": str(e)}

    def on_public_message(self, ws: Any, message: str) -> None:
        try:
            data = json.loads(message)
            payload = data.get("data", data)
            sym = payload.get("s", "")
            if payload.get("e") == "aggTrade":
                price = float(payload.get("p", 0.0))
                if sym and price > 0:
                    with self.ws_lock:
                        self.live_prices[sym] = price
                        self.last_traded_prices[sym] = price
        except Exception: pass

    def subscribe_ticker(self, symbol: str) -> None:
        if symbol == self.current_subscribed_symbol: return
        if self.public_ws:
            try: self.public_ws.close()
            except Exception: pass
        with self.ws_lock:
            self.live_prices.clear()
            self.last_traded_prices.clear()
            self.current_subscribed_symbol = symbol

        def runner(target_sym: str):
            stream_url = f"{self.ws_url}/stream?streams={target_sym.lower()}@aggTrade"
            while self.current_subscribed_symbol == target_sym:
                try:
                    self.public_ws = websocket.WebSocketApp(stream_url, on_message=self.on_public_message)
                    self.public_ws.run_forever(ping_interval=10, ping_timeout=5)
                except Exception: pass
                time.sleep(1)
        threading.Thread(target=runner, args=(symbol,), daemon=True).start()

    def get_live_price(self, symbol: str) -> float:
        with self.ws_lock: return self.live_prices.get(symbol) or self.last_traded_prices.get(symbol, 0.0)
    def get_last_traded_price(self, symbol: str) -> float:
        with self.ws_lock: return self.last_traded_prices.get(symbol, 0.0)
    def get_filled_qty(self, symbol: str) -> float:
        with self.ws_lock: return self.filled_quantities.get(symbol, 0.0)
    def get_avg_fill_price(self, symbol: str) -> Optional[float]:
        with self.ws_lock: return self.buy_avg_prices.get(symbol) or self.last_fill_prices.get(symbol)
    def get_sell_fill_price(self, symbol: str) -> Optional[float]:
        with self.ws_lock: return self.sell_fill_prices.get(symbol)

    def connect(self) -> None:
        while not self.acquire_listen_key(): time.sleep(5)
        threading.Thread(target=self.keep_alive_listen_key, daemon=True).start()

        def user_ws_runner():
            while True:
                try:
                    self.user_ws = websocket.WebSocketApp(
                        f"{self.ws_url}/ws/{self.listen_key}",
                        on_message=self.on_user_message
                    )
                    self.user_ws.run_forever(ping_interval=10, ping_timeout=5)
                except Exception: pass
                time.sleep(2)

        def order_ws_runner():
            while True:
                try:
                    self.order_ws = websocket.WebSocketApp(
                        self.ws_api_url,
                        on_message=self.on_order_message
                    )
                    self.order_ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception: pass
                time.sleep(0.2)

        threading.Thread(target=user_ws_runner, daemon=True).start()
        threading.Thread(target=order_ws_runner, daemon=True).start()


def execute_market_exit_3layer(ws_pipeline, symbol: str, qty_str: str, rest_url: str, api_key: str, api_secret: str, get_synced_time_ms_fn, sign_params_fn) -> tuple[bool, str]:
    now_exit_dt = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
    req_id_1 = f"exit-ws1-{uuid.uuid4().hex[:6]}"
    res1 = ws_pipeline.place_order_ws(symbol=symbol, side="SELL", order_type="MARKET", quantity=qty_str, client_order_id=req_id_1, timeout=0.5, reduce_only=True)
    if res1.get("status") == 200 or "orderId" in res1.get("result", {}):
        return True, now_exit_dt

    try:
        p = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty_str, "reduceOnly": "true", "timestamp": int(get_synced_time_ms_fn())}
        requests.post(f"{rest_url}/fapi/v1/order?{sign_params_fn(p, api_secret)}", headers={"X-MBX-APIKEY": api_key}, timeout=2.5)
        return True, now_exit_dt
    except Exception: pass
    return False, now_exit_dt


def run_strategy1_cycle(ws_pipeline, client, target_symbol, rate, last_price, settle_epoch, config, state_callbacks):
    add_ui_log = state_callbacks["add_ui_log"]
    get_synced_time_ms = state_callbacks["get_synced_time_ms"]
    precision_wait_until = state_callbacks["precision_wait_until"]
    format_step = state_callbacks["format_step"]
    format_tick = state_callbacks["format_tick"]
    sign_params = state_callbacks["sign_params"]

    t_limit_burst = settle_epoch - config["ENTRY_BEFORE_SETTLE_MS"]
    t_cancel_shallow = settle_epoch + config["SHALLOW_CANCEL_AFTER_SETTLE_MS"]
    t_cancel_all = settle_epoch + config["CANCEL_ORDERS_AFTER_SETTLE_MS"]  # T+6000ms (T+6 seconds)
    t_hard_exit = settle_epoch + config["EXIT_AFTER_SETTLE_MS"]           # T+7000ms (T+7 seconds)

    abs_r = abs(float(rate))
    shallow_notional_usd = max(5.5, config["LIMIT_ORDER_MARGIN_USD"] * config["MAX_LEVERAGE_CAP"])

    epoch_tag = f"{int(settle_epoch / 1000) % 100000}"
    cid_shlw1 = f"shlw1-{target_symbol[:4]}-{epoch_tag}"
    cid_shlw2 = f"shlw2-{target_symbol[:4]}-{epoch_tag}"
    cid_shlw3 = f"shlw3-{target_symbol[:4]}-{epoch_tag}"
    shallow_cids = [cid_shlw1, cid_shlw2, cid_shlw3]

    try:
        precision_wait_until(t_limit_burst)
        entry_dispatch_time = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]

        live_ws_p = ws_pipeline.get_live_price(target_symbol)
        anchor_price = live_ws_p if (live_ws_p and live_ws_p > 0) else last_price

        dec_anchor = Decimal(str(anchor_price))
        abs_rate_dec = Decimal(str(abs_r))

        min_qty, qty_step, tick_size = 1.0, 1.0, 0.001
        try:
            ex_info = client.exchange_info()
            s_info = next((s for s in ex_info.get('symbols', []) if s['symbol'] == target_symbol), None)
            if s_info:
                for f in s_info.get('filters', []):
                    if f.get('filterType') == 'LOT_SIZE':
                        min_qty = float(f.get('minQty', 1.0))
                        qty_step = float(f.get('stepSize', 1.0))
                    elif f.get('filterType') == 'PRICE_FILTER':
                        tick_size = float(f.get('tickSize', 0.001))
        except Exception: pass

        limit_orders_batch = []
        shallow_order_info_list = []
        shallow_specs = [
            (cid_shlw1, config["SHALLOW_LIMIT_RATIOS"][0], "Shallow Limit #1 (30% FR)"),
            (cid_shlw2, config["SHALLOW_LIMIT_RATIOS"][1], "Shallow Limit #2 (50% FR)"),
            (cid_shlw3, config["SHALLOW_LIMIT_RATIOS"][2], "Shallow Limit #3 (70% FR)"),
        ]

        for cid_s, s_ratio, s_label in shallow_specs:
            drop_dec = abs_rate_dec * Decimal(str(s_ratio))
            p_dec = dec_anchor * (Decimal("1") - drop_dec)
            p_str = format_tick(float(p_dec), tick_size)
            q_val = max(min_qty, shallow_notional_usd / float(p_str))
            q_str = format_step(q_val, qty_step)
            limit_orders_batch.append((cid_s, q_str, p_str, float(drop_dec), s_label))
            shallow_order_info_list.append({"cid": cid_s, "label": s_label, "price": p_str, "qty": q_str})

        # Send Limit Burst
        ws_pipeline.place_orders_burst_ws(target_symbol, limit_orders_batch, timeout=0.09)

        # Shallow Cancel at T+230ms
        precision_wait_until(t_cancel_shallow)
        ws_pipeline.cancel_orders_batch_ws(target_symbol, shallow_cids, timeout=0.08)

        # Cancel ALL remaining open orders at T+6 seconds
        precision_wait_until(t_cancel_all)
        ws_pipeline.cancel_all_orders_ws(target_symbol)

        # Strict Hard Exit at T+7 seconds
        precision_wait_until(t_hard_exit)
        time.sleep(0.02)
        ws_pos = ws_pipeline.get_filled_qty(target_symbol)
        exit_price_detected = ws_pipeline.get_live_price(target_symbol) or last_price

        if ws_pos > 0:
            exit_qty = format_step(ws_pos, qty_step)
            exit_status = "SETTLEMENT HARVEST EXECUTED"
            execute_market_exit_3layer(ws_pipeline, target_symbol, exit_qty, config["BINANCE_REST_URL"], config["API_KEY"], config["API_SECRET"], get_synced_time_ms, sign_params)
        else:
            exit_status = "CLEAN PASS (NO FILL)"

        # Prepare 3 Shallow Box Display Telemetry for Dashboard UI
        shallow_boxes_ui = []
        for info in shallow_order_info_list:
            cid = info["cid"]
            telemetry = ws_pipeline.shallow_cancels_telemetry.get(cid, {})
            is_filled = cid in ws_pipeline.filled_orders_set

            fired_ms = telemetry.get("cancel_fired_ms", 0)
            canceled_ms = telemetry.get("binance_canceled_ms", 0)
            lat_ms = max(1, canceled_ms - fired_ms) if (canceled_ms > 0 and fired_ms > 0) else 1

            shallow_boxes_ui.append({
                "label": info["label"],
                "price": info["price"],
                "qty": info["qty"],
                "status": "FILLED" if is_filled else "CANCELED",
                "cancel_fired": telemetry.get("cancel_fired_str", "--"),
                "binance_canceled": telemetry.get("binance_canceled_str", "--"),
                "latency_ms": lat_ms
            })

        final_avg_entry = ws_pipeline.get_avg_fill_price(target_symbol) or 0.0
        final_exit_price = ws_pipeline.get_sell_fill_price(target_symbol) or exit_price_detected

        return {
            "symbol": target_symbol,
            "funding_rate": f"{rate * 100:+.4f}%",
            "tier": "STRATEGY 1 (TOKYO SNIPER)",
            "entry_sent": entry_dispatch_time,
            "entry_ack": entry_dispatch_time,
            "entry_fill_time": ws_pipeline.last_fill_times.get(target_symbol, "No Fill"),
            "entry_fill_price": str(final_avg_entry) if final_avg_entry > 0 else "0.0",
            "exit_price": str(final_exit_price),
            "status": exit_status,
            "pnl": "$0.0000",
            "shallow_orders": shallow_boxes_ui
        }

    except Exception as e:
        add_ui_log(f"🚨 [Strategy 1 Error]: {e}")
        return None
    finally:
        gc.enable()
        ws_pipeline.reset_cycle_state(target_symbol)
