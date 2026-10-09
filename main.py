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
            'options': {
                'defaultType': 'swap', # USD-M Futures
                'adjustForTimeDifference': True
            }
        })
        if testnet:
            self.exchange.set_sandbox_mode(True)
        self.running = False

    def sync_time(self):
        try:
            server_time = self.exchange.public_fapi_v1_get_time()['serverTime']
            local_time = int(time.time() * 1000)
            bot_state["time_offset"] = server_time - local_time
            log_message(f"Binance Clock Synced. Offset: {bot_state['time_offset']} ms")
        except Exception as e:
            log_message(f"Time sync error: {e}")

    def get_precision_filtered_qty(self, symbol, price, notional_target):
        """Exchange ke stepSize ke hisab se quantity round off karna (.090788 jese prices ke liye)"""
        market = self.exchange.market(symbol)
        step_size = Decimal(str(market['limits']['amount']['min'] or market['info']['filters'][1]['stepSize']))
        
        raw_qty = Decimal(str(notional_target)) / Decimal(str(price))
        qty = (raw_qty // step_size) * step_size
        return float(f"{qty:.8f}")

    def scan_negative_funding(self):
        try:
            self.exchange.load_markets()
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
                
                # Threshold: -0.4% or more negative (<= -0.004)
                if funding_rate <= -0.004:
                    scanned.append({
                        "symbol": symbol,
                        "funding_rate": funding_rate * 100, # in %
                        "next_funding_time": next_funding_time,
                        "mark_price": mark_price,
                        "time_str": datetime.fromtimestamp(next_funding_time/1000, timezone.utc).strftime('%H:%M:%S UTC')
                    })
            
            # Sort by nearest funding time (Priority: Jo opportunity sabse pehle aayegi, use pehle lenge)
            scanned.sort(key=lambda x: x['next_funding_time'])
            bot_state["scanned_coins"] = scanned
            return scanned
        except Exception as e:
            log_message(f"Error scanning markets: {e}")
            return []

    def execute_arbitrage_trade(self, coin):
        symbol = coin['symbol']
        next_funding = coin['next_funding_time']
        
        log_message(f"Target locked for {symbol} | Funding: {coin['funding_rate']:.3f}% | Next Funding at {coin['time_str']}")
        
        while self.running:
            current_time = int(time.time() * 1000) + bot_state["time_offset"]
            
            # Precise entry at T + 900 ms after funding exact timestamp
            if current_time >= next_funding + 900 and current_time < next_funding + 2000:
                try:
                    # 1. Set Leverage (Max 25x or coin max, whichever is lower)
                    leverage_brackets = self.exchange.fetch_leverage_brackets(symbol)
                    max_lev = leverage_brackets[0]['brackets'][0]['initialLeverage'] if leverage_brackets else 20
                    leverage = min(25, max_lev)
                    
                    self.exchange.fapiPrivate_post_leverage({'symbol': symbol.replace('/', ''), 'leverage': leverage})
                    
                    # 2. Calculate Qty for $1 Margin with leverage
                    mark_price = float(self.exchange.fetch_ticker(symbol)['last'])
                    target_notional = 1.0 * leverage # $1 margin * leverage
                    qty = self.get_precision_filtered_qty(symbol, mark_price, target_notional)
                    
                    log_message(f"[{symbol}] Entering LONG at T+900ms | Qty: {qty} | Leverage: {leverage}x")
                    
                    # 3. Entry Order (Market LONG)
                    self.exchange.create_market_buy_order(symbol, qty)
                    bot_state["trades"].insert(0, {
                        "symbol": symbol,
                        "type": "ENTRY",
                        "time": datetime.now().strftime('%H:%M:%S.%f')[:-3],
                        "price": mark_price,
                        "qty": qty
                    })
                    
                    # 4. Wait for Hard Exit at T + 8000 ms (8 seconds after funding timestamp)
                    target_exit_time = next_funding + 8000
                    while int(time.time() * 1000) + bot_state["time_offset"] < target_exit_time:
                        time.sleep(0.005)
                        
                    # 5. Hard Exit Order (Market SELL to close position)
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
                    break
                except Exception as e:
                    log_message(f"Trade execution error for {symbol}: {e}")
                    break
                    
            time.sleep(0.005)

    def bot_loop(self, api_key, secret_key):
        self.running = True
        bot_state["status"] = "RUNNING"
        log_message("Arbitrage Bot started successfully.")
        
        while self.running:
            try:
                self.sync_time()
                
                # 1. Scan market every 15 minutes and dynamically re-arm/re-check
                scanned = self.scan_negative_funding()
                
                # Filter valid coins meeting threshold <= -0.4%
                valid_coins = [c for c in scanned if c['funding_rate'] <= -0.4]
                bot_state["armed_coins"] = valid_coins
                
                if valid_coins:
                    log_message(f"Found {len(valid_coins)} negative funding coins. Top priority: {valid_coins[0]['symbol']} ({valid_coins[0]['funding_rate']:.2f}%)")
                    top_coin = valid_coins[0]
                    
                    # Spawn thread to monitor and execute trade precisely at funding time
                    threading.Thread(target=self.execute_arbitrage_trade, args=(top_coin,)).start()
                else:
                    log_message("No coins meeting threshold <= -0.4%. Re-scanning in 15 minutes...")
                
                # Dynamic 15-minute loop with continuous re-verification
                for _ in range(900): # 900 seconds = 15 minutes
                    if not self.running:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                log_message(f"Loop error: {e}")
                time.sleep(10)

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
        return jsonify({"status": "error", "message": "API Key and Secret Required!"}), 400
        
    bot_instance = BinanceFundingBot(api_key, secret_key, testnet)
    bot_state["api_configured"] = True
    
    t = threading.Thread(target=bot_instance.bot_loop, args=(api_key, secret_key))
    t.daemon = True
    t.start()
    
    return jsonify({"status": "success", "message": "Bot started successfully!"})

@app.route('/api/stop', methods=['POST'])
def api_stop():
    global bot_instance
    if bot_instance:
        bot_instance.running = False
    bot_state["status"] = "STOPPED"
    log_message("Bot stopped by user.")
    return jsonify({"status": "success", "message": "Bot stopped."})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
