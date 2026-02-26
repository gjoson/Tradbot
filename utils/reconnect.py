"""
Reconnection and resilience utilities.
Handles graceful reconnection attempts with exponential backoff.
"""

import asyncio
import logging
from typing import Callable, Optional
from datetime import datetime, timedelta
from pytz import timezone

logger = logging.getLogger(__name__)
IST = timezone('Asia/Kolkata')


class ReconnectionManager:
    """
    Manages reconnection attempts with exponential backoff.
    
    Used for WebSocket reconnection and broker API recovery.
    """
    
    def __init__(self, max_retries: int = 10, initial_delay: float = 5.0):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self._logger = logging.getLogger(f"{__name__}.ReconnectionManager")
        self._retry_count = 0
        self._last_retry_time = None
        self._is_connected = False
    
    async def attempt_reconnection(self, connect_func: Callable) -> bool:
        """
        Attempt reconnection with exponential backoff.
        
        Args:
            connect_func: Async function to call to establish connection
        
        Returns:
            True if connected successfully, False if max retries exceeded
        """
        while self._retry_count < self.max_retries:
            try:
                # Calculate backoff delay
                delay = self.initial_delay * (2 ** self._retry_count)
                
                self._logger.info(
                    f"Reconnection attempt {self._retry_count + 1}/{self.max_retries}, "
                    f"waiting {delay:.1f}s"
                )
                
                await asyncio.sleep(delay)
                
                # Attempt connection
                self._last_retry_time = datetime.now(IST)
                result = await connect_func()
                
                if result:
                    self._is_connected = True
                    self._retry_count = 0
                    self._logger.info("Reconnection successful")
                    return True
                
                self._retry_count += 1
            
            except Exception as e:
                self._logger.warning(f"Reconnection attempt failed: {e}")
                self._retry_count += 1
        
        self._logger.error(f"Reconnection failed after {self.max_retries} attempts")
        return False
    
    def reset(self) -> None:
        """Reset retry counter (after successful connection)."""
        self._retry_count = 0
        self._is_connected = True
    
    def is_connected(self) -> bool:
        """Check current connection status."""
        return self._is_connected
    
    def get_retry_count(self) -> int:
        """Get current retry count."""
        return self._retry_count


async def retry_with_backoff(
    func: Callable,
    max_attempts: int = 5,
    initial_delay: float = 1.0,
    backoff_multiplier: float = 2.0
) -> Optional[object]:
    """
    Retry a function with exponential backoff.
    
    Args:
        func: Async function to retry
        max_attempts: Maximum retry attempts
        initial_delay: Initial delay in seconds
        backoff_multiplier: Multiplier for exponential backoff
    
    Returns:
        Result of successful function call or None
    """
    delay = initial_delay
    
    for attempt in range(max_attempts):
        try:
            result = await func()
            return result
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed: {e}")
            
            if attempt < max_attempts - 1:
                logger.info(f"Retrying in {delay:.2f}s...")
                await asyncio.sleep(delay)
                delay *= backoff_multiplier
    
    logger.error(f"All {max_attempts} attempts failed")
    return None


class CircuitBreaker:
    """
    Circuit breaker pattern for preventing cascading failures.
    
    States: CLOSED (normal), OPEN (failing), HALF_OPEN (recovery)
    """
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._last_failure_time = None
        self._state = 'CLOSED'  # CLOSED, OPEN, HALF_OPEN
        self._logger = logging.getLogger(f"{__name__}.CircuitBreaker")
    
    def record_failure(self) -> None:
        """Record a failure."""
        self._failure_count += 1
        self._last_failure_time = datetime.now(IST)
        
        if self._failure_count >= self.failure_threshold:
            self._state = 'OPEN'
            self._logger.error(
                f"Circuit breaker OPEN after {self._failure_count} failures"
            )
    
    def record_success(self) -> None:
        """Record a success."""
        if self._state == 'HALF_OPEN':
            self._state = 'CLOSED'
            self._failure_count = 0
            self._logger.info("Circuit breaker CLOSED")
    
    def is_available(self) -> bool:
        """Check if requests are allowed."""
        if self._state == 'CLOSED':
            return True
        
        if self._state == 'OPEN':
            # Check if recovery timeout elapsed
            if self._last_failure_time:
                elapsed = (datetime.now(IST) - self._last_failure_time).total_seconds()
                if elapsed >= self.recovery_timeout:
                    self._state = 'HALF_OPEN'
                    self._logger.info("Circuit breaker HALF_OPEN (recovery mode)")
                    return True
            return False
        
        # HALF_OPEN: allow one request to test
        return True
    
    def get_state(self) -> str:
        """Get current circuit breaker state."""
        return self._state
