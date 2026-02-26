"""
Internal Event Bus for Tradbot.
Provides pub/sub architecture for inter-module communication.
Ensures no blocking network calls on strategy thread.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, List, Optional, Dict, Any
from pytz import timezone

logger = logging.getLogger(__name__)
IST = timezone('Asia/Kolkata')


class EventType(Enum):
    """All event types in the system."""
    
    # Market Data Events
    MARKET_TICK = "market_tick"
    OPTION_CHAIN_UPDATE = "option_chain_update"
    VIX_UPDATE = "vix_update"
    CANDLE_CLOSE = "candle_close"
    PRE_MARKET_DATA = "pre_market_data"
    
    # Connection Events
    WEBSOCKET_CONNECTED = "websocket_connected"
    WEBSOCKET_DISCONNECTED = "websocket_disconnected"
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    TOKEN_EXPIRED = "token_expired"
    
    # Market Classification Events
    MARKET_CLASSIFIED = "market_classified"
    MARKET_CLASSIFICATION_FAILED = "market_classification_failed"
    
    # Trading Events
    TRADE_SIGNAL = "trade_signal"
    ENTRY_PERMISSIBLE = "entry_permissible"
    ENTRY_NOT_ALLOWED = "entry_not_allowed"
    
    # Order Events
    ORDER_PLACED = "order_placed"
    ORDER_FAILED = "order_failed"
    ORDER_FILLED = "order_filled"
    ORDER_PARTIAL_FILL = "order_partial_fill"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_REJECTED = "order_rejected"
    
    # Position Events
    POSITION_OPENED = "position_opened"
    POSITION_UPDATED = "position_updated"
    POSITION_CLOSED = "position_closed"
    
    # Stop Loss / Exit Events
    STOP_LOSS_TRIGGERED = "stop_loss_triggered"
    TRAILING_STOP_TRIGGERED = "trailing_stop_triggered"
    PROFIT_TARGET_HIT = "profit_target_hit"
    FORCED_EXIT = "forced_exit"
    
    # Risk Management Events
    RISK_BREACH = "risk_breach"
    DAILY_LOSS_LIMIT_HIT = "daily_loss_limit_hit"
    WEEKLY_LOSS_LIMIT_HIT = "weekly_loss_limit_hit"
    TRADING_HALTED = "trading_halted"
    
    # System Events
    SYSTEM_READY = "system_ready"
    SYSTEM_SHUTDOWN = "system_shutdown"
    DAILY_RESET = "daily_reset"
    ERROR = "system_error"


@dataclass
class Event:
    """Base event object."""
    event_type: EventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(IST))
    data: Dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None  # Module that emitted this event
    
    def __str__(self):
        return f"{self.event_type.value}@{self.timestamp.strftime('%H:%M:%S')} | {self.data}"


class EventBus:
    """
    Asynchronous event pub/sub system.
    
    Thread-safe and async-safe for inter-module communication.
    Subscribers are callbacks that handle events asynchronously.
    """
    
    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = {}
        self._event_queue: asyncio.Queue = None  # Will be initialized
        self._logger = logging.getLogger(f"{__name__}.EventBus")
    
    async def initialize(self):
        """Initialize async components."""
        self._event_queue = asyncio.Queue(maxsize=1000)
        self._logger.info("EventBus initialized with async queue")
    
    def subscribe(self, event_type: EventType, handler: Callable) -> None:
        """
        Subscribe a handler to an event type.
        
        Args:
            event_type: EventType to listen for
            handler: Async callable(Event) to execute on event
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        
        self._subscribers[event_type].append(handler)
        self._logger.debug(f"Subscribed {handler.__name__} to {event_type.value}")
    
    def unsubscribe(self, event_type: EventType, handler: Callable) -> None:
        """Remove a subscriber."""
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                h for h in self._subscribers[event_type] if h != handler
            ]
    
    async def emit(self, event: Event) -> None:
        """
        Emit an event asynchronously.
        Queue-based to prevent blocking.
        
        Args:
            event: Event object to emit
        """
        try:
            await self._event_queue.put(event)
            self._logger.debug(f"Event queued: {event}")
        except asyncio.QueueFull:
            self._logger.error(f"Event queue full, dropping: {event}")
    
    async def process_events(self) -> None:
        """
        Main event processing loop.
        Run this in the main asyncio loop.
        """
        while True:
            try:
                event = await self._event_queue.get()
                await self._dispatch_event(event)
                self._event_queue.task_done()
            except asyncio.CancelledError:
                self._logger.info("Event processor cancelled")
                break
            except Exception as e:
                self._logger.error(f"Error processing event: {e}", exc_info=True)
    
    async def _dispatch_event(self, event: Event) -> None:
        """
        Dispatch event to all subscribers.
        Subscribers are awaited sequentially (safe execution order).
        """
        handlers = self._subscribers.get(event.event_type, [])
        
        if not handlers:
            self._logger.debug(f"No subscribers for {event.event_type.value}")
            return
        
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    # Call sync handler in thread pool
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, handler, event)
            except Exception as e:
                self._logger.error(
                    f"Error in handler {handler.__name__} for {event.event_type.value}: {e}",
                    exc_info=True
                )
    
    async def wait_for_event(self, event_type: EventType, timeout: float = 30.0) -> Optional[Event]:
        """
        Wait for a specific event type (blocking).
        Useful for synchronization points.
        
        Args:
            event_type: EventType to wait for
            timeout: Maximum seconds to wait
        
        Returns:
            Event if received, None if timeout
        """
        received_event = None
        
        async def capture_event(event: Event):
            nonlocal received_event
            received_event = event
        
        self.subscribe(event_type, capture_event)
        
        try:
            # Wait for the event or timeout
            start = datetime.now(IST)
            while received_event is None:
                elapsed = (datetime.now(IST) - start).total_seconds()
                if elapsed > timeout:
                    self._logger.warning(f"Timeout waiting for {event_type.value}")
                    break
                await asyncio.sleep(0.1)
            
            return received_event
        finally:
            self.unsubscribe(event_type, capture_event)
    
    def get_subscriber_count(self, event_type: EventType) -> int:
        """Get number of subscribers for an event type."""
        return len(self._subscribers.get(event_type, []))
    
    async def shutdown(self) -> None:
        """Shutdown event bus gracefully."""
        self._logger.info("EventBus shutting down")
        await self._event_queue.join()
        self._logger.info("EventBus shutdown complete")


# Global event bus instance
_event_bus_instance: Optional[EventBus] = None


async def get_event_bus() -> EventBus:
    """Get the global event bus instance (lazy initialization)."""
    global _event_bus_instance
    if _event_bus_instance is None:
        _event_bus_instance = EventBus()
        await _event_bus_instance.initialize()
    return _event_bus_instance


async def reset_event_bus() -> None:
    """Reset event bus (for testing)."""
    global _event_bus_instance
    if _event_bus_instance:
        await _event_bus_instance.shutdown()
    _event_bus_instance = None
