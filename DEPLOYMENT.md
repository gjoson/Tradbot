# Tradbot Production Deployment Guide

## Overview

Tradbot is a production-grade automated options trading system for NIFTY options on Flattrade.

**Key Features:**
- Multi-leg option strategies (debit spreads, iron condors, backspreads)
- Real-time market classification and trade signals
- Strict risk management and capital protection
- Persistent trade logging and P&L tracking
- IST timezone aware scheduling
- Graceful error handling and recovery
- Systemd service integration for VPS deployment

## System Requirements

- **OS:** Linux (Ubuntu 20.04 LTS or later recommended)
- **Python:** 3.11 or higher
- **Broker:** Flattrade PI Connect API account
- **Internet:** Stable connection for WebSocket and API calls
- **Storage:** Minimum 1GB for logs and database

## Installation

### 1. Environment Setup

```bash
# Create system user
sudo useradd -r -s /bin/bash -d /opt/tradbot tradbot

# Create directories
sudo mkdir -p /opt/tradbot {/var/log,/var/lib}/tradbot
sudo chown tradbot:tradbot /opt/tradbot {/var/log,/var/lib}/tradbot
sudo chmod 750 /opt/tradbot {/var/log,/var/lib}/tradbot

# Clone/copy application
cd /opt/tradbot
# Copy all files here

# Create virtual environment
python3.11 -m venv venv
venv/bin/pip install --upgrade pip setuptools wheel

# Install dependencies
venv/bin/pip install -r requirements.txt
```

### 2. Configuration

Create or update environment variables:

```bash
# Edit tradbot.service to set credentials:
export FLATTRADE_USER="your_username"
export FLATTRADE_PASSWORD="your_password"
export FLATTRADE_API_KEY="your_api_key"
export FLATTRADE_API_SECRET="your_api_secret"

# Optional settings:
export LOG_LEVEL="INFO"           # [DEBUG, INFO, WARNING, ERROR, CRITICAL]
export DRY_RUN="false"            # Set true for testing without real orders
```

### 3. Database Setup

```bash
# Create initial database
sudo -u tradbot mkdir -p /var/lib/tradbot
sudo -u tradbot python3 -c "
from core.trade_logger import TradeLogger
TradeLogger('/var/lib/tradbot/trades.db')
print('Database initialized')
"
```

### 4. Systemd Service Installation

```bash
# Copy service file
sudo cp tradbot.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable service (starts on boot)
sudo systemctl enable tradbot

# Start service
sudo systemctl start tradbot

# Check status
sudo systemctl status tradbot
```

## Operations

### Starting the System

```bash
# Via systemd
sudo systemctl start tradbot

# Check logs
journalctl -u tradbot -f

# Or manually (for testing)
cd /opt/tradbot
source venv/bin/activate
python main.py
```

### Monitoring

```bash
# View real-time logs
journalctl -u tradbot -f

# View historical logs
journalctl -u tradbot -n 100

# Check service status
systemctl status tradbot

# View error logs
tail -f /var/log/tradbot/tradbot.log
```

### Stopping the System

```bash
# Graceful shutdown
sudo systemctl stop tradbot

# Force stop (if hung)
sudo systemctl kill tradbot

# Status after stop
systemctl status tradbot
```

### Restarting

```bash
# Restart service
sudo systemctl restart tradbot

# Reload configuration (without restart)
sudo systemctl reload tradbot
```

## Trading Rules

### Market Hours
- **Entry Window:** 09:45 - 13:30 IST
- **Position Management:** 09:45 - 15:10 IST
- **Forced Exit:** 15:10 IST (daily)
- **Market Open/Close:** 09:15 / 15:30 IST

### Trading Constraints
- **Max Trades/Day:** 2
- **Max Capital/Trade:** 35% of account
- **Daily Loss Limit:** -2% (disables trading for day)
- **Weekly Loss Limit:** 3 consecutive losses (halts trading for week)
- **Position Type:** Only multi-leg defined-risk strategies
- **Order Type:** Limit orders only
- **Expiry:** Current weekly options only

### Strategies
1. **Bull Call Debit Spread** - Bullish trend, VIX < 15
2. **Bear Put Debit Spread** - Bearish trend, VIX < 15
3. **Iron Condor** - Range-bound, VIX > 14
4. **Call/Put Backspread** - Volatility compression, VIX < 13.5

### Stop Loss & Profit Taking
- **Premium Stop:** 50% loss triggers exit
- **Spot Invalidation:** VWAP ± 0.25% or swing levels
- **Trailing Stop:** Activates after 0.35% favorable move
- **Profit Target:** 50% for spreads, 40% for iron condor

## Database & Logging

### Trade Database
Location: `/var/lib/tradbot/trades.db` (SQLite)

Tables:
- `trades` - Completed trades with entry/exit details
- `no_trade_days` - Days with no trading activity

Access trades:
```python
from core.trade_logger import TradeLogger
logger = TradeLogger('/var/lib/tradbot/trades.db')
trades = await logger.get_trades(date_from='2024-01-01', limit=50)
stats = await logger.get_statistics()
```

### Log Files
- **Main Log:** `/var/log/tradbot/tradbot.log`
- **Systemd Journal:** `journalctl -u tradbot`

## Troubleshooting

### Authentication Failures
```bash
# Check credentials
echo $FLATTRADE_USER
echo $FLATTRADE_PASSWORD

# Re-login manually
python3 -c "from core.login_manager import LoginManager; import asyncio; asyncio.run(LoginManager().login())"
```

### WebSocket Connection Issues
```bash
# Check connectivity
telnet feed.pxl.finvasia.com 443
curl -I https://api.smartapi.smartbroker.com/auth/login
```

### High Memory Usage
```bash
# Check memory
free -h
ps aux | grep tradbot

# Reduce log level
export LOG_LEVEL=WARNING
systemctl restart tradbot
```

### Order Execution Delays
- Check network latency
- Verify broker API rate limits (10 orders/second enforced)
- Review broker status page

## Security Considerations

1. **Credentials:** Use environment variables, never hardcode
2. **File Permissions:** Database and logs restricted to tradbot user
3. **Network:** Use VPN if accessing from untrusted networks
4. **Monitoring:** Set up alerts for errors and unusual activity
5. **Backups:** Regular backups of `/var/lib/tradbot/`

## Performance Tuning

### Resource Limits (in tradbot.service)
```
MemoryLimit=512M      # Adjust based on usage
CPUQuota=50%          # Adjust based on CPU availability
```

### Log Rotation
```bash
# Add to /etc/logrotate.d/tradbot
/var/log/tradbot/*.log {
    daily
    rotate 10
    compress
    delaycompress
    notifempty
    create 0640 tradbot tradbot
    sharedscripts
    postrotate
        systemctl reload tradbot > /dev/null 2>&1 || true
    endscript
}
```

## Backup & Recovery

### Backup Strategy
```bash
# Daily backup script
#!/bin/bash
BACKUP_DIR=/backups/tradbot-$(date +%Y%m%d)
mkdir -p $BACKUP_DIR
cp -r /var/lib/tradbot/* $BACKUP_DIR/
gzip $BACKUP_DIR/trades.db
```

### Recovery
```bash
# Stop service
systemctl stop tradbot

# Restore database
cp /backups/tradbot-YYYYMMDD/trades.db /var/lib/tradbot/

# Start service
systemctl start tradbot
```

## Support & Maintenance

### Logs to Review Daily
1. Error logs - any exceptions or failures
2. Trade logs - entry/exit times and P&L
3. Risk alerts - margin/capital warnings

### Monthly Maintenance
1. Review trading statistics
2. Verify all market events are logged
3. Check broker API status page
4. Update credentials if expired
5. Backup database to external storage

### Quarterly Tasks
1. Test recovery procedures
2. Review and update trading rules
3. Analyze strategy performance
4. Security audit of credentials

## Uninstall

```bash
# Stop service
sudo systemctl stop tradbot
sudo systemctl disable tradbot

# Remove service file
sudo rm /etc/systemd/system/tradbot.service

# Remove application and user
sudo rm -rf /opt/tradbot
sudo userdel tradbot

# Remove log directories (optional)
sudo rm -rf /var/log/tradbot /var/lib/tradbot
```

---

For issues or questions, check logs first:
```bash
journalctl -u tradbot -n 1000 | grep ERROR
```
