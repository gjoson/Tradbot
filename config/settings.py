"""
Configuration and settings for Tradbot trading system.
All sensitive values are loaded from environment variables.
"""

import os
from typing import Dict
from datetime import time
from pytz import timezone

# ============================================================================
# ENVIRONMENT-BASED CONFIGURATION (No Hardcoding)
# ============================================================================

BROKER_USER = os.getenv('FLATTRADE_USER', '')
BROKER_PASSWORD = os.getenv('FLATTRADE_PASSWORD', '')
BROKER_API_KEY = os.getenv('FLATTRADE_API_KEY', '')
BROKER_API_SECRET = os.getenv('FLATTRADE_API_SECRET', '')  # for PI Connect

LOG_DB_PATH = os.getenv('LOG_DB_PATH', '/data/trades.db')
STATE_FILE_PATH = os.getenv('STATE_FILE_PATH', '/data/state.json')

# ============================================================================
# TIMEZONE & TIME CONFIGURATION
# ============================================================================

IST = timezone('Asia/Kolkata')
MARKET_OPEN = time(9, 15)        # 09:15 IST
ENTRY_OPEN = time(9, 45)         # Entry allowed from 09:45
ENTRY_CLOSE = time(13, 30)       # Entry allowed until 13:30
POS_MGMT_END = time(15, 10)      # Forced exit at 15:10
MARKET_CLOSE = time(15, 30)      # Market closes at 15:30

# Pre-market data window
PRE_MARKET_START = time(8, 45)
PRE_MARKET_END = time(9, 15)

# ============================================================================
# UNDERLYINGS & INSTRUMENTS
# ============================================================================

PRIMARY_UNDERLYING = 'NIFTY'
NIFTY_TOKENS = {
    'NIFTY': '99926000',
    'NIFTYIT': '99926009',
    'BANKNIFTY': '99926010'
}

# ============================================================================
# VOLATILITY & MARKET REGIMES
# ============================================================================

VIX_THRESHOLD_TRENDING = 12.0      # VIX < 12 → Trending
VIX_THRESHOLD_NORMAL = 16.0        # 12–16 → Normal, >16 → Range
VIX_BACKSPREAD_MAX = 13.5          # Backspread allowed only if VIX < 13.5

# Global Bias Score Thresholds
GIFT_NIFTY_BULL_THRESHOLD = 0.4    # +1 if > +0.4%
GIFT_NIFTY_BEAR_THRESHOLD = -0.4   # -1 if < -0.4%
SP500_BULL_THRESHOLD = 0.7         # +1 if > +0.7%
SP500_BEAR_THRESHOLD = -0.7        # -1 if < -0.7%
NASDAQ_BULL_THRESHOLD = 1.0        # +1 if > +1%
NASDAQ_BEAR_THRESHOLD = -1.0       # -1 if < -1%

BIAS_BULLISH_THRESHOLD = 2         # Score ≥ +2 → Bullish
BIAS_BEARISH_THRESHOLD = -2        # Score ≤ -2 → Bearish

# ============================================================================
# STRIKE SELECTION (DELTA-BASED)
# ============================================================================

DELTA_DEBIT_LONG = (0.45, 0.60)        # Buy leg delta for debit spread
DELTA_DEBIT_SHORT = (0.20, 0.30)       # Sell leg delta for debit spread

DELTA_IC_SHORT = (0.18, 0.22)          # Short strangle in IC
DELTA_IC_HEDGE = (0.05, 0.10)          # Hedge wings in IC

DELTA_BACKSPREAD_SHORT = 0.40          # Sell leg delta
DELTA_BACKSPREAD_LONG = (0.20, 0.25)   # Buy leg delta
BACKSPREAD_RATIO = 2                    # 1 short : 2 long

# ============================================================================
# INTRADAY STRUCTURE THRESHOLDS
# ============================================================================

RSI_BULL_MIN = 45
RSI_BULL_MAX = 68
RSI_BEAR_MIN = 32
RSI_BEAR_MAX = 55

PCR_BULL_REJECT = 1.3              # Reject bullish if PCR > 1.3
PCR_BEAR_REJECT = 0.7              # Reject bearish if PCR < 0.7
PCR_BACKSPREAD_MIN = 0.8
PCR_BACKSPREAD_MAX = 1.2

EMA_THRESHOLD_PERCENT = 0.15       # |EMA20 - EMA50| < 0.15%
VWAP_CROSS_THRESHOLD = 4           # Range if VWAP crossed ≥4 times in 60 min
OPENING_RANGE_MINUTES = 15         # 9:15–9:30

# ============================================================================
# RISK MANAGEMENT
# ============================================================================

MAX_CAPITAL_PER_TRADE = 0.35       # 35% of account
DAILY_LOSS_LIMIT = -0.02           # -2% triggers disable for day
WEEKLY_LOSS_TRADES = 3             # 3 consecutive losses → halt week

PREMIUM_STOP_LOSS_PERCENT = 0.50   # Exit if spread loses 50%

# Spot invalidation for SL
BULL_SL_VWAP_OFFSET = -0.0025      # VWAP − 0.25%
BEAR_SL_VWAP_OFFSET = 0.0025       # VWAP + 0.25%

# Trailing stop activation & tightening
TRAILING_STOP_ACTIVATION = 0.0035  # Activates after spot moves 0.35%
TRAILING_STOP_OFFSET_NORMAL = -0.0010    # -0.10%
TRAILING_STOP_OFFSET_POST_1345 = -0.0005  # -0.05% after 13:45

# ============================================================================
# PROFIT TARGETS
# ============================================================================

DEBIT_SPREAD_TARGET = 0.50         # Exit at 50% of max profit
DEBIT_SPREAD_SQUEEZE = 0.70        # Exit if 70% achieved then falls to 50%

IC_TARGET_PROFIT = 0.40            # Exit if profit ≥40%
IC_SPOT_PROXIMITY = 0.0030         # Exit if spot within 0.30% of short strike

BACKSPREAD_RISK_CAP = 0.015        # Risk ≤1.5% of account
BACKSPREAD_DOUBLE_PROFIT = 0.70    # Book 70% when position doubles
BACKSPREAD_MAX_STAGNATION = 30     # Exit if stagnant near short strike for 30 min

# ============================================================================
# ORDER EXECUTION & THROTTLING
# ============================================================================

MAX_ORDERS_PER_SECOND = 10
ORDER_THROTTLE_DELAY = 1.0 / MAX_ORDERS_PER_SECOND  # 0.1 seconds

SLIPPAGE_TOLERANCE = 0.005         # ±0.5% from theoretical price
EXECUTION_BUFFER_SPREAD = 0.005    # ±0.5% for spread limits

# Order retry logic
ORDER_RETRY_ATTEMPTS = 3
ORDER_RETRY_DELAY = 2.0            # seconds between retries

# ============================================================================
# SESSION & POSITION MANAGEMENT
# ============================================================================

MAX_TRADES_PER_DAY = 2
POSITION_TIMEOUT_SECONDS = 15 * 60  # Re-check open positions every 15 min

# ============================================================================
# MARKET DATA BUFFER & CANDLE BUILDING
# ============================================================================

CANDLE_INTERVAL_SECONDS = 300      # 5-minute candles
EMA_PERIOD_SHORT = 20
EMA_PERIOD_LONG = 50
RSI_PERIOD = 14

VWAP_UPDATE_INTERVAL = 60          # Update VWAP every 60 seconds
VIX_UPDATE_INTERVAL = 60           # Update VIX every 60 seconds

# Data retention (for state recovery)
DATA_RETENTION_BARS = 100          # Keep last 100 candles in memory

# ============================================================================
# LOGGING & DATABASE
# ============================================================================

LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
LOG_DIR = os.getenv('LOG_DIR', '/logs')
LOG_TRADE_SCHEMA = {
    'date': 'TEXT',
    'time': 'TEXT',
    'strategy': 'TEXT',
    'market_regime': 'TEXT',
    'vix': 'REAL',
    'strikes': 'TEXT',  # JSON
    'entry_time': 'TEXT',
    'entry_price': 'REAL',
    'exit_time': 'TEXT',
    'exit_price': 'REAL',
    'pnl': 'REAL',
    'pnl_percent': 'REAL',
    'max_drawdown': 'REAL',
    'exit_reason': 'TEXT',
    'order_ids': 'TEXT',  # JSON
}

# ============================================================================
# EVENT FILTER DATES (2024-2025)
# ============================================================================

EVENT_FILTER_DATES = {
    '2024-02-29': 'RBI Policy',
    '2024-03-01': 'RBI Policy Decision',
    '2024-04-09': 'Union Budget',
    '2024-06-05': 'RBI MPC Decision',
    '2024-08-09': 'RBI MPC Decision',
    '2024-10-09': 'RBI MPC Decision',
    '2024-12-06': 'RBI MPC Decision',
    '2025-02-07': 'RBI MPC Decision',
    '2025-02-28': 'Expiry Day',
    # Add more as needed
}

# ============================================================================
# WEBSOCKET & BROKER SETTINGS
# ============================================================================

BROKER_WS_URL = 'wss://feed.pxl.finvasia.com/ws'
# Use Flattrade / PI Connect API base; can be overridden via environment
BROKER_API_BASE = os.getenv('FLATTRADE_API_BASE', 'https://api.flattrade.com')

WEBSOCKET_HEARTBEAT_INTERVAL = 30   # Send heartbeat every 30 seconds
WEBSOCKET_TIMEOUT = 5               # Connection timeout
WEBSOCKET_RECONNECT_DELAY = 5       # Reconnect after 5 sec on disconnect
# WebSocket protection
WEBSOCKET_MISSING_TICK_THRESHOLD_SECONDS = 20  # If no tick in this many seconds during market hours
WEBSOCKET_MAX_MISSES_BEFORE_DISABLE = 3       # Disable trading after this many consecutive reconnect failures

# Token refresh settings
TOKEN_REFRESH_MARGIN_SECONDS = 120   # Refresh token this many seconds before expiry
TOKEN_REFRESH_MAX_RETRIES = 5
TOKEN_REFRESH_BACKOFF_BASE = 2       # Exponential backoff multiplier

# ============================================================================
# SYSTEM BEHAVIOR & RECOVERY
# ============================================================================

DAILY_RESET_HOUR = 9
DAILY_RESET_MINUTE = 0              # 09:00 IST daily reset

GRACEFUL_SHUTDOWN_TIMEOUT = 30      # Seconds to wait for clean shutdown
AUTO_LOGOUT_INACTIVITY = 3600       # Re-login after 1 hour inactivity

# State persistence
SAVE_STATE_INTERVAL = 60            # Save state every 60 seconds
POSITION_RECHECK_INTERVAL = 900     # Recheck positions every 15 minutes

# ============================================================================
# BROKER API LIMITS (PI Connect / Flattrade)
# ============================================================================

NIFTY_LOT_SIZE = 50
BANKNIFTY_LOT_SIZE = 15
NIFTYIT_LOT_SIZE = 25

# ============================================================================
# VALIDATION FLAGS
# ============================================================================

VALIDATE_BEFORE_EXECUTION = True
ALLOW_PAPER_TRADING = False        # Set True for backtest only
DRY_RUN_MODE = os.getenv('DRY_RUN', 'false').lower() == 'true'
