"""
Scheduler: Manages timing and scheduled tasks in IST timezone.
Handles market hours, entry windows, daily resets, and forced exits.
"""

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Optional, Callable, List, Dict
from pytz import timezone as tz

from config.settings import (
    IST, MARKET_OPEN, ENTRY_OPEN, ENTRY_CLOSE, POS_MGMT_END, MARKET_CLOSE,
    PRE_MARKET_START, PRE_MARKET_END, DAILY_RESET_HOUR, DAILY_RESET_MINUTE,
    EVENT_FILTER_DATES
)
from core.event_bus import get_event_bus, Event, EventType

logger = logging.getLogger(__name__)


class Scheduler:
    """
    IST-timezone aware scheduler.
    
    Manages:
    - Market hours monitoring
    - Pre-market data feed window
    - Entry window (09:45-13:30)
    - Forced exit (15:10)
    - Daily reset (09:00)
    - Event day detection
    """
    
    def __init__(self):
        self._logger = logging.getLogger(f"{__name__}.Scheduler")
        self._scheduled_callbacks: List[tuple] = []  # (time, callback)
        self._event_days = set()
        self._load_event_days()
        self._last_daily_reset = None
    
    def _load_event_days(self) -> None:
        """Load event filter dates from config."""
        for date_str in EVENT_FILTER_DATES.keys():
            self._event_days.add(date_str)
    
    # =====================================================================
    # TIME QUERIES
    # =====================================================================
    
    def is_market_open(self) -> bool:
        """Check if market is open (9:15-15:30 IST)."""
        now = datetime.now(IST)
        return MARKET_OPEN <= now.time() < MARKET_CLOSE
    
    def is_in_entry_window(self) -> bool:
        """Check if current time allows new trades (9:45-13:30 IST)."""
        now = datetime.now(IST)
        return ENTRY_OPEN <= now.time() <= ENTRY_CLOSE
    
    def is_in_position_management_window(self) -> bool:
        """Check if current time allows position management (9:45-15:10 IST)."""
        now = datetime.now(IST)
        return ENTRY_OPEN <= now.time() <= POS_MGMT_END
    
    def is_pre_market_window(self) -> bool:
        """Check if in pre-market window (8:45-9:15 IST)."""
        now = datetime.now(IST)
        return PRE_MARKET_START <= now.time() < PRE_MARKET_END
    
    def is_event_day(self) -> bool:
        """Check if today is an event day (no trading)."""
        today = datetime.now(IST).strftime('%Y-%m-%d')
        return today in self._event_days
    
    def time_until_market_open(self) -> timedelta:
        """Get time until market opens."""
        now = datetime.now(IST)
        market_open_today = now.replace(
            hour=MARKET_OPEN.hour,
            minute=MARKET_OPEN.minute,
            second=0,
            microsecond=0
        )
        
        if now > market_open_today:
            # Next trading day
            next_day = now + timedelta(days=1)
            market_open_today = next_day.replace(
                hour=MARKET_OPEN.hour,
                minute=MARKET_OPEN.minute,
                second=0,
                microsecond=0
            )
        
        return market_open_today - now
    
    def time_until_forced_exit(self) -> timedelta:
        """Get time until forced position exit at 15:10."""
        now = datetime.now(IST)
        exit_time = now.replace(
            hour=POS_MGMT_END.hour,
            minute=POS_MGMT_END.minute,
            second=0,
            microsecond=0
        )
        
        if now > exit_time:
            return timedelta(0)
        
        return exit_time - now
    
    def time_until_entry_close(self) -> timedelta:
        """Get time until entry window closes at 13:30."""
        now = datetime.now(IST)
        close_time = now.replace(
            hour=ENTRY_CLOSE.hour,
            minute=ENTRY_CLOSE.minute,
            second=0,
            microsecond=0
        )
        
        if now > close_time:
            return timedelta(0)
        
        return close_time - now
    
    def minutes_until_next_candle(self, candle_interval_seconds: int = 300) -> float:
        """
        Get minutes until next candle close.
        
        Args:
            candle_interval_seconds: Candle interval (e.g., 300 for 5-minute)
        
        Returns:
            Minutes until next candle
        """
        now = datetime.now(IST)
        seconds_in_minute = now.second + now.microsecond / 1e6
        elapsed_in_interval = (now.minute * 60 + seconds_in_minute) % candle_interval_seconds
        remaining = candle_interval_seconds - elapsed_in_interval
        
        return remaining / 60.0
    
    # =====================================================================
    # SCHEDULED TASKS
    # =====================================================================
    
    def schedule_at(self, target_time: time, callback: Callable) -> None:
        """
        Schedule a callback to run at a specific time (daily).
        
        Args:
            target_time: Time to execute (HH:MM)
            callback: Async function to call
        """
        self._scheduled_callbacks.append((target_time, callback))
        self._logger.info(f"Scheduled callback at {target_time.strftime('%H:%M')}")
    
    async def run_scheduler(self) -> None:
        """
        Main scheduler loop.
        Checks scheduled callbacks and emits events at key times.
        """
        last_check_date = None
        
        while True:
            try:
                now = datetime.now(IST)
                current_date = now.date()
                
                # Daily reset at 09:00
                if (now.hour == DAILY_RESET_HOUR and 
                    now.minute == DAILY_RESET_MINUTE and
                    current_date != last_check_date):
                    
                    await self._emit_event(EventType.DAILY_RESET, {
                        'timestamp': now.isoformat()
                    })
                    self._logger.info("Daily reset triggered at 09:00 IST")
                    last_check_date = current_date
                
                # Market open
                if (now.hour == MARKET_OPEN.hour and
                    now.minute == MARKET_OPEN.minute and
                    self.is_market_open()):
                    
                    await self._emit_event(EventType.SYSTEM_READY, {
                        'event': 'market_open',
                        'timestamp': now.isoformat()
                    })
                    self._logger.info("Market open at 09:15 IST")
                
                # Pre-market data window
                if self.is_pre_market_window():
                    await self._emit_event(EventType.PRE_MARKET_DATA, {
                        'timestamp': now.isoformat()
                    })
                
                # Forced exit at 15:10
                if (now.hour == POS_MGMT_END.hour and
                    now.minute == POS_MGMT_END.minute):
                    
                    await self._emit_event(EventType.FORCED_EXIT, {
                        'reason': 'daily_close_15:10',
                        'timestamp': now.isoformat()
                    })
                    self._logger.info("Forced exit signal at 15:10 IST")
                
                # Run scheduled callbacks
                for target_time, callback in self._scheduled_callbacks:
                    if (now.hour == target_time.hour and
                        now.minute == target_time.minute):
                        try:
                            if asyncio.iscoroutinefunction(callback):
                                await callback()
                            else:
                                callback()
                        except Exception as e:
                            self._logger.error(f"Scheduled callback error: {e}")
                
                # Sleep briefly and recheck
                await asyncio.sleep(30)
            
            except asyncio.CancelledError:
                self._logger.info("Scheduler cancelled")
                break
            except Exception as e:
                self._logger.error(f"Scheduler error: {e}", exc_info=True)
                await asyncio.sleep(5)
    
    # =====================================================================
    # STATE QUERIES
    # =====================================================================
    
    def get_market_state(self) -> Dict[str, bool]:
        """Get current market state."""
        return {
            'market_open': self.is_market_open(),
            'entry_allowed': self.is_in_entry_window(),
            'position_management_allowed': self.is_in_position_management_window(),
            'pre_market_window': self.is_pre_market_window(),
            'event_day': self.is_event_day()
        }
    
    def get_time_info(self) -> Dict:
        """Get detailed time information."""
        now = datetime.now(IST)
        
        return {
            'current_time_ist': now.strftime('%H:%M:%S'),
            'current_date': now.strftime('%Y-%m-%d'),
            'day_of_week': now.strftime('%A'),
            'time_to_market_open': str(self.time_until_market_open()),
            'time_to_entry_close': str(self.time_until_entry_close()),
            'time_to_forced_exit': str(self.time_until_forced_exit())
        }
    
    async def _emit_event(self, event_type: EventType, data: Dict) -> None:
        """Emit scheduled event."""
        try:
            bus = await get_event_bus()
            event = Event(
                event_type=event_type,
                data=data,
                source='Scheduler'
            )
            await bus.emit(event)
        except Exception as e:
            self._logger.error(f"Failed to emit scheduler event: {e}")
