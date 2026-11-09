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


def resolve_limit_level_meta(client_order_id: str, fallback_id: Any = "") -> tuple[str, str, int]:
    cid = (client_order_id or "").lower()
    if "shlw1" in cid:
        return "Shallow Limit #1 (30% FR)", "shallow", 1
    if "shlw2" in cid:
        return "Shallow Limit #2 (50% FR)", "shallow", 2
    if "shlw3" in cid:
        return "Shallow Limit #3 (70% FR)", "shallow", 3
    if "dca-dip1" in cid:
        return "DCA Limit #1", "dca", 4
    if "dca-dip2" in cid:
        return "DCA Limit #2", "dca", 5
    lbl = f"Limit Order #{fallback_id}" if fallback_id else "Limit Order Fill"
    return lbl, "shallow", 99


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
        self.public_ws_ready = False
        self.ws_ready = False

        self.order_registry: dict[str, dict[str, Any]] = {}
        self.live_prices: dict[str, float] = {}
        self.last_traded_prices: dict[str, float] = {}
        self.current_subscribed_symbol: Optional[str] = None

        self.pending_order_events: dict[str, threading.Event] = {}
        self.pending_order_responses: dict[str, dict[str, Any]] = {}

        self.long_filled_symbols: set[str] = set()
        self.position_closed_symbols: set[str] = set()
        self.last_fill_prices: dict[str, float] = {}
        self.buy_avg_prices: dict[str, float] = {}
        self.sell_fill_prices: dict[str, float] = {}
        self.last_fill_times: dict[str, str] = {}
        self.last_fill_types: dict[str, str] = {}
        self.filled_quantities: dict[str, float] = {}
        self.total_buy_cost: dict[str, float] = {}
        self.rejected_orders: set[str] = set()
        self.dca_hits: dict[str, list[dict[str, Any]]] = {}

        self.active_limit_orders: set[str] = set()
        self.shallow_cancels_telemetry: dict[str, dict[str, Any]] = {}

        self.ping_send_perf: float = 0.0
        self.last_measured_rtt_ms: float = 2.4

    def reset_cycle_state(self, symbol: str) -> None:
        with self.ws_lock:
            self.last_fill_prices.pop(symbol, None)
            self.buy_avg_prices.pop(symbol, None)
            self.sell_fill_prices.pop(symbol, None)
            self.last_fill_times.pop(symbol, None)
            self.last_fill_types.pop(symbol, None)
            self.filled_quantities.pop(symbol, None)
            self.total_buy_cost.pop(symbol, None)
            self.last_traded_prices.pop(symbol, None)
            self.dca_hits.pop(symbol, None)
            self.long_filled_symbols.discard(symbol)
            self.position_closed_symbols.discard(symbol)
            self.rejected_orders.clear()
            self.pending_order_events.clear()
            self.pending_order_responses.clear()
            self.active_limit_orders.clear()
            self.shallow_cancels_telemetry.clear()
            self.ping_send_perf = 0.0
            self.order_registry.clear()

    def acquire_listen_key(self) -> bool:
        if not self.api_key:
            return False
        url = f"{self.rest_url}/fapi/v1/listenKey"
        headers = {"X-MBX-APIKEY": self.api_key, "User-Agent": DEFAULT_UA, "Accept": "application/json"}
        try:
            res = requests.post(url, headers=headers, timeout=5)
            if res.status_code == 200:
                self.listen_key = res.json().get("listenKey", "")
                return True
        except Exception:
            pass
        return False

    def keep_alive_listen_key(self) -> None:
        url = f"{self.rest_url}/fapi/v1/listenKey"
        headers = {"X-MBX-APIKEY": self.api_key, "User-Agent": DEFAULT_UA, "Accept": "application/json"}
        while True:
            time.sleep(20 * 60)
            if self.listen_key:
                try:
                    res = requests.put(url, headers=headers, timeout=5)
                    if res.status_code != 200:
                        self.acquire_listen_key()
                except Exception:
                    pass

    def on_user_message(self, ws: Any, message: str) -> None:
        try:
            data = json.loads(message)
        except Exception:
            return

        event_type = data.get("e", "")
        if event_type == "ORDER_TRADE_UPDATE":
            order_data = data.get("o", {})
            sym = order_data.get("s", "")
            side = order_data.get("S", "").lower()
            order_type = order_data.get("o", "").upper()
            execution_type = order_data.get("x", "")
            order_status = order_data.get("X", "")
            client_order_id = order_data.get("c", "")
            order_id = order_data.get("i", "")
            last_filled_qty = float(order_data.get("l", 0.0))
            last_filled_price = float(order_data.get("L", 0.0))

            trade_ts_ms = int(order_data.get("T") or data.get("E") or int(time.time() * 1000))
            exact_trade_time = datetime.fromtimestamp(trade_ts_ms / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3]

            is_active_state = execution_type in ("NEW", "TRADE") or order_status in ("NEW", "PARTIALLY_FILLED", "FILLED")
            is_closed_state = execution_type in ("CANCELED", "REJECTED", "EXPIRED") or order_status in ("CANCELED", "REJECTED", "EXPIRED")

            if client_order_id:
                with self.ws_lock:
                    if is_active_state:
                        self.active_limit_orders.add(client_order_id)
                    elif is_closed_state:
                        self.active_limit_orders.discard(client_order_id)
                    if execution_type == "CANCELED" and client_order_id in self.shallow_cancels_telemetry:
                        self.shallow_cancels_telemetry[client_order_id]["binance_engine_ms"] = trade_ts_ms

            if execution_type == "TRADE" and last_filled_qty > 0:
                with self.ws_lock:
                    if sym not in self.last_fill_times or side != "buy":
                        self.last_fill_times[sym] = exact_trade_time
                    self.last_fill_types[sym] = order_type
                    self.log_fn(f"⚡ [STRATEGY 1 EXECUTION] [{exact_trade_time}] | [{order_type} {side.upper()}] on {sym} | Qty: {last_filled_qty} @ {last_filled_price}")

                    if side == "buy":
                        self.long_filled_symbols.add(sym)
                        self.position_closed_symbols.discard(sym)
                        old_qty = self.filled_quantities.get(sym, 0.0)
                        new_qty = old_qty + last_filled_qty
                        self.filled_quantities[sym] = new_qty
                        self.total_buy_cost[sym] = self.total_buy_cost.get(sym, 0.0) + (last_filled_price * last_filled_qty)
                        avg_p = self.total_buy_cost[sym] / new_qty if new_qty > 0 else last_filled_price
                        self.buy_avg_prices[sym] = avg_p
                        self.last_fill_prices[sym] = last_filled_price

                        lvl_name, grp_type, s_idx = resolve_limit_level_meta(client_order_id, order_id)
                        if sym not in self.dca_hits:
                            self.dca_hits[sym] = []

                        existing_hit = next((h for h in self.dca_hits[sym] if h.get("level") == lvl_name), None)
                        if existing_hit is None:
                            clean_q = int(last_filled_qty) if float(last_filled_qty).is_integer() else round(last_filled_qty, 6)
                            hit_record = {
                                "level": lvl_name, "group": grp_type, "sort_idx": s_idx,
                                "price": round(last_filled_price, 8), "qty": clean_q,
                                "_raw_qty": last_filled_qty, "_raw_cost": last_filled_price * last_filled_qty,
                                "first_ms": trade_ts_ms, "last_ms": trade_ts_ms,
                                "time": exact_trade_time, "first_time": exact_trade_time, "last_time": exact_trade_time,
                                "span_ms": 0, "fills_count": 1
                            }
                            self.dca_hits[sym].append(hit_record)
                            self.dca_hits[sym].sort(key=lambda x: (x.get("sort_idx", 99), x.get("first_ms", 0)))
                        else:
                            raw_q = existing_hit.get("_raw_qty", float(existing_hit.get("qty", 0.0))) + last_filled_qty
                            raw_c = existing_hit.get("_raw_cost", float(existing_hit.get("price", 0.0)) * float(existing_hit.get("qty", 0.0))) + (last_filled_price * last_filled_qty)
                            existing_hit["_raw_qty"] = raw_q
                            existing_hit["_raw_cost"] = raw_c
                            existing_hit["price"] = round(raw_c / raw_q, 8) if raw_q > 0 else last_filled_price
                            existing_hit["qty"] = int(raw_q) if float(raw_q).is_integer() else round(raw_q, 6)
                            if trade_ts_ms < existing_hit["first_ms"]:
                                existing_hit["first_ms"] = trade_ts_ms
                                existing_hit["first_time"] = exact_trade_time
                                existing_hit["time"] = exact_trade_time
                            if trade_ts_ms > existing_hit["last_ms"]:
                                existing_hit["last_ms"] = trade_ts_ms
                                existing_hit["last_time"] = exact_trade_time
                            existing_hit["span_ms"] = max(0, existing_hit["last_ms"] - existing_hit["first_ms"])
                            existing_hit["fills_count"] = existing_hit.get("fills_count", 1) + 1
                            self.dca_hits[sym].sort(key=lambda x: (x.get("sort_idx", 99), x.get("first_ms", 0)))
                    elif side == "sell":
                        self.sell_fill_prices[sym] = last_filled_price
                        current_q = self.filled_quantities.get(sym, 0.0)
                        remaining_q = max(0.0, current_q - last_filled_qty)
                        if remaining_q < 1e-6:
                            remaining_q = 0.0
                            self.position_closed_symbols.add(sym)
                        self.filled_quantities[sym] = remaining_q

    def on_user_open(self, ws: Any) -> None:
        self.user_ws_ready = True
        self.check_overall_ready()

    def on_user_close(self, ws: Any, close_code: Any, close_msg: Any) -> None:
        self.user_ws_ready = False
        self.ws_ready = False

    def on_user_error(self, ws: Any, error: Any) -> None:
        self.user_ws_ready = False
        self.ws_ready = False

    def on_order_open(self, ws: Any) -> None:
        self.order_ws_ready = True
        self.check_overall_ready()

    def on_order_message(self, ws: Any, message: str) -> None:
        try:
            data = json.loads(message)
            req_id = data.get("id", "")
            status_code = data.get("status")
            res_obj = data.get("result", {})

            if req_id == "warmup-ping":
                if self.ping_send_perf > 0:
                    measured_rtt = (time.perf_counter() - self.ping_send_perf) * 1000.0
                    with self.ws_lock:
                        self.last_measured_rtt_ms = measured_rtt
                return

            if req_id and (status_code == 200 or res_obj.get("status") in ("NEW", "FILLED", "PARTIALLY_FILLED")):
                with self.ws_lock:
                    self.active_limit_orders.add(req_id)

            if req_id and req_id in self.pending_order_events:
                self.pending_order_responses[req_id] = data
                self.pending_order_events[req_id].set()
        except Exception:
            pass

    def on_order_close(self, ws: Any, close_code: Any, close_msg: Any) -> None:
        self.order_ws_ready = False

    def on_order_error(self, ws: Any, error: Any) -> None:
        self.order_ws_ready = False

    def check_overall_ready(self) -> None:
        if self.user_ws_ready and self.order_ws_ready:
            self.ws_ready = True
            self.status_fn("STRATEGY 1 TOKYO WS (READY)")

    def reconnect_order_ws(self) -> None:
        with self.ws_lock:
            self.order_ws_ready = False
        if self.order_ws:
            try:
                self.order_ws.close()
            except Exception:
                pass

    def ping_order_ws(self) -> bool:
        if self.order_ws and self.order_ws_ready:
            try:
                self.ping_send_perf = time.perf_counter()
                with self.order_send_lock:
                    self.order_ws.send(json.dumps({"id": "warmup-ping", "method": "ping"}))
                return True
            except Exception:
                self.ping_send_perf = 0.0
                return False
        return False

    def get_measured_lead_latency_ms(self, min_lead: float = 0.8, max_lead: float = 2.0, default_lead: float = 1.2) -> float:
        with self.ws_lock:
            if self.last_measured_rtt_ms > 0:
                raw_lead = self.last_measured_rtt_ms / 2.0
                return max(min_lead, min(max_lead, raw_lead))
            return default_lead

    def place_order_ws(self, symbol: str, side: str, order_type: str, quantity: str, client_order_id: str,
                       price: str = "", time_in_force: str = "", timeout: float = 0.5, reduce_only: bool = False) -> dict:
        if not self.order_ws or not self.order_ws_ready:
            return {"error": "Order WebSocket not connected"}

        timestamp_ms = int(time.time() * 1000)
        sign_dict = {
            "apiKey": self.api_key, "symbol": symbol, "side": side, "type": order_type,
            "quantity": quantity, "newClientOrderId": client_order_id, "timestamp": timestamp_ms
        }
        if price: sign_dict["price"] = price
        if time_in_force: sign_dict["timeInForce"] = time_in_force
        if reduce_only: sign_dict["reduceOnly"] = "true"

        query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
        sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()

        payload = {"id": client_order_id, "method": "order.place", "params": {**sign_dict, "signature": sig}}
        event = threading.Event()
        self.pending_order_events[client_order_id] = event

        try:
            with self.order_send_lock:
                self.order_ws.send(json.dumps(payload))
        except Exception as e:
            self.pending_order_events.pop(client_order_id, None)
            return {"error": f"WS send error: {e}"}

        confirmed = event.wait(timeout=timeout)
        res = self.pending_order_responses.pop(client_order_id, None)
        self.pending_order_events.pop(client_order_id, None)

        if not confirmed or res is None:
            return {"error": "Timeout waiting for WebSocket order confirmation"}
        return res

    def place_orders_burst_ws(self, symbol: str, orders_batch: list[tuple[str, str, str, float, str]], timeout: float = 0.09) -> list[tuple[str, str, str, float, str, dict]]:
        if not self.order_ws or not self.order_ws_ready:
            return [(cid, q, p, r, lbl, {"error": "Order WebSocket not connected"}) for cid, q, p, r, lbl in orders_batch]

        timestamp_ms = int(time.time() * 1000)
        prepared_frames: list[tuple[str, str]] = []

        for cid, q_str, p_str, _, _ in orders_batch:
            sign_dict = {
                "apiKey": self.api_key, "symbol": symbol, "side": "BUY", "type": "LIMIT",
                "quantity": q_str, "price": p_str, "timeInForce": "GTC", "newClientOrderId": cid, "timestamp": timestamp_ms
            }
            query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
            sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
            payload = {"id": cid, "method": "order.place", "params": {**sign_dict, "signature": sig}}
            event = threading.Event()
            self.pending_order_events[cid] = event
            prepared_frames.append((cid, json.dumps(payload)))

        send_errors: dict[str, str] = {}
        with self.order_send_lock:
            for cid, raw_frame in prepared_frames:
                try:
                    self.order_ws.send(raw_frame)
                except Exception as e:
                    send_errors[cid] = f"WS send error: {e}"
                    self.pending_order_events.pop(cid, None)

        deadline = time.time() + timeout
        results: list[tuple[str, str, str, float, str, dict]] = []
        for cid, q_str, p_str, r_val, lbl in orders_batch:
            if cid in send_errors:
                results.append((cid, q_str, p_str, r_val, lbl, {"error": send_errors[cid]}))
                continue
            ev = self.pending_order_events.get(cid)
            confirmed = False
            if ev is not None:
                rem = max(0.001, deadline - time.time())
                confirmed = ev.wait(timeout=rem)
            res = self.pending_order_responses.pop(cid, None)
            self.pending_order_events.pop(cid, None)
            if not confirmed or res is None:
                res = {"error": "Timeout waiting for WebSocket order confirmation"}
            results.append((cid, q_str, p_str, r_val, lbl, res))
        return results

    def cancel_order_ws(self, symbol: str, client_order_id: str, timeout: float = 0.25) -> dict:
        if not self.order_ws or not self.order_ws_ready:
            return {"error": "Order WebSocket not connected"}
        timestamp_ms = int(time.time() * 1000)
        sign_dict = {"apiKey": self.api_key, "symbol": symbol, "origClientOrderId": client_order_id, "timestamp": timestamp_ms}
        query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
        sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
        cancel_req_id = f"cancel-{client_order_id}"
        payload = {"id": cancel_req_id, "method": "order.cancel", "params": {**sign_dict, "signature": sig}}
        event = threading.Event()
        self.pending_order_events[cancel_req_id] = event
        try:
            with self.order_send_lock:
                self.order_ws.send(json.dumps(payload))
        except Exception as e:
            self.pending_order_events.pop(cancel_req_id, None)
            return {"error": f"WS cancel send error: {e}"}
        confirmed = event.wait(timeout=timeout)
        res = self.pending_order_responses.pop(cancel_req_id, None)
        self.pending_order_events.pop(cancel_req_id, None)
        if not confirmed or res is None:
            return {"error": "Timeout waiting for WS order.cancel"}
        return res

    def cancel_orders_batch_ws(self, symbol: str, client_order_ids: list[str], timeout: float = 0.08) -> list[tuple[str, dict]]:
        valid_cids = [c for c in client_order_ids if c]
        if not self.order_ws or not self.order_ws_ready:
            return [(cid, {"error": "Order WebSocket not connected"}) for cid in valid_cids]
        timestamp_ms = int(time.time() * 1000)
        prepared_frames: list[tuple[str, str, str]] = []
        for cid in valid_cids:
            sign_dict = {"apiKey": self.api_key, "symbol": symbol, "origClientOrderId": cid, "timestamp": timestamp_ms}
            query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
            sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
            cancel_req_id = f"cancel-{cid}"
            payload = {"id": cancel_req_id, "method": "order.cancel", "params": {**sign_dict, "signature": sig}}
            event = threading.Event()
            self.pending_order_events[cancel_req_id] = event
            prepared_frames.append((cid, cancel_req_id, json.dumps(payload)))

        fired_local_exact_ts_ms = int(time.time() * 1000)
        with self.order_send_lock:
            for cid, cancel_req_id, raw_frame in prepared_frames:
                try:
                    self.order_ws.send(raw_frame)
                except Exception as e:
                    self.pending_order_events.pop(cancel_req_id, None)

        deadline = time.time() + timeout
        results: list[tuple[str, dict]] = []
        for cid, cancel_req_id, _ in prepared_frames:
            ev = self.pending_order_events.get(cancel_req_id)
            confirmed = False
            if ev is not None:
                rem = max(0.001, deadline - time.time())
                confirmed = ev.wait(timeout=rem)
            res = self.pending_order_responses.pop(cancel_req_id, None)
            self.pending_order_events.pop(cancel_req_id, None)
            binance_engine_update_ts = None
            if isinstance(res, dict) and "result" in res and isinstance(res["result"], dict):
                binance_engine_update_ts = res["result"].get("updateTime")
            with self.ws_lock:
                self.shallow_cancels_telemetry[cid] = {
                    "fired_local_ms": fired_local_exact_ts_ms,
                    "binance_engine_ms": int(binance_engine_update_ts) if binance_engine_update_ts else (fired_local_exact_ts_ms + 2),
                    "response": res
                }
            results.append((cid, res))
        return results

    def get_shallow_cancel_telemetry(self, cid: str) -> dict[str, Any]:
        with self.ws_lock:
            return dict(self.shallow_cancels_telemetry.get(cid, {}))

    def cancel_all_orders_ws(self, symbol: str, timeout: float = 0.3) -> dict:
        if not self.order_ws or not self.order_ws_ready:
            return {"error": "Order WebSocket not connected"}
        timestamp_ms = int(time.time() * 1000)
        sign_dict = {"apiKey": self.api_key, "symbol": symbol, "timestamp": timestamp_ms}
        query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
        sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
        req_id = f"cancel-all-{symbol[:4]}-{int(time.time() * 1000) % 100000}"
        payload = {"id": req_id, "method": "order.cancelAll", "params": {**sign_dict, "signature": sig}}
        event = threading.Event()
        self.pending_order_events[req_id] = event
        try:
            with self.order_send_lock:
                self.order_ws.send(json.dumps(payload))
        except Exception as e:
            self.pending_order_events.pop(req_id, None)
            return {"error": f"WS cancelAll send error: {e}"}
        confirmed = event.wait(timeout=timeout)
        res = self.pending_order_responses.pop(req_id, None)
        self.pending_order_events.pop(req_id, None)
        if not confirmed or res is None:
            return {"error": "Timeout waiting for WS order.cancelAll"}
        return res

    def on_public_message(self, ws: Any, message: str) -> None:
        try:
            data = json.loads(message)
            stream = data.get("stream", "")
            payload = data.get("data", data)
            sym = payload.get("s", "")
            if "aggTrade" in stream or payload.get("e") == "aggTrade":
                price = float(payload.get("p", 0.0))
                if sym and price > 0:
                    with self.ws_lock:
                        self.live_prices[sym] = price
                        self.last_traded_prices[sym] = price
            elif "bookTicker" in stream or ("b" in payload and "a" in payload):
                best_bid = float(payload.get("b", 0.0))
                if sym and best_bid > 0:
                    with self.ws_lock:
                        self.live_prices[sym] = best_bid
        except Exception:
            pass

    def subscribe_ticker(self, symbol: str) -> None:
        if symbol == self.current_subscribed_symbol:
            return
        if self.public_ws:
            try: self.public_ws.close()
            except Exception: pass
        with self.ws_lock:
            self.live_prices.clear()
            self.last_traded_prices.clear()
            self.current_subscribed_symbol = symbol

        def runner(target_sym: str):
            stream_url = f"{self.ws_url}/stream?streams={target_sym.lower()}@aggTrade/{target_sym.lower()}@bookTicker"
            while self.current_subscribed_symbol == target_sym:
                try:
                    self.public_ws = websocket.WebSocketApp(stream_url, on_message=self.on_public_message)
                    self.public_ws.run_forever(ping_interval=10, ping_timeout=5)
                except Exception:
                    pass
                time.sleep(1)
        threading.Thread(target=runner, args=(symbol,), daemon=True).start()

    def get_live_price(self, symbol: str) -> float:
        with self.ws_lock:
            return self.live_prices.get(symbol) or self.last_traded_prices.get(symbol, 0.0)

    def get_last_traded_price(self, symbol: str) -> float:
        with self.ws_lock:
            return self.last_traded_prices.get(symbol, 0.0)

    def is_long_filled(self, symbol: str) -> bool:
        with self.ws_lock: return symbol in self.long_filled_symbols
    def is_position_closed(self, symbol: str) -> bool:
        with self.ws_lock: return symbol in self.position_closed_symbols
    def get_filled_qty(self, symbol: str) -> float:
        with self.ws_lock: return self.filled_quantities.get(symbol, 0.0)
    def get_avg_fill_price(self, symbol: str) -> Optional[float]:
        with self.ws_lock: return self.buy_avg_prices.get(symbol) or self.last_fill_prices.get(symbol)
    def get_sell_fill_price(self, symbol: str) -> Optional[float]:
        with self.ws_lock: return self.sell_fill_prices.get(symbol)
    def get_dca_hits(self, symbol: str) -> list[dict[str, Any]]:
        with self.ws_lock:
            return [{k: v for k, v in h.items() if not k.startswith("_")} for h in self.dca_hits.get(symbol, [])]
    def get_last_fill_time(self, symbol: str) -> str:
        with self.ws_lock: return self.last_fill_times.get(symbol, "")

    def connect(self) -> None:
        while not self.acquire_listen_key():
            time.sleep(5)
        threading.Thread(target=self.keep_alive_listen_key, daemon=True).start()

        def user_ws_runner():
            while True:
                try:
                    self.user_ws = websocket.WebSocketApp(
                        f"{self.ws_url}/ws/{self.listen_key}",
                        on_open=self.on_user_open, on_message=self.on_user_message,
                        on_close=self.on_user_close, on_error=self.on_user_error
                    )
                    self.user_ws.run_forever(ping_interval=10, ping_timeout=5)
                except Exception:
                    pass
                time.sleep(2)

        def order_ws_runner():
            while True:
                try:
                    self.order_ws = websocket.WebSocketApp(
                        self.ws_api_url,
                        on_open=self.on_order_open, on_message=self.on_order_message,
                        on_close=self.on_order_close, on_error=self.on_order_error
                    )
                    self.order_ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception:
                    pass
                time.sleep(0.2)

        threading.Thread(target=user_ws_runner, daemon=True).start()
        threading.Thread(target=order_ws_runner, daemon=True).start()


def fast_cancel_shallow_orders(ws_pipeline, symbol: str, shallow_cids: Sequence[str], log_fn) -> None:
    now_cancel_dt = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
    log_fn(f"⚡ [SHALLOW CANCEL BURST at {now_cancel_dt}] Firing simultaneous WS cancel for 3 shallow orders on {symbol}...")
    results = ws_pipeline.cancel_orders_batch_ws(symbol, list(shallow_cids), timeout=0.08)
    for cid, res in results:
        telem = ws_pipeline.get_shallow_cancel_telemetry(cid)
        f_ms = telem.get("fired_local_ms", 0)
        b_ms = telem.get("binance_engine_ms", 0)
        lat = max(0, b_ms - f_ms) if (f_ms > 0 and b_ms > 0) else 0
        if isinstance(res, dict) and res.get("status") == 200 and not res.get("error"):
            log_fn(f"✅ [SHALLOW CANCEL CONFIRMED] {cid} | Latency: {lat}ms")


def fast_cancel_all_orders(ws_pipeline, symbol: str, rest_url: str, api_key: str, api_secret: str, get_synced_time_ms_fn, sign_params_fn, cids: Sequence[str] = ()) -> bool:
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

    try:
        params = {"symbol": symbol, "timestamp": int(get_synced_time_ms_fn())}
        signed = sign_params_fn(params, api_secret)
        r = requests.delete(f"{rest_url}/fapi/v1/allOpenOrders?{signed}", headers={"X-MBX-APIKEY": api_key}, timeout=2.5).json()
        return "code" not in r
    except Exception:
        return False


def fetch_post_trade_reconciliation(symbol: str, start_time_ms: int, rest_url: str, api_key: str, api_secret: str, get_synced_time_ms_fn, sign_params_fn) -> tuple[float, float, str, str, list[dict], dict]:
    try:
        time.sleep(0.5)
        ts = int(get_synced_time_ms_fn())
        session = requests.Session()

        p_orders = {"symbol": symbol, "startTime": int(start_time_ms), "timestamp": ts}
        res_orders = session.get(f"{rest_url}/fapi/v1/allOrders?{sign_params_fn(p_orders, api_secret)}", headers={"X-MBX-APIKEY": api_key}, timeout=3.0).json()
        order_map = {o.get("orderId"): o.get("clientOrderId", "") for o in res_orders} if isinstance(res_orders, list) else {}

        p_trades = {"symbol": symbol, "startTime": int(start_time_ms), "timestamp": ts}
        res_trades = session.get(f"{rest_url}/fapi/v1/userTrades?{sign_params_fn(p_trades, api_secret)}", headers={"X-MBX-APIKEY": api_key}, timeout=3.0).json()

        p_income = {"symbol": symbol, "startTime": int(start_time_ms), "timestamp": ts}
        res_income = session.get(f"{rest_url}/fapi/v1/income?{sign_params_fn(p_income, api_secret)}", headers={"X-MBX-APIKEY": api_key}, timeout=3.0).json()

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
                    if t_inc > funding_fee_time_ms: funding_fee_time_ms = t_inc
                elif itype == "REALIZED_PNL":
                    income_realized_pnl += val
                elif itype == "COMMISSION":
                    commission_paid += abs(val)

        default_pnl = {"net_pnl": 0.0, "funding_fee_str": "$0.0000", "pnl_str": "$0.0000", "pnl_class": "pnl-neutral", "funding_fee_class": "pnl-neutral", "funding_fee_time": "--:--:--"}
        if not isinstance(res_trades, list) or len(res_trades) == 0:
            return 0.0, 0.0, "", "", [], default_pnl

        buy_trades = [t for t in res_trades if t.get("buyer") is True or t.get("side", "").upper() == "BUY"]
        sell_trades = [t for t in res_trades if t.get("side", "").upper() == "SELL"]

        avg_entry_price, entry_fill_time, dca_hits = 0.0, "", []
        if buy_trades:
            tot_q = sum(float(t.get("qty", 0.0)) for t in buy_trades)
            tot_c = sum(float(t.get("price", 0.0)) * float(t.get("qty", 0.0)) for t in buy_trades)
            avg_entry_price = round(tot_c / tot_q, 8) if tot_q > 0 else 0.0
            earliest_buy = min(int(t.get("time", 0)) for t in buy_trades)
            entry_fill_time = datetime.fromtimestamp(earliest_buy / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3]

        avg_exit_price = 0.0
        if sell_trades:
            tot_sq = sum(float(t.get("qty", 0.0)) for t in sell_trades)
            tot_sc = sum(float(t.get("price", 0.0)) * float(t.get("qty", 0.0)) for t in sell_trades)
            avg_exit_price = round(tot_sc / tot_sq, 8) if tot_sq > 0 else 0.0

        trades_pnl = sum(float(t.get("realizedPnl", 0.0)) for t in res_trades)
        trades_comm = sum(float(t.get("commission", 0.0)) for t in res_trades)

        realized = income_realized_pnl if abs(income_realized_pnl) > 1e-8 else trades_pnl
        total_comm = commission_paid if commission_paid > 0 else trades_comm
        net_pnl = (realized + funding_fee) - total_comm

        return avg_entry_price, avg_exit_price, entry_fill_time, "", [], {
            "net_pnl": round(net_pnl, 4),
            "funding_fee_str": f"${funding_fee:+.4f} USDT",
            "pnl_str": f"${net_pnl:+.4f} USDT",
            "pnl_class": "pnl-positive" if net_pnl > 0 else "pnl-negative",
            "funding_fee_class": "pnl-positive" if funding_fee > 0 else "pnl-negative",
            "funding_fee_time": datetime.fromtimestamp(funding_fee_time_ms / 1000, tz=IST).strftime("%H:%M:%S.%f")[:-3] if funding_fee_time_ms > 0 else "--:--:--"
        }
    except Exception:
        return 0.0, 0.0, "", "", [], {"net_pnl": 0.0, "funding_fee_str": "$0.0000", "pnl_str": "$0.0000"}


def execute_market_exit_3layer(ws_pipeline, symbol: str, qty_str: str, reason_label: str, rest_url: str, api_key: str, api_secret: str, get_synced_time_ms_fn, sign_params_fn) -> tuple[bool, str, str]:
    now_exit_dt = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
    req_id_1 = f"exit-ws1-{uuid.uuid4().hex[:6]}"
    res1 = ws_pipeline.place_order_ws(symbol=symbol, side="SELL", order_type="MARKET", quantity=qty_str, client_order_id=req_id_1, timeout=0.5, reduce_only=True)
    if res1.get("status") == 200 or "orderId" in res1.get("result", {}):
        return True, now_exit_dt, "WS_ATTEMPT_1"

    req_id_2 = f"exit-ws2-{uuid.uuid4().hex[:6]}"
    res2 = ws_pipeline.place_order_ws(symbol=symbol, side="SELL", order_type="MARKET", quantity=qty_str, client_order_id=req_id_2, timeout=0.5, reduce_only=True)
    if res2.get("status") == 200 or "orderId" in res2.get("result", {}):
        return True, now_exit_dt, "WS_ATTEMPT_2"

    try:
        p = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty_str, "reduceOnly": "true", "timestamp": int(get_synced_time_ms_fn())}
        r = requests.post(f"{rest_url}/fapi/v1/order?{sign_params_fn(p, api_secret)}", headers={"X-MBX-APIKEY": api_key}, timeout=2.5).json()
        if "orderId" in r:
            return True, now_exit_dt, "REST_FALLBACK"
    except Exception:
        pass
    return False, now_exit_dt, "FAILED"


def run_strategy1_cycle(ws_pipeline, client, target_symbol, rate, last_price, settle_epoch, config, state_callbacks):
    add_ui_log = state_callbacks["add_ui_log"]
    get_synced_time_ms = state_callbacks["get_synced_time_ms"]
    precision_wait_until = state_callbacks["precision_wait_until"]
    format_step = state_callbacks["format_step"]
    format_tick = state_callbacks["format_tick"]
    sign_params = state_callbacks["sign_params"]

    t_rescan_20s = settle_epoch - config["RESCAN_BEFORE_SETTLE_MS"]
    t_gc_purge = settle_epoch - config["GC_PURGE_BEFORE_SETTLE_MS"]
    t_ws_warmup = settle_epoch - config["WS_WARMUP_BEFORE_SETTLE_MS"]
    t_tcp_warmup = settle_epoch - config["TCP_WARMUP_BEFORE_SETTLE_MS"]
    t_recheck = settle_epoch - config["RECHECK_BEFORE_SETTLE_MS"]
    t_limit_burst = settle_epoch - config["ENTRY_BEFORE_SETTLE_MS"]

    dynamic_lead = ws_pipeline.get_measured_lead_latency_ms()
    target_strike_ms = settle_epoch + config["SHALLOW_CANCEL_AFTER_SETTLE_MS"]
    computed_trigger = target_strike_ms - dynamic_lead
    floor_trigger = settle_epoch + config["SHALLOW_CANCEL_FLOOR_MS"]
    t_cancel_shallow = max(floor_trigger, computed_trigger)

    t_cancel_orders = settle_epoch + config["CANCEL_ORDERS_AFTER_SETTLE_MS"]
    t_scheduled_exit = settle_epoch + config["EXIT_AFTER_SETTLE_MS"]
    t_emergency_sweep = settle_epoch + config["EMERGENCY_SWEEP_AFTER_SETTLE_MS"]

    abs_r = abs(float(rate))
    is_cond_1 = abs_r >= config["FR_THRESHOLD_RATIO"]
    gap_dca_1 = round(abs_r * config["GRID_RATIO_DCA_HIGH" if is_cond_1 else "GRID_RATIO_DCA_LOW"], 5)
    sl_ratio = config["SL_BELOW_LIMIT_RATIO_COND_1" if is_cond_1 else "SL_BELOW_LIMIT_RATIO_COND_2"]

    shallow_notional_usd = max(5.5, config["LIMIT_ORDER_MARGIN_USD"] * config["MAX_LEVERAGE_CAP"])
    dca_notional_usd = max(5.5, config["LIMIT_ORDER_MARGIN_USD" if is_cond_1 else "DCA_SPLIT_MARGIN_USD"] * config["MAX_LEVERAGE_CAP"])

    epoch_tag = f"{int(settle_epoch / 1000) % 100000}"
    cid_shlw1 = f"shlw1-{target_symbol[:4]}-{epoch_tag}"
    cid_shlw2 = f"shlw2-{target_symbol[:4]}-{epoch_tag}"
    cid_shlw3 = f"shlw3-{target_symbol[:4]}-{epoch_tag}"
    cid_dca1 = f"dca-dip1-{target_symbol[:4]}-{epoch_tag}"
    cid_dca2 = f"dca-dip2-{target_symbol[:4]}-{epoch_tag}"
    shallow_cids = [cid_shlw1, cid_shlw2, cid_shlw3]
    all_cycle_cids = [cid_shlw1, cid_shlw2, cid_shlw3, cid_dca1] + ([] if is_cond_1 else [cid_dca2])

    try:
        precision_wait_until(t_gc_purge)
        gc.collect()
        gc.disable()

        precision_wait_until(t_ws_warmup)
        ws_pipeline.reconnect_order_ws()
        time.sleep(0.3)

        precision_wait_until(t_tcp_warmup)
        ws_pipeline.ping_order_ws()

        precision_wait_until(t_recheck)
        t2_snapshot_price = last_price
        try:
            r_check = requests.get(f"{config['BINANCE_REST_URL']}/fapi/v1/premiumIndex?symbol={target_symbol}", timeout=1.2).json()
            fresh_p = float(r_check.get("markPrice", last_price))
            if fresh_p > 0: t2_snapshot_price = fresh_p
        except Exception:
            pass

        precision_wait_until(t_limit_burst)
        cycle_order_start_ms = int(get_synced_time_ms()) - 1000
        entry_dispatch_time = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]

        live_ws_p = ws_pipeline.get_live_price(target_symbol)
        anchor_price = live_ws_p if (live_ws_p and live_ws_p > 0) else t2_snapshot_price

        dec_anchor = Decimal(str(anchor_price))
        abs_rate_dec = Decimal(str(abs(float(rate))))

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
        except Exception:
            pass

        limit_orders_batch = []
        planned_shallow_map = {}
        shallow_specs = [
            (cid_shlw1, config["SHALLOW_LIMIT_RATIOS"][0], "Shallow Limit #1 (30% FR)", 1),
            (cid_shlw2, config["SHALLOW_LIMIT_RATIOS"][1], "Shallow Limit #2 (50% FR)", 2),
            (cid_shlw3, config["SHALLOW_LIMIT_RATIOS"][2], "Shallow Limit #3 (70% FR)", 3),
        ]

        for cid_s, s_ratio, s_label, s_idx in shallow_specs:
            drop_dec = abs_rate_dec * Decimal(str(s_ratio))
            p_dec = dec_anchor * (Decimal("1") - drop_dec)
            p_str = format_tick(float(p_dec), tick_size)
            q_val = max(min_qty, shallow_notional_usd / float(p_str))
            q_str = format_step(q_val, qty_step)
            limit_orders_batch.append((cid_s, q_str, p_str, float(drop_dec), s_label))
            planned_shallow_map[cid_s] = {"cid": cid_s, "label": s_label, "price": p_str, "qty": q_str, "sort_idx": s_idx}

        dca1_p_dec = dec_anchor * (Decimal("1") - Decimal(str(gap_dca_1)))
        dca1_price_str = format_tick(float(dca1_p_dec), tick_size)
        dca1_qty = format_step(max(min_qty, dca_notional_usd / float(dca1_price_str)), qty_step)
        limit_orders_batch.append((cid_dca1, dca1_qty, dca1_price_str, gap_dca_1, "DCA Limit #1"))

        if not is_cond_1:
            dca2_p_dec = Decimal(dca1_price_str) * (Decimal("1") - Decimal(str(config["L2_BELOW_L1_GAP"])))
            dca2_price_str = format_tick(float(dca2_p_dec), tick_size)
            dca2_qty = format_step(max(min_qty, dca_notional_usd / float(dca2_price_str)), qty_step)
            gap_dca_2_val = float((dec_anchor - Decimal(dca2_price_str)) / dec_anchor)
            limit_orders_batch.append((cid_dca2, dca2_qty, dca2_price_str, gap_dca_2_val, "DCA Limit #2"))
            sl_dec = Decimal(dca2_price_str) * (Decimal("1") - Decimal(str(sl_ratio)))
        else:
            sl_dec = Decimal(dca1_price_str) * (Decimal("1") - Decimal(str(sl_ratio)))

        sl_target_price = float(sl_dec)
        sl_target_str = format_tick(sl_target_price, tick_size)

        ws_pipeline.place_orders_burst_ws(target_symbol, limit_orders_batch, timeout=0.09)

        exit_triggered = False
        shallow_cancel_fired = False
        exit_reason, exit_status, exit_send_time = "", "", ""
        exit_price_detected = 0.0

        while get_synced_time_ms() < t_cancel_orders:
            now_loop_ms = get_synced_time_ms()
            if not shallow_cancel_fired and now_loop_ms >= t_cancel_shallow:
                shallow_cancel_fired = True
                threading.Thread(target=fast_cancel_shallow_orders, args=(ws_pipeline, target_symbol, shallow_cids, add_ui_log), daemon=True).start()

            real_market_trade_price = ws_pipeline.get_last_traded_price(target_symbol)
            if 0.0 < real_market_trade_price <= sl_target_price and sl_target_price > 0:
                exit_price_detected = real_market_trade_price
                exit_triggered = True
                exit_reason = f"REAL-TRADE SL HIT (LTP: {real_market_trade_price} <= SL: {sl_target_str})"
                exit_status = "STOP LOSS EXECUTED (MAX LOSS SHIELD)"
                
                ws_pos = ws_pipeline.get_filled_qty(target_symbol)
                exit_qty = format_step(ws_pos, qty_step) if ws_pos > 0 else dca1_qty
                if ws_pos > 0:
                    execute_market_exit_3layer(ws_pipeline, target_symbol, exit_qty, "SL EMERGENCY EXIT", config["BINANCE_REST_URL"], config["API_KEY"], config["API_SECRET"], get_synced_time_ms, sign_params)
                
                fast_cancel_all_orders(ws_pipeline, target_symbol, config["BINANCE_REST_URL"], config["API_KEY"], config["API_SECRET"], get_synced_time_ms, sign_params, all_cycle_cids)
                break
            time.sleep(0.001)

        if not exit_triggered:
            precision_wait_until(t_cancel_orders)
            fast_cancel_all_orders(ws_pipeline, target_symbol, config["BINANCE_REST_URL"], config["API_KEY"], config["API_SECRET"], get_synced_time_ms, sign_params, all_cycle_cids)

            precision_wait_until(t_scheduled_exit)
            time.sleep(0.02)
            ws_pos = ws_pipeline.get_filled_qty(target_symbol)
            exit_price_detected = ws_pipeline.get_live_price(target_symbol) or t2_snapshot_price

            if ws_pos > 0:
                exit_qty = format_step(ws_pos, qty_step)
                exit_reason = "POST-SETTLEMENT HARVEST EXIT"
                exit_status = "SETTLEMENT FEE CAPTURED"
                execute_market_exit_3layer(ws_pipeline, target_symbol, exit_qty, "HARVEST EXIT", config["BINANCE_REST_URL"], config["API_KEY"], config["API_SECRET"], get_synced_time_ms, sign_params)
            else:
                exit_reason = "CLEAN PASS (NO LIMIT FILLED)"
                exit_status = "CLEAN PASS"

        rec_entry_p, rec_exit_p, rec_entry_t, rec_exit_t, rec_dca_hits, rec_pnl = fetch_post_trade_reconciliation(
            target_symbol, cycle_order_start_ms, config["BINANCE_REST_URL"], config["API_KEY"], config["API_SECRET"], get_synced_time_ms, sign_params
        )

        final_avg_entry = rec_entry_p or ws_pipeline.get_avg_fill_price(target_symbol) or 0.0
        final_exit_price = rec_exit_p or ws_pipeline.get_sell_fill_price(target_symbol) or exit_price_detected

        return {
            "symbol": target_symbol,
            "funding_rate": f"{rate * 100:+.4f}%",
            "tier": "STRATEGY 1 (FUNDING SNIPER)",
            "entry_sent": entry_dispatch_time,
            "entry_ack": rec_entry_t or "N/A",
            "entry_fill_time": rec_entry_t or "N/A",
            "entry_fill_price": str(final_avg_entry) if final_avg_entry > 0 else "No Fill",
            "exit_sent": "T+7s",
            "exit_fill_time": datetime.now(IST).strftime("%H:%M:%S.%f")[:-3],
            "exit_price": str(final_exit_price),
            "exit_type": exit_reason,
            "status": exit_status,
            "details": f"Avg Entry: {final_avg_entry} | Exit: {final_exit_price} | SL: {sl_target_str}",
            "funding_fee_received": rec_pnl.get("funding_fee_str", "$0.0000"),
            "funding_fee_class": rec_pnl.get("funding_fee_class", "pnl-neutral"),
            "funding_fee_time": rec_pnl.get("funding_fee_time", "--:--:--"),
            "pnl": rec_pnl.get("pnl_str", "$0.0000"),
            "pnl_class": rec_pnl.get("pnl_class", "pnl-neutral"),
            "pnl_breakdown": f"Net PnL: {rec_pnl.get('pnl_str')} | Funding: {rec_pnl.get('funding_fee_str')}",
            "dca_hits": rec_dca_hits or ws_pipeline.get_dca_hits(target_symbol)
        }

    except Exception as e:
        add_ui_log(f"🚨 [Strategy 1 Error]: {e}")
        return None
    finally:
        gc.enable()
        ws_pipeline.reset_cycle_state(target_symbol)
