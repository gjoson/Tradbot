"""
PnL Tracker: Monitors profit & loss and position value updates.
Coordinates with Risk Manager for position management.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, List
from pytz import timezone

from core.event_bus import get_event_bus, Event, EventType

logger = logging.getLogger(__name__)
IST = timezone('Asia/Kolkata')


@dataclass
class PnLRecord:
    """P&L tracking record."""
    timestamp: datetime
    position_id: str
    strategy: str
    entry_price: float
    current_price: float
    pnl: float  # Profit/loss amount
    pnl_percent: float  # Profit/loss percentage
    risk_amount: float  # Max risk for position
    reward_amount: float  # Max reward
    win: bool  # Is position winning?


class PnLTracker:
    """
    Tracks P&L in real-time.
    
    Monitors:
    - Per-position P&L
    - Daily P&L
    - Max drawdown
    - Win rate
    """
    
    def __init__(self):
        self._logger = logging.getLogger(f"{__name__}.PnLTracker")
        
        self.records: List[PnLRecord] = []
        self.current_position_pnl: Dict[str, Dict] = {}
        
        self.daily_pnl = 0.0
        self.max_drawdown = 0.0
        self.peak_equity = 0.0
    
    async def update_position_pnl(
        self,
        position_id: str,
        strategy: str,
        entry_price: float,
        current_price: float,
        risk_amount: float,
        reward_amount: float
    ) -> None:
        """
        Update P&L for a position.
        
        Args:
            position_id: Position identifier
            strategy: Strategy type
            entry_price: Entry price/premium
            current_price: Current price/premium
            risk_amount: Max risk for position
            reward_amount: Max reward for position
        """
        try:
            pnl = current_price - entry_price
            pnl_percent = (pnl / entry_price * 100) if entry_price > 0 else 0.0
            win = pnl > 0
            
            record = PnLRecord(
                timestamp=datetime.now(IST),
                position_id=position_id,
                strategy=strategy,
                entry_price=entry_price,
                current_price=current_price,
                pnl=pnl,
                pnl_percent=pnl_percent,
                risk_amount=risk_amount,
                reward_amount=reward_amount,
                win=win
            )
            
            self.current_position_pnl[position_id] = {
                'pnl': pnl,
                'pnl_percent': pnl_percent,
                'win': win,
                'timestamp': datetime.now(IST)
            }
            
            # Emit event
            await self._emit_pnl_update(record)
        
        except Exception as e:
            self._logger.error(f"Error updating PnL: {e}")
    
    async def record_closed_position(
        self,
        position_id: str,
        pnl: float,
        pnl_percent: float
    ) -> None:
        """Record a closed position."""
        try:
            self.daily_pnl += pnl
            self.records.append(PnLRecord(
                timestamp=datetime.now(IST),
                position_id=position_id,
                strategy="closed",
                entry_price=0,
                current_price=0,
                pnl=pnl,
                pnl_percent=pnl_percent,
                risk_amount=0,
                reward_amount=0,
                win=pnl > 0
            ))
            
            # Update peak equity and max drawdown
            if self.daily_pnl > self.peak_equity:
                self.peak_equity = self.daily_pnl
            
            drawdown = self.peak_equity - self.daily_pnl
            if drawdown > self.max_drawdown:
                self.max_drawdown = drawdown
            
            self._logger.info(
                f"Position closed: {position_id} | "
                f"PnL: {pnl:.2f} ({pnl_percent:.2f}%) | "
                f"Daily Total: {self.daily_pnl:.2f}"
            )
        
        except Exception as e:
            self._logger.error(f"Error recording closed position: {e}")
    
    def get_daily_pnl(self) -> float:
        """Get today's total P&L."""
        return self.daily_pnl
    
    def get_max_drawdown(self) -> float:
        """Get max drawdown for the day."""
        return self.max_drawdown
    
    def get_position_pnl(self, position_id: str) -> Optional[Dict]:
        """Get P&L for a specific position."""
        return self.current_position_pnl.get(position_id)
    
    def get_win_rate(self) -> float:
        """Calculate win rate (0-100%)."""
        if not self.records:
            return 0.0
        
        wins = sum(1 for r in self.records if r.win)
        return (wins / len(self.records)) * 100 if self.records else 0.0
    
    def get_average_win(self) -> float:
        """Average P&L of winning trades."""
        winning = [r.pnl for r in self.records if r.win]
        return sum(winning) / len(winning) if winning else 0.0
    
    def get_average_loss(self) -> float:
        """Average P&L of losing trades."""
        losing = [r.pnl for r in self.records if not r.win]
        return sum(losing) / len(losing) if losing else 0.0
    
    def get_profit_factor(self) -> float:
        """Profit factor = Total Wins / Total Losses."""
        total_wins = sum(r.pnl for r in self.records if r.win)
        total_losses = abs(sum(r.pnl for r in self.records if not r.win))
        
        return total_wins / total_losses if total_losses > 0 else 0.0
    
    def get_stats_summary(self) -> Dict:
        """Get comprehensive P&L statistics."""
        return {
            'daily_pnl': self.daily_pnl,
            'max_drawdown': self.max_drawdown,
            'peak_equity': self.peak_equity,
            'win_rate': self.get_win_rate(),
            'avg_win': self.get_average_win(),
            'avg_loss': self.get_average_loss(),
            'profit_factor': self.get_profit_factor(),
            'total_trades': len(self.records)
        }
    
    async def _emit_pnl_update(self, record: PnLRecord) -> None:
        """Emit P&L update event."""
        try:
            bus = await get_event_bus()
            event = Event(
                event_type=EventType.POSITION_UPDATED,
                data={
                    'position_id': record.position_id,
                    'pnl': record.pnl,
                    'pnl_percent': record.pnl_percent,
                    'win': record.win
                },
                source='PnLTracker'
            )
            await bus.emit(event)
        except Exception as e:
            self._logger.error(f"Failed to emit PnL event: {e}")
    
    def reset_daily(self) -> None:
        """Reset daily P&L stats."""
        self.daily_pnl = 0.0
        self.max_drawdown = 0.0
        self.peak_equity = 0.0
        self.current_position_pnl.clear()
        self._logger.info("Daily P&L reset")
