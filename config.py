from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

# Absolute path se .env load karein taaki systemd execution me API key miss na ho
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Binance Futures API Credentials
API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()

# Endpoints (Tokyo Gateway Direct Routing)
BINANCE_REST_URL = "https://fapi.binance.com"
BINANCE_WS_URL = "wss://fstream.binance.com"
BINANCE_WS_API_URL = "wss://ws-fapi.binance.com/ws-fapi/v1"

# Margin & Dynamic Leverage Configuration ($7.00 USDT Base Margin @ 20x)
TRADE_MARGIN_USD = 7.0                 # Scanner preflight & UI base margin reference ($7.00 USDT)
LIMIT_ORDER_MARGIN_USD = 7.0           # Shallow limits (aur Cond 1 DCA) ke liye fixed $7.00 USDT margin
DCA_SPLIT_MARGIN_USD = 3.5             # Cond 2 (<0.90% FR) ke 2 DCA orders ke liye 50-50 split ($3.50 USDT each)
MAX_LEVERAGE_CAP = 20.0                # 20x hard cap ceiling ($3.50 * 20 = $70.0 USDT > $5.0 min notional)
BINANCE_MIN_NOTIONAL = 5.0             # Binance Futures minimum notional safety threshold

# Scanner Base Eligibility & Danger Ceiling
MIN_FUNDING_RATE = -0.0035             # Minimum -0.35% base threshold
MAX_DANGER_FUNDING_RATE = -0.0160      # Maximum -1.60% hard risk ceiling (Safe Automatic Bound for all cycles)

# Smart Selection Sweet Spot Range (-0.80% to -1.40%)
SWEET_SPOT_MIN = -0.0140               # -1.40% (Updated Priority Bound)
SWEET_SPOT_MAX = -0.0080               # -0.80%

# Funding Rate Threshold for Condition 1 vs Condition 2
FR_THRESHOLD_RATIO = 0.0090            # 0.90% boundary (|FR| >= 0.90% = Cond 1, < 0.90% = Cond 2)

# 3 Shallow Limit Orders (30%, 50%, 70% of |FR| @ $7.00 USDT each)
SHALLOW_LIMIT_RATIOS = (0.30, 0.50, 0.70)

# Condition 1: High Negative Funding Rate (|FR| >= 0.90%)
# 3 Shallow Limits ($7.00 each) + 1 DCA Limit Order at 130% of FR ($7.00), SL 0.50% below DCA Limit #1
GRID_RATIO_DCA_HIGH = 1.30             # DCA Limit #1: 130% of funding rate
SL_BELOW_LIMIT_RATIO_COND_1 = 0.0050   # SL: Exactly 0.50% below DCA Limit #1 Price

# Condition 2: Low/Normal Negative Funding Rate (|FR| < 0.90%)
# 3 Shallow Limits ($7.00 each) + 2 DCA Limit Orders ($3.50 each: L1 at 130% FR with 0.50% floor, L2 = L1 - 0.50%), SL 0.50% below L2
MIN_GRID_GAP_DCA_1_LOW = 0.0050        # DCA Limit #1: Minimum 0.50% fixed floor drop (agar 130% of FR < 0.50%)
GRID_RATIO_DCA_LOW = 1.30              # DCA Limit #1: 130% of funding rate
L2_BELOW_L1_GAP = 0.0050               # DCA Limit #2: Exactly 0.50% below DCA Limit #1 Price
SL_BELOW_LIMIT_RATIO_COND_2 = 0.0050   # SL: Exactly 0.50% below DCA Limit #2 Price

# Standard Reference Timing for Dashboard Tagging (IST)
STANDARD_SETTLEMENT_HOURS_IST = (1, 5, 9, 13, 17, 21)
STANDARD_SETTLEMENT_MINUTE_IST = 30

# Scheduled Periodic Scan Pillars (Ghadi ke har ghante ke 4 fixed minutes)
SCHEDULED_SCAN_MINUTES = (5, 20, 35, 50)

# Timeline Offsets (Milliseconds before Settlement T)
RESCAN_BEFORE_SETTLE_MS = 20000        # T-20s: Fresh market coin lock
GC_PURGE_BEFORE_SETTLE_MS = 10000      # T-10s: Purge and pause GC (Dedicated CPU)
WS_WARMUP_BEFORE_SETTLE_MS = 8000      # T-8s: Fresh TLS Handshake for Order WebSocket
TCP_WARMUP_BEFORE_SETTLE_MS = 4000     # T-4s: Order WebSocket Backup Ping Warm-Up
RECHECK_BEFORE_SETTLE_MS = 2500        # T-2.5s (2500ms): Live rate recheck & Backup Snapshot Anchor
MULTI_PING_BEFORE_SETTLE_MS = 500      # T-500ms: 3-Ping Rapid WS Burst for Fresh Min-RTT Lock

# Single-Shot Limit Execution Target Offset on Binance Engine
ENTRY_BEFORE_SETTLE_MS = 50            # T-50ms target arrival on Binance (compensated by 1-way lead)

# Post-Settlement Timeline Offsets & Dynamic Lead Bounds (228ms Floor Lock Removed)
SHALLOW_CANCEL_AFTER_SETTLE_MS = 230.0 # Target cancellation strike time (T+230ms) on Binance matching engine
SHALLOW_CANCEL_LEAD_MIN_MS = 0.5       # Minimum clamped 1-way network lead
SHALLOW_CANCEL_LEAD_MAX_MS = 25.0      # Maximum clamped 1-way network lead (supports full compensation)
SHALLOW_CANCEL_LEAD_DEFAULT_MS = 1.2   # Safe fallback lead if ping not yet measured

CANCEL_ORDERS_AFTER_SETTLE_MS = 6000          # T+6s: Cancel remaining unfilled limit orders (DCA)
EXIT_AFTER_SETTLE_MS = 7000                   # T+7s: Scheduled 100% Market Exit
EMERGENCY_SWEEP_AFTER_SETTLE_MS = 9000        # T+9s: Hard Emergency Sweep & Safety Net #1
POST_TRADE_AUDIT_AFTER_SETTLE_MS = 15000      # T+15s: Official post-trade reconciliation & PnL audit (Eliminates 0.5s cache lag)
SECOND_EMERGENCY_SWEEP_AFTER_SETTLE_MS = 20000 # T+20s: Hard Emergency Sweep & Safety Net #2

# Long-term Watchdog Delays (in seconds)
WATCHDOG_CHECK_1_SEC = 60              # Settlement ke 1 min baad audit
WATCHDOG_CHECK_2_SEC = 120             # Settlement ke 2 min baad audit

# Web Server Port
DASHBOARD_PORT = int(os.getenv("PORT", 10000))
