import time
import threading
import logging
import os
import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from flask import Flask, render_template, jsonify, request
import ccxt
import websockets

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

bot_state = {
    "status": "STOPPED",
    "api_configured": False,
    "time_offset": 0,
    "clock_drift": 0,
    "scanned_coins": [],
    "armed_coins": [],
    "live_target": None,
    "entry_time_str": "--",
    "exit_time_str": "--",
    "logs": [],
    "trades": []
}

def log_message(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted = f"[{timestamp}] {msg}"
    logging.info(msg)
    bot_state["logs"].insert(0, formatted)
    if len(bot_state["logs"]) > 100:
        bot_state["logs"].pop()

class BinanceFundingBot:
    def __init__(self, api_key, secret_key, testnet=False):
        self.exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {'defaultType': 'swap', 'adjustForTimeDifference': True}
        })
        if testnet:
            self.exchange.set_sandbox_mode(True)
        self.running = False

    def sync_time(self):
        try:
            server_time = self.exchange.fetch_time()
            local_time = int(time.time() * 1000)
            bot_state["time_offset"] = server_time - local_time
            bot_state["clock_drift"] = round(abs(bot_state["time_offset"]), 1)
        except Exception as e:
            log_message(f"Time sync error: {e}")

    def get_precision_filtered_qty(self, symbol, price, notional_target):
        market = self.exchange.market(symbol)
        step_size = Decimal(str(market['limits']['amount']['min'] or market['info']['filters'][1]['stepSize']))
        raw_qty = Decimal(str(notional_target)) / Decimal(str(price))
        qty = (raw_qty // step_size) * step_size
        return float(f"{qty:.8f}")

    def execute_arbitrage_trade(self, coin):
        symbol = coin['symbol']
        next_funding = coin['next_funding_time']
        bot_state["live_target"] = coin
        
        entry_ts = next_funding + 900
        exit_ts = next_funding + 8000
        bot_state["entry_time_str"] = datetime.fromtimestamp(entry_ts/1000, timezone.utc).strftime('%H:%M:%S.900 UTC')
        bot_state["exit_time_str"] = datetime.fromtimestamp(exit_ts/1000, timezone.utc).strftime('%H:%M:%S.000 UTC')
        
        log_message(f"Target Locked: {symbol} | Funding: {coin['funding_rate']:.3f}% | Next: {coin['time_str']}")
        
        while self.running:
            current_time = int(time.time() * 1000) + bot_state["time_offset"]
            
            # Precise Entry at T + 900ms
            if current_time >= next_funding + 900 and current_time < next_funding + 2000:
                try:
                    leverage_brackets = self.exchange.fetch_leverage_brackets(symbol)
                    max_lev = leverage_brackets[0]['brackets'][0]['initialLeverage'] if leverage_brackets else 20
                    leverage = min(25, max_lev) # Max 25x capped
                    
                    self.exchange.fapiPrivate_post_leverage({'symbol': symbol.replace('/', ''), 'leverage': leverage})
                    
                    mark_price = float(self.exchange.fetch_ticker(symbol)['last'])
                    target_notional = 1.0 * leverage # $1 Margin * Leverage
                    qty = self.get_precision_filtered_qty(symbol, mark_price, target_notional)
                    
                    log_message(f"[{symbol}] ENTRY at T+900ms | Qty: {qty} | Lev: {leverage}x | Price: {mark_price}")
                    
                    self.exchange.create_market_buy_order(symbol, qty)
                    bot_state["trades"].insert(0, {
                        "symbol": symbol,
                        "type": "ENTRY",
                        "time": datetime.now().strftime('%H:%M:%S.%f')[:-3],
                        "price": mark_price,
                        "qty": qty
                    })
                    
                    # Hard Exit at T + 8000ms
                    target_exit_time = next_funding + 8000
                    while int(time.time() * 1000) + bot_state["time_offset"] < target_exit_time:
                        time.sleep(0.005)
                        
                    exit_price = float(self.exchange.fetch_ticker(symbol)['last'])
                    self.exchange.create_market_sell_order(symbol, qty)
                    
                    log_message(f"[{symbol}] HARD EXIT at T+8000ms | Exit Price: {exit_price}")
                    bot_state["trades"].insert(0, {
                        "symbol": symbol,
                        "type": "EXIT",
                        "time": datetime.now().strftime('%H:%M:%S.%f')[:-3],
                        "price": exit_price,
                        "qty": qty
                    })
                    bot_state["live_target"] = None
                    break
                except Exception as e:
                    log_message(f"Trade execution error for {symbol}: {e}")
                    bot_state["live_target"] = None
                    break
                    
            time.sleep(0.005)

    async def ws_funding_scanner(self):
        """Binance WebSocket Continuous Stream (Zero Rate Limit / No IP Ban)"""
        uri = "wss://fstream.binance.com/ws/!continuousKline_1h@arr" # or fallback to premium index stream
        # Using continuous futures mark price stream which includes funding rate
        uri_mark = "wss://fstream.binance.com/ws/!markPrice@arr"
        
        log_message("Connecting to Binance WebSocket Stream for real-time scanning...")
        while self.running:
            try:
                async with websockets.connect(uri_mark) as websocket:
                    log_message("WebSocket connected successfully!")
                    while self.running:
                        message = await websocket.recv()
                        data = json.loads(message)
                        
                        scanned = []
                        for item in data:
                            symbol_raw = item.get('s', '')
                            # Convert BTCUSDT to BTC/USDT format for CCXT compatibility
                            if not symbol_raw.endswith('USDT'):
                                continue
                            symbol = symbol_raw[:-4] + '/USDT'
                            
                            funding_rate = float(item.get('r', 0)) # funding rate
                            next_funding_time = int(item.get('T', 0)) # next funding time
                            mark_price = float(item.get('p', 0))
                            rate_pct = funding_rate * 100
                            
                            if rate_pct <= -0.4:
                                scanned.append({
                                    "symbol": symbol,
                                    "funding_rate": rate_pct,
                                    "next_funding_time": next_funding_time,
                                    "mark_price": mark_price,
                                    "time_str": datetime.fromtimestamp(next_funding_time/1000, timezone.utc).strftime('%H:%M:%S UTC')
                                })
                        
                        scanned.sort(key=lambda x: x['next_funding_time'])
                        bot_state["scanned_coins"] = scanned
                        bot_state["armed_coins"] = scanned
                        
                        if scanned:
                            top_coin = scanned[0]
                            # Spawn trade thread if not already running for this target
                            if not bot_state["live_target"] or bot_state["live_target"]["symbol"] != top_coin["symbol"]:
                                log_message(f"WebSocket Armed Top Coin: {top_coin['symbol']} ({top_coin['funding_rate']:.3f}%)")
                                threading.Thread(target=self.execute_arbitrage_trade, args=(top_coin,)).start()
                        
                        await asyncio.sleep(1)
            except Exception as e:
                log_message(f"WebSocket disconnected: {e}. Reconnecting in 5 seconds...")
                await asyncio.sleep(5)

    def start_websocket_loop(self):
        self.sync_time()
        asyncio.run(self.ws_funding_scanner())

    def bot_loop(self):
        self.running = True
        bot_state["status"] = "RUNNING"
        log_message("Binance Dynamic WSS Funding Engine started.")
        self.start_websocket_loop()

bot_instance = None

def auto_start_from_env():
    global bot_instance
    api_key = os.environ.get('BINANCE_API_KEY', '')
    secret_key = os.environ.get('BINANCE_SECRET_KEY', '')
    if api_key and secret_key:
        bot_state["api_configured"] = True
        bot_instance = BinanceFundingBot(api_key, secret_key)
        t = threading.Thread(target=bot_instance.bot_loop)
        t.daemon = True
        t.start()
        log_message("Auto-started bot via Environment Variables.")

auto_start_from_env()

@app.route('/')
def index():
    return render_template('index.html', state=bot_state)

@app.route('/api/status')
def api_status():
    return jsonify(bot_state)

@app.route('/api/start', methods=['POST'])
def api_start():
    global bot_instance
    data = request.json or {}
    api_key = data.get('api_key') or os.environ.get('BINANCE_API_KEY')
    secret_key = data.get('secret_key') or os.environ.get('BINANCE_SECRET_KEY')
    
    if not api_key or not secret_key:
        return jsonify({"status": "error", "message": "API Key required!"}), 400
        
    bot_instance = BinanceFundingBot(api_key, secret_key)
    bot_state["api_configured"] = True
    
    if bot_state["status"] != "RUNNING":
        t = threading.Thread(target=bot_instance.bot_loop)
        t.daemon = True
        t.start()
    
    return jsonify({"status": "success", "message": "Bot started!"})

@app.route('/api/stop', methods=['POST'])
def api_stop():
    global bot_instance
    if bot_instance:
        bot_instance.running = False
    bot_state["status"] = "STOPPED"
    log_message("Bot stopped.")
    return jsonify({"status": "success", "message": "Bot stopped."})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
