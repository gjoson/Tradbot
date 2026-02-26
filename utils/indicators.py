"""
Utility functions for technical indicators and calculations.
"""

import numpy as np
from typing import List, Optional, Dict


def calculate_ema(values: List[float], period: int) -> float:
    """
    Calculate Exponential Moving Average.
    
    Args:
        values: List of prices
        period: EMA period
    
    Returns:
        Current EMA value
    """
    if len(values) < period:
        return values[-1] if values else 0.0
    
    multiplier = 2.0 / (period + 1)
    ema = values[0]
    
    for price in values[1:]:
        ema = price * multiplier + ema * (1 - multiplier)
    
    return ema


def calculate_rsi(values: List[float], period: int = 14) -> Optional[float]:
    """
    Calculate Relative Strength Index (RSI).
    
    Args:
        values: List of prices
        period: RSI period (default 14)
    
    Returns:
        RSI value (0-100) or None if insufficient data
    """
    if len(values) < period + 1:
        return None
    
    values_array = np.array(values[-period - 1:])
    deltas = np.diff(values_array)
    
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 0.0
    
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    
    return rsi


def calculate_vwap(prices: List[float], volumes: List[int]) -> float:
    """
    Calculate Volume Weighted Average Price (VWAP).
    
    Args:
        prices: List of prices (typically high + low + close) / 3
        volumes: List of volumes
    
    Returns:
        VWAP value
    """
    if len(prices) != len(volumes) or not prices:
        return 0.0
    
    pv = np.array(prices) * np.array(volumes)
    return float(np.sum(pv) / np.sum(volumes)) if np.sum(volumes) > 0 else 0.0


def calculate_atr(highs: List[float], lows: List[float], closes: List[float],
                  period: int = 14) -> Optional[float]:
    """
    Calculate Average True Range (ATR).
    
    Args:
        highs: List of highs
        lows: List of lows
        closes: List of closes
        period: ATR period
    
    Returns:
        ATR value or None
    """
    if len(highs) < period:
        return None
    
    trs = []
    for i in range(len(highs)):
        if i == 0:
            tr = highs[i] - lows[i]
        else:
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
        trs.append(tr)
    
    atr = np.mean(trs[-period:])
    return float(atr)


def calculate_bollinger_bands(values: List[float], period: int = 20,
                              std_dev: float = 2.0) -> tuple:
    """
    Calculate Bollinger Bands.
    
    Args:
        values: List of prices
        period: Band period
        std_dev: Standard deviation multiple
    
    Returns:
        (middle, upper, lower) band values
    """
    if len(values) < period:
        return (0.0, 0.0, 0.0)
    
    middle = np.mean(values[-period:])
    std = np.std(values[-period:])
    
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    
    return (float(middle), float(upper), float(lower))


def calculate_macd(values: List[float],
                  fast: int = 12, slow: int = 26,
                  signal: int = 9) -> tuple:
    """
    Calculate MACD (Moving Average Convergence Divergence).
    
    Args:
        values: List of prices
        fast: Fast EMA period
        slow: Slow EMA period
        signal: Signal line period
    
    Returns:
        (macd_line, signal_line, histogram) or (0, 0, 0) if insufficient data
    """
    if len(values) < slow:
        return (0.0, 0.0, 0.0)
    
    ema_fast = calculate_ema(values, fast)
    ema_slow = calculate_ema(values, slow)
    
    macd_line = ema_fast - ema_slow
    
    # Signal line is EMA of MACD
    # Simplified: use recent MACD values
    signal_line = macd_line * 0.666  # Approximation
    histogram = macd_line - signal_line
    
    return (float(macd_line), float(signal_line), float(histogram))


def calculate_stochastic(highs: List[float], lows: List[float],
                         closes: List[float], period: int = 14) -> tuple:
    """
    Calculate Stochastic Oscillator.
    
    Args:
        highs: List of highs
        lows: List of lows
        closes: List of closes
        period: Stochastic period
    
    Returns:
        (k_line, d_line) or (0, 0) if insufficient data
    """
    if len(closes) < period:
        return (0.0, 0.0)
    
    lowest_low = min(lows[-period:])
    highest_high = max(highs[-period:])
    
    if highest_high == lowest_low:
        return (50.0, 50.0)
    
    k_line = ((closes[-1] - lowest_low) / (highest_high - lowest_low)) * 100
    d_line = np.mean(closes[-3:]) if len(closes) >= 3 else k_line
    
    return (float(k_line), float(d_line))


def calculate_pivot_levels(high: float, low: float, close: float) -> Dict[str, float]:
    """
    Calculate Pivot Point levels.
    
    Args:
        high: Previous day high
        low: Previous day low
        close: Previous day close
    
    Returns:
        Dict with pivot, R1, R2, S1, S2 levels
    """
    pivot = (high + low + close) / 3
    r1 = (2 * pivot) - low
    r2 = pivot + (high - low)
    s1 = (2 * pivot) - high
    s2 = pivot - (high - low)
    
    return {
        'pivot': float(pivot),
        'r1': float(r1),
        'r2': float(r2),
        's1': float(s1),
        's2': float(s2)
    }
