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
        self.token_expiry = None  # datetime when token expires
        self._refresh_task: Optional[asyncio.Task] = None
        self._disabled = False
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
            
            # Extract auth token and expiry
            self.auth_token = data.get('data', {}).get('authToken')
            self.broker_session_id = data.get('data', {}).get('sessionId')
            # Some brokers provide expires_in seconds
            expires_in = data.get('data', {}).get('expires_in') or data.get('data', {}).get('expiry_seconds')
            try:
                if expires_in:
                    self.token_expiry = datetime.now(IST) + timedelta(seconds=int(expires_in))
                else:
                    # Default to one hour if not provided
                    self.token_expiry = datetime.now(IST) + timedelta(seconds=3600)
            except Exception:
                self.token_expiry = datetime.now(IST) + timedelta(seconds=3600)
            
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

            # Start background token refresher
            if self._refresh_task is None or self._refresh_task.done():
                self._refresh_task = asyncio.create_task(self._token_refresher_loop())
            
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
            self.token_expiry = None
            # cancel refresher
            if self._refresh_task:
                try:
                    self._refresh_task.cancel()
                except Exception:
                    pass
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
        
        # If token is near expiry, attempt refresh
        try:
            from config.settings import TOKEN_REFRESH_MARGIN_SECONDS

            if self.token_expiry:
                seconds_left = (self.token_expiry - datetime.now(IST)).total_seconds()
                if seconds_left < TOKEN_REFRESH_MARGIN_SECONDS:
                    self._logger.info(f"Token expiring in {seconds_left:.0f}s, attempting refresh")
                    refreshed = await self._refresh_token()
                    if not refreshed:
                        self._logger.warning("Token refresh failed during session check")
                        await self._emit_event(EventType.TRADING_HALTED, {'reason': 'auth_lost'})
                        return False

        except Exception:
            pass

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

    async def _refresh_token(self) -> bool:
        """Attempt to refresh token using exponential backoff."""
        try:
            from config.settings import TOKEN_REFRESH_MAX_RETRIES, TOKEN_REFRESH_BACKOFF_BASE

            attempt = 0
            while attempt < TOKEN_REFRESH_MAX_RETRIES:
                try:
                    self._logger.info(f"Refreshing auth token (attempt {attempt+1})")
                    # Some brokers provide dedicated refresh endpoint; fall back to login
                    url = f"{BROKER_API_BASE}/auth/refresh"
                    headers = self._build_headers()
                    payload = {}
                    resp = requests.post(url, headers=headers, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        new_token = data.get('data', {}).get('authToken')
                        expires_in = data.get('data', {}).get('expires_in')
                        if new_token:
                            self.auth_token = new_token
                            if expires_in:
                                self.token_expiry = datetime.now(IST) + timedelta(seconds=int(expires_in))
                            self._logger.info("Token refreshed successfully")
                            await self._emit_event(EventType.LOGIN_SUCCESS, {'refreshed': True})
                            return True
                    # fallback to full login
                    ok = await self.login()
                    if ok:
                        return True
                except Exception as e:
                    self._logger.warning(f"Token refresh attempt failed: {e}")

                attempt += 1
                await asyncio.sleep((TOKEN_REFRESH_BACKOFF_BASE ** attempt))

            # If here, refresh failed
            self._logger.error("Token refresh failed after retries; disabling trading")
            await self._emit_event(EventType.TRADING_HALTED, {'reason': 'auth_refresh_failed'})
            self._disabled = True
            return False

        except Exception as e:
            self._logger.error(f"Unexpected error in _refresh_token: {e}")
            return False

    async def _token_refresher_loop(self) -> None:
        """Background loop to refresh token before expiry."""
        try:
            from config.settings import TOKEN_REFRESH_MARGIN_SECONDS

            while True:
                if not self.token_expiry:
                    await asyncio.sleep(30)
                    continue

                seconds_left = (self.token_expiry - datetime.now(IST)).total_seconds()
                sleep_time = max(10, seconds_left - TOKEN_REFRESH_MARGIN_SECONDS)
                await asyncio.sleep(sleep_time)

                # Attempt refresh
                refreshed = await self._refresh_token()
                if not refreshed:
                    # If refresh fails, try again after backoff inside _refresh_token
                    break
        except asyncio.CancelledError:
            self._logger.info("Token refresher cancelled")
        except Exception as e:
            self._logger.error(f"Token refresher loop error: {e}")
    
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
