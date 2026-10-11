from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()

# Binance Futures API Credentials
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# Endpoints (Tokyo Gateway Direct Routing)
BINANCE_REST_URL = "https://fapi.binance.com"
BINANCE_WS_URL = "wss://fstream.binance.com"
BINANCE_WS_API_URL = "wss://ws-fapi.binance.com/ws-fapi/v1"

# Margin & Leverage Configuration
TRADE_MARGIN_USD = 2.0
MAX_LEVERAGE_CAP = 25.0
MIN_FUNDING_RATE = -0.004

# Binance USDT-M Futures Minimum Notional Filter
BINANCE_MIN_NOTIONAL = 5.0

# 24x7 Periodic Market Re-Scan Interval
PERIODIC_SCAN_INTERVAL_MIN = 15

# Realignment: Entry & Safety Timeline (Milliseconds)
RESCAN_BEFORE_SETTLE_MS = 15000
GC_PURGE_BEFORE_SETTLE_MS = 5000
RECHECK_BEFORE_SETTLE_MS = 2000
ENTRY_AFTER_SETTLE_MS = 900
EXIT_AFTER_SETTLE_MS = 8000
EMERGENCY_SWEEP_AFTER_SETTLE_MS = 10000

WATCHDOG_CHECK_1_SEC = 60
WATCHDOG_CHECK_2_SEC = 120

DASHBOARD_PORT = int(os.getenv("PORT", 10000))
