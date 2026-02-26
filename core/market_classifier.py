"""
Market Classification Engine.
Determines market regime, bias, and volatility state.
Required before any trade decision.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta
from typing import Optional, List
from pytz import timezone

from config.settings import (
    GIFT_NIFTY_BULL_THRESHOLD, GIFT_NIFTY_BEAR_THRESHOLD,
    SP500_BULL_THRESHOLD, SP500_BEAR_THRESHOLD,
    NASDAQ_BULL_THRESHOLD, NASDAQ_BEAR_THRESHOLD,
    BIAS_BULLISH_THRESHOLD, BIAS_BEARISH_THRESHOLD,
    VIX_THRESHOLD_TRENDING, VIX_THRESHOLD_NORMAL,
    RSI_BULL_MIN, RSI_BULL_MAX, RSI_BEAR_MIN, RSI_BEAR_MAX,
    PCR_BULL_REJECT, PCR_BEAR_REJECT, PCR_BACKSPREAD_MIN, PCR_BACKSPREAD_MAX,
    EMA_THRESHOLD_PERCENT, VWAP_CROSS_THRESHOLD,
    OPENING_RANGE_MINUTES, ENTRY_OPEN, MARKET_OPEN
)
from core.market_data import MarketSnapshot, Candle

logger = logging.getLogger(__name__)
IST = timezone('Asia/Kolkata')


class BiasContext(Enum):
    """Global market bias context."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class VolatilityRegime(Enum):
    """VIX-based volatility regime."""
    TRENDING = "trending"  # VIX < 12
    NORMAL = "normal"      # 12-16
    RANGE = "range"        # VIX > 16


class TrendDirection(Enum):
    """Intraday trend direction."""
    UP = "up"
    DOWN = "down"
    RANGE = "range"
    UNDEFINED = "undefined"


@dataclass
class ClassificationResult:
    """Market classification output."""
    bias_context: BiasContext
    volatility_regime: VolatilityRegime
    trend_direction: TrendDirection
    
    # Scores and intermediate results
    bias_score: int
    vix_value: float
    rsi_value: float
    pcr_value: float
    ema_divergence_pct: float
    
    # Validity flags
    is_valid: bool
    reason: str  # If not valid, reason why
    
    # Supporting data
    trend_confirmation: bool
    filter_passed: bool


class MarketClassifier:
    """
    Multi-step market classification engine.
    
    Step A: Global Bias Score (GIFT, S&P500, NASDAQ)
    Step B: Volatility Regime (VIX)
    Step C: Intraday Structure (EMA, VWAP, RSI, PCR)
    """
    
    def __init__(self):
        self._logger = logging.getLogger(f"{__name__}.MarketClassifier")
        self._vwap_crosses: List[datetime] = []
        self._opening_range_high = None
        self._opening_range_low = None
        self._opening_range_set = False
    
    async def classify(self, snapshot: MarketSnapshot, candles_nifty: List[Candle]) -> ClassificationResult:
        """
        Full market classification from snapshot and candle data.
        
        Args:
            snapshot: Current market snapshot
            candles_nifty: Recent 5-minute candles for NIFTY
        
        Returns:
            ClassificationResult with regime and validity
        """
        try:
            # Step A: Global Bias Score
            bias_context, bias_score = self._classify_bias(snapshot)
            
            # Step B: Volatility Regime
            vix_regime = self._classify_volatility(snapshot.vix)
            
            # Step C: Intraday Structure
            trend, validity, reason, trend_confirmation = self._classify_intraday(
                snapshot, candles_nifty
            )
            
            # Determine if classification is valid
            is_valid = validity and self._passes_filters(snapshot, trend)
            
            # Build result
            result = ClassificationResult(
                bias_context=bias_context,
                volatility_regime=vix_regime,
                trend_direction=trend,
                bias_score=bias_score,
                vix_value=snapshot.vix,
                rsi_value=snapshot.nifty_rsi,
                pcr_value=snapshot.pcr,
                ema_divergence_pct=self._compute_ema_divergence(snapshot),
                is_valid=is_valid,
                reason=reason,
                trend_confirmation=trend_confirmation,
                filter_passed=self._passes_filters(snapshot, trend)
            )
            
            self._log_classification(result)
            
            return result
        
        except Exception as e:
            self._logger.error(f"Classification error: {e}", exc_info=True)
            return ClassificationResult(
                bias_context=BiasContext.NEUTRAL,
                volatility_regime=VolatilityRegime.NORMAL,
                trend_direction=TrendDirection.UNDEFINED,
                bias_score=0,
                vix_value=snapshot.vix,
                rsi_value=snapshot.nifty_rsi,
                pcr_value=snapshot.pcr,
                ema_divergence_pct=0.0,
                is_valid=False,
                reason=f"Classification error: {e}",
                trend_confirmation=False,
                filter_passed=False
            )
    
    def _classify_bias(self, snapshot: MarketSnapshot) -> tuple:
        """Step A: Calculate global bias score from pre-market data."""
        bias_score = 0
        
        # GIFT Nifty
        if snapshot.gift_nifty_pct > GIFT_NIFTY_BULL_THRESHOLD:
            bias_score += 1
        elif snapshot.gift_nifty_pct < GIFT_NIFTY_BEAR_THRESHOLD:
            bias_score -= 1
        
        # S&P 500
        if snapshot.sp500_pct > SP500_BULL_THRESHOLD:
            bias_score += 1
        elif snapshot.sp500_pct < SP500_BEAR_THRESHOLD:
            bias_score -= 1
        
        # NASDAQ
        if snapshot.nasdaq_pct > NASDAQ_BULL_THRESHOLD:
            bias_score += 1
        elif snapshot.nasdaq_pct < NASDAQ_BEAR_THRESHOLD:
            bias_score -= 1
        
        # Determine context
        if bias_score >= BIAS_BULLISH_THRESHOLD:
            context = BiasContext.BULLISH
        elif bias_score <= BIAS_BEARISH_THRESHOLD:
            context = BiasContext.BEARISH
        else:
            context = BiasContext.NEUTRAL
        
        self._logger.debug(f"Bias Score: {bias_score} → {context.value}")
        
        return context, bias_score
    
    def _classify_volatility(self, vix: float) -> VolatilityRegime:
        """Step B: Classify volatility regime based on VIX."""
        if vix < VIX_THRESHOLD_TRENDING:
            regime = VolatilityRegime.TRENDING
        elif vix <= VIX_THRESHOLD_NORMAL:
            regime = VolatilityRegime.NORMAL
        else:
            regime = VolatilityRegime.RANGE
        
        self._logger.debug(f"VIX {vix:.1f} → {regime.value}")
        
        return regime
    
    def _classify_intraday(self, snapshot: MarketSnapshot,
                           candles: List[Candle]) -> tuple:
        """
        Step C: Classify intraday trend using EMA, VWAP, RSI, opening range.
        
        Returns:
            (trend_direction, is_valid, reason, trend_confirmation)
        """
        if len(candles) < 3:
            return (TrendDirection.UNDEFINED, False, "Insufficient candles", False)
        
        # Get current candle values
        current_ema20 = snapshot.nifty_ema20
        current_ema50 = snapshot.nifty_ema50
        current_price = snapshot.nifty_spot
        vwap = snapshot.nifty_vwap
        
        # Check for trend signals
        trend_up = (current_price > vwap and 
                    current_ema20 > current_ema50)
        trend_down = (current_price < vwap and 
                      current_ema20 < current_ema50)
        
        # Range detection: VWAP crosses
        range_detected = self._detect_range(candles, vwap)
        
        # Determine trend
        if trend_up:
            # Check if price broke above opening range high
            if self._opening_range_high and current_price > self._opening_range_high:
                trend = TrendDirection.UP
                confirmation = True
            else:
                trend = TrendDirection.UP
                confirmation = False
        
        elif trend_down:
            # Check if price broke below opening range low
            if self._opening_range_low and current_price < self._opening_range_low:
                trend = TrendDirection.DOWN
                confirmation = True
            else:
                trend = TrendDirection.DOWN
                confirmation = False
        
        elif range_detected:
            trend = TrendDirection.RANGE
            confirmation = True
        
        else:
            trend = TrendDirection.UNDEFINED
            confirmation = False
        
        # Determine validity
        if trend == TrendDirection.UNDEFINED:
            is_valid = False
            reason = "No confirmed trend, range, or breakout"
        else:
            is_valid = True
            reason = f"Trend {trend.value} confirmed"
        
        self._logger.debug(
            f"Trend: {trend.value} (EMA20={current_ema20:.0f}, "
            f"EMA50={current_ema50:.0f}, Price={current_price:.0f})"
        )
        
        return trend, is_valid, reason, confirmation
    
    def _detect_range(self, candles: List[Candle], vwap: float) -> bool:
        """
        Detect if market is in range: VWAP crossed ≥4 times in 60 minutes.
        """
        if len(candles) < 4:
            return False
        
        # Count VWAP crosses in last minute (current + previous candles)
        # This is a simplified version
        vwap_crosses = 0
        prev_side = None
        
        for candle in candles[-12:]:  # Last 12 candles = 60 minutes
            curr_side = "above" if candle.close > vwap else "below"
            
            if prev_side and prev_side != curr_side:
                vwap_crosses += 1
            
            prev_side = curr_side
        
        range_detected = vwap_crosses >= VWAP_CROSS_THRESHOLD
        
        if range_detected:
            self._logger.debug(f"Range detected: {vwap_crosses} VWAP crosses")
        
        return range_detected
    
    def update_opening_range(self, candles: List[Candle]) -> None:
        """
        Update opening range (9:15-9:30).
        Should be called at market open.
        """
        if not candles:
            return
        
        # Find candles between 9:15-9:30
        opening_candles = [
            c for c in candles
            if (MARKET_OPEN <= c.timestamp.time() < 
                (datetime.combine(datetime.today(), MARKET_OPEN) + timedelta(minutes=OPENING_RANGE_MINUTES)).time())
        ]
        
        if opening_candles:
            self._opening_range_high = max(c.high for c in opening_candles)
            self._opening_range_low = min(c.low for c in opening_candles)
            self._opening_range_set = True
            self._logger.info(
                f"Opening range set: {self._opening_range_low:.0f} - {self._opening_range_high:.0f}"
            )
    
    def _passes_filters(self, snapshot: MarketSnapshot, trend: TrendDirection) -> bool:
        """Apply RSI and PCR filters based on trend direction."""
        if trend == TrendDirection.UNDEFINED:
            return False
        
        rsi = snapshot.nifty_rsi
        pcr = snapshot.pcr
        
        if trend == TrendDirection.UP:
            # Bull filters
            if not (RSI_BULL_MIN <= rsi <= RSI_BULL_MAX):
                self._logger.debug(f"RSI filter failed: {rsi:.0f} not in {RSI_BULL_MIN}-{RSI_BULL_MAX}")
                return False
            
            if pcr > PCR_BULL_REJECT:
                self._logger.debug(f"PCR filter failed: {pcr:.2f} > {PCR_BULL_REJECT}")
                return False
            
            return True
        
        elif trend == TrendDirection.DOWN:
            # Bear filters
            if not (RSI_BEAR_MIN <= rsi <= RSI_BEAR_MAX):
                self._logger.debug(f"RSI filter failed: {rsi:.0f} not in {RSI_BEAR_MIN}-{RSI_BEAR_MAX}")
                return False
            
            if pcr < PCR_BEAR_REJECT:
                self._logger.debug(f"PCR filter failed: {pcr:.2f} < {PCR_BEAR_REJECT}")
                return False
            
            return True
        
        elif trend == TrendDirection.RANGE:
            # Range (IC) filters: PCR should be in backspread range for flexibility
            if not (PCR_BACKSPREAD_MIN <= pcr <= PCR_BACKSPREAD_MAX):
                self._logger.debug(
                    f"PCR filter failed for range: {pcr:.2f} not in "
                    f"{PCR_BACKSPREAD_MIN}-{PCR_BACKSPREAD_MAX}"
                )
                return False
            
            return True
        
        return False
    
    def _compute_ema_divergence(self, snapshot: MarketSnapshot) -> float:
        """Compute EMA divergence as percentage."""
        if snapshot.nifty_ema50 == 0:
            return 0.0
        
        divergence = abs(snapshot.nifty_ema20 - snapshot.nifty_ema50) / snapshot.nifty_ema50 * 100
        return divergence
    
    def _log_classification(self, result: ClassificationResult) -> None:
        """Log classification result."""
        valid_str = "VALID" if result.is_valid else "INVALID"
        self._logger.info(
            f"Classification [{valid_str}]: {result.bias_context.value} | "
            f"{result.volatility_regime.value} | {result.trend_direction.value} | "
            f"VIX={result.vix_value:.1f} RSI={result.rsi_value:.0f} PCR={result.pcr_value:.2f} | "
            f"Reason: {result.reason}"
        )
