import asyncio
import json
import os
import time
from dotenv import load_dotenv
import ccxt.async_support as ccxt
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import websockets

# Load environment variables from .env file
load_dotenv()

# --- API CONFIGURATION ---
API_KEY = os.getenv('BINANCE_API_KEY', 'YOUR_BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET', 'YOUR_BINANCE_API_SECRET')

exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
    'options': {'defaultType': 'future', 'adjustForTimeDifference': True}
})

# --- STRATEGY SETTINGS ---
TRADE_MARGIN_USD = 0.45  
LEVERAGE = 20
POSITION_SIZE_USD = TRADE_MARGIN_USD * LEVERAGE

TP_PCT = 0.0006  
SL_PCT = 0.0015  
HOLD_TIME_SECONDS = 10

# --- RULES & COOLDOWNS ---
MAX_CONSECUTIVE_LOSSES = 5
PAUSE_DURATION_SECONDS = 5 * 3600  
SUCCESS_COOLDOWN_SECONDS = 120 

consecutive_losses = 0
trading_paused_until = 0
last_clock_drift = 0.0

COIN_CONFIG = {
    'SOLUSDT': {'symbol': 'SOL/USDT', 'threshold': 10000},
    'DOGEUSDT': {'symbol': 'DOGE/USDT', 'threshold': 5000},
    'XRPUSDT': {'symbol': 'XRP/USDT', 'threshold': 5000},
    'BNBUSDT': {'symbol': 'BNB/USDT', 'threshold': 10000}
}
active_trades = {info['symbol']: False for info in COIN_CONFIG.values()}

# --- DASHBOARD & LOGGING DATA ---
app = FastAPI()
system_logs = []
market_scans = []
active_coin = "NONE"

def add_log(msg):
    t = time.strftime('%H:%M:%S')
    log_msg = f"[{t}] {msg}"
    print(log_msg)
    system_logs.insert(0, log_msg)
    if len(system_logs) > 50:
        system_logs.pop()

def add_scan(msg):
    market_scans.insert(0, msg)
    if len(market_scans) > 3: 
        market_scans.pop()

# --- FASTAPI DASHBOARD ---
@app.get("/")
async def get_dashboard():
    html_content = '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>HFT Bot Dashboard</title>
        <style>
            body { background-color: #16181c; color: #b7bdc6; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 16px; }
            .card { background-color: #20242d; border-radius: 12px; padding: 16px; margin-bottom: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
            .header-card { display: flex; justify-content: space-between; align-items: stretch; gap: 10px; }
            .title-box { flex: 1; }
            .title { color: #fcd535; font-size: 16px; font-weight: bold; margin: 0; line-height: 1.4; }
            .status-badge { background-color: #1e3a29; color: #0ecb81; padding: 10px; border-radius: 8px; font-size: 11px; font-weight: bold; display: flex; flex-direction: column; justify-content: center; text-align: center; }
            .status-badge.paused { background-color: #3a1e1e; color: #f6465d; }
            .label { font-size: 10px; text-transform: uppercase; color: #848e9c; margin-bottom: 6px; font-weight: 600; letter-spacing: 0.5px; }
            .value { font-size: 18px; font-weight: bold; color: #eaecef; }
            .text-green { color: #0ecb81; }
            .text-red { color: #f6465d; }
            .text-yellow { color: #fcd535; }
            .ledger { font-size: 13px; max-height: 250px; overflow-y: auto; line-height: 1.6; }
            .ledger p { margin: 4px 0; border-bottom: 1px solid #2b3139; padding-bottom: 4px; }
            ::-webkit-scrollbar { width: 4px; }
            ::-webkit-scrollbar-thumb { background: #474d57; border-radius: 4px; }
        </style>
        <script>
            async function fetchData() {
                try {
                    const res = await fetch("/status");
                    const data = await res.json();
                    
                    let statusBox = document.getElementById("status-box");
                    if (data.paused_until > 0) {
                        let remaining = Math.round(data.paused_until - (Date.now()/1000));
                        if (remaining > 0) {
                            statusBox.className = "status-badge paused";
                            statusBox.innerHTML = `PAUSED<br>Cooldown:<br>${remaining}s left`;
                        } else {
                            statusBox.className = "status-badge";
                            statusBox.innerHTML = `ARMED<br>Active Scans<br>Running`;
                        }
                    } else {
                        statusBox.className = "status-badge";
                        statusBox.innerHTML = `ARMED<br>Losses: ${data.losses}/5<br>Running`;
                    }
                    
                    document.getElementById("target-coin").innerText = data.active_coin;
                    document.getElementById("drift").innerText = data.drift + " ms";
                    
                    let scansHtml = data.scans.join("<br>");
                    document.getElementById("liquidation").innerHTML = scansHtml || "-";
                    
                    let logsHtml = data.logs.map(log => `<p>${log}</p>`).join("");
                    document.getElementById("ledger").innerHTML = logsHtml;
                } catch(e) {}
            }
            setInterval(fetchData, 1000);
        </script>
    </head>
    <body>
        <div class="card header-card">
            <div class="title-box">
                <h1 class="title">⚡ Binance WS HFT Micro-Scalper<br>($0.45 Margin + Max 20x Leverage)</h1>
            </div>
            <div id="status-box" class="status-badge">LOADING...</div>
        </div>
        <div class="card">
            <div class="label">LIVE TARGET COIN</div>
            <div id="target-coin" class="value text-yellow">NONE</div>
        </div>
        <div class="card">
            <div class="label">LATEST LIQUIDATION SCAN</div>
            <div id="liquidation" class="value text-red" style="font-size: 15px;">-</div>
        </div>
        <div class="card">
            <div class="label">DIRECTION</div>
            <div class="value text-yellow">LONG CAPTURE (10 SECONDS)</div>
        </div>
        <div class="card">
            <div class="label">CLOCK DRIFT</div>
            <div id="drift" class="value text-yellow">0.00 ms</div>
        </div>
        <div class="card">
            <div class="label">📋 Execution Ledger</div>
            <div id="ledger" class="ledger"></div>
        </div>
    </body>
    </html>
    '''
    return HTMLResponse(content=html_content)

@app.get("/status")
async def get_status():
    global last_clock_drift
    return {
        "losses": consecutive_losses,
        "paused_until": trading_paused_until,
        "logs": system_logs,
        "scans": market_scans,
        "active_coin": active_coin,
        "drift": round(last_clock_drift, 2)
    }

# --- TRADING LOGIC ---
async def set_leverage(symbol):
    try:
        await exchange.set_leverage(LEVERAGE, symbol)
        add_log(f"Leverage set to {LEVERAGE}x for {symbol}")
    except Exception as e:
        add_log(f"Leverage error {symbol}: {e}")

def handle_trade_result(is_win):
    global consecutive_losses, trading_paused_until, active_coin
    active_coin = "NONE"
    
    if is_win:
        consecutive_losses = 0
        trading_paused_until = time.time() + SUCCESS_COOLDOWN_SECONDS
        add_log(f"✅ Trade WON (TP Hit)! 2-MIN REST.")
    else:
        consecutive_losses += 1
        add_log(f"❌ Trade LOST. Losses: {consecutive_losses}/{MAX_CONSECUTIVE_LOSSES}")
        if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            trading_paused_until = time.time() + PAUSE_DURATION_SECONDS
            add_log(f"🚨 5 Losses! 5-Hour PAUSE triggered.")

async def execute_trade(symbol, entry_price):
    global trading_paused_until, active_coin
    
    try:
        active_trades[symbol] = True
        active_coin = f"{symbol} (SCALPING)"
        
        raw_qty = POSITION_SIZE_USD / entry_price
        amount = exchange.amount_to_precision(symbol, raw_qty)
        price = exchange.price_to_precision(symbol, entry_price)
        
        add_log(f"🟢 ENTRY: {symbol} at {price} (Qty: {amount})")
        
        entry_order = await exchange.create_order(
            symbol=symbol, type='limit', side='buy',
            amount=float(amount), price=float(price),
            params={'timeInForce': 'GTX'}
        )
        
        await asyncio.sleep(1)
        check_order = await exchange.fetch_order(entry_order['id'], symbol)
        
        if check_order['status'] == 'closed':
            add_log(f"[{symbol}] Filled! Placing OCO limits.")
            
            tp_price = exchange.price_to_precision(symbol, float(price) * (1 + TP_PCT))
            sl_price = exchange.price_to_precision(symbol, float(price) * (1 - SL_PCT))
            
            tp_order = await exchange.create_order(
                symbol=symbol, type='limit', side='sell',
                amount=float(amount), price=float(tp_price), params={'timeInForce': 'GTX'}
            )
            
            await exchange.create_order(
                symbol=symbol, type='stop_market', side='sell',
                amount=float(amount), params={'stopPrice': float(sl_price), 'reduceOnly': True}
            )
            
            is_win = False
            trade_closed = False
            
            for _ in range(HOLD_TIME_SECONDS):
                await asyncio.sleep(1)
                pos = await exchange.fetch_position(symbol)
                
                if float(pos['info']['positionAmt']) == 0:
                    trade_closed = True
                    check_tp = await exchange.fetch_order(tp_order['id'], symbol)
                    if check_tp['status'] == 'closed':
                        is_win = True
                    break
            
            if not trade_closed:
                add_log(f"🔴 [{symbol}] 10s Timeout! Force Market Exit.")
                await exchange.cancel_all_orders(symbol)
                
                pos = await exchange.fetch_position(symbol)
                current_amt = float(pos['info']['positionAmt'])
                if current_amt != 0:
                    side = 'sell' if current_amt > 0 else 'buy'
                    await exchange.create_order(
                        symbol=symbol, type='market', side=side, 
                        amount=abs(current_amt), params={'reduceOnly': True}
                    )
                is_win = False 
            
            await exchange.cancel_all_orders(symbol)
            handle_trade_result(is_win)
            
        else:
            add_log(f"[{symbol}] Order missed. Canceling.")
            await exchange.cancel_order(entry_order['id'], symbol)
            active_coin = "NONE"

    except Exception as e:
        add_log(f"[{symbol}] Trade Crash Prevented: {e}")
        active_coin = "NONE"
    finally:
        active_trades[symbol] = False

async def monitor_multi_liquidations():
    streams = "/".join([f"{coin.lower()}@forceOrder" for coin in COIN_CONFIG.keys()])
    ws_url = f"wss://fstream.binance.com/stream?streams={streams}"
    
    while True: 
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                add_log("✅ WS Connected. Armed & Scanning...")
                
                while True:
                    response = await ws.recv()
                    
                    if time.time() < trading_paused_until:
                        continue 
                        
                    payload = json.loads(response)
                    if 'data' in payload:
                        order_data = payload['data']['o']
                        raw_symbol = order_data['s']
                        
                        if raw_symbol in COIN_CONFIG:
                            info = COIN_CONFIG[raw_symbol]
                            symbol = info['symbol']
                            
                            side = order_data['S'] 
                            price = float(order_data['p'])
                            qty = float(order_data['q'])
                            notional = price * qty
                            
                            if side == 'SELL':
                                scan_time = time.strftime('%H:%M:%S')
                                add_scan(f"{scan_time} | {raw_symbol} Liq: ${notional:.0f} @ {price}")
                            
                            if side == 'SELL' and notional >= info['threshold'] and not active_trades[symbol]:
                                add_log(f"⚡ MASSIVE DROP DETECTED: {raw_symbol}")
                                asyncio.create_task(execute_trade(symbol, price))
                                
        except Exception as e:
            add_log(f"WS Error. Reconnecting in 3s...")
            await asyncio.sleep(3)

async def update_clock_drift():
    global last_clock_drift
    while True:
        try:
            st = time.time() * 1000
            res = await exchange.fetch_time()
            et = time.time() * 1000
            latency = et - st
            server_time = res
            last_clock_drift = (et - server_time) - (latency / 2)
            await asyncio.sleep(30) 
        except:
            await asyncio.sleep(30)

async def start_trading_bot():
    add_log("Syncing clock and markets...")
    await exchange.load_markets()
    asyncio.create_task(update_clock_drift())
    
    for config in COIN_CONFIG.values():
        await set_leverage(config['symbol'])
        
    asyncio.create_task(monitor_multi_liquidations())

@app.on_event("startup")
async def startup_event():
    await start_trading_bot()

@app.on_event("shutdown")
async def shutdown_event():
    await exchange.close()
    add_log("Exchange connection closed gracefully.")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000, log_level="error")
