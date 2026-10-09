import time
import threading
import logging
from datetime import datetime, timezone
from decimal import Decimal
from flask import Flask, render_template, jsonify, request
import ccxt

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# Global Dashboard State
bot_state = {
    "status": "STOPPED",
    "api_configured": False,
    "time_offset": 0,
    "scanned_coins": [],
    "armed_coins": [],
    "live_target": None,
    "clock_drift": 0,
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
            'enableRateLimit': True, # CCXT rate limiter active to avoid IP ban
            'options': {
                'defaultType': 'swap',
                'adjustForTimeDifference': True
            }
        })
        if testnet:
            self.exchange.set_sandbox_mode(True)
        self.running = False

    def sync_time(self):
        try:
            # Using CCXT built-in fetch_time to avoid attribute errors and rate-limits
            server_time = self.exchange.fetch_time()
            local_time = int(time.time() * 1000)
            bot_state["time_offset"] = server_time - local_time
            bot_state["clock_drift"] = abs(bot_state["time_offset"])
            log_message(f"Clock synced. Drift: {bot_state['clock_drift']} ms")
        except Exception as e:
            log_message(f"Time sync error: {e}")

    def get_precision_filtered_qty(self, symbol, price, notional_target):
        market = self.exchange.market(symbol)
        step_size = Decimal(str(market['limits']['amount']['min'] or market['info']['filters'][1]['stepSize']))
        raw_qty = Decimal(str(notional_target)) / Decimal(str(price))
        qty = (raw_qty // step_size) * step_size
        return float(f"{qty:.8f}")

    def scan_negative_funding(self):
        try:
            self.exchange.load_markets()
            # Fetching tickers/premium index safely with rate limit protection
            tickers = self.exchange.public_fapi_v1_get_premiumindex()
            scanned = []
            
            for t in tickers:
                symbol_info = self.exchange.markets_by_id.get(t['symbol'])
                if not symbol_info or not symbol_info['active'] or not symbol_info['linear']:
                    continue
                
                symbol = symbol_info['symbol']
                funding_rate = float(t.get('lastFundingRate', 0))
                next_funding_time = int(t.get('nextFundingTime', 0))
                mark_price = float(t.get('markPrice', 0))
                
                # Threshold: -0.4% or more negative
                if funding_rate <= -0.004:
                    scanned.append({
                        "symbol": symbol,
                        "funding_rate": funding_rate * 100,
                        "next_funding_time": next_funding_time,
                        "mark_price": mark_price,
                        "time_str": datetime.fromtimestamp(next_funding_time/1000, timezone.utc).strftime('%H:%M:%S UTC')
                    })
            
            scanned.sort(key=lambda x: x['next_funding_time'])
            bot_state["scanned_coins"] = scanned
            return scanned
        except Exception as e:
            log_message(f"Error scanning markets: {e}")
            return []

    def execute_arbitrage_trade(self, coin):
        symbol = coin['symbol']
        next_funding = coin['next_funding_time']
        bot_state["live_target"] = coin
        
        log_message(f"Target locked: {symbol} | Funding: {coin['funding_rate']:.3f}%")
        
        while self.running:
            current_time = int(time.time() * 1000) + bot_state["time_offset"]
            
            # Entry at T + 900 ms
            if current_time >= next_funding + 900 and current_time < next_funding + 2000:
                try:
                    leverage_brackets = self.exchange.fetch_leverage_brackets(symbol)
                    max_lev = leverage_brackets[0]['brackets'][0]['initialLeverage'] if leverage_brackets else 20
                    leverage = min(25, max_lev) # Capped at max 25x
                    
                    self.exchange.fapiPrivate_post_leverage({'symbol': symbol.replace('/', ''), 'leverage': leverage})
                    
                    mark_price = float(self.exchange.fetch_ticker(symbol)['last'])
                    target_notional = 1.0 * leverage # $1 Margin * Leverage
                    qty = self.get_precision_filtered_qty(symbol, mark_price, target_notional)
                    
                    log_message(f"[{symbol}] Entering LONG at T+900ms | Qty: {qty} | Lev: {leverage}x")
                    
                    self.exchange.create_market_buy_order(symbol, qty)
                    bot_state["trades"].insert(0, {
                        "symbol": symbol,
                        "type": "ENTRY",
                        "time": datetime.now().strftime('%H:%M:%S.%f')[:-3],
                        "price": mark_price,
                        "qty": qty
                    })
                    
                    # Hard Exit at T + 8000 ms
                    target_exit_time = next_funding + 8000
                    while int(time.time() * 1000) + bot_state["time_offset"] < target_exit_time:
                        time.sleep(0.005)
                        
                    exit_price = float(self.exchange.fetch_ticker(symbol)['last'])
                    self.exchange.create_market_sell_order(symbol, qty)
                    
                    log_message(f"[{symbol}] Hard Exit executed at T+8000ms | Exit Price: {exit_price}")
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

    def bot_loop(self, api_key, secret_key):
        self.running = True
        bot_state["status"] = "RUNNING"
        log_message("Arbitrage Bot started successfully.")
        
        while self.running:
            try:
                self.sync_time()
                scanned = self.scan_negative_funding()
                
                valid_coins = [c for c in scanned if c['funding_rate'] <= -0.4]
                bot_state["armed_coins"] = valid_coins
                
                if valid_coins:
                    top_coin = valid_coins[0]
                    log_message(f"Top priority coin armed: {top_coin['symbol']} ({top_coin['funding_rate']:.2f}%)")
                    threading.Thread(target=self.execute_arbitrage_trade, args=(top_coin,)).start()
                else:
                    log_message("No coins meeting threshold <= -0.4%. Re-scanning in 15 minutes...")
                
                # 15 minutes scan interval with dynamic checks
                for _ in range(900):
                    if not self.running:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                log_message(f"Loop error: {e}")
                time.sleep(15)

bot_instance = None

@app.route('/')
def index():
    return render_template('index.html', state=bot_state)

@app.route('/api/status')
def api_status():
    return jsonify(bot_state)

@app.route('/api/start', methods=['POST'])
def api_start():
    global bot_instance
    data = request.json
    api_key = data.get('api_key')
    secret_key = data.get('secret_key')
    testnet = data.get('testnet', False)
    
    if not api_key or not secret_key:
        return jsonify({"status": "error", "message": "API Key Required!"}), 400
        
    bot_instance = BinanceFundingBot(api_key, secret_key, testnet)
    bot_state["api_configured"] = True
    
    t = threading.Thread(target=bot_instance.bot_loop, args=(api_key, secret_key))
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
