from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()

BINANCE_REST_URL = "https://fapi.binance.com"
BINANCE_WS_URL = "wss://fstream.binance.com"
BINANCE_WS_API_URL = "wss://ws-fapi.binance.com/ws-fapi/v1"

# Margin & Dynamic Leverage Configuration ($3.00 USDT Base Margin @ 20x)
TRADE_MARGIN_USD = 3.00                # Scanner preflight & UI base margin reference ($3.00 USDT)
LIMIT_ORDER_MARGIN_USD = 3.00          # Shallow limits (aur Cond 1 DCA) ke liye fixed $3.00 USDT margin
DCA_SPLIT_MARGIN_USD = 1.50            # Cond 2 (<0.90% FR) ke 2 DCA orders ke liye 50-50 split ($1.50 USDT each)
MAX_ARMED_COINS = 2                    # Ek baar mein 2 coins auto-arm honge (Multi-coin active limit)
MAX_LEVERAGE_CAP = 20.0                # 20x hard cap ceiling ($1.50 * 20 = $30.0 USDT > $5.0 min notional)
BINANCE_MIN_NOTIONAL = 5.0             # Binance Futures minimum notional safety threshold

MIN_FUNDING_RATE = -0.0035             # Minimum -0.35% base threshold
MAX_DANGER_FUNDING_RATE = -0.0250      # Maximum -2.50% hard risk ceiling

SWEET_SPOT_MIN = -0.0130               # -1.30%
SWEET_SPOT_MAX = -0.0080               # -0.80%

FR_THRESHOLD_RATIO = 0.0090            # 0.90% boundary

SHALLOW_LIMIT_RATIOS = (0.30, 0.50, 0.70)

GRID_RATIO_DCA_HIGH = 1.30             
SL_BELOW_LIMIT_RATIO_COND_1 = 0.0050   

MIN_GRID_GAP_DCA_1_LOW = 0.0050        
GRID_RATIO_DCA_LOW = 1.30              
L2_BELOW_L1_GAP = 0.0050               
SL_BELOW_LIMIT_RATIO_COND_2 = 0.0050   

SCHEDULED_SCAN_MINUTES = (5, 20, 35, 50)

RESCAN_BEFORE_SETTLE_MS = 20000        
GC_PURGE_BEFORE_SETTLE_MS = 10000      
WS_WARMUP_BEFORE_SETTLE_MS = 8000      
TCP_WARMUP_BEFORE_SETTLE_MS = 4000     
RECHECK_BEFORE_SETTLE_MS = 2500        

# Single-Shot Limit Execution Timeline Offset updated to T-50ms
ENTRY_BEFORE_SETTLE_MS = 50            # T-50ms: Single-shot burst of all limit orders closer to settlement

SHALLOW_CANCEL_FLOOR_MS = 228.0        
SHALLOW_CANCEL_AFTER_SETTLE_MS = 230.0 
SHALLOW_CANCEL_LEAD_MIN_MS = 0.8       
SHALLOW_CANCEL_LEAD_MAX_MS = 2.0       
SHALLOW_CANCEL_LEAD_DEFAULT_MS = 1.2   

CANCEL_ORDERS_AFTER_SETTLE_MS = 6000   
EXIT_AFTER_SETTLE_MS = 7000            
EMERGENCY_SWEEP_AFTER_SETTLE_MS = 9000 

WATCHDOG_CHECK_1_SEC = 60              
WATCHDOG_CHECK_2_SEC = 120             

DASHBOARD_PORT = int(os.getenv("PORT", 10000))
