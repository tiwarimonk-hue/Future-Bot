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

class BinanceFastLaneWS:
    def __init__(self, api_key: str, api_secret: str, log_fn: Callable[[str], None], status_fn: Callable[[str], None]):
        self.api_key = api_key
        self.api_secret = api_secret
        self.log_fn = log_fn
        self.status_fn = status_fn
        self.listen_key: str = ""
        self.user_ws: Optional[websocket.WebSocketApp] = None
        self.order_ws: Optional[websocket.WebSocketApp] = None
        self.public_ws: Optional[websocket.WebSocketApp] = None
        self.user_ws_ready = False
        self.order_ws_ready = False
        self.public_ws_ready = False
        self.ws_ready = False
        self.live_prices: dict[str, float] = {}
        self.current_subscribed_symbol: Optional[str] = None
        self.pending_order_events: dict[str, threading.Event] = {}
        self.pending_order_responses: dict[str, dict[str, Any]] = {}
        self.long_filled_symbols: set[str] = set()
        self.position_closed_symbols: set[str] = set()
        self.last_fill_prices: dict[str, float] = {}
        self.last_fill_times: dict[str, str] = {}
        self.last_fill_types: dict[str, str] = {}
        self.filled_quantities: dict[str, float] = {}
        self.rejected_orders: set[str] = set()

    def reset_cycle_state(self, symbol: str) -> None:
        self.last_fill_prices.clear()
        self.last_fill_times.clear()
        self.last_fill_types.clear()
        self.filled_quantities.clear()
        self.long_filled_symbols.clear()
        self.position_closed_symbols.clear()
        self.rejected_orders.clear()
        self.live_prices.clear()
        self.pending_order_events.clear()
        self.pending_order_responses.clear()

    def acquire_listen_key(self) -> bool:
        url = f"{config.BINANCE_REST_URL}/fapi/v1/listenKey"
        headers = {"X-MBX-APIKEY": self.api_key}
        try:
            res = requests.post(url, headers=headers, timeout=5)
            if res.status_code == 200:
                self.listen_key = res.json().get("listenKey", "")
                self.log_fn(f"Binance ListenKey acquired: {self.listen_key[:8]}...")
                return True
            else:
                self.log_fn(f"ListenKey acquisition failed: {res.text}")
        except Exception as e:
            self.log_fn(f"ListenKey acquisition error: {e}")
        return False

    def keep_alive_listen_key(self) -> None:
        url = f"{config.BINANCE_REST_URL}/fapi/v1/listenKey"
        headers = {"X-MBX-APIKEY": self.api_key}
        while True:
            time.sleep(1800)
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
        recv_time = datetime.now(IST).strftime('%H:%M:%S.%f')[:-3]
        if event_type == "ORDER_TRADE_UPDATE":
            order_data = data.get("o", {})
            sym = order_data.get("s", "")
            side = order_data.get("S", "").lower()
            order_type = order_data.get("o", "")
            execution_type = order_data.get("x", "")
            client_order_id = order_data.get("c", "")
            last_filled_qty = float(order_data.get("l", 0.0))
            last_filled_price = float(order_data.get("L", 0.0))
            if execution_type == "TRADE" and last_filled_qty > 0:
                self.last_fill_times[sym] = recv_time
                self.last_fill_types[sym] = order_type
                self.log_fn(f"🟢 EXECUTION CONFIRMED [{recv_time}] [{order_type} ({side.upper())}] on {sym} | Qty: {last_filled_qty} @ {last_filled_price}")
                if side == "buy":
                    self.long_filled_symbols.add(sym)
                    self.filled_quantities[sym] = self.filled_quantities.get(sym, 0.0) + last_filled_qty
                    self.last_fill_prices[sym] = last_filled_price
                elif side == "sell":
                    current_q = self.filled_quantities.get(sym, 0.0)
                    remaining_q = max(0.0, current_q - last_filled_qty)
                    if remaining_q < 1e-6:
                        remaining_q = 0.0
                        self.position_closed_symbols.add(sym)
                    self.filled_quantities[sym] = remaining_q
            elif execution_type == "REJECTED":
                self.rejected_orders.add(client_order_id)
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

    def place_order_ws(self, symbol: str, side: str, order_type: str, quantity: str, client_order_id: str, timestamp_ms: int, timeout: float = 0.3) -> dict:
        if not self.order_ws or not self.order_ws_ready:
            return {"error": "Order WebSocket not connected"}
        sign_dict = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "newClientOrderId": client_order_id,
            "timestamp": timestamp_ms
        }
        query_string = "&".join([f"{k}={sign_dict[k]}" for k in sorted(sign_dict.keys())])
        sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
        payload = {
            "id": client_order_id,
            "method": "order.place",
            "params": {**sign_dict, "apiKey": self.api_key, "signature": sig}
        }
        event = threading.Event()
        self.pending_order_events[client_order_id] = event
        try:
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

    def on_public_message(self, ws: Any, message: str) -> None:
        try:
            data = json.loads(message)
            sym = data.get("s", "")
            price = float(data.get("p", 0.0))
            if sym and price > 0:
                self.live_prices[sym] = price
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
        self.live_prices.clear()
        self.current_subscribed_symbol = symbol

        def runner(target_sym: str):
            stream_url = f"{config.BINANCE_WS_URL}/ws/{target_sym.lower()}@aggTrade"
            while self.current_subscribed_symbol == target_sym:
                try:
                    self.public_ws = websocket.WebSocketApp(
                        stream_url,
                        on_message=self.on_public_message
                    )
                    self.public_ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception:
                    pass
                time.sleep(1)

        threading.Thread(target=runner, args=(symbol,), daemon=True).start()

    def get_live_price(self, symbol: str) -> float:
        return self.live_prices.get(symbol, 0.0)

    def is_long_filled(self, symbol: str) -> bool:
        return symbol in self.long_filled_symbols

    def is_position_closed(self, symbol: str) -> bool:
        return symbol in self.position_closed_symbols

    def get_filled_qty(self, symbol: str) -> float:
        return self.filled_quantities.get(symbol, 0.0)

    def get_avg_fill_price(self, symbol: str) -> Optional[float]:
        return self.last_fill_prices.get(symbol)

    def get_last_fill_time(self, symbol: str) -> str:
        return self.last_fill_times.get(symbol, "")

    def is_order_rejected(self, client_order_id: str) -> bool:
        return client_order_id in self.rejected_orders

    def connect(self) -> None:
        if not self.acquire_listen_key():
            return
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
                    self.user_ws.run_forever(ping_interval=20, ping_timeout=10)
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
                time.sleep(2)

        threading.Thread(target=user_ws_runner, daemon=True).start()
        threading.Thread(target=order_ws_runner, daemon=True).start()
