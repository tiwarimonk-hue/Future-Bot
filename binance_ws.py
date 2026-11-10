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

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": DEFAULT_UA, "Accept": "application/json"})

        self.ws_prices: dict[str, float] = {}
        self.ws_trades: dict[str, float] = {}
        self.order_fills: dict[str, float] = {}
        self.order_avg_prices: dict[str, float] = {}
        self.order_sell_prices: dict[str, float] = {}
        self.order_fill_times: dict[str, str] = {}
        self.dca_hits_store: dict[str, list[dict]] = {}
        self.shallow_cancel_telemetry: dict[str, dict] = {}
        self.client_id_to_order_id: dict[str, int] = {}

        self.last_ping_send_ms = 0.0
        self.measured_lead_ms = config.SHALLOW_CANCEL_LEAD_DEFAULT_MS
        self._ping_rtt_history: list[float] = []

    def _sign_fapi(self, params: dict) -> str:
        query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        sig = hmac.new(self.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{query_string}&signature={sig}"

    def _get_listen_key(self) -> None:
        try:
            url = f"{config.BINANCE_REST_URL}/fapi/v1/listenKey"
            r = self.session.post(url, headers={"X-MBX-APIKEY": self.api_key}, timeout=4)
            data = r.json()
            if isinstance(data, dict) and "listenKey" in data:
                self.listen_key = data["listenKey"]
        except Exception as e:
            self.log_fn(f"⚠️ [LISTEN KEY ERROR]: {e}")

    def connect(self) -> None:
        self._get_listen_key()
        threading.Thread(target=self._run_public_ws, daemon=True).start()
        threading.Thread(target=self._run_order_ws, daemon=True).start()
        threading.Thread(target=self._run_user_ws, daemon=True).start()

    def reconnect_order_ws(self) -> None:
        with self.ws_lock:
            if self.order_ws:
                try:
                    self.order_ws.close()
                except Exception:
                    pass
            self.order_ws_ready = False
        threading.Thread(target=self._run_order_ws, daemon=True).start()

    def _run_public_ws(self) -> None:
        url = f"{config.BINANCE_WS_URL}/ws"
        def on_open(ws):
            self.public_ws_ready = True
        def on_message(ws, msg):
            try:
                data = json.loads(msg)
                if "e" in data and data["e"] == "trade":
                    sym = data.get("s", "")
                    p = float(data.get("p", 0.0))
                    if sym and p > 0:
                        with self.ws_lock:
                            self.ws_prices[sym] = p
                            self.ws_trades[sym] = p
            except Exception:
                pass
        def on_close(ws, status, msg):
            self.public_ws_ready = False
            time.sleep(1)
            threading.Thread(target=self._run_public_ws, daemon=True).start()
        def on_error(ws, err):
            self.public_ws_ready = False

        while True:
            try:
                self.public_ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message, on_close=on_close, on_error=on_error)
                self.public_ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                time.sleep(2)

    def _run_order_ws(self) -> None:
        url = f"{config.BINANCE_WS_API_URL}"
        def on_open(ws):
            self.order_ws_ready = True
            self.log_fn("⚡ [ORDER WS CONNECTED] Tokyo WebSocket API (ws-fapi) authenticated and ready.")
        def on_message(ws, msg):
            try:
                data = json.loads(msg)
                if isinstance(data, dict):
                    if "id" in data and data["id"] == "ping-warmup":
                        recv_ms = time.time() * 1000
                        rtt = max(0.5, recv_ms - self.last_ping_send_ms)
                        self._ping_rtt_history.append(rtt)
                        if len(self._ping_rtt_history) > 5:
                            self._ping_rtt_history.pop(0)
                        avg_rtt = sum(self._ping_rtt_history) / len(self._ping_rtt_history)
                        estimated_one_way = avg_rtt / 2.0
                        self.measured_lead_ms = max(config.SHALLOW_CANCEL_LEAD_MIN_MS, min(config.SHALLOW_CANCEL_LEAD_MAX_MS, estimated_one_way))
                        self.log_fn(f"🔥 [ORDER WS PING RTT] RTT: {avg_rtt:.2f}ms | Estimated 1-way lead: {self.measured_lead_ms:.2f}ms")
                    
                    if "result" in data and isinstance(data["result"], dict):
                        res = data["result"]
                        oid = res.get("orderId")
                        cid = res.get("clientOrderId")
                        if cid and oid:
                            with self.ws_lock:
                                self.client_id_to_order_id[cid] = oid
                                
                    if "id" in data and str(data["id"]).startswith("cancel-shlw"):
                        recv_ms = time.time() * 1000
                        req_id = str(data["id"])
                        engine_time = 0.0
                        if isinstance(data.get("result"), dict):
                            engine_time = float(data["result"].get("updateTime", 0.0))
                        for cid, telem in self.shallow_cancel_telemetry.items():
                            if telem.get("req_id") == req_id:
                                telem["binance_engine_ms"] = engine_time if engine_time > 0 else recv_ms
                                break
            except Exception:
                pass
        def on_close(ws, status, msg):
            self.order_ws_ready = False
            time.sleep(1)
            threading.Thread(target=self._run_order_ws, daemon=True).start()
        def on_error(ws, err):
            self.order_ws_ready = False

        while True:
            try:
                self.order_ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message, on_close=on_close, on_error=on_error)
                self.order_ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                time.sleep(2)

    def _run_user_ws(self) -> None:
        while not self.listen_key:
            time.sleep(1)
        url = f"{config.BINANCE_WS_URL}/ws/{self.listen_key}"
        def on_open(ws):
            self.user_ws_ready = True
        def on_message(ws, msg):
            try:
                data = json.loads(msg)
                if isinstance(data, dict) and data.get("e") == "ORDER_TRADE_UPDATE":
                    o = data.get("o", {})
                    sym = o.get("s", "")
                    cid = o.get("c", "")
                    exec_type = o.get("x", "")
                    order_status = o.get("X", "")
                    side = o.get("S", "")
                    price = float(o.get("p", 0.0))
                    qty = float(o.get("q", 0.0))
                    last_qty = float(o.get("l", 0.0))
                    last_price = float(o.get("L", 0.0))
                    cum_qty = float(o.get("z", 0.0))
                    
                    if order_status == "FILLED" or exec_type == "TRADE":
                        if side == "BUY" and last_qty > 0 and last_price > 0:
                            with self.ws_lock:
                                self.order_fills[sym] = self.order_fills.get(sym, 0.0) + last_qty
                                t_str = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
                                self.order_fill_times[sym] = t_str
                                
                                # Track DCA Level Fills
                                lvl_name, grp_type, s_idx = resolve_limit_level_meta(cid)
                                dca_list = self.dca_hits_store.setdefault(sym, [])
                                existing_hit = next((x for x in dca_list if x["level"] == lvl_name), None)
                                if existing_hit:
                                    existing_hit["qty"] += last_qty
                                    existing_hit["price"] = last_price
                                    existing_hit["fills_count"] += 1
                                    existing_hit["last_time"] = t_str
                                else:
                                    dca_list.append({
                                        "level": lvl_name,
                                        "group": grp_type,
                                        "sort_idx": s_idx,
                                        "price": last_price,
                                        "qty": last_qty,
                                        "time": t_str,
                                        "first_time": t_str,
                                        "last_time": t_str,
                                        "fills_count": 1
                                    })
                                    
                        elif side == "SELL" and last_qty > 0 and last_price > 0:
                            with self.ws_lock:
                                self.order_sell_prices[sym] = last_price
            except Exception:
                pass
        def on_close(ws, status, msg):
            self.user_ws_ready = False
            time.sleep(2)
            threading.Thread(target=self._run_user_ws, daemon=True).start()
        def on_error(ws, err):
            self.user_ws_ready = False

        while True:
            try:
                self.user_ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message, on_close=on_close, on_error=on_error)
                self.user_ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception:
                time.sleep(2)

    def subscribe_ticker(self, symbol: str) -> None:
        pass

    def reset_cycle_state(self, symbol: str) -> None:
        with self.ws_lock:
            if symbol in self.ws_prices:
                del self.ws_prices[symbol]
            if symbol in self.ws_trades:
                del self.ws_trades[symbol]
            if symbol in self.order_fills:
                del self.order_fills[symbol]
            if symbol in self.order_avg_prices:
                del self.order_avg_prices[symbol]
            if symbol in self.order_sell_prices:
                del self.order_sell_prices[symbol]
            if symbol in self.order_fill_times:
                del self.order_fill_times[symbol]
            if symbol in self.dca_hits_store:
                del self.dca_hits_store[symbol]
            self.client_id_to_order_id.clear()
            self.shallow_cancel_telemetry.clear()

    def get_live_price(self, symbol: str) -> Optional[float]:
        with self.ws_lock:
            return self.ws_prices.get(symbol) or self.ws_trades.get(symbol)

    def get_last_traded_price(self, symbol: str) -> float:
        with self.ws_lock:
            return self.ws_trades.get(symbol, 0.0)

    def get_filled_qty(self, symbol: str) -> float:
        with self.ws_lock:
            return self.order_fills.get(symbol, 0.0)

    def get_avg_fill_price(self, symbol: str) -> float:
        with self.ws_lock:
            return self.order_avg_prices.get(symbol, 0.0)

    def get_sell_fill_price(self, symbol: str) -> float:
        with self.ws_lock:
            return self.order_sell_prices.get(symbol, 0.0)

    def get_last_fill_time(self, symbol: str) -> str:
        with self.ws_lock:
            return self.order_fill_times.get(symbol, "")

    def get_dca_hits(self, symbol: str) -> list[dict]:
        with self.ws_lock:
            return list(self.dca_hits_store.get(symbol, []))

    def get_shallow_cancel_telemetry(self, cid: str) -> dict:
        with self.ws_lock:
            return self.shallow_cancel_telemetry.get(cid, {})

    def get_measured_lead_latency_ms(self) -> float:
        return self.measured_lead_ms

    def ping_order_ws(self) -> bool:
        if not self.order_ws_ready or not self.order_ws:
            return False
        try:
            self.last_ping_send_ms = time.time() * 1000
            req = {"id": "ping-warmup", "method": "ping", "params": {}}
            self.order_ws.send(json.dumps(req))
            return True
        except Exception:
            return False

    def place_order_ws(self, symbol: str, side: str, order_type: str, quantity: str, price: Optional[str] = None, time_in_force: str = "GTC", client_order_id: Optional[str] = None, timeout: float = 0.1, reduce_only: bool = False) -> dict:
        if not self.order_ws_ready or not self.order_ws:
            return {"error": "Order WS not ready"}
        try:
            ts = int(time.time() * 1000)
            params = {
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "quantity": quantity,
                "timestamp": ts
            }
            if price:
                params["price"] = price
                params["timeInForce"] = time_in_force
            if client_order_id:
                params["newClientOrderId"] = client_order_id
            if reduce_only:
                params["reduceOnly"] = "true"
            
            sig = hmac.new(self.api_secret.encode("utf-8"), "&".join([f"{k}={v}" for k, v in sorted(params.items())]).encode("utf-8"), hashlib.sha256).hexdigest()
            params["signature"] = sig
            params["apiKey"] = self.api_key

            req = {"id": f"ord-{int(time.time()*1000)}", "method": "order.place", "params": params}
            with self.order_send_lock:
                self.order_ws.send(json.dumps(req))
            return {"status": 200, "result": {"clientOrderId": client_order_id}}
        except Exception as e:
            return {"error": str(e)}

    def place_orders_burst_ws(self, symbol: str, orders_list: list[tuple[str, str, str, float, str]]) -> list:
        if not self.order_ws_ready or not self.order_ws:
            return []
        results = []
        with self.order_send_lock:
            for cid, q_str, p_str, ratio, label in orders_list:
                try:
                    ts = int(time.time() * 1000)
                    params = {
                        "symbol": symbol,
                        "side": "BUY",
                        "type": "LIMIT",
                        "quantity": q_str,
                        "price": p_str,
                        "timeInForce": "GTC",
                        "newClientOrderId": cid,
                        "timestamp": ts
                    }
                    sig = hmac.new(self.api_secret.encode("utf-8"), "&".join([f"{k}={v}" for k, v in sorted(params.items())]).encode("utf-8"), hashlib.sha256).hexdigest()
                    params["signature"] = sig
                    params["apiKey"] = self.api_key
                    req = {"id": f"burst-{cid}", "method": "order.place", "params": params}
                    self.order_ws.send(json.dumps(req))
                    results.append((cid, q_str, p_str, ratio, label, {"status": 200}))
                except Exception as e:
                    results.append((cid, q_str, p_str, ratio, label, {"error": str(e)}))
        return results

    def cancel_order_ws(self, symbol: str, client_order_id: str, timeout: float = 0.08) -> dict:
        if not self.order_ws_ready or not self.order_ws:
            return {"error": "Order WS not ready"}
        try:
            ts = int(time.time() * 1000)
            params = {"symbol": symbol, "origClientOrderId": client_order_id, "timestamp": ts}
            sig = hmac.new(self.api_secret.encode("utf-8"), "&".join([f"{k}={v}" for k, v in sorted(params.items())]).encode("utf-8"), hashlib.sha256).hexdigest()
            params["signature"] = sig
            params["apiKey"] = self.api_key
            req = {"id": f"cancel-{client_order_id}", "method": "order.cancel", "params": params}
            with self.order_send_lock:
                self.order_ws.send(json.dumps(req))
            return {"status": 200}
        except Exception as e:
            return {"error": str(e)}

    def cancel_orders_batch_ws(self, symbol: str, cids: list[str], timeout: float = 0.08) -> list[tuple[str, dict]]:
        results = []
        fired_ms = int(time.time() * 1000)
        with self.order_send_lock:
            for cid in cids:
                try:
                    req_id = f"cancel-shlw-{cid}"
                    self.shallow_cancel_telemetry[cid] = {"fired_local_ms": fired_ms, "binance_engine_ms": 0, "req_id": req_id}
                    ts = int(time.time() * 1000)
                    params = {"symbol": symbol, "origClientOrderId": cid, "timestamp": ts}
                    sig = hmac.new(self.api_secret.encode("utf-8"), "&".join([f"{k}={v}" for k, v in sorted(params.items())]).encode("utf-8"), hashlib.sha256).hexdigest()
                    params["signature"] = sig
                    params["apiKey"] = self.api_key
                    req = {"id": req_id, "method": "order.cancel", "params": params}
                    if self.order_ws:
                        self.order_ws.send(json.dumps(req))
                    results.append((cid, {"status": 200}))
                except Exception as e:
                    results.append((cid, {"error": str(e)}))
        return results

    def cancel_all_orders_ws(self, symbol: str, timeout: float = 0.15) -> dict:
        if not self.order_ws_ready or not self.order_ws:
            return {"error": "Order WS not ready"}
        try:
            ts = int(time.time() * 1000)
            params = {"symbol": symbol, "timestamp": ts}
            sig = hmac.new(self.api_secret.encode("utf-8"), "&".join([f"{k}={v}" for k, v in sorted(params.items())]).encode("utf-8"), hashlib.sha256).hexdigest()
            params["signature"] = sig
            params["apiKey"] = self.api_key
            req = {"id": f"cancel-all-{int(time.time()*1000)}", "method": "openOrders.cancelAll", "params": params}
            with self.order_send_lock:
                self.order_ws.send(json.dumps(req))
            return {"status": 200}
        except Exception as e:
            return {"error": str(e)}
