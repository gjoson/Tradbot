"""
Login Manager for Flattrade/PI Connect.
Handles broker authentication, token refresh, and session management.
"""

import asyncio
import logging
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from pytz import timezone

import requests

from config.settings import (
    BROKER_USER, BROKER_PASSWORD, BROKER_API_KEY, BROKER_API_SECRET,
    BROKER_API_BASE, AUTO_LOGOUT_INACTIVITY
)
from core.event_bus import get_event_bus, Event, EventType

logger = logging.getLogger(__name__)
IST = timezone('Asia/Kolkata')


class LoginManager:
    """
    Manages broker authentication and session lifecycle.
    
    Flattrade uses PI Connect API with:
    - User credentials (username, password)
    - API key and secret
    - Auth token for API calls
    """
    
    def __init__(self):
        self.user = BROKER_USER
        self.password = BROKER_PASSWORD
        self.api_key = BROKER_API_KEY
        self.api_secret = BROKER_API_SECRET
        
        self.auth_token = None
        self.session = None
        self.broker_session_id = None
        self.last_activity = datetime.now(IST)
        self._logger = logging.getLogger(f"{__name__}.LoginManager")
    
    async def login(self) -> bool:
        """
        Authenticate with broker via PI Connect.
        
        Returns:
            True if successful, False otherwise
        """
        self._logger.info("Attempting login to Flattrade...")
        
        try:
            # PI Connect login endpoint
            url = f"{BROKER_API_BASE}/auth/login"
            
            payload = {
                "userId": self.user,
                "password": self.password,
                "apiKey": self.api_key
            }
            
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') != 200:
                self._logger.error(f"Login failed: {data.get('message')}")
                await self._emit_event(EventType.LOGIN_FAILED, {
                    'error': data.get('message', 'Unknown error')
                })
                return False
            
            # Extract auth token
            self.auth_token = data.get('data', {}).get('authToken')
            self.broker_session_id = data.get('data', {}).get('sessionId')
            
            if not self.auth_token:
                self._logger.error("No auth token in response")
                await self._emit_event(EventType.LOGIN_FAILED, {'error': 'No token'})
                return False
            
            self.last_activity = datetime.now(IST)
            self._logger.info(f"Login successful. Token: {self.auth_token[:20]}...")
            
            await self._emit_event(EventType.LOGIN_SUCCESS, {
                'user': self.user,
                'session_id': self.broker_session_id
            })
            
            return True
        
        except requests.exceptions.RequestException as e:
            self._logger.error(f"Login request failed: {e}")
            await self._emit_event(EventType.LOGIN_FAILED, {'error': str(e)})
            return False
        except Exception as e:
            self._logger.error(f"Unexpected error during login: {e}", exc_info=True)
            await self._emit_event(EventType.LOGIN_FAILED, {'error': str(e)})
            return False
    
    async def logout(self) -> bool:
        """Logout from broker."""
        self._logger.info("Logging out from broker...")
        
        try:
            if not self.auth_token:
                self._logger.debug("Not logged in, skipping logout")
                return True
            
            url = f"{BROKER_API_BASE}/auth/logout"
            headers = self._build_headers()
            
            response = requests.post(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            self.auth_token = None
            self.broker_session_id = None
            self._logger.info("Logout successful")
            return True
        
        except Exception as e:
            self._logger.error(f"Logout failed: {e}")
            self.auth_token = None
            return False
    
    async def check_session_validity(self) -> bool:
        """
        Check if current session is still valid.
        Re-login if expired or inactive too long.
        
        Returns:
            True if session valid, False if re-login needed
        """
        if not self.auth_token:
            return False
        
        # Check inactivity timeout
        inactive_seconds = (datetime.now(IST) - self.last_activity).total_seconds()
        if inactive_seconds > AUTO_LOGOUT_INACTIVITY:
            self._logger.warning(f"Session inactive for {inactive_seconds}s, re-logging in...")
            await self.logout()
            return await self.login()
        
        # Verify token with broker (optional heartbeat)
        try:
            url = f"{BROKER_API_BASE}/auth/validate"
            headers = self._build_headers()
            response = requests.get(url, headers=headers, timeout=5)
            
            if response.status_code == 200:
                self.last_activity = datetime.now(IST)
                return True
            else:
                self._logger.warning("Token validation failed, re-logging in...")
                await self.emit_token_expired()
                return await self.login()
        
        except Exception as e:
            self._logger.warning(f"Session check failed: {e}")
            return await self.login()
    
    async def emit_token_expired(self) -> None:
        """Emit token expiry event."""
        await self._emit_event(EventType.TOKEN_EXPIRED, {
            'timestamp': datetime.now(IST).isoformat()
        })
    
    def _build_headers(self) -> Dict[str, str]:
        """Build API request headers with auth token."""
        return {
            'Authorization': f'Bearer {self.auth_token}',
            'Content-Type': 'application/json',
            'X-User-Id': self.user,
            'X-API-Key': self.api_key
        }
    
    def get_auth_token(self) -> Optional[str]:
        """Get current auth token."""
        return self.auth_token
    
    def get_headers(self) -> Dict[str, str]:
        """Get headers for API calls."""
        return self._build_headers()
    
    async def _emit_event(self, event_type: EventType, data: Dict[str, Any]) -> None:
        """Emit event via global event bus."""
        try:
            bus = await get_event_bus()
            event = Event(
                event_type=event_type,
                data=data,
                source='LoginManager'
            )
            await bus.emit(event)
        except Exception as e:
            self._logger.error(f"Failed to emit event: {e}")
    
    async def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.now(IST)
    
    def is_authenticated(self) -> bool:
        """Check if currently authenticated."""
        return self.auth_token is not None
