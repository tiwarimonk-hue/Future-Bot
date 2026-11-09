from __future__ import annotations
import hmac
import hashlib
import json
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional
import requests
import websocket
import config

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


def resolve_limit_level_meta(client_order_id: str, fallback_id: Any = "") -> tuple[str, str, int]:
    """
    clientOrderId ke aadhar par (Level Name, Group ['shallow' | 'dca'], Sort Index [1..5]) return karta hai.
    """
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


def resolve_limit_level_label(client_order_id: str) -> str:
    lbl, _, _ = resolve_limit_level_meta(client_order_id)
    return lbl


class BinanceFastLaneWS:
    def __init__(self, api_key: str, api_secret: str, log_fn: Callable[[str], None], status_fn: Callable[[str], None]):
        self.api_key = api_key
        self.api_secret = api_secret
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

        # Limit Order Tracking Flags & Shallow Cancel Telemetry
        self.active_limit_orders: set[str] = set()
        self.shallow_cancels_telemetry: dict[str, dict[str, Any]] = {}

        # Live Dynamic Latency Measurement (T-4s Ping RTT)
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

            # Instant memory clean slate har cycle ke baad
            self.order_registry.clear()

    def acquire_listen_key(self) -> bool:
        if not self.api_key:
            self.log_fn("❌ [CONFIG ERROR] BINANCE_API_KEY is empty! Check your .env file.")
            return False

        url = f"{config.BINANCE_REST_URL}/fapi/v1/listenKey"
        headers = {
            "X-MBX-APIKEY": self.api_key,
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json"
        }
        try:
            res = requests.post(url, headers=headers, timeout=5)
            if res.status_code == 200:
                self.listen_key = res.json().get("listenKey", "")
                self.log_fn(f"Binance ListenKey acquired: {self.listen_key[:8]}***")
                return True
            self.log_fn(f"ListenKey acquisition failed: {res.text}")
        except Exception as e:
            self.log_fn(f"ListenKey acquisition error: {e}")
        return False

    def keep_alive_listen_key(self) -> None:
        url = f"{config.BINANCE_REST_URL}/fapi/v1/listenKey"
        headers = {
            "X-MBX-APIKEY": self.api_key,
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json"
        }
        while True:
            time.sleep(20 * 60)
            if self.listen_key:
                try:
                    res = requests.put(url, headers=headers, timeout=5)
                    if res.status_code == 200:
                        self.log_fn("Binance ListenKey refreshed (Keep-Alive).")
                    else:
                        self.log_fn("ListenKey renewal failed, re-acquiring...")
                        self.acquire_listen_key()
                except Exception as e:
                    self.log_fn(f"ListenKey keep-alive warning: {e}")

    def on_user_message(self, ws: Any, message: str) -> None:
        try:
            data = json.loads(message)
        except Exception:
            return

        event_type = data.get("e", "")
        recv_time = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]

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
                        # Binance matching engine exact cancel timestamp
                        self.shallow_cancels_telemetry[client_order_id]["binance_engine_ms"] = trade_ts_ms

            if execution_type == "TRADE" and last_filled_qty > 0:
                with self.ws_lock:
                    if sym not in self.last_fill_times or side != "buy":
                        self.last_fill_times[sym] = exact_trade_time
                    self.last_fill_types[sym] = order_type
                    self.log_fn(f"⚡ EXECUTION CONFIRMED [{exact_trade_time}] | [{order_type} {side.upper()}] on {sym} | Qty: {last_filled_qty} @ {last_filled_price}")

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

                        # Group partial fills per limit order level (1 box per limit order)
                        existing_hit = next((h for h in self.dca_hits[sym] if h.get("level") == lvl_name), None)
                        if existing_hit is None:
                            clean_q = int(last_filled_qty) if float(last_filled_qty).is_integer() else round(last_filled_qty, 6)
                            hit_record = {
                                "level": lvl_name,
                                "group": grp_type,
                                "sort_idx": s_idx,
                                "price": round(last_filled_price, 8),
                                "qty": clean_q,
                                "_raw_qty": last_filled_qty,
                                "_raw_cost": last_filled_price * last_filled_qty,
                                "first_ms": trade_ts_ms,
                                "last_ms": trade_ts_ms,
                                "time": exact_trade_time,
                                "first_time": exact_trade_time,
                                "last_time": exact_trade_time,
                                "span_ms": 0,
                                "fills_count": 1
                            }
                            self.dca_hits[sym].append(hit_record)
                            self.dca_hits[sym].sort(key=lambda x: (x.get("sort_idx", 99), x.get("first_ms", 0)))
                            self.log_fn(f"🎯 [{lvl_name} HIT] {sym} @ {last_filled_price} USDT (Qty: {clean_q}) at {exact_trade_time} IST")
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

                        if old_qty > 0:
                            self.log_fn(f"🔄 [POSITION AVERAGED] {sym} | Total Qty: {new_qty} | New Weighted Avg Price: {avg_p:.6f} USDT")

                    elif side == "sell":
                        self.sell_fill_prices[sym] = last_filled_price
                        current_q = self.filled_quantities.get(sym, 0.0)
                        remaining_q = max(0.0, current_q - last_filled_qty)
                        if remaining_q < 1e-6:
                            remaining_q = 0.0
                            self.position_closed_symbols.add(sym)
                        self.filled_quantities[sym] = remaining_q

            elif execution_type == "REJECTED":
                with self.ws_lock:
                    self.rejected_orders.add(client_order_id)
                    self.active_limit_orders.discard(client_order_id)
                self.log_fn(f"❌ ORDER REJECTED on {sym} | ID: {client_order_id}")

    def on_user_open(self, ws: Any) -> None:
        self.user_ws_ready = True
        self.check_overall_ready()
        self.log_fn("Binance User Data WebSocket Authenticated & Ready.")

    def on_user_close(self, ws: Any, close_code: Any, close_msg: Any) -> None:
        self.user_ws_ready = False
        self.ws_ready = False
        self.status_fn("USER WS DISCONNECTED")
        self.log_fn(f"Binance User WS Closed: {close_msg}")

    def on_user_error(self, ws: Any, error: Any) -> None:
        self.user_ws_ready = False
        self.ws_ready = False
        self.log_fn(f"Binance User WS Error: {error}")

    def on_order_open(self, ws: Any) -> None:
        self.order_ws_ready = True
        self.check_overall_ready()
        self.log_fn("Binance Order Fast-Lane WebSocket Connected & Armed.")

    def on_order_message(self, ws: Any, message: str) -> None:
        try:
            data = json.loads(message)
            req_id = data.get("id", "")
            status_code = data.get("status")
            res_obj = data.get("result", {})

            # Measure Live WebSocket RTT when warmup-ping response arrives
            if req_id == "warmup-ping":
                if self.ping_send_perf > 0:
                    measured_rtt = (time.perf_counter() - self.ping_send_perf) * 1000.0
                    with self.ws_lock:
                        self.last_measured_rtt_ms = measured_rtt
                    one_way_lead = measured_rtt / 2.0
                    self.log_fn(
                        f"🔥 [WS RTT MEASURED] Warm-up ping RTT: {measured_rtt:.2f}ms "
                        f"(Dynamic 1-way Lead: {one_way_lead:.2f}ms)"
                    )
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
        self.log_fn(f"Order WS Disconnected: {close_msg}")

    def on_order_error(self, ws: Any, error: Any) -> None:
        self.order_ws_ready = False
        self.log_fn(f"Order WS Error: {error}")

    def check_overall_ready(self) -> None:
        if self.user_ws_ready and self.order_ws_ready:
            self.ws_ready = True
            self.status_fn("BINANCE TOKYO WS (READY)")

    def reconnect_order_ws(self) -> None:
        """T-8s par purane dormant TLS session ko close karke fresh handshake trigger karta hai"""
        with self.ws_lock:
            self.order_ws_ready = False
        if self.order_ws:
            try:
                self.order_ws.close()
            except Exception:
                pass

    def ping_order_ws(self) -> bool:
        """T-4s par raw JSON RPC ping frame bhejkar TCP/TLS buffers ko awake karta hai aur RTT measure karta hai"""
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

    def get_measured_lead_latency_ms(
        self,
        min_lead: float = config.SHALLOW_CANCEL_LEAD_MIN_MS,
        max_lead: float = config.SHALLOW_CANCEL_LEAD_MAX_MS,
        default_lead: float = config.SHALLOW_CANCEL_LEAD_DEFAULT_MS
    ) -> float:
        """
        T-4s par naape gaye live RTT se dynamic 1-way flight latency nikalta hai (clamped between safe bounds).
        """
        with self.ws_lock:
            if self.last_measured_rtt_ms > 0:
                raw_lead = self.last_measured_rtt_ms / 2.0
                return max(min_lead, min(max_lead, raw_lead))
            return default_lead

    def place_order_ws(self, symbol: str, side: str, order_type: str, quantity: str, client_order_id: str,
                       price: str = "", time_in_force: str = "", timeout: float = 0.5,
                       reduce_only: bool = False) -> dict:
        if not self.order_ws or not self.order_ws_ready:
            return {"error": "Order WebSocket not connected"}

        timestamp_ms = int(time.time() * 1000)

        sign_dict = {
            "apiKey": self.api_key,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "newClientOrderId": client_order_id,
            "timestamp": timestamp_ms
        }
        if price:
            sign_dict["price"] = price
        if time_in_force:
            sign_dict["timeInForce"] = time_in_force
        if reduce_only:
            sign_dict["reduceOnly"] = "true"

        query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
        sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()

        payload = {
            "id": client_order_id,
            "method": "order.place",
            "params": {
                **sign_dict,
                "signature": sig
            }
        }

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

    def place_orders_burst_ws(self, symbol: str, orders_batch: list[tuple[str, str, str, float, str]],
                              timeout: float = 0.09) -> list[tuple[str, str, str, float, str, dict]]:
        """
        T-100ms par saare 4 ya 5 limit orders ko exact sequence (30% -> 50% -> 70% -> DCA#1 -> DCA#2)
        me ek hi millisecond ke andar socket pipeline me flush karta hai, bina thread lock race ke.
        """
        if not self.order_ws or not self.order_ws_ready:
            return [(cid, q, p, r, lbl, {"error": "Order WebSocket not connected"}) for cid, q, p, r, lbl in orders_batch]

        timestamp_ms = int(time.time() * 1000)
        prepared_frames: list[tuple[str, str]] = []

        for cid, q_str, p_str, _, _ in orders_batch:
            sign_dict = {
                "apiKey": self.api_key,
                "symbol": symbol,
                "side": "BUY",
                "type": "LIMIT",
                "quantity": q_str,
                "price": p_str,
                "timeInForce": "GTC",
                "newClientOrderId": cid,
                "timestamp": timestamp_ms
            }
            query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
            sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()

            payload = {
                "id": cid,
                "method": "order.place",
                "params": {
                    **sign_dict,
                    "signature": sig
                }
            }
            event = threading.Event()
            self.pending_order_events[cid] = event
            prepared_frames.append((cid, json.dumps(payload)))

        # Single locked sequential flush: 30% -> 50% -> 70% -> DCA #1 -> DCA #2
        send_errors: dict[str, str] = {}
        with self.order_send_lock:
            for cid, raw_frame in prepared_frames:
                try:
                    self.order_ws.send(raw_frame)
                except Exception as e:
                    send_errors[cid] = f"WS send error: {e}"
                    self.pending_order_events.pop(cid, None)

        # Wait for all confirmations within shared deadline
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
        """Fast-Lane WebSocket se single clientOrderId ko 2-4ms me cancel karta hai"""
        if not self.order_ws or not self.order_ws_ready:
            return {"error": "Order WebSocket not connected"}

        timestamp_ms = int(time.time() * 1000)
        sign_dict = {
            "apiKey": self.api_key,
            "symbol": symbol,
            "origClientOrderId": client_order_id,
            "timestamp": timestamp_ms
        }
        query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
        sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()

        cancel_req_id = f"cancel-{client_order_id}"
        payload = {
            "id": cancel_req_id,
            "method": "order.cancel",
            "params": {
                **sign_dict,
                "signature": sig
            }
        }

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
        """
        T+230ms par teeno shallow limit orders ke liye simultaneous order.cancel frames flush karta hai.
        Local hardware clock ka exact fired timestamp (fired_local_ms) aur Binance engine updateTime alag-alag record karta hai.
        """
        valid_cids = [c for c in client_order_ids if c]
        if not self.order_ws or not self.order_ws_ready:
            return [(cid, {"error": "Order WebSocket not connected"}) for cid in valid_cids]

        timestamp_ms = int(time.time() * 1000)
        prepared_frames: list[tuple[str, str, str]] = []

        for cid in valid_cids:
            sign_dict = {
                "apiKey": self.api_key,
                "symbol": symbol,
                "origClientOrderId": cid,
                "timestamp": timestamp_ms
            }
            query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
            sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()

            cancel_req_id = f"cancel-{cid}"
            payload = {
                "id": cancel_req_id,
                "method": "order.cancel",
                "params": {
                    **sign_dict,
                    "signature": sig
                }
            }
            event = threading.Event()
            self.pending_order_events[cancel_req_id] = event
            prepared_frames.append((cid, cancel_req_id, json.dumps(payload)))

        send_errors: dict[str, str] = {}
        # Pure local VPS hardware time when frame is pushed to network
        fired_local_exact_ts_ms = int(time.time() * 1000)

        with self.order_send_lock:
            for cid, cancel_req_id, raw_frame in prepared_frames:
                try:
                    self.order_ws.send(raw_frame)
                except Exception as e:
                    send_errors[cid] = f"WS cancel send error: {e}"
                    self.pending_order_events.pop(cancel_req_id, None)

        deadline = time.time() + timeout
        results: list[tuple[str, dict]] = []
        for cid, cancel_req_id, _ in prepared_frames:
            if cid in send_errors:
                results.append((cid, {"error": send_errors[cid]}))
                continue

            ev = self.pending_order_events.get(cancel_req_id)
            confirmed = False
            if ev is not None:
                rem = max(0.001, deadline - time.time())
                confirmed = ev.wait(timeout=rem)

            res = self.pending_order_responses.pop(cancel_req_id, None)
            self.pending_order_events.pop(cancel_req_id, None)
            if not confirmed or res is None:
                res = {"error": "Timeout waiting for WS order.cancel"}

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
        """Fast-Lane WebSocket se symbol ke saare open orders ko 2-4ms me cancelAll karta hai"""
        if not self.order_ws or not self.order_ws_ready:
            return {"error": "Order WebSocket not connected"}

        timestamp_ms = int(time.time() * 1000)
        sign_dict = {
            "apiKey": self.api_key,
            "symbol": symbol,
            "timestamp": timestamp_ms
        }
        query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
        sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()

        req_id = f"cancel-all-{symbol[:4]}-{int(time.time() * 1000) % 100000}"
        payload = {
            "id": req_id,
            "method": "order.cancelAll",
            "params": {
                **sign_dict,
                "signature": sig
            }
        }

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
            try:
                self.public_ws.close()
            except Exception:
                pass

        with self.ws_lock:
            self.live_prices.clear()
            self.last_traded_prices.clear()
            self.current_subscribed_symbol = symbol

        def runner(target_sym: str):
            stream_url = f"{config.BINANCE_WS_URL}/stream?streams={target_sym.lower()}@aggTrade/{target_sym.lower()}@bookTicker"
            while self.current_subscribed_symbol == target_sym:
                try:
                    self.public_ws = websocket.WebSocketApp(
                        stream_url,
                        on_message=self.on_public_message
                    )
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
        with self.ws_lock:
            return symbol in self.long_filled_symbols

    def is_position_closed(self, symbol: str) -> bool:
        with self.ws_lock:
            return symbol in self.position_closed_symbols

    def get_filled_qty(self, symbol: str) -> float:
        with self.ws_lock:
            return self.filled_quantities.get(symbol, 0.0)

    def get_avg_fill_price(self, symbol: str) -> Optional[float]:
        with self.ws_lock:
            return self.buy_avg_prices.get(symbol) or self.last_fill_prices.get(symbol)

    def get_sell_fill_price(self, symbol: str) -> Optional[float]:
        with self.ws_lock:
            return self.sell_fill_prices.get(symbol)

    def get_dca_hits(self, symbol: str) -> list[dict[str, Any]]:
        with self.ws_lock:
            hits = []
            for h in self.dca_hits.get(symbol, []):
                clean_copy = {k: v for k, v in h.items() if not k.startswith("_")}
                hits.append(clean_copy)
            return hits

    def get_last_fill_time(self, symbol: str) -> str:
        with self.ws_lock:
            return self.last_fill_times.get(symbol, "")

    def is_order_rejected(self, client_order_id: str) -> bool:
        with self.ws_lock:
            return client_order_id in self.rejected_orders

    def connect(self) -> None:
        attempt = 1
        retry_delays = [5, 10, 15, 30]
        while not self.acquire_listen_key():
            delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
            self.status_fn(f"CONNECTING BINANCE (RETRY #{attempt} in {delay}s)...")
            attempt += 1
            time.sleep(delay)

        threading.Thread(target=self.keep_alive_listen_key, daemon=True).start()

        def user_ws_runner():
            while True:
                try:
                    stream_url = f"{config.BINANCE_WS_URL}/ws/{self.listen_key}"
                    self.user_ws = websocket.WebSocketApp(
                        stream_url,
                        on_open=self.on_user_open,
                        on_message=self.on_user_message,
                        on_close=self.on_user_close,
                        on_error=self.on_user_error
                    )
                    self.user_ws.run_forever(ping_interval=10, ping_timeout=5)
                except Exception as e:
                    self.log_fn(f"User WS Connection failure: {e}")
                time.sleep(2)

        def order_ws_runner():
            while True:
                try:
                    self.order_ws = websocket.WebSocketApp(
                        config.BINANCE_WS_API_URL,
                        on_open=self.on_order_open,
                        on_message=self.on_order_message,
                        on_close=self.on_order_close,
                        on_error=self.on_order_error
                    )
                    self.order_ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as e:
                    self.log_fn(f"Order WS Connection failure: {e}")
                time.sleep(0.2)

        threading.Thread(target=user_ws_runner, daemon=True).start()
        threading.Thread(target=order_ws_runner, daemon=True).start()
