"""
Time utilities for IST timezone handling and market hours.
"""

from datetime import datetime, time, timedelta
from typing import Tuple
from pytz import timezone

IST = timezone('Asia/Kolkata')
UTC = timezone('UTC')


def get_current_ist_time() -> datetime:
    """Get current time in IST."""
    return datetime.now(IST)


def get_ist_time(dt: datetime) -> datetime:
    """Convert any datetime to IST."""
    if dt.tzinfo is None:
        # Assume UTC if naive
        dt = UTC.localize(dt)
    return dt.astimezone(IST)


def is_trading_day(date: datetime = None) -> bool:
    """
    Check if a date is a trading day (Mon-Fri).
    
    Args:
        date: Date to check (default today)
    
    Returns:
        True if trading day
    """
    if date is None:
        date = get_current_ist_time()
    
    # Monday = 0, Sunday = 6
    return date.weekday() < 5  # Mon-Fri


def get_market_hours_today() -> Tuple[datetime, datetime]:
    """
    Get market open and close times for today in IST.
    
    Returns:
        (market_open, market_close) as datetime objects with IST tz
    """
    now = get_current_ist_time()
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    return market_open, market_close


def seconds_until_next_candle(interval_seconds: int = 300) -> float:
    """
    Calculate seconds until next candle close.
    
    Args:
        interval_seconds: Candle interval (e.g., 300 for 5-min)
    
    Returns:
        Seconds remaining until next candle
    """
    now = get_current_ist_time()
    
    # Time within current candle
    minute = now.minute
    second = now.second
    microsecond = now.microsecond
    
    seconds_in_candle = minute * 60 + second + microsecond / 1e6
    seconds_elapsed_in_interval = seconds_in_candle % interval_seconds
    
    return interval_seconds - seconds_elapsed_in_interval


def minutes_since(dt: datetime) -> float:
    """
    Calculate minutes since a given time.
    
    Args:
        dt: Reference time
    
    Returns:
        Minutes elapsed
    """
    delta = get_current_ist_time() - dt
    return delta.total_seconds() / 60.0


def format_ist_time(dt: datetime = None, format_str: str = '%Y-%m-%d %H:%M:%S') -> str:
    """
    Format datetime as IST string.
    
    Args:
        dt: Datetime to format (default now)
        format_str: Format string
    
    Returns:
        Formatted string
    """
    if dt is None:
        dt = get_current_ist_time()
    else:
        dt = get_ist_time(dt)
    
    return dt.strftime(format_str)


def parse_ist_time(time_str: str, format_str: str = '%Y-%m-%d %H:%M:%S') -> datetime:
    """
    Parse string as IST datetime.
    
    Args:
        time_str: Time string to parse
        format_str: Format string
    
    Returns:
        Datetime in IST
    """
    naive_dt = datetime.strptime(time_str, format_str)
    return IST.localize(naive_dt)


def get_trading_day_start() -> datetime:
    """Get start of current trading day (09:00 IST today)."""
    now = get_current_ist_time()
    return now.replace(hour=9, minute=0, second=0, microsecond=0)


def get_trading_day_end() -> datetime:
    """Get end of current trading day (15:30 IST today)."""
    now = get_current_ist_time()
    return now.replace(hour=15, minute=30, second=0, microsecond=0)


def is_market_open_time(dt: datetime = None) -> bool:
    """
    Check if time falls within market hours (9:15-15:30).
    
    Args:
        dt: Time to check (default now)
    
    Returns:
        True if within market hours
    """
    if dt is None:
        dt = get_current_ist_time()
    else:
        dt = get_ist_time(dt)
    
    time_obj = dt.time()
    market_open = time(9, 15)
    market_close = time(15, 30)
    
    return market_open <= time_obj <= market_close


def timestamp_to_ist(timestamp: float) -> datetime:
    """
    Convert Unix timestamp to IST datetime.
    
    Args:
        timestamp: Unix timestamp (seconds)
    
    Returns:
        Datetime in IST
    """
    return datetime.fromtimestamp(timestamp, tz=IST)


def ist_to_timestamp(dt: datetime) -> float:
    """
    Convert IST datetime to Unix timestamp.
    
    Args:
        dt: Datetime in IST
    
    Returns:
        Unix timestamp
    """
    return dt.timestamp()


def get_next_trading_day(date: datetime = None) -> datetime:
    """
    Get next trading day (skip weekends).
    
    Args:
        date: Reference date (default today)
    
    Returns:
        Next trading day
    """
    if date is None:
        date = get_current_ist_time()
    
    next_day = date + timedelta(days=1)
    
    # Skip weekends
    while not is_trading_day(next_day):
        next_day += timedelta(days=1)
    
    return next_day.replace(hour=9, minute=15, second=0, microsecond=0)
