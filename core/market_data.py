"""
Market Data processing and indicator calculation.
Builds candles, computes EMA, RSI, VWAP, PCR internally.
Emits MARKET_TICK and CANDLE_CLOSE events.
"""

import asyncio
import logging
from collections import deque, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from pytz import timezone

import numpy as np

from config.settings import (
    CANDLE_INTERVAL_SECONDS, EMA_PERIOD_SHORT, EMA_PERIOD_LONG,
    RSI_PERIOD, VWAP_UPDATE_INTERVAL, VIX_UPDATE_INTERVAL,
    DATA_RETENTION_BARS, NIFTY_TOKENS
)
from core.event_bus import get_event_bus, Event, EventType

logger = logging.getLogger(__name__)
IST = timezone('Asia/Kolkata')


@dataclass
class Candle:
    """OHLCV candle."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    vwap: float = 0.0
    
    def __post_init__(self):
        if self.high == 0 or self.low == 0:
            raise ValueError("High/Low cannot be zero")


@dataclass
class Tick:
    """Market tick data."""
    token: str
    timestamp: datetime
    ltp: float  # Last traded price
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    prev_close: float = 0.0


@dataclass
class MarketSnapshot:
    """Current market state snapshot."""
    timestamp: datetime
    nifty_spot: float
    nifty_vwap: float = 0.0
    nifty_ema20: float = 0.0
    nifty_ema50: float = 0.0
    nifty_rsi: float = 0.0
    vix: float = 0.0
    pcr: float = 0.0
    option_chain: Dict = field(default_factory=dict)
    gift_nifty_pct: float = 0.0  # Pre-market
    sp500_pct: float = 0.0  # Pre-market
    nasdaq_pct: float = 0.0  # Pre-market


class MarketData:
    """
    Central market data aggregator.
    
    Maintains:
    - Real-time ticks for spot and options
    - 5-minute candles with computed indicators
    - VIX and PCR data
    - Pre-market data
    """
    
    def __init__(self):
        self._logger = logging.getLogger(f"{__name__}.MarketData")
        
        # Tick buffers
        self._ticks: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        
        # OHLC candles (5-minute)
        self._candles: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=DATA_RETENTION_BARS)
        )
        
        # Current candle being built
        self._current_candles: Dict[str, Optional[Candle]] = {}
        self._last_candle_close_time: Dict[str, datetime] = {}
        
        # Indicator caches
        self._ema_cache: Dict[str, Tuple[deque, deque]] = {}  # token -> (ema20, ema50)
        self._rsi_cache: Dict[str, deque] = defaultdict(lambda: deque(maxlen=RSI_PERIOD + 1))
        
        # Market aggregates
        self.nifty_spot: float = 0.0
        self.vix: float = 0.0
        self.pcr: float = 0.0
        self.last_vwap: float = 0.0
        self.last_vix_update = None
        self.last_pcr_update = None
        
        # Pre-market data
        self.gift_nifty_pct: float = 0.0
        self.sp500_pct: float = 0.0
        self.nasdaq_pct: float = 0.0
        
        # State tracking
        self._is_processing = False
        self._ticks_received = 0
    
    async def process_tick(self, tick: Tick) -> None:
        """
        Process incoming market tick.
        Updates candles, indicators, emits events.
        """
        try:
            # Store tick
            self._ticks[tick.token].append(tick)
            self._ticks_received += 1
            
            # Update spot if NIFTY
            if tick.token == NIFTY_TOKENS['NIFTY']:
                self.nifty_spot = tick.ltp
            
            # Build/update candle
            await self._update_candle(tick)
            
            # Emit tick event
            await self._emit_tick_event(tick)
        
        except Exception as e:
            self._logger.error(f"Error processing tick: {e}", exc_info=True)
    
    async def _update_candle(self, tick: Tick) -> None:
        """Build 5-minute candles from ticks."""
        token = tick.token
        current_time = tick.timestamp
        
        # Get or create current candle
        if token not in self._current_candles:
            self._current_candles[token] = Candle(
                timestamp=current_time.replace(second=0, microsecond=0),
                open=tick.ltp,
                high=tick.ltp,
                low=tick.ltp,
                close=tick.ltp
            )
        
        candle = self._current_candles[token]
        candle_start = candle.timestamp
        
        # Check if we need to close this candle
        seconds_elapsed = (current_time - candle_start).total_seconds()
        
        if seconds_elapsed >= CANDLE_INTERVAL_SECONDS:
            # Close current candle and start new one
            candle.close = tick.ltp
            candle.volume += tick.volume
            
            self._candles[token].append(candle)
            self._logger.debug(
                f"Candle closed for {token}: O={candle.open} H={candle.high} "
                f"L={candle.low} C={candle.close}"
            )
            
            # Update indicators
            self._update_indicators(token)
            
            # Emit candle close event
            await self._emit_candle_close_event(token, candle)
            
            # Start new candle
            self._current_candles[token] = Candle(
                timestamp=current_time.replace(second=0, microsecond=0),
                open=tick.ltp,
                high=tick.ltp,
                low=tick.ltp,
                close=tick.ltp
            )
        else:
            # Update current candle
            candle.high = max(candle.high, tick.ltp)
            candle.low = min(candle.low, tick.ltp)
            candle.close = tick.ltp
            candle.volume += tick.volume
    
    def _update_indicators(self, token: str) -> None:
        """Compute EMA, RSI for closed candle."""
        if len(self._candles[token]) < 2:
            return
        
        # Get closes
        closes = [c.close for c in self._candles[token]]
        
        # Compute EMA20 and EMA50
        ema20 = self._compute_ema(closes, EMA_PERIOD_SHORT)
        ema50 = self._compute_ema(closes, EMA_PERIOD_LONG)
        
        if token not in self._ema_cache:
            self._ema_cache[token] = (deque(maxlen=100), deque(maxlen=100))
        
        self._ema_cache[token][0].append(ema20)
        self._ema_cache[token][1].append(ema50)
        
        # Compute RSI
        rsi = self._compute_rsi(closes, RSI_PERIOD)
        if rsi is not None:
            self._rsi_cache[token].append(rsi)
    
    def _compute_ema(self, closes: List[float], period: int) -> float:
        """Compute EMA for given period."""
        if len(closes) < period:
            return closes[-1] if closes else 0.0
        
        closes_array = np.array(closes[-period:])
        ema = closes_array.mean()  # Simple version; use proper EMA smoothing
        
        # Proper EMA calculation
        multiplier = 2.0 / (period + 1)
        ema_values = [closes[-period]]
        
        for price in closes[-period + 1:]:
            ema_val = price * multiplier + ema_values[-1] * (1 - multiplier)
            ema_values.append(ema_val)
        
        return ema_values[-1] if ema_values else 0.0
    
    def _compute_rsi(self, closes: List[float], period: int) -> Optional[float]:
        """Compute RSI (Relative Strength Index)."""
        if len(closes) < period + 1:
            return None
        
        deltas = np.diff(closes[-period - 1:])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 0.0
        
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        
        return rsi
    
    async def set_vix(self, vix_value: float) -> None:
        """Update VIX value."""
        self.vix = vix_value
        self.last_vix_update = datetime.now(IST)
        self._logger.debug(f"VIX updated: {vix_value}")
    
    async def set_pcr(self, pcr_value: float) -> None:
        """Update Put/Call ratio."""
        self.pcr = pcr_value
        self.last_pcr_update = datetime.now(IST)
        self._logger.debug(f"PCR updated: {pcr_value}")
    
    async def set_pre_market_data(self, gift_nifty_pct: float, sp500_pct: float,
                                   nasdaq_pct: float) -> None:
        """Store pre-market data for market classification."""
        self.gift_nifty_pct = gift_nifty_pct
        self.sp500_pct = sp500_pct
        self.nasdaq_pct = nasdaq_pct
        self._logger.info(
            f"Pre-market: GIFT={gift_nifty_pct:.2f}% "
            f"S&P500={sp500_pct:.2f}% NASDAQ={nasdaq_pct:.2f}%"
        )
    
    def get_snapshot(self) -> MarketSnapshot:
        """Get current market snapshot."""
        nifty_token = NIFTY_TOKENS['NIFTY']
        ema20, ema50 = self._ema_cache.get(nifty_token, (0.0, 0.0))
        rsi_list = self._rsi_cache.get(nifty_token, [0.0])
        rsi = rsi_list[-1] if rsi_list else 0.0
        
        return MarketSnapshot(
            timestamp=datetime.now(IST),
            nifty_spot=self.nifty_spot,
            nifty_vwap=self.last_vwap,
            nifty_ema20=ema20[-1] if len(ema20) > 0 else 0.0,
            nifty_ema50=ema50[-1] if len(ema50) > 0 else 0.0,
            nifty_rsi=rsi,
            vix=self.vix,
            pcr=self.pcr,
            gift_nifty_pct=self.gift_nifty_pct,
            sp500_pct=self.sp500_pct,
            nasdaq_pct=self.nasdaq_pct
        )
    
    def get_candles(self, token: str, count: int = 50) -> List[Candle]:
        """Get last N candles for a token."""
        candles = self._candles.get(token, deque())
        return list(candles)[-count:] if candles else []
    
    async def _emit_tick_event(self, tick: Tick) -> None:
        """Emit MARKET_TICK event."""
        try:
            bus = await get_event_bus()
            event = Event(
                event_type=EventType.MARKET_TICK,
                data={
                    'token': tick.token,
                    'ltp': tick.ltp,
                    'timestamp': tick.timestamp.isoformat()
                },
                source='MarketData'
            )
            await bus.emit(event)
        except Exception as e:
            self._logger.error(f"Failed to emit tick event: {e}")
    
    async def _emit_candle_close_event(self, token: str, candle: Candle) -> None:
        """Emit CANDLE_CLOSE event."""
        try:
            bus = await get_event_bus()
            event = Event(
                event_type=EventType.CANDLE_CLOSE,
                data={
                    'token': token,
                    'candle': {
                        'timestamp': candle.timestamp.isoformat(),
                        'open': candle.open,
                        'high': candle.high,
                        'low': candle.low,
                        'close': candle.close,
                        'volume': candle.volume
                    }
                },
                source='MarketData'
            )
            await bus.emit(event)
        except Exception as e:
            self._logger.error(f"Failed to emit candle event: {e}")
