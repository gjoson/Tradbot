"""
Order Manager: Executes orders with throttling and retry logic.
Ensures ≤10 orders/second rate limit.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List
from pytz import timezone
from enum import Enum

from config.settings import (
    ORDER_THROTTLE_DELAY, ORDER_RETRY_ATTEMPTS, ORDER_RETRY_DELAY,
    SLIPPAGE_TOLERANCE, EXECUTION_BUFFER_SPREAD
)
from core.event_bus import get_event_bus, Event, EventType

logger = logging.getLogger(__name__)
IST = timezone('Asia/Kolkata')


class OrderSide(Enum):
    """Order side."""
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """Order execution status."""
    PENDING = "pending"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass
class OrderLeg:
    """Single leg of a multi-leg order."""
    leg_id: str
    token: str
    instrument: str  # 'CE' or 'PE'
    strike: float
    side: OrderSide
    quantity: int
    price: float  # Limit price
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
    avg_fill_price: float = 0.0
    broker_order_id: Optional[str] = None


@dataclass
class Order:
    """Multi-leg order."""
    order_id: str
    legs: List[OrderLeg] = field(default_factory=list)
    strategy: str = ""
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(IST))
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    
    # Retry tracking
    retry_count: int = 0
    last_error: Optional[str] = None
    
    def is_complete(self) -> bool:
        """Check if order is fully filled or cancelled."""
        return self.status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED]
    
    def avg_fill_price(self) -> float:
        """Get weighted average fill price."""
        if not self.legs:
            return 0.0
        
        total_value = sum(
            leg.avg_fill_price * leg.filled_quantity
            for leg in self.legs
        )
        total_quantity = sum(leg.filled_quantity for leg in self.legs)
        
        return total_value / total_quantity if total_quantity > 0 else 0.0


class OrderManager:
    """
    Order execution manager with:
    - Rate limiting (max 10 orders/second)
    - Retry logic
    - Atomic multi-leg execution
    - Cancellation handling
    """
    
    def __init__(self):
        self._logger = logging.getLogger(f"{__name__}.OrderManager")
        
        # Order queue for rate limiting
        self._order_queue: asyncio.Queue = None  # Initialized later
        self._orders: Dict[str, Order] = {}
        
        # Rate limiter
        self._last_order_time = 0
        self._order_history = []  # For rate limit tracking
        
        self._is_processing = False
        # Dependencies (injected)
        self.trade_logger = None
        self.broker_interface = None
    
    async def initialize(self) -> None:
        """Initialize async components."""
        self._order_queue = asyncio.Queue()
        self._logger.info("OrderManager initialized")

    def set_dependencies(self, trade_logger, broker_interface) -> None:
        """Inject dependencies required for idempotency and broker calls."""
        self.trade_logger = trade_logger
        self.broker_interface = broker_interface
    
    async def place_order(self, legs: List[Dict], strategy: str) -> str:
        """
        Place a multi-leg order.
        
        Args:
            legs: List of leg dicts with token, strike, instrument, side, price
            strategy: Strategy name
        
        Returns:
            Order ID
        """
        try:
            order_id = str(uuid.uuid4())[:8]
            client_order_id = f"cli_{order_id}"

            # Idempotency: check DB if this client_order_id already exists
            try:
                if self.trade_logger:
                    existing = await self.trade_logger.get_order_by_client_id(client_order_id)
                    if existing:
                        self._logger.warning(f"Duplicate client_order_id detected: {client_order_id}, returning existing order")
                        return existing.get('client_order_id')
            except Exception:
                pass
            
            # Create order
            order = Order(
                order_id=order_id,
                strategy=strategy
            )
            
            # Create legs
            for leg_data in legs:
                leg = OrderLeg(
                    leg_id=f"{order_id}_{len(order.legs)}",
                    token=leg_data['token'],
                    instrument=leg_data['instrument'],
                    strike=leg_data['strike'],
                    side=OrderSide[leg_data['side'].upper()],
                    quantity=leg_data['quantity'],
                    price=leg_data['price']
                )
                order.legs.append(leg)
            
            self._orders[order_id] = order

            # Persist client order id for idempotency
            try:
                if self.trade_logger:
                    await self.trade_logger.record_order(client_order_id, None, {'order_id': order_id, 'strategy': strategy})
            except Exception:
                self._logger.warning("Failed to record client_order_id in DB")
            
            # Queue for execution
            # Attach client_order_id to order for later use
            order.client_order_id = client_order_id
            await self._order_queue.put(order)
            
            self._logger.info(f"Order placed: {order_id} ({strategy}) with {len(legs)} legs")
            
            return order_id
        
        except Exception as e:
            self._logger.error(f"Error placing order: {e}")
            raise
    
    async def execute_orders(self) -> None:
        """
        Main order processing loop.
        Implements rate limiting and retry logic.
        """
        while True:
            try:
                # Get order from queue
                order = await self._order_queue.get()
                
                # Apply rate limiting
                await self._apply_rate_limit()
                
                # Execute order with retry logic
                success = await self._execute_order_with_retry(order)
                
                if success:
                    order.status = OrderStatus.FILLED
                    order.filled_at = datetime.now(IST)
                    await self._emit_event(EventType.ORDER_FILLED, {
                        'order_id': order.order_id,
                        'strategy': order.strategy
                    })
                else:
                    order.status = OrderStatus.FAILED
                    await self._emit_event(EventType.ORDER_FAILED, {
                        'order_id': order.order_id,
                        'reason': order.last_error
                    })
                
                self._order_queue.task_done()
            
            except asyncio.CancelledError:
                self._logger.info("Order execution loop cancelled")
                break
            except Exception as e:
                self._logger.error(f"Error in order execution loop: {e}", exc_info=True)
    
    async def _apply_rate_limit(self) -> None:
        """Enforce max 10 orders per second."""
        now = datetime.now(IST)
        
        # Remove old entries (> 1 second ago)
        self._order_history = [
            t for t in self._order_history
            if (now - t).total_seconds() < 1.0
        ]
        
        # If we've reached the limit, wait
        if len(self._order_history) >= 10:
            oldest = self._order_history[0]
            wait_time = 1.0 - (now - oldest).total_seconds()
            if wait_time > 0:
                self._logger.debug(f"Rate limit reached, waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
        
        # Record this order timestamp
        self._order_history.append(datetime.now(IST))
    
    async def _execute_order_with_retry(self, order: Order) -> bool:
        """
        Execute order with retry logic.
        
        Args:
            order: Order to execute
        
        Returns:
            True if order filled, False if failed
        """
        for attempt in range(ORDER_RETRY_ATTEMPTS):
            try:
                order.retry_count = attempt + 1
                order.submitted_at = datetime.now(IST)

                # Set current client order id for legs
                self.current_client_order_id = getattr(order, 'client_order_id', None)
                
                self._logger.info(
                    f"Executing order {order.order_id} "
                    f"(attempt {attempt + 1}/{ORDER_RETRY_ATTEMPTS})"
                )
                
                # Validate order before execution
                if not await self._validate_order(order):
                    return False
                
                # Execute order legs (atomic)
                success = await self._execute_legs(order)
                
                if success:
                    return True
                
                # If not the last attempt, wait before retry
                if attempt < ORDER_RETRY_ATTEMPTS - 1:
                    self._logger.debug(
                        f"Order execution failed, retry in {ORDER_RETRY_DELAY}s"
                    )
                    await asyncio.sleep(ORDER_RETRY_DELAY)
            
            except Exception as e:
                self._logger.error(f"Order execution error (attempt {attempt + 1}): {e}")
                order.last_error = str(e)
        
        self._logger.error(f"Order {order.order_id} failed after {ORDER_RETRY_ATTEMPTS} attempts")
        return False
    
    async def _validate_order(self, order: Order) -> bool:
        """Validate order before execution."""
        try:
            if not order.legs:
                order.last_error = "Order has no legs"
                return False
            
            for leg in order.legs:
                if leg.quantity <= 0:
                    order.last_error = "Invalid quantity"
                    return False
                
                if leg.price <= 0:
                    order.last_error = "Invalid price"
                    return False
            
            return True
        
        except Exception as e:
            order.last_error = str(e)
            return False
    
    async def _execute_legs(self, order: Order) -> bool:
        """
        Execute all legs of an order atomically.
        If one fails, attempt to cancel others.
        """
        try:
            results = []
            
            # Execute each leg
            for leg in order.legs:
                success = await self._execute_leg(leg)
                results.append(success)
                
                if not success:
                    self._logger.warning(f"Leg {leg.leg_id} execution failed")
            
            # Check if all legs succeeded
            all_success = all(results)
            
            if not all_success:
                # Attempt to cancel successfully placed legs
                for i, leg in enumerate(order.legs):
                    if results[i] and leg.broker_order_id:
                        await self._cancel_leg(leg)
            
            return all_success
        
        except Exception as e:
            self._logger.error(f"Error executing legs: {e}")
            return False
    
    async def _execute_leg(self, leg: OrderLeg) -> bool:
        """
        Execute single leg (place order with broker).
        
        In production, this would call broker API.
        For now, we simulate.
        """
        try:
            self._logger.debug(
                f"Executing leg {leg.leg_id}: "
                f"{leg.side.value} {leg.quantity}x {leg.strike} {leg.instrument} @ {leg.price}"
            )
            
            # Real broker API call
            try:
                if not self.broker_interface:
                    # Fallback to simulated behavior
                    await asyncio.sleep(0.05)
                    leg.broker_order_id = str(uuid.uuid4())[:8]
                else:
                    # Use order-level client id if available
                    client_order_id = getattr(leg, 'client_order_id', None) or getattr(self, 'current_client_order_id', None)
                    result = await self.broker_interface.place_order(
                        token=leg.token,
                        side=leg.side.value,
                        quantity=leg.quantity,
                        price=leg.price,
                        client_order_id=client_order_id
                    )
                    if not result:
                        self._logger.error("Broker rejected leg placement")
                        return False
                    leg.broker_order_id = result.get('order_id')

                leg.status = OrderStatus.FILLED
                leg.filled_quantity = leg.quantity
                leg.avg_fill_price = leg.price
            
            self._logger.info(
                f"Leg executed: {leg.leg_id} "
                f"filled {leg.filled_quantity}@{leg.avg_fill_price}"
            )
            
            return True
        
        except Exception as e:
            self._logger.error(f"Leg execution failed: {e}")
            return False
    
    async def _cancel_leg(self, leg: OrderLeg) -> bool:
        """Cancel a placed leg."""
        try:
            if not leg.broker_order_id:
                return False
            
            self._logger.info(f"Cancelling leg {leg.leg_id} (broker_order_id: {leg.broker_order_id})")
            
            # Simulate broker API call
            await asyncio.sleep(0.05)
            
            leg.status = OrderStatus.CANCELLED
            leg.cancelled_at = datetime.now(IST)
            
            return True
        
        except Exception as e:
            self._logger.error(f"Leg cancellation failed: {e}")
            return False
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an entire order."""
        try:
            if order_id not in self._orders:
                self._logger.warning(f"Order not found: {order_id}")
                return False
            
            order = self._orders[order_id]
            
            # Cancel all legs
            for leg in order.legs:
                if leg.broker_order_id and leg.status == OrderStatus.FILLED:
                    await self._cancel_leg(leg)
            
            order.status = OrderStatus.CANCELLED
            order.cancelled_at = datetime.now(IST)
            
            self._logger.info(f"Order cancelled: {order_id}")
            
            await self._emit_event(EventType.ORDER_CANCELLED, {
                'order_id': order_id
            })
            
            return True
        
        except Exception as e:
            self._logger.error(f"Order cancellation error: {e}")
            return False
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order status."""
        return self._orders.get(order_id)
    
    async def _emit_event(self, event_type: EventType, data: Dict) -> None:
        """Emit event via global event bus."""
        try:
            bus = await get_event_bus()
            event = Event(
                event_type=event_type,
                data=data,
                source='OrderManager'
            )
            await bus.emit(event)
        except Exception as e:
            self._logger.error(f"Failed to emit event: {e}")
