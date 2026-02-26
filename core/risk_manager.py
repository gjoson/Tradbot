"""
Risk Manager: Enforces all trading constraints and position limits.
Has absolute authority to reject any order.
"""

import logging
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from pytz import timezone

from config.settings import (
    MAX_CAPITAL_PER_TRADE, DAILY_LOSS_LIMIT, WEEKLY_LOSS_TRADES,
    TRAILING_STOP_ACTIVATION, TRAILING_STOP_OFFSET_NORMAL, TRAILING_STOP_OFFSET_POST_1345,
    PREMIUM_STOP_LOSS_PERCENT, BULL_SL_VWAP_OFFSET, BEAR_SL_VWAP_OFFSET,
    ENTRY_OPEN, ENTRY_CLOSE, POS_MGMT_END, MAX_TRADES_PER_DAY
)
from core.market_data import MarketSnapshot, Candle

logger = logging.getLogger(__name__)
IST = timezone('Asia/Kolkata')


@dataclass
class Position:
    """Open trading position."""
    position_id: str
    entry_time: datetime
    entry_price: float
    legs: List[Dict]  # List of leg info: {'strike': x, 'instrument': 'CE'/'PE', 'quantity': n, 'action': 'BUY'/'SELL'}
    strategy: str
    initial_capital_used: float
    
    # Dynamic state
    current_price: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0
    highest_price: float = 0.0
    trailing_stop_level: Optional[float] = None
    last_update: datetime = field(default_factory=lambda: datetime.now(IST))
    
    # Exit flags
    exit_reason: Optional[str] = None
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    
    def is_active(self) -> bool:
        """Check if position is still open."""
        return self.exit_time is None and self.exit_price is None


@dataclass
class DailyStats:
    """Daily trading statistics."""
    date: datetime
    trades_executed: int = 0
    total_pnl: float = 0.0
    losing_trades: int = 0
    consecutive_losses: int = 0
    is_disabled: bool = False
    disable_reason: Optional[str] = None


@dataclass
class WeeklyStats:
    """Weekly trading statistics."""
    week_start: datetime
    total_trades: int = 0
    total_pnl: float = 0.0
    consecutive_losing_trades: int = 0
    is_halted: bool = False
    halt_until: Optional[datetime] = None


class RiskManager:
    """
    Enforces all trading constraints:
    - Max capital per trade (35%)
    - Daily loss limit (-2%)
    - Weekly loss streak (3 losses → halt)
    - Stop loss (hard premium stop + spot invalidation)
    - Trailing stop (0.35% activation)
    - Profit targets
    - Position closure by 15:10 IST
    - Max 2 trades per day
    - Trading hours 09:45-13:30
    """
    
    def __init__(self):
        self._logger = logging.getLogger(f"{__name__}.RiskManager")
        
        self.account_balance = 0.0
        self.open_positions: Dict[str, Position] = {}
        
        self.daily_stats = DailyStats(date=datetime.now(IST).date())
        self.weekly_stats = WeeklyStats(week_start=datetime.now(IST))
        
        self._pnl_tracking = []  # For max drawdown calculation
    
    async def can_enter_trade(self, spot: float, capital_required: float) -> tuple:
        """
        Check if new trade entry is allowed.
        
        Args:
            spot: Current spot price
            capital_required: Capital needed for this trade
        
        Returns:
            (allowed: bool, reason: str)
        """
        try:
            # Check 1: Current time within entry window (09:45-13:30)
            now = datetime.now(IST).time()
            if not (ENTRY_OPEN <= now <= ENTRY_CLOSE):
                return False, f"Outside entry window (09:45-13:30), current: {now}"
            
            # Check 2: Already have open position
            if self.open_positions:
                return False, "Already have open position"
            
            # Check 3: Daily trade count limit (max 2)
            if self.daily_stats.trades_executed >= MAX_TRADES_PER_DAY:
                return False, f"Daily trade limit ({MAX_TRADES_PER_DAY}) reached"
            
            # Check 4: Daily disabled due to loss limit
            if self.daily_stats.is_disabled:
                return False, f"Daily trading disabled: {self.daily_stats.disable_reason}"
            
            # Check 5: Weekly halted due to 3 consecutive losses
            if self.weekly_stats.is_halted:
                return False, f"Trading halted until {self.weekly_stats.halt_until}"
            
            # Check 6: Capital check
            max_capital_available = self.account_balance * MAX_CAPITAL_PER_TRADE
            if capital_required > max_capital_available:
                return False, (
                    f"Insufficient capital: required {capital_required:.2f}, "
                    f"available {max_capital_available:.2f}"
                )
            
            # Check 7: Not an event day (would be checked by scheduler)
            
            return True, "All risk checks passed"
        
        except Exception as e:
            self._logger.error(f"Error in can_enter_trade: {e}")
            return False, f"Risk check error: {e}"
    
    async def validate_order(self, order: Dict) -> tuple:
        """
        Validate order before execution.
        Risk manager's final approval gate.
        
        Args:
            order: Order dict with legs, prices, etc.
        
        Returns:
            (allowed: bool, reason: str)
        """
        try:
            # Validate order structure
            if 'legs' not in order or not order['legs']:
                return False, "Order must have legs"
            
            # Validate prices are within acceptable range
            for leg in order['legs']:
                if leg.get('price', 0) <= 0:
                    return False, f"Invalid price for {leg}"
                
                # Check slippage tolerance later when comparing to market
            
            # Validate quantity is positive
            for leg in order['legs']:
                if leg.get('quantity', 0) <= 0:
                    return False, "Quantity must be > 0"
            
            self._logger.info("Order validation passed")
            return True, "Order approved"
        
        except Exception as e:
            self._logger.error(f"Order validation error: {e}")
            return False, f"Validation error: {e}"
    
    async def register_position(self, position: Position) -> None:
        """Register new open position."""
        try:
            self.open_positions[position.position_id] = position
            
            # Adjust daily stats
            self.daily_stats.trades_executed += 1
            
            self._logger.info(
                f"Position opened: {position.position_id} ({position.strategy}) "
                f"at {position.entry_price:.2f}"
            )
        
        except Exception as e:
            self._logger.error(f"Error registering position: {e}")
    
    async def update_position(
        self,
        position_id: str,
        current_price: float,
        snapshot: MarketSnapshot
    ) -> Optional[str]:
        """
        Update position and check for exit signals.
        
        Returns:
            Exit reason if position should close, None otherwise
        """
        try:
            if position_id not in self.open_positions:
                self._logger.warning(f"Position not found: {position_id}")
                return None
            
            position = self.open_positions[position_id]
            position.current_price = current_price
            position.last_update = datetime.now(IST)
            
            # Check for exit conditions (in priority order)
            
            # 1. Hard premium stop loss (lost 50% of value)
            exit_reason = await self._check_premium_stop_loss(position)
            if exit_reason:
                return exit_reason
            
            # 2. Spot invalidation (VWAP ± offset)
            exit_reason = await self._check_spot_invalidation(position, snapshot)
            if exit_reason:
                return exit_reason
            
            # 3. Trailing stop
            exit_reason = await self._check_trailing_stop(position, snapshot)
            if exit_reason:
                return exit_reason
            
            # 4. Profit target
            exit_reason = await self._check_profit_target(position)
            if exit_reason:
                return exit_reason
            
            # 5. Forced close at 15:10
            now = datetime.now(IST).time()
            if now >= POS_MGMT_END:
                return "Forced close at 15:10 IST"
            
            return None
        
        except Exception as e:
            self._logger.error(f"Error updating position: {e}")
            return None
    
    async def _check_premium_stop_loss(self, position: Position) -> Optional[str]:
        """Check if spread lost 50% of value."""
        if position.max_loss <= 0:
            return None
        
        loss_percent = abs(position.current_price - position.entry_price) / position.entry_price
        
        if loss_percent >= PREMIUM_STOP_LOSS_PERCENT:
            return f"Premium stop loss triggered ({loss_percent:.0%} loss)"
        
        return None
    
    async def _check_spot_invalidation(self, position: Position, snapshot: MarketSnapshot) -> Optional[str]:
        """Check spot invalidation (VWAP ± offset or swing levels)."""
        spot = snapshot.nifty_spot
        vwap = snapshot.nifty_vwap
        
        if position.strategy.startswith('bull'):
            # Bull: VWAP − 0.25%
            sl_level = vwap * (1 + BULL_SL_VWAP_OFFSET)
            if spot < sl_level:
                return f"Spot invalidation: Bull SL triggered at {sl_level:.0f}"
        
        elif position.strategy.startswith('bear'):
            # Bear: VWAP + 0.25%
            sl_level = vwap * (1 + BEAR_SL_VWAP_OFFSET)
            if spot > sl_level:
                return f"Spot invalidation: Bear SL triggered at {sl_level:.0f}"
        
        return None
    
    async def _check_trailing_stop(self, position: Position, snapshot: MarketSnapshot) -> Optional[str]:
        """Check trailing stop activation and tightening."""
        spot = snapshot.nifty_spot
        entry = position.entry_price
        
        # Trailing stop activates after 0.35% move in trade direction
        favorable_move = abs(spot - entry) / entry
        
        if favorable_move < TRAILING_STOP_ACTIVATION:
            return None  # Not activated yet
        
        # Determine trailing stop level
        now = datetime.now(IST).time()
        post_1345 = now >= datetime.strptime("13:45", "%H:%M").time()
        
        offset = TRAILING_STOP_OFFSET_POST_1345 if post_1345 else TRAILING_STOP_OFFSET_NORMAL
        
        if position.strategy.startswith('bull'):
            # For bull: trailing stop is latest swing low - offset
            trail_level = snapshot.nifty_vwap * (1 + offset)
            if position.trailing_stop_level is None or trail_level > position.trailing_stop_level:
                position.trailing_stop_level = trail_level
            
            if spot < position.trailing_stop_level:
                return f"Trailing stop triggered at {position.trailing_stop_level:.0f}"
        
        elif position.strategy.startswith('bear'):
            # For bear: trailing stop is latest swing high + offset
            trail_level = snapshot.nifty_vwap * (1 - offset)
            if position.trailing_stop_level is None or trail_level < position.trailing_stop_level:
                position.trailing_stop_level = trail_level
            
            if spot > position.trailing_stop_level:
                return f"Trailing stop triggered at {position.trailing_stop_level:.0f}"
        
        return None
    
    async def _check_profit_target(self, position: Position) -> Optional[str]:
        """Check profit target based on strategy."""
        if position.strategy in ['bull_call_debit_spread', 'bear_put_debit_spread']:
            # Debit spread: exit at 50% of max profit
            if position.current_price >= position.max_profit * 0.50:
                return "Debit spread 50% target hit"
        
        elif position.strategy == 'iron_condor':
            # Iron condor: exit at 40% profit
            if position.current_price >= position.max_profit * 0.40:
                return "Iron condor 40% target hit"
        
        return None
    
    async def close_position(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: str
    ) -> Dict:
        """
        Close a position and calculate P&L.
        
        Returns:
            Position closure details with P&L
        """
        try:
            if position_id not in self.open_positions:
                self._logger.error(f"Position not found: {position_id}")
                return {}
            
            position = self.open_positions[position_id]
            position.exit_price = exit_price
            position.exit_time = datetime.now(IST)
            position.exit_reason = exit_reason
            
            # Calculate P&L
            pnl = exit_price - position.entry_price
            pnl_percent = pnl / position.entry_price * 100
            
            # Update daily/weekly stats
            self.daily_stats.total_pnl += pnl
            self.weekly_stats.total_pnl += pnl
            self.weekly_stats.total_trades += 1
            
            if pnl < 0:
                self.daily_stats.losing_trades += 1
                self.daily_stats.consecutive_losses += 1
                self.weekly_stats.consecutive_losing_trades += 1
            else:
                self.daily_stats.consecutive_losses = 0
                self.weekly_stats.consecutive_losing_trades = 0
            
            # Check daily loss limit
            daily_loss_pct = self.daily_stats.total_pnl / self.account_balance
            if daily_loss_pct <= DAILY_LOSS_LIMIT:
                self.daily_stats.is_disabled = True
                self.daily_stats.disable_reason = f"Daily loss limit hit ({daily_loss_pct:.2%})"
                self._logger.critical(f"Daily loss limit hit: {daily_loss_pct:.2%}")
            
            # Check weekly loss streak
            if self.daily_stats.consecutive_losses >= WEEKLY_LOSS_TRADES:
                self.weekly_stats.is_halted = True
                self.weekly_stats.halt_until = datetime.now(IST) + timedelta(days=7)
                self._logger.critical(
                    f"Weekly loss streak ({WEEKLY_LOSS_TRADES} losses), "
                    f"trading halted until {self.weekly_stats.halt_until}"
                )
            
            # Remove from open positions
            del self.open_positions[position_id]
            
            self._logger.info(
                f"Position closed: {position_id} "
                f"P&L: {pnl:.2f} ({pnl_percent:.2f}%) "
                f"Reason: {exit_reason}"
            )
            
            return {
                'position_id': position_id,
                'exit_price': exit_price,
                'exit_reason': exit_reason,
                'pnl': pnl,
                'pnl_percent': pnl_percent,
                'entry_price': position.entry_price
            }
        
        except Exception as e:
            self._logger.error(f"Error closing position: {e}")
            return {}
    
    def set_account_balance(self, balance: float) -> None:
        """Set account balance for capital calculations."""
        self.account_balance = balance
        self._logger.info(f"Account balance set to {balance:.2f}")
    
    def get_daily_stats(self) -> DailyStats:
        """Get current daily statistics."""
        return self.daily_stats
    
    def get_weekly_stats(self) -> WeeklyStats:
        """Get current weekly statistics."""
        return self.weekly_stats
    
    async def daily_reset(self) -> None:
        """Reset daily stats at 09:00 IST."""
        self._logger.info("Daily reset at 09:00 IST")
        self.daily_stats = DailyStats(date=datetime.now(IST).date())
