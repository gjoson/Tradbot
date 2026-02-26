"""
Strategy Engine: Select and generate trade signals.
Maps market classification to appropriate multi-leg strategies.
"""

import logging
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, Tuple
from datetime import datetime
from pytz import timezone

from config.settings import VIX_BACKSPREAD_MAX
from core.market_classifier import ClassificationResult, TrendDirection, VolatilityRegime
from core.atm_strike_finder import OptionStrike, ATMStrikeFinder
from core.market_data import MarketSnapshot

logger = logging.getLogger(__name__)
IST = timezone('Asia/Kolkata')


class StrategyType(Enum):
    """Available trading strategies."""
    BULL_CALL_DEBIT_SPREAD = "bull_call_debit_spread"
    BEAR_PUT_DEBIT_SPREAD = "bear_put_debit_spread"
    IRON_CONDOR = "iron_condor"
    CALL_BACKSPREAD = "call_backspread"
    PUT_BACKSPREAD = "put_backspread"
    NO_TRADE = "no_trade"


@dataclass
class TradeSignal:
    """Trade signal from strategy engine."""
    strategy: StrategyType
    spot: float
    timestamp: datetime
    
    # For spreads and backspreads
    legs: List[Tuple[str, float, str]]  # List of (action, strike, instrument)
    
    # Greeks if available
    delta: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    
    # Risk/reward
    max_risk: float = 0.0
    max_reward: float = 0.0
    
    confidence: float = 1.0  # 0-1 confidence in signal
    
    def __str__(self):
        return f"{self.strategy.value} @ {self.spot:.0f} (Confidence: {self.confidence:.0%})"


class StrategyEngine:
    """
    Strategy selection and signal generation.
    
    Decision matrix:
    ┌─────────────────┬────────┬────────────────────────────┐
    │ Market Regime   │ VIX    │ Strategy                   │
    ├─────────────────┼────────┼────────────────────────────┤
    │ Trend Up        │ <15    │ Bull Call Debit Spread    │
    │ Trend Down      │ <15    │ Bear Put Debit Spread     │
    │ Range           │ >14    │ Iron Condor                │
    │ Volatility Break│ any    │ Call/Put Backspread        │
    │ Else            │ -      │ No Trade                   │
    └─────────────────┴────────┴────────────────────────────┘
    """
    
    def __init__(self, atm_finder: ATMStrikeFinder):
        self._logger = logging.getLogger(f"{__name__}.StrategyEngine")
        self.atm_finder = atm_finder
    
    async def generate_signal(
        self,
        classification: ClassificationResult,
        snapshot: MarketSnapshot
    ) -> Optional[TradeSignal]:
        """
        Generate trade signal from market classification.
        
        Args:
            classification: Market classification result
            snapshot: Current market snapshot
        
        Returns:
            TradeSignal or None if no trade
        """
        try:
            # Validate classification
            if not classification.is_valid:
                self._logger.debug(f"Classification invalid: {classification.reason}")
                return None
            
            # Validate option chain is fresh
            if not self.atm_finder.is_chain_fresh():
                self._logger.warning("Option chain data stale")
                return None
            
            # Select strategy based on classification
            signal = await self._select_strategy(classification, snapshot)
            
            if signal:
                self._logger.info(f"Trade signal generated: {signal}")
            
            return signal
        
        except Exception as e:
            self._logger.error(f"Error generating signal: {e}", exc_info=True)
            return None
    
    async def _select_strategy(
        self,
        classification: ClassificationResult,
        snapshot: MarketSnapshot
    ) -> Optional[TradeSignal]:
        """Select strategy based on classification."""
        trend = classification.trend_direction
        vix = classification.vix_value
        spot = snapshot.nifty_spot
        
        # Decision logic
        if trend == TrendDirection.UP and vix < 15:
            return await self._bull_call_spread(spot, snapshot)
        
        elif trend == TrendDirection.DOWN and vix < 15:
            return await self._bear_put_spread(spot, snapshot)
        
        elif trend == TrendDirection.RANGE and vix > 14:
            return await self._iron_condor(spot, snapshot)
        
        elif self._is_volatility_compression_breakout(classification):
            # For backspread: VIX < 13.5 and last 2-hour range < 0.6%
            if vix < VIX_BACKSPREAD_MAX:
                return await self._backspread(spot, snapshot, classification)
        
        self._logger.debug(f"No trade condition met: trend={trend.value}, vix={vix:.1f}")
        return None
    
    async def _bull_call_spread(self, spot: float, snapshot: MarketSnapshot) -> Optional[TradeSignal]:
        """Bull call debit spread: Buy call, Sell call."""
        try:
            self._logger.info(f"Evaluating Bull Call Debit Spread at {spot:.0f}")
            
            legs_pair = self.atm_finder.find_debit_spread_legs(spot, 'BULL')
            if not legs_pair:
                return None
            
            long_leg, short_leg = legs_pair
            
            # Verify spread width is reasonable (e.g., 100 points for NIFTY)
            if abs(long_leg.strike - short_leg.strike) < 50:
                self._logger.warning("Spread width too narrow")
                return None
            
            spread_price = self.atm_finder.get_spread_price(long_leg, short_leg)
            max_risk = spread_price
            max_reward = abs(long_leg.strike - short_leg.strike) - spread_price
            
            signal = TradeSignal(
                strategy=StrategyType.BULL_CALL_DEBIT_SPREAD,
                spot=spot,
                timestamp=datetime.now(IST),
                legs=[
                    ('BUY', long_leg.strike, 'CE'),
                    ('SELL', short_leg.strike, 'CE')
                ],
                delta=long_leg.delta - short_leg.delta,
                theta=long_leg.theta - short_leg.theta,
                vega=long_leg.vega - short_leg.vega,
                max_risk=max_risk,
                max_reward=max_reward,
                confidence=0.85
            )
            
            self._logger.info(
                f"Bull Call Signal: Buy {long_leg.strike} CE (Δ{long_leg.delta:.2f}), "
                f"Sell {short_leg.strike} CE (Δ{short_leg.delta:.2f}), "
                f"Risk: {max_risk:.2f}, Reward: {max_reward:.2f}"
            )
            
            return signal
        
        except Exception as e:
            self._logger.error(f"Bull call signal generation failed: {e}")
            return None
    
    async def _bear_put_spread(self, spot: float, snapshot: MarketSnapshot) -> Optional[TradeSignal]:
        """Bear put debit spread: Buy put, Sell put."""
        try:
            self._logger.info(f"Evaluating Bear Put Debit Spread at {spot:.0f}")
            
            legs_pair = self.atm_finder.find_debit_spread_legs(spot, 'BEAR')
            if not legs_pair:
                return None
            
            long_leg, short_leg = legs_pair
            
            if abs(long_leg.strike - short_leg.strike) < 50:
                self._logger.warning("Spread width too narrow")
                return None
            
            spread_price = self.atm_finder.get_spread_price(long_leg, short_leg)
            max_risk = spread_price
            max_reward = abs(long_leg.strike - short_leg.strike) - spread_price
            
            signal = TradeSignal(
                strategy=StrategyType.BEAR_PUT_DEBIT_SPREAD,
                spot=spot,
                timestamp=datetime.now(IST),
                legs=[
                    ('BUY', long_leg.strike, 'PE'),
                    ('SELL', short_leg.strike, 'PE')
                ],
                delta=-(long_leg.delta - short_leg.delta),  # Puts are negative delta
                theta=long_leg.theta - short_leg.theta,
                vega=long_leg.vega - short_leg.vega,
                max_risk=max_risk,
                max_reward=max_reward,
                confidence=0.85
            )
            
            self._logger.info(
                f"Bear Put Signal: Buy {long_leg.strike} PE (Δ{long_leg.delta:.2f}), "
                f"Sell {short_leg.strike} PE (Δ{short_leg.delta:.2f}), "
                f"Risk: {max_risk:.2f}, Reward: {max_reward:.2f}"
            )
            
            return signal
        
        except Exception as e:
            self._logger.error(f"Bear put signal generation failed: {e}")
            return None
    
    async def _iron_condor(self, spot: float, snapshot: MarketSnapshot) -> Optional[TradeSignal]:
        """Iron condor: Sell CE strangle, Buy OTM CE & PE."""
        try:
            self._logger.info(f"Evaluating Iron Condor at {spot:.0f}")
            
            legs = self.atm_finder.find_iron_condor_legs(spot)
            if not legs:
                return None
            
            short_ce, short_pe, long_ce, long_pe = legs
            
            # Calculate risk/reward
            short_distance = min(
                abs(short_ce.strike - spot),
                abs(short_pe.strike - spot)
            )
            
            if short_distance < 100:
                self._logger.warning("Short strikes too close to spot")
                return None
            
            signal = TradeSignal(
                strategy=StrategyType.IRON_CONDOR,
                spot=spot,
                timestamp=datetime.now(IST),
                legs=[
                    ('SELL', short_ce.strike, 'CE'),
                    ('SELL', short_pe.strike, 'PE'),
                    ('BUY', long_ce.strike, 'CE'),
                    ('BUY', long_pe.strike, 'PE')
                ],
                delta=short_ce.delta + short_pe.delta - long_ce.delta - long_pe.delta,
                theta=short_ce.theta + short_pe.theta - long_ce.theta - long_pe.theta,
                vega=short_ce.vega + short_pe.vega - long_ce.vega - long_pe.vega,
                confidence=0.80
            )
            
            self._logger.info(
                f"Iron Condor Signal: Sell {short_ce.strike} CE & {short_pe.strike} PE, "
                f"Buy {long_ce.strike} CE & {long_pe.strike} PE"
            )
            
            return signal
        
        except Exception as e:
            self._logger.error(f"Iron condor signal generation failed: {e}")
            return None
    
    async def _backspread(
        self,
        spot: float,
        snapshot: MarketSnapshot,
        classification: ClassificationResult
    ) -> Optional[TradeSignal]:
        """
        Backspread: Sell 1, Buy 2 at lower delta.
        Allowed only if VIX < 13.5, last 2-hour range < 0.6%, breakout volume spike.
        """
        try:
            self._logger.info(f"Evaluating Backspread at {spot:.0f}")
            
            # Determine direction based on trend
            if classification.trend_direction == TrendDirection.UP:
                direction = 'CALL'
            elif classification.trend_direction == TrendDirection.DOWN:
                direction = 'PUT'
            else:
                self._logger.warning("Cannot determine backspread direction")
                return None
            
            legs_pair = self.atm_finder.find_backspread_legs(spot, direction)
            if not legs_pair:
                return None
            
            short_leg, long_legs = legs_pair
            
            signal = TradeSignal(
                strategy=StrategyType.CALL_BACKSPREAD if direction == 'CALL' else StrategyType.PUT_BACKSPREAD,
                spot=spot,
                timestamp=datetime.now(IST),
                legs=[
                    ('SELL', short_leg.strike, direction[0:2]),  # 'CE' or 'PE'
                    ('BUY', long_legs[0].strike, direction[0:2]),
                    ('BUY', long_legs[1].strike, direction[0:2])
                ],
                confidence=0.75  # Lower confidence for backspread
            )
            
            self._logger.info(
                f"Backspread Signal: {direction} - Sell 1x{short_leg.strike}, "
                f"Buy 2x{long_legs[0].strike}"
            )
            
            return signal
        
        except Exception as e:
            self._logger.error(f"Backspread signal generation failed: {e}")
            return None
    
    def _is_volatility_compression_breakout(self, classification: ClassificationResult) -> bool:
        """Check if market shows volatility compression with breakout setup."""
        # This would require last 2-hour range < 0.6% and volume spike
        # Simplified detection based on EMA divergence
        ema_div = classification.ema_divergence_pct
        
        # Low EMA divergence suggests consolidation
        return ema_div < 0.20
