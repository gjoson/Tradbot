"""
ATM Strike Finder and Delta-based strike selection.
Finds appropriate strikes for multi-leg strategies based on delta targets.
"""

import logging
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime
from pytz import timezone

from config.settings import (
    DELTA_DEBIT_LONG, DELTA_DEBIT_SHORT,
    DELTA_IC_SHORT, DELTA_IC_HEDGE,
    DELTA_BACKSPREAD_SHORT, DELTA_BACKSPREAD_LONG
)

logger = logging.getLogger(__name__)
IST = timezone('Asia/Kolkata')


@dataclass
class OptionStrike:
    """Option strike details."""
    expiry: str
    token: str
    strike: float
    instrument: str  # 'CE' or 'PE'
    
    # Greeks
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    iv: float = 0.0
    
    # Market data
    bid: float = 0.0
    ask: float = 0.0
    ltp: float = 0.0
    volume: int = 0
    open_interest: int = 0
    
    def __hash__(self):
        return hash((self.expiry, self.strike, self.instrument))
    
    def __eq__(self, other):
        if not isinstance(other, OptionStrike):
            return False
        return (self.expiry == other.expiry and 
                self.strike == other.strike and 
                self.instrument == other.instrument)


@dataclass
class Strike:
    """For backward compatibility."""
    strike: float
    delta: float
    instrument: str
    token: str


class ATMStrikeFinder:
    """
    Find ATM and appropriate strikes based on delta targets.
    
    Responsibilities:
    - Identify ATM strike for current spot
    - Filter strikes by liquidity (volume, OI)
    - Select strikes matching delta targets
    - Support multi-leg strike combinations
    """
    
    def __init__(self):
        self._logger = logging.getLogger(f"{__name__}.ATMStrikeFinder")
        self._options_chain: Dict[str, List[OptionStrike]] = {}
        self._last_update = None
        self._strike_spacing = 100  # NIFTY has 100-point spacing
    
    async def update_option_chain(self, option_chain: Dict[str, Any]) -> None:
        """
        Update option chain from broker data.
        
        Args:
            option_chain: Full option chain data from broker
                Expected format:
                {
                    'expires': 'DDMMMYY',
                    'options': [
                        {
                            'token': '...',
                            'strike': 20000,
                            'ce': {...},  # CE data
                            'pe': {...}   # PE data
                        },
                        ...
                    ]
                }
        """
        try:
            self._options_chain.clear()
            self._last_update = datetime.now(IST)
            
            if not option_chain or 'options' not in option_chain:
                self._logger.warning("Invalid option chain format")
                return
            
            for option_data in option_chain.get('options', []):
                strike = option_data.get('strike')
                
                # Process CE
                if 'ce' in option_data:
                    ce_data = option_data['ce']
                    ce_strike = OptionStrike(
                        expiry=option_chain.get('expires', ''),
                        token=ce_data.get('token', ''),
                        strike=strike,
                        instrument='CE',
                        delta=ce_data.get('delta', 0.0),
                        gamma=ce_data.get('gamma', 0.0),
                        theta=ce_data.get('theta', 0.0),
                        vega=ce_data.get('vega', 0.0),
                        iv=ce_data.get('iv', 0.0),
                        bid=ce_data.get('bid', 0.0),
                        ask=ce_data.get('ask', 0.0),
                        ltp=ce_data.get('ltp', 0.0),
                        volume=ce_data.get('volume', 0),
                        open_interest=ce_data.get('open_interest', 0)
                    )
                    
                    if strike not in self._options_chain:
                        self._options_chain[strike] = []
                    self._options_chain[strike].append(ce_strike)
                
                # Process PE
                if 'pe' in option_data:
                    pe_data = option_data['pe']
                    pe_strike = OptionStrike(
                        expiry=option_chain.get('expires', ''),
                        token=pe_data.get('token', ''),
                        strike=strike,
                        instrument='PE',
                        delta=pe_data.get('delta', 0.0),
                        gamma=pe_data.get('gamma', 0.0),
                        theta=pe_data.get('theta', 0.0),
                        vega=pe_data.get('vega', 0.0),
                        iv=pe_data.get('iv', 0.0),
                        bid=pe_data.get('bid', 0.0),
                        ask=pe_data.get('ask', 0.0),
                        ltp=pe_data.get('ltp', 0.0),
                        volume=pe_data.get('volume', 0),
                        open_interest=pe_data.get('open_interest', 0)
                    )
                    
                    if strike not in self._options_chain:
                        self._options_chain[strike] = []
                    self._options_chain[strike].append(pe_strike)
            
            self._logger.info(f"Option chain updated: {len(self._options_chain)} strikes")
        
        except Exception as e:
            self._logger.error(f"Failed to update option chain: {e}", exc_info=True)
    
    def find_atm_strike(self, spot: float) -> float:
        """
        Find nearest ATM strike to spot price.
        
        Args:
            spot: Current spot price
        
        Returns:
            Nearest strike to spot
        """
        if not self._options_chain:
            self._logger.warning("No option chain data")
            return round(spot / 100) * 100  # Round to nearest 100
        
        strikes = sorted(self._options_chain.keys())
        atm = min(strikes, key=lambda x: abs(x - spot))
        
        return atm
    
    def find_strike_by_delta(self, spot: float, delta_target: float,
                           instrument: str, liquidity_filter: bool = True) -> Optional[OptionStrike]:
        """
        Find strike matching delta target.
        
        Args:
            spot: Current spot price
            delta_target: Target delta (e.g., 0.50 for 0.50 delta)
            instrument: 'CE' or 'PE'
            liquidity_filter: Require minimum volume/OI
        
        Returns:
            Best matching strike or None
        """
        if not self._options_chain:
            self._logger.warning("No option chain data")
            return None
        
        candidates = []
        
        for strike_price, strikes in self._options_chain.items():
            for strike_obj in strikes:
                if strike_obj.instrument != instrument:
                    continue
                
                # Liquidity check
                if liquidity_filter:
                    if strike_obj.volume < 10 or strike_obj.open_interest < 100:
                        continue
                
                # Delta proximity
                delta_diff = abs(strike_obj.delta - delta_target)
                candidates.append((delta_diff, strike_obj))
        
        if not candidates:
            self._logger.warning(
                f"No strikes found for {instrument} delta={delta_target}"
            )
            return None
        
        # Return best match
        candidates.sort(key=lambda x: x[0])
        best_strike = candidates[0][1]
        
        self._logger.debug(
            f"Found {instrument} strike {best_strike.strike} "
            f"with delta={best_strike.delta:.2f} (target={delta_target:.2f})"
        )
        
        return best_strike
    
    def find_debit_spread_legs(self, spot: float, direction: str) -> Optional[Tuple[OptionStrike, OptionStrike]]:
        """
        Find bull call / bear put debit spread legs.
        
        Args:
            spot: Current spot price
            direction: 'BULL' (call spread) or 'BEAR' (put spread)
        
        Returns:
            Tuple of (long_leg, short_leg) or None
        """
        try:
            if direction == 'BULL':
                # Bull call: buy 0.45-0.60 delta, sell 0.20-0.30 delta CE
                long_delta = (DELTA_DEBIT_LONG[0] + DELTA_DEBIT_LONG[1]) / 2
                short_delta = (DELTA_DEBIT_SHORT[0] + DELTA_DEBIT_SHORT[1]) / 2
                instrument = 'CE'
            elif direction == 'BEAR':
                # Bear put: buy 0.45-0.60 delta, sell 0.20-0.30 delta PE
                long_delta = (DELTA_DEBIT_LONG[0] + DELTA_DEBIT_LONG[1]) / 2
                short_delta = (DELTA_DEBIT_SHORT[0] + DELTA_DEBIT_SHORT[1]) / 2
                instrument = 'PE'
            else:
                self._logger.error(f"Invalid direction: {direction}")
                return None
            
            long_leg = self.find_strike_by_delta(spot, long_delta, instrument)
            short_leg = self.find_strike_by_delta(spot, short_delta, instrument)
            
            if not long_leg or not short_leg:
                self._logger.warning(f"Could not find {direction} spread legs")
                return None
            
            return (long_leg, short_leg)
        
        except Exception as e:
            self._logger.error(f"Error finding {direction} spread: {e}")
            return None
    
    def find_iron_condor_legs(self, spot: float) -> Optional[Tuple[OptionStrike, OptionStrike, OptionStrike, OptionStrike]]:
        """
        Find iron condor legs: short CE, short PE, long CE, long PE.
        
        Returns:
            Tuple of (short_ce, short_pe, long_ce, long_pe) or None
        """
        try:
            # Short legs (0.18-0.22 delta)
            short_delta = (DELTA_IC_SHORT[0] + DELTA_IC_SHORT[1]) / 2
            
            # Long legs (0.05-0.10 delta)
            long_delta = (DELTA_IC_HEDGE[0] + DELTA_IC_HEDGE[1]) / 2
            
            short_ce = self.find_strike_by_delta(spot, short_delta, 'CE')
            short_pe = self.find_strike_by_delta(spot, short_delta, 'PE')
            long_ce = self.find_strike_by_delta(spot, long_delta, 'CE')
            long_pe = self.find_strike_by_delta(spot, long_delta, 'PE')
            
            if not all([short_ce, short_pe, long_ce, long_pe]):
                self._logger.warning("Could not find all iron condor legs")
                return None
            
            return (short_ce, short_pe, long_ce, long_pe)
        
        except Exception as e:
            self._logger.error(f"Error finding iron condor legs: {e}")
            return None
    
    def find_backspread_legs(self, spot: float, direction: str) -> Optional[Tuple[OptionStrike, List[OptionStrike]]]:
        """
        Find backspread legs: 1 short, 2 long.
        
        Args:
            spot: Current spot
            direction: 'CALL' or 'PUT'
        
        Returns:
            Tuple of (short_leg, [long_leg1, long_leg2]) or None
        """
        try:
            if direction == 'CALL':
                instrument = 'CE'
            elif direction == 'PUT':
                instrument = 'PE'
            else:
                return None
            
            short_delta = DELTA_BACKSPREAD_SHORT
            long_delta = (DELTA_BACKSPREAD_LONG[0] + DELTA_BACKSPREAD_LONG[1]) / 2
            
            short_leg = self.find_strike_by_delta(spot, short_delta, instrument)
            
            # Find 2 long legs at lower delta
            long_legs = []
            for _ in range(2):
                long_leg = self.find_strike_by_delta(spot, long_delta, instrument)
                if long_leg:
                    long_legs.append(long_leg)
            
            if not short_leg or len(long_legs) < 2:
                self._logger.warning(f"Could not find all {direction} backspread legs")
                return None
            
            return (short_leg, long_legs)
        
        except Exception as e:
            self._logger.error(f"Error finding backspread legs: {e}")
            return None
    
    def get_spread_price(self, long_leg: OptionStrike, short_leg: OptionStrike) -> float:
        """
        Get debit spread price (net debit).
        
        Use mid-point: (bid + ask) / 2
        """
        long_mid = (long_leg.bid + long_leg.ask) / 2 if long_leg.ask else long_leg.ltp
        short_mid = (short_leg.bid + short_leg.ask) / 2 if short_leg.ask else short_leg.ltp
        
        # Debit spread = buy long, sell short
        net_debit = long_mid - short_mid
        
        return max(net_debit, 0.05)  # Ensure minimum spread
    
    def is_chain_fresh(self, max_age_seconds: int = 60) -> bool:
        """Check if option chain data is recent."""
        if self._last_update is None:
            return False
        
        age = (datetime.now(IST) - self._last_update).total_seconds()
        return age < max_age_seconds
