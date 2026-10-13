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

# Live tracking stats for each coin to show on dashboard
coin_live_stats = {
    'SOLUSDT': {'time': '-', 'notional': 0, 'price': 0.0, 'status': 'CONNECTING...'},
    'DOGEUSDT': {'time': '-', 'notional': 0, 'price': 0.0, 'status': 'CONNECTING...'},
    'XRPUSDT': {'time': '-', 'notional': 0, 'price': 0.0, 'status': 'CONNECTING...'},
    'BNBUSDT': {'time': '-', 'notional': 0, 'price': 0.0, 'status': 'CONNECTING...'},
}

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
    if len(market_scans) > 6: 
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
            body { background-color: #16181c; color: #b7bdc6; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 12px; }
            .card { background-color: #20242d; border-radius: 10px; padding: 12px; margin-bottom: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
            .header-card { display: flex; justify-content: space-between; align-items: stretch; gap: 10px; }
            .title-box { flex: 1; }
            .title { color: #fcd535; font-size: 15px; font-weight: bold; margin: 0; line-height: 1.4; }
            .status-badge { background-color: #1e3a29; color: #0ecb81; padding: 8px; border-radius: 8px; font-size: 11px; font-weight: bold; display: flex; flex-direction: column; justify-content: center; text-align: center; min-width: 80px; }
            .status-badge.paused { background-color: #3a1e1e; color: #f6465d; }
            .label { font-size: 10px; text-transform: uppercase; color: #848e9c; margin-bottom: 4px; font-weight: 600; letter-spacing: 0.5px; }
            .value { font-size: 16px; font-weight: bold; color: #eaecef; }
            .text-green { color: #0ecb81; }
            .text-red { color: #f6465d; }
            .text-yellow { color: #fcd535; }
            .coins-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
            .coin-box { background-color: #16181c; border-radius: 8px; padding: 8px; border: 1px solid #2b3139; }
            .coin-name { font-size: 12px; font-weight: bold; color: #fcd535; display: flex; justify-content: space-between; }
            .coin-val { font-size: 14px; color: #eaecef; margin-top: 3px; font-weight: bold; }
            .coin-status { font-size: 10px; margin-top: 3px; font-weight: bold; }
            .ledger { font-size: 12px; max-height: 200px; overflow-y: auto; line-height: 1.5; }
            .ledger p { margin: 3px 0; border-bottom: 1px solid #2b3139; padding-bottom: 3px; }
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
                            statusBox.innerHTML = `PAUSED<br>${remaining}s left`;
                        } else {
                            statusBox.className = "status-badge";
                            statusBox.innerHTML = `ARMED<br>Running`;
                        }
                    } else {
                        statusBox.className = "status-badge";
                        statusBox.innerHTML = `ARMED<br>Losses: ${data.losses}/5`;
                    }
                    
                    document.getElementById("target-coin").innerText = data.active_coin;
                    document.getElementById("drift").innerText = data.drift + " ms";
                    
                    let coinsHtml = "";
                    for (let [ticker, info] of Object.entries(data.coin_stats)) {
                        let colorClass = info.status.includes("TRIGGERED") ? "text-green" : (info.status.includes("SKIPPED") || info.status.includes("PAUSED") ? "text-red" : "text-yellow");
                        coinsHtml += `
                            <div class="coin-box">
                                <div class="coin-name"><span>${ticker}</span> <span>${info.time}</span></div>
                                <div class="coin-val">$${info.price}</div>
                                <div class="coin-status ${colorClass}">${info.status}</div>
                            </div>
                        `;
                    }
                    document.getElementById("coins-grid").innerHTML = coinsHtml;
                    
                    let scansHtml = data.scans.map(s => `<p>${s}</p>`).join("");
                    document.getElementById("liquidation-feed").innerHTML = scansHtml || "-";
                    
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
                <h1 class="title">⚡ Binance WS HFT Micro-Scalper<br>($0.45 Margin + 20x Leverage)</h1>
            </div>
            <div id="status-box" class="status-badge">LOADING...</div>
        </div>
        
        <div class="card">
            <div class="label">LIVE TARGET COIN (ACTIVE TRADE)</div>
            <div id="target-coin" class="value text-yellow">NONE</div>
        </div>

        <div class="card">
            <div class="label">🪙 ALL COINS LIVE PRICES & LIQUIDATIONS</div>
            <div id="coins-grid" class="coins-grid">
                <!-- Dynamically filled -->
            </div>
        </div>

        <div class="card">
            <div class="label">📊 RECENT LIQUIDATION EVENTS & ACTIONS</div>
            <div id="liquidation-feed" class="ledger text-red" style="font-size: 12px; max-height: 120px; overflow-y: auto;">-</div>
        </div>

        <div class="card">
            <div class="label">CLOCK DRIFT</div>
            <div id="drift" class="value text-yellow">0.00 ms</div>
        </div>

        <div class="card">
            <div class="label">📋 Execution Ledger & Logs</div>
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
        "coin_stats": coin_live_stats,
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
    global coin_live_stats
    
    streams_list = []
    for coin in COIN_CONFIG.keys():
        c_lower = coin.lower()
        streams_list.append(f"{c_lower}@forceOrder")
        streams_list.append(f"{c_lower}@miniTicker")
        
    streams = "/".join(streams_list)
    ws_url = f"wss://fstream.binance.com/stream?streams={streams}"
    
    while True: 
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                add_log("✅ WS Connected. Scanning Prices & Liquidations Live...")
                
                while True:
                    response = await ws.recv()
                    payload = json.loads(response)
                    
                    if 'data' in payload:
                        data = payload['data']
                        event_type = data.get('e')
                        
                        # 1. Handle Mini-Ticker (Real-time live prices safely separated)
                        if event_type == '24hrMiniTicker':
                            raw_symbol = data['s'].upper()
                            if raw_symbol in coin_live_stats:
                                current_price = float(data['c'])
                                coin_live_stats[raw_symbol]['price'] = current_price
                                coin_live_stats[raw_symbol]['time'] = time.strftime('%H:%M:%S')
                                if coin_live_stats[raw_symbol]['status'] in ['CONNECTING...', 'WAITING']:
                                    coin_live_stats[raw_symbol]['status'] = 'SCANNING (Live)'

                        # 2. Handle Force Order (Liquidations safely separated)
                        elif event_type == 'forceOrder' and 'o' in data:
                            order_data = data['o']
                            raw_symbol = order_data['s'].upper()
                            
                            if raw_symbol in COIN_CONFIG:
                                info = COIN_CONFIG[raw_symbol]
                                symbol = info['symbol']
                                threshold = info['threshold']
                                
                                side = order_data['S'] 
                                price = float(order_data['p'])
                                qty = float(order_data['q'])
                                notional = price * qty
                                scan_time = time.strftime('%H:%M:%S')
                                
                                is_paused = time.time() < trading_paused_until
                                is_active = active_trades.get(symbol, False)
                                
                                if side == 'SELL':
                                    if is_paused:
                                        status_desc = "PAUSED (Cooldown)"
                                    elif is_active:
                                        status_desc = "SKIPPED (In Trade)"
                                    elif notional >= threshold:
                                        status_desc = "⚡ TRIGGERED TRADE"
                                        asyncio.create_task(execute_trade(symbol, price))
                                    else:
                                        status_desc = f"SKIPPED (< ${threshold})"
                                else:
                                    status_desc = f"BUY LIQ (${notional:.0f})"

                                coin_live_stats[raw_symbol] = {
                                    'time': scan_time,
                                    'notional': round(notional, 2),
                                    'price': price,
                                    'status': status_desc
                                }
                                
                                add_scan(f"[{scan_time}] {raw_symbol} ({side}) | ${notional:.0f} @ {price} ➔ {status_desc}")
                                
        except Exception as e:
            add_log(f"WS Error: {e}. Reconnecting in 3s...")
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
