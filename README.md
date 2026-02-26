# Tradbot - Production Options Trading System

## Overview

Tradbot is a **production-grade automated trading system** for NIFTY options. It implements sophisticated multi-leg option strategies with strict risk management, real-time market classification, and conservative trade execution rules.

**Key Principles:**
- **Capital Protection First** - Every trade must have defined risk
- **Rule-Based Execution** - No discretion, no overrides, no emotions
- **Conservative Entry** - Better to miss trades than take bad ones
- **Deterministic Logic** - All decisions follow predetermined rules
- **Persistent Execution** - Runs 24/7 ready to trade during market hours

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Event Bus (Pub/Sub)                    │
└─────────────────────────────────────────────────────────────┘
         ↓                       ↓                     ↓
    ┌─────────┐         ┌──────────────┐       ┌──────────────┐
    │ Scheduler│         │ WebSocket    │       │Risk Manager  │
    │ (IST)    │         │ (Market Data)│       │(Enforcement) │
    └─────────┘         └──────────────┘       └──────────────┘
         ↓                       ↓                     ↓
    ┌──────────────┐     ┌──────────────┐       ┌──────────────┐
    │Market        │     │Market Data   │       │Order Manager │
    │Classifier    │──→  │Aggregator    │──→    │(Throttle)    │
    │(Regime)      │     │(Indicators)  │       └──────────────┘
    └──────────────┘     └──────────────┘              ↓
         ↓                       ↓              ┌──────────────┐
    ┌──────────────┐     ┌──────────────┐     │Broker API    │
    │Strategy      │──→  │Strike Finder │     │(Flattrade)   │
    │Engine        │     │(Delta-based) │     └──────────────┘
    │(Trade Logic) │     └──────────────┘              ↓
    └──────────────┘                        ┌──────────────┐
                                           │PnL Tracker   │
                                           │Trade Logger  │
                                           └──────────────┘
```

## System Components

### Core Modules

**1. event_bus.py** - Inter-module communication
- Async pub/sub system
- Queue-based event processing
- Thread-safe and lock-free

**2. login_manager.py** - Broker authentication
- Session management
- Token refresh handling
- Automatic re-login on expiry

**3. websocket_client.py** - Real-time market data
- Connection management
- Automatic reconnection
- Subscription/unsubscription handling

**4. market_data.py** - Data aggregation
- Tick buffering
- 5-minute candle building
- Indicator computation (EMA, RSI, VWAP)

**5. atm_strike_finder.py** - Strike selection
- Delta-based strike matching
- Multi-leg strategy construction
- Liquidity filtering

**6. market_classifier.py** - Market regime analysis
- Global bias scoring (GiftNifty, S&P500, Nasdaq)
- Volatility regime detection (VIX-based)
- Intraday trend identification
- Entry signal validation

**7. strategy_engine.py** - Trade signal generation
- Decision matrix: market condition → strategy mapping
- 4 major strategies supported
- Risk/reward calculation

**8. risk_manager.py** - Risk enforcement
- Capital allocation (35% per trade)
- Daily loss limit (-2%)
- Weekly loss streak detection (3 losses)
- Stop loss and trailing stop logic
- Position closure by 15:10 IST

**9. order_manager.py** - Order execution
- Rate limiting (≤10 orders/second)
- Atomic multi-leg execution
- Retry logic with exponential backoff
- Partial fill handling

**10. broker_interface.py** - API abstraction
- Order placement/cancellation
- Position queries
- Option chain fetching
- VIX and PCR retrieval

**11. pnl_tracker.py** - Profit & loss monitoring
- Real-time position P&L
- Daily/weekly statistics
- Max drawdown tracking
- Win rate calculation

**12. trade_logger.py** - Trade persistence
- SQLite database storage
- Trade history with entry/exit details
- No-trade day logging
- Statistical analysis

**13. scheduler.py** - IST timezone scheduling
- Market hours management
- Pre-market data window (8:45-9:15)
- Entry window enforcement (9:45-13:30)
- Forced exit scheduling (15:10)
- Daily reset (09:00)
- Event day detection

## Trading Rules

### Market Hours (IST)
| Activity | Time |
|----------|------|
| Market Open | 09:15 |
| Entry Allowed | 09:45 - 13:30 |
| Position Management | 09:45 - 15:10 |
| Forced Exit | 15:10 |
| Market Close | 15:30 |

### Constraints
- **Max Trades/Day:** 2
- **Max Capital/Trade:** 35% of account
- **Daily Loss Limit:** -2% (blocks all trades)
- **Weekly Loss Limit:** 3 consecutive losses (halt 7 days)
- **Order Type:** Limit only (no market orders)
- **Position Type:** Multi-leg defined-risk only
- **No Naked Selling:** All positions must be hedged
- **No Overnight:** All positions closed by 15:10 IST

### Market Classification

**Step A: Global Bias Score**
```
GiftNifty > +0.4%  → +1
GiftNifty < -0.4%  → -1
S&P500 > +0.7%     → +1
S&P500 < -0.7%     → -1
Nasdaq > +1%       → +1
Nasdaq < -1%       → -1

Score ≥ +2 → BULLISH
Score ≤ -2 → BEARISH
Otherwise → NEUTRAL
```

**Step B: Volatility Regime**
```
VIX < 12  → TRENDING
12-16     → NORMAL
VIX > 16  → RANGE
```

**Step C: Intraday Structure**
```
Price > VWAP + EMA20 > EMA50 → UPTREND
Price < VWAP + EMA20 < EMA50 → DOWNTREND
VWAP crossed ≥4 times/60min → RANGE
Else → NO TRADE
```

### Supported Strategies

**1. Bull Call Debit Spread**
- Condition: Trend UP, VIX < 15
- Entry: Buy 0.45-0.60 delta CE, Sell 0.20-0.30 delta CE
- Exit: 50% max profit or 50% loss
- Risk: Limited to premium paid

**2. Bear Put Debit Spread**
- Condition: Trend DOWN, VIX < 15
- Entry: Buy 0.45-0.60 delta PE, Sell 0.20-0.30 delta PE
- Exit: 50% max profit or 50% loss
- Risk: Limited to premium paid

**3. Iron Condor**
- Condition: Range-bound, VIX > 14
- Entry: Sell 0.18-0.22 delta CE & PE, Buy 0.05-0.10 delta hedges
- Exit: 40% profit or spot within 0.30% of short strike
- Risk: Width of short strikes minus credit received

**4. Call/Put Backspread**
- Condition: Volatility compression, VIX < 13.5
- Entry: Sell 1 contract ~0.40 delta, Buy 2 contracts ~0.20-0.25 delta
- Exit: 70% profit or 100% loss reaches cap
- Risk: Capped at 1.5% of account

### Stop Loss & Exit Rules

**Hard Premium Stop**
- Exit if spread loses 50% of paid premium

**Spot Invalidation**
- Bull: VWAP - 0.25% or previous swing low
- Bear: VWAP + 0.25% or previous swing high

**Trailing Stop**
- Activates after 0.35% favorable price move
- Tightens to latest swing level - 0.10%
- Further tightened to 0.05% after 13:45

**Profit Targets**
- Debit Spreads: Exit at 50% of max profit
- Iron Condor: Exit at 40% profit
- Backspread: Book 70% when position doubles

**Forced Exit**
- All positions must be closed by 15:10 IST

## Installation

### Prerequisites
- Python 3.11+
- Linux VPS (Ubuntu 20.04+ recommended)
- Flattrade account with PI Connect API access

### Quick Start

```bash
# Clone repository
git clone https://github.com/yourusername/tradbot.git
cd tradbot

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
export FLATTRADE_USER="your_username"
export FLATTRADE_PASSWORD="your_password"
export FLATTRADE_API_KEY="your_api_key"
export FLATTRADE_API_SECRET="your_api_secret"

# Run system
python main.py
```

### Production Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for systemd installation and monitoring.

## Configuration

All settings are in `config/settings.py`. Key configurations:

```python
# Capital Management
MAX_CAPITAL_PER_TRADE = 0.35  # 35% of account
DAILY_LOSS_LIMIT = -0.02      # -2% triggers disable
WEEKLY_LOSS_TRADES = 3        # 3 losses → halt week

# Market Classification
GIFT_NIFTY_BULL_THRESHOLD = 0.4
VIX_THRESHOLD_TRENDING = 12.0
VIX_BACKSPREAD_MAX = 13.5

# Order Management
MAX_ORDERS_PER_SECOND = 10
ORDER_RETRY_ATTEMPTS = 3
SLIPPAGE_TOLERANCE = 0.005  # ±0.5%

# Trading Hours (IST)
ENTRY_OPEN = time(9, 45)
ENTRY_CLOSE = time(13, 30)
POS_MGMT_END = time(15, 10)
```

## Monitoring & Logging

### Log Files
- **Main:** `/var/log/tradbot/tradbot.log`
- **Database:** `/var/lib/tradbot/trades.db` (SQLite)

### Log Levels
```bash
export LOG_LEVEL=DEBUG    # Verbose (development)
export LOG_LEVEL=INFO     # Normal (production)
export LOG_LEVEL=WARNING  # Minimal (quiet)
```

### Trade Analysis
```python
from core.trade_logger import TradeLogger

logger = TradeLogger('/var/lib/tradbot/trades.db')

# Get trades
trades = await logger.get_trades(date_from='2024-01-01', limit=100)

# Get statistics
stats = await logger.get_statistics()
print(f"Win Rate: {stats['win_rate']:.1f}%")
print(f"Total P&L: {stats['total_pnl']:.2f}")
print(f"Profit Factor: {stats['profit_factor']:.2f}")
```

## Development

### Code Structure
```
tradbot/
├── config/
│   ├── __init__.py
│   └── settings.py          # All configuration
├── core/
│   ├── __init__.py
│   ├── event_bus.py         # Message bus
│   ├── login_manager.py     # Authentication
│   ├── websocket_client.py  # Data feed
│   ├── market_data.py       # Aggregation
│   ├── atm_strike_finder.py # Strike selection
│   ├── market_classifier.py # Regime analysis
│   ├── strategy_engine.py   # Trade logic
│   ├── risk_manager.py      # Risk enforcement
│   ├── order_manager.py     # Execution
│   ├── broker_interface.py  # Broker API
│   ├── pnl_tracker.py       # P&L monitoring
│   ├── trade_logger.py      # Persistence
│   └── scheduler.py         # Timing
├── utils/
│   ├── __init__.py
│   ├── indicators.py        # Technical analysis
│   ├── reconnect.py         # Resilience utilities
│   └── time_utils.py        # IST timezone utilities
├── main.py                  # Entry point
├── requirements.txt         # Dependencies
└── README.md               # This file
```

### Running in Development

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG

# Run with dry-run (no real orders)
export DRY_RUN=true
python main.py
```

### Testing

```bash
# Run tests
pytest tests/

# Test with coverage
pytest --cov=core tests/
```

## Troubleshooting

### Connection Issues
```bash
# Check broker API connectivity
curl -I https://api.smartapi.smartbroker.com/auth/login

# Check WebSocket
telnet feed.pxl.finvasia.com 443
```

### High Memory Usage
- Check number of cached candles/ticks
- Reduce `DATA_RETENTION_BARS` in settings
- Enable compression for logs

### Order Execution Delays
- Verify network latency (<100ms IST to AWS)
- Check broker order queue
- Review rate limiting settings

### Authentication Failures
- Verify credentials in environment
- Check broker token expiration
- Review login logs

## Best Practices

### Daily Operations
1. Review pre-market analysis
2. Check system logs for errors
3. Verify open positions
4. Monitor P&L throughout day
5. Ensure forced exit at 15:10

### Weekly Reviews
1. Analyze trade statistics
2. Review market classification accuracy
3. Check risk limit compliance
4. Audit system logs

### Monthly Maintenance
1. Backup database
2. Review and optimize configurations
3. Test recovery procedures
4. Update broker credentials if needed

## Performance Metrics

Typical system performance:
- **Latency:** <100ms (order to fill)
- **Memory:** 100-200 MB
- **CPU:** <5% average
- **Data Points/Second:** 1-10 (depending on symbol activity)
- **Event Processing:** <10ms per event

## Security

### Credential Management
- **Never** hardcode credentials in code
- Use environment variables only
- Rotate API keys regularly
- Restrict file permissions (chmod 600)

### Network Security
- Use VPN for remote access
- Enable firewall rules
- Monitor for unusual API activity
- Log all authentication events

### Audit Trail
- All trades logged to database with timestamps
- Entry/exit reasons recorded
- Order IDs tracked for reconciliation
- P&L calculated and verified

## Contributing

Issues, suggestions, and PRs are welcome. Please follow:
1. PEP 8 style guide
2. Type hints for all functions
3. Docstrings for public APIs
4. Test coverage for new features

## License

Proprietary - Tradbot Trading System

## Support

For issues or questions:
1. Check logs first: `journalctl -u tradbot -n 100`
2. Review [DEPLOYMENT.md](DEPLOYMENT.md)
3. Check config settings
4. Test with DRY_RUN=true

---

**Last Updated:** February 2024
**Version:** 1.0.0
**Status:** Production Ready

AI agent Nifty option trading bot 
