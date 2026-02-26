"""
WebSocket client for Flattrade real-time market data.
Handles connection, subscriptions, and reconnection logic.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, List, Callable, Dict, Any
from pytz import timezone

import websockets
from websockets.client import WebSocketClientProtocol

from config.settings import (
    BROKER_WS_URL, WEBSOCKET_HEARTBEAT_INTERVAL, WEBSOCKET_TIMEOUT,
    WEBSOCKET_RECONNECT_DELAY
)
from core.event_bus import get_event_bus, Event, EventType
from core.login_manager import LoginManager

logger = logging.getLogger(__name__)
IST = timezone('Asia/Kolkata')


class WebSocketClient:
    """
    Asynchronous WebSocket client for Flattrade PI Connect.
    
    Handles:
    - Connection management
    - Subscription/unsubscription
    - Heartbeat
    - Reconnection on disconnect
    - Auto-resubscribe after reconnection
    """
    
    def __init__(self, login_manager: LoginManager):
        self.login_manager = login_manager
        self.ws: Optional[WebSocketClientProtocol] = None
        self.connected = False
        
        self._subscriptions: Dict[str, Any] = {}  # token -> subscription details
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        
        self._logger = logging.getLogger(f"{__name__}.WebSocketClient")
        self._message_handlers: List[Callable] = []
    
    async def connect(self) -> bool:
        """
        Establish WebSocket connection.
        
        Returns:
            True if connected successfully
        """
        self._logger.info(f"Connecting to {BROKER_WS_URL}...")
        
        try:
            # Build auth headers/params
            auth_token = self.login_manager.get_auth_token()
            if not auth_token:
                self._logger.error("No auth token available for WebSocket")
                return False
            
            # Flattrade expects auth in URL or headers
            url_with_auth = f"{BROKER_WS_URL}?token={auth_token}"
            
            self.ws = await websockets.connect(
                url_with_auth,
                close_timeout=WEBSOCKET_TIMEOUT,
                ping_interval=WEBSOCKET_HEARTBEAT_INTERVAL
            )
            
            self.connected = True
            self._logger.info("WebSocket connected successfully")
            
            await self._emit_event(EventType.WEBSOCKET_CONNECTED, {
                'url': BROKER_WS_URL,
                'timestamp': datetime.now(IST).isoformat()
            })
            
            # Start receive and heartbeat tasks
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            
            return True
        
        except Exception as e:
            self._logger.error(f"WebSocket connection failed: {e}")
            self.connected = False
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        self._logger.info("Disconnecting WebSocket...")
        
        self.connected = False
        
        # Cancel tasks
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._receive_task:
            self._receive_task.cancel()
        if self._reconnect_task:
            self._reconnect_task.cancel()
        
        # Close connection
        if self.ws:
            try:
                await self.ws.close()
            except Exception as e:
                self._logger.debug(f"Error closing WS: {e}")
        
        self.ws = None
        
        await self._emit_event(EventType.WEBSOCKET_DISCONNECTED, {
            'timestamp': datetime.now(IST).isoformat()
        })
    
    async def subscribe(self, token: str, mode: str = 'LTP') -> None:
        """
        Subscribe to market data for a token.
        
        Args:
            token: Instrument token (e.g., '99926000' for NIFTY)
            mode: Subscription mode ('LTP', 'QUOTE', 'FULL')
        """
        if not self.connected:
            self._logger.warning(f"Cannot subscribe {token}, not connected")
            return
        
        try:
            if token in self._subscriptions:
                self._logger.debug(f"Already subscribed to {token}")
                return
            
            message = {
                'mode': 'subscribe',
                'token': token,
                'dataMode': mode
            }
            
            await self.ws.send(json.dumps(message))
            self._subscriptions[token] = {'mode': mode, 'subscribed_at': datetime.now(IST)}
            self._logger.info(f"Subscribed to {token} ({mode})")
        
        except Exception as e:
            self._logger.error(f"Subscription failed for {token}: {e}")
    
    async def unsubscribe(self, token: str) -> None:
        """Unsubscribe from a token."""
        if not self.connected:
            return
        
        try:
            message = {
                'mode': 'unsubscribe',
                'token': token
            }
            
            await self.ws.send(json.dumps(message))
            if token in self._subscriptions:
                del self._subscriptions[token]
            self._logger.info(f"Unsubscribed from {token}")
        
        except Exception as e:
            self._logger.error(f"Unsubscription failed for {token}: {e}")
    
    def add_message_handler(self, handler: Callable) -> None:
        """
        Add a message handler for incoming market data.
        Handler signature: handler(message: Dict)
        """
        self._message_handlers.append(handler)
    
    async def _receive_loop(self) -> None:
        """Main receive loop for incoming messages."""
        try:
            while self.connected and self.ws:
                try:
                    message_str = await asyncio.wait_for(
                        self.ws.recv(),
                        timeout=WEBSOCKET_TIMEOUT * 2
                    )
                    
                    message = json.loads(message_str)
                    
                    # Process message
                    await self._process_message(message)
                
                except asyncio.TimeoutError:
                    self._logger.warning("WebSocket receive timeout")
                    break
                except json.JSONDecodeError:
                    self._logger.warning(f"Invalid JSON message: {message_str}")
                    continue
        
        except asyncio.CancelledError:
            self._logger.info("Receive loop cancelled")
        except Exception as e:
            self._logger.error(f"Receive loop error: {e}")
        finally:
            self.connected = False
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())
    
    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeat to keep connection alive."""
        try:
            while self.connected and self.ws:
                await asyncio.sleep(WEBSOCKET_HEARTBEAT_INTERVAL)
                
                try:
                    heartbeat = {'mode': 'ping'}
                    await self.ws.send(json.dumps(heartbeat))
                    self._logger.debug("Heartbeat sent")
                except Exception as e:
                    self._logger.warning(f"Heartbeat failed: {e}")
                    break
        
        except asyncio.CancelledError:
            self._logger.info("Heartbeat loop cancelled")
    
    async def _reconnect_loop(self) -> None:
        """Attempt to reconnect on disconnect."""
        self._logger.info("WebSocket disconnected, attempting reconnect...")
        
        retry_count = 0
        max_retries = 5
        
        while retry_count < max_retries:
            await asyncio.sleep(WEBSOCKET_RECONNECT_DELAY * (2 ** retry_count))
            
            self._logger.info(f"Reconnect attempt {retry_count + 1}/{max_retries}")
            
            # Ensure we have valid auth token
            if not await self.login_manager.check_session_validity():
                self._logger.warning("Auth check failed, login needed")
                if not await self.login_manager.login():
                    retry_count += 1
                    continue
            
            if await self.connect():
                # Resubscribe to previous tokens
                for token in list(self._subscriptions.keys()):
                    mode = self._subscriptions[token].get('mode', 'LTP')
                    await self.subscribe(token, mode)
                self._logger.info("Reconnection successful")
                return
            
            retry_count += 1
        
        self._logger.error(f"Reconnection failed after {max_retries} attempts")
        await self._emit_event(EventType.WEBSOCKET_DISCONNECTED, {
            'reason': 'max_retries_exceeded',
            'timestamp': datetime.now(IST).isoformat()
        })
    
    async def _process_message(self, message: Dict[str, Any]) -> None:
        """Process incoming market data message."""
        try:
            # Call all registered handlers
            for handler in self._message_handlers:
                if asyncio.iscoroutinefunction(handler):
                    await handler(message)
                else:
                    handler(message)
        
        except Exception as e:
            self._logger.error(f"Error processing message: {e}")
    
    async def _emit_event(self, event_type: EventType, data: Dict[str, Any]) -> None:
        """Emit event via global event bus."""
        try:
            bus = await get_event_bus()
            event = Event(
                event_type=event_type,
                data=data,
                source='WebSocketClient'
            )
            await bus.emit(event)
        except Exception as e:
            self._logger.error(f"Failed to emit event: {e}")
    
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self.connected and self.ws is not None
    
    def get_subscriptions(self) -> List[str]:
        """Get list of subscribed tokens."""
        return list(self._subscriptions.keys())
