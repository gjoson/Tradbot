"""
Broker Interface: Gateway to Flattrade PI Connect API.
Handles order placement, position queries, and market data retrieval.
"""

import logging
import asyncio
from typing import Optional, Dict, List, Any
from datetime import datetime
import requests
from pytz import timezone

from config.settings import BROKER_API_BASE
from core.login_manager import LoginManager
from core.event_bus import get_event_bus, Event, EventType

logger = logging.getLogger(__name__)
IST = timezone('Asia/Kolkata')


class BrokerInterface:
    """
    Interface to Flattrade broker API (PI Connect).
    
    Responsibilities:
    - Place orders (limit only)
    - Query open positions
    - Fetch option chain and market data
    - Handle authentication
    """
    
    def __init__(self, login_manager: LoginManager):
        self.login_manager = login_manager
        self._logger = logging.getLogger(f"{__name__}.BrokerInterface")
        self._session = requests.Session()
    
    # =====================================================================
    # ORDER PLACEMENT
    # =====================================================================
    
    async def place_order(
        self,
        token: str,
        side: str,  # 'BUY' or 'SELL'
        quantity: int,
        price: float,
        order_type: str = 'LIMIT',
        product_type: str = 'MIS'
        , client_order_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Place a single leg order.
        
        Args:
            token: Broker token for instrument
            side: 'BUY' or 'SELL'
            quantity: Quantity
            price: Limit price
            order_type: 'LIMIT' (only option permitted)
            product_type: 'MIS' or 'CNC'
        
        Returns:
            Order response dict with order_id, or None if failed
        """
        try:
            # Verify authentication
            if not await self.login_manager.check_session_validity():
                self._logger.error("Authentication failed before order placement")
                return None
            
            url = f"{BROKER_API_BASE}/order/place"
            headers = self.login_manager.get_headers()
            
            payload = {
                'mode': 'REGULAR',
                'exchange': 'NFO',  # NIFTY options on NFO segment
                'symbol': token,
                'quantity': quantity,
                'price': price,
                'pricetype': order_type,
                'product': product_type,
                'ordertype': side,  # For Flattrade API
                'duration': 'DAY'
            }
            if client_order_id:
                payload['clientOrderId'] = client_order_id
            
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') != 200:
                self._logger.error(
                    f"Order placement failed: {data.get('message')}"
                )
                return None
            
            order_id = data.get('data', {}).get('orderid')
            self._logger.info(
                f"Order placed: {order_id} | {side} {quantity}@{price}"
            )
            
            return {
                'order_id': order_id,
                'client_order_id': client_order_id,
                'token': token,
                'side': side,
                'quantity': quantity,
                'price': price,
                'timestamp': datetime.now(IST).isoformat()
            }
        
        except requests.exceptions.RequestException as e:
            self._logger.error(f"Order placement API error: {e}")
            return None
        except Exception as e:
            self._logger.error(f"Unexpected error during order placement: {e}")
            return None
    
    async def place_basket_order(
        self,
        legs: List[Dict[str, Any]]
    ) -> Optional[List[str]]:
        """
        Place multi-leg basket order (atomic execution).
        
        Args:
            legs: List of leg dicts:
                   {
                       'token': '...',
                       'side': 'BUY'/'SELL',
                       'quantity': int,
                       'price': float
                   }
        
        Returns:
            List of order IDs if successful, None if failed
        """
        try:
            order_ids = []
            
            # Place each leg
            for leg in legs:
                order_result = await self.place_order(
                    token=leg['token'],
                    side=leg['side'],
                    quantity=leg['quantity'],
                    price=leg['price']
                )
                
                if not order_result:
                    # If any leg fails, cancel previous legs
                    self._logger.error(f"Basket order failed at leg: {leg}")
                    
                    for order_id in order_ids:
                        await self.cancel_order(order_id)
                    
                    return None
                
                order_ids.append(order_result['order_id'])
            
            self._logger.info(f"Basket order placed: {order_ids}")
            return order_ids
        
        except Exception as e:
            self._logger.error(f"Basket order error: {e}")
            return None
    
    # =====================================================================
    # ORDER MANAGEMENT
    # =====================================================================
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        try:
            if not await self.login_manager.check_session_validity():
                return False
            
            url = f"{BROKER_API_BASE}/order/cancel"
            headers = self.login_manager.get_headers()
            
            payload = {
                'orderid': order_id
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') == 200:
                self._logger.info(f"Order cancelled: {order_id}")
                return True
            else:
                self._logger.error(f"Cancellation failed: {data.get('message')}")
                return False
        
        except Exception as e:
            self._logger.error(f"Cancellation error: {e}")
            return False
    
    async def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order status from broker."""
        try:
            if not await self.login_manager.check_session_validity():
                return None
            
            url = f"{BROKER_API_BASE}/order/status"
            headers = self.login_manager.get_headers()
            
            params = {'orderid': order_id}
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') == 200:
                return data.get('data', {})
            
            return None
        
        except Exception as e:
            self._logger.error(f"Order status query error: {e}")
            return None
    
    # =====================================================================
    # POSITION QUERIES
    # =====================================================================
    
    async def get_open_positions(self) -> Optional[List[Dict[str, Any]]]:
        """
        Get all open positions from broker.
        
        Returns:
            List of position dicts or None if error
        """
        try:
            if not await self.login_manager.check_session_validity():
                return None
            
            url = f"{BROKER_API_BASE}/position/open"
            headers = self.login_manager.get_headers()
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') == 200:
                positions = data.get('data', [])
                self._logger.debug(f"Open positions: {len(positions)}")
                return positions
            
            return None
        
        except Exception as e:
            self._logger.error(f"Position query error: {e}")
            return None
    
    # =====================================================================
    # MARKET DATA
    # =====================================================================
    
    async def get_option_chain(self, exchange: str = 'NFO', symbol: str = 'NIFTY') -> Optional[Dict]:
        """
        Fetch complete option chain for current expiry.
        
        Args:
            exchange: 'NFO'
            symbol: 'NIFTY'
        
        Returns:
            Option chain dict
        """
        try:
            if not await self.login_manager.check_session_validity():
                return None
            
            url = f"{BROKER_API_BASE}/data/optionchain"
            headers = self.login_manager.get_headers()
            
            params = {
                'exchange': exchange,
                'symbol': symbol
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') == 200:
                self._logger.debug("Option chain fetched")
                return data.get('data', {})
            
            return None
        
        except Exception as e:
            self._logger.error(f"Option chain fetch error: {e}")
            return None
    
    async def get_vix(self) -> Optional[float]:
        """
        Fetch India VIX value.
        
        Returns:
            VIX value or None
        """
        try:
            if not await self.login_manager.check_session_validity():
                return None
            
            url = f"{BROKER_API_BASE}/data/vix"
            headers = self.login_manager.get_headers()
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') == 200:
                vix = float(data.get('data', {}).get('value', 0))
                self._logger.debug(f"VIX: {vix:.2f}")
                return vix
            
            return None
        
        except Exception as e:
            self._logger.error(f"VIX fetch error: {e}")
            return None
    
    async def get_nifty_pcr(self) -> Optional[float]:
        """
        Calculate Put/Call ratio from option chain.
        PCR = Total Put OI / Total Call OI
        """
        try:
            option_chain = await self.get_option_chain()
            if not option_chain:
                return None
            
            total_put_oi = 0
            total_call_oi = 0
            
            for option_data in option_chain.get('options', []):
                if 'pe' in option_data:
                    total_put_oi += option_data['pe'].get('open_interest', 0)
                if 'ce' in option_data:
                    total_call_oi += option_data['ce'].get('open_interest', 0)
            
            if total_call_oi == 0:
                return 1.0
            
            pcr = total_put_oi / total_call_oi
            self._logger.debug(f"PCR: {pcr:.2f}")
            
            return pcr
        
        except Exception as e:
            self._logger.error(f"PCR calculation error: {e}")
            return None
    
    async def get_quote(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Get live quote for an instrument.
        
        Args:
            token: Broker token
        
        Returns:
            Quote dict with ltp, bid, ask, volume, etc.
        """
        try:
            if not await self.login_manager.check_session_validity():
                return None
            
            url = f"{BROKER_API_BASE}/data/quote"
            headers = self.login_manager.get_headers()
            
            params = {'token': token}
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') == 200:
                return data.get('data', {})
            
            return None
        
        except Exception as e:
            self._logger.error(f"Quote fetch error: {e}")
            return None
    
    # =====================================================================
    # PRE-MARKET DATA
    # =====================================================================
    
    async def get_pre_market_data(self) -> Optional[Dict[str, float]]:
        """
        Fetch pre-market data: GIFT Nifty %, S&P500 %, NASDAQ %.
        
        Returns:
            Dict with 'gift_nifty_pct', 'sp500_pct', 'nasdaq_pct'
        """
        try:
            url = f"{BROKER_API_BASE}/data/premarket"
            headers = self.login_manager.get_headers()
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') == 200:
                pre_market = data.get('data', {})
                self._logger.info(
                    f"Pre-market: GIFT={pre_market.get('gift_nifty_pct'):.2f}%, "
                    f"S&P500={pre_market.get('sp500_pct'):.2f}%, "
                    f"NASDAQ={pre_market.get('nasdaq_pct'):.2f}%"
                )
                return pre_market
            
            return None

    async def get_open_orders(self) -> Optional[List[Dict[str, Any]]]:
        """Fetch all open orders from broker."""
        try:
            if not await self.login_manager.check_session_validity():
                return None

            url = f"{BROKER_API_BASE}/order/open"
            headers = self.login_manager.get_headers()

            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get('status') == 200:
                orders = data.get('data', [])
                self._logger.debug(f"Open orders fetched: {len(orders)}")
                return orders

            return None

        except Exception as e:
            self._logger.error(f"Open orders fetch error: {e}")
            return None
        
        except Exception as e:
            self._logger.error(f"Pre-market data fetch error: {e}")
            return None
    
    # =====================================================================
    # UTILITY
    # =====================================================================
    
    async def validate_connectivity(self) -> bool:
        """Check broker API connectivity."""
        try:
            url = f"{BROKER_API_BASE}/health"
            response = requests.get(url, timeout=5)
            return response.status_code == 200
        except:
            return False
