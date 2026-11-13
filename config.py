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

# Margin & Dynamic Leverage Configuration ($7.0 USDT Base Margin @ 20x)
TRADE_MARGIN_USD = 7.0                 # Scanner preflight & UI base margin reference ($7.0 USDT)
LIMIT_ORDER_MARGIN_USD = 7.0           # 4 Shallow limit orders ke liye fixed $7.0 USDT margin each
MAX_LEVERAGE_CAP = 20.0                # 20x hard cap ceiling ($7.0 * 20 = $140.0 USDT > $5.0 min notional)
BINANCE_MIN_NOTIONAL = 5.0             # Binance Futures minimum notional safety threshold

# Scanner Base Eligibility & Danger Ceiling
MIN_FUNDING_RATE = -0.0035             # Minimum -0.35% base threshold
MAX_DANGER_FUNDING_RATE = -0.0160      # Maximum -1.60% hard risk ceiling (Safe Automatic Bound for all cycles)

# Smart Selection Sweet Spot Range (-0.80% to -1.40%)
SWEET_SPOT_MIN = -0.0140               # -1.40% (Updated Priority Bound)
SWEET_SPOT_MAX = -0.0080               # -0.80%

# 4 Shallow Limit Orders (20%, 40%, 60%, 80% of |FR| @ $7.0 USDT each; No DCA Orders)
SHALLOW_LIMIT_RATIOS = (0.20, 0.40, 0.60, 0.80)

# Stop-Loss Configuration (150% of |FR| below Anchor Price, with strict 0.60% Minimum Floor)
SL_FUNDING_MULTIPLIER = 1.50           # Stop-Loss distance = 150% of |FR| below Anchor Price
MIN_SL_DROP_RATIO = 0.0060             # Minimum 0.60% floor below Anchor Price (prevents premature SL on low FR)

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

# Post-Settlement Timeline Offsets & Dynamic Lead Bounds
SHALLOW_CANCEL_AFTER_SETTLE_MS = 265.0 # Target cancellation strike time (T+265ms) on Binance matching engine
SHALLOW_CANCEL_LEAD_MIN_MS = 0.5       # Minimum clamped 1-way network lead
SHALLOW_CANCEL_LEAD_MAX_MS = 25.0      # Maximum clamped 1-way network lead (supports full compensation)
SHALLOW_CANCEL_LEAD_DEFAULT_MS = 1.2   # Safe fallback lead if ping not yet measured

CANCEL_ORDERS_AFTER_SETTLE_MS = 6000          # T+6s: Cancel any remaining open orders
EXIT_AFTER_SETTLE_MS = 7000                   # T+7s: Scheduled 100% Market Exit
EMERGENCY_SWEEP_AFTER_SETTLE_MS = 9000        # T+9s: Hard Emergency Sweep & Safety Net #1
POST_TRADE_AUDIT_AFTER_SETTLE_MS = 15000      # T+15s: Official post-trade reconciliation & PnL audit (Eliminates 0.5s cache lag)
SECOND_EMERGENCY_SWEEP_AFTER_SETTLE_MS = 20000 # T+20s: Hard Emergency Sweep & Safety Net #2

# Long-term Watchdog Delays (in seconds)
WATCHDOG_CHECK_1_SEC = 60              # Settlement ke 1 min baad audit
WATCHDOG_CHECK_2_SEC = 120             # Settlement ke 2 min baad audit

# Web Server Port
DASHBOARD_PORT = int(os.getenv("PORT", 10000))
