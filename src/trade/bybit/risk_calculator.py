"""
Risk management utilities
- Symbol cleaning
- Slippage calculation
- Risk validation
"""

import re
from src.utils.rounding import round_price


def clean_symbol(symbol: str) -> str:
    """
    Remove timeframe suffix from a symbol.
    Examples: 'BTCUSDT_1m' -> 'BTCUSDT', 'BTC/USDT:USDT_5m' -> 'BTC/USDT:USDT'

    Args:
        symbol: Symbol with or without a timeframe suffix

    Returns:
        The cleaned symbol
    """
    symbol = re.sub(r'_\d+[mhd]$', '', symbol.strip())
    return symbol


def adjust_levels_for_slippage(
    expected_entry: float,
    actual_entry: float,
    expected_sl: float,
    expected_tp: float,
    side: str = 'buy',
) -> dict:
    """
    Adjust SL/TP based on the actual fill price compared to the expected entry.

    Args:
        expected_entry: Original expected entry price
        actual_entry: Actual fill price from the market order
        expected_sl: Original expected stop loss
        expected_tp: Original expected take profit
        side: 'buy' or 'sell'

    Returns:
        dict: Adjusted SL, TP and slippage information
    """
    if side.lower() == 'buy':
        # LONG: entry tăng = lỗ tiền hơn, phải tính lại
        slippage = actual_entry - expected_entry
        
        # Tính khoảng cách giữa expected entry -> SL và expected entry -> TP
        distance_sl = expected_entry - expected_sl
        distance_tp = expected_tp - expected_entry
        
        # Áp dụng cùng khoảng cách cho actual entry
        adjusted_sl = actual_entry - distance_sl
        adjusted_tp = actual_entry + distance_tp
        
    else:  # sell (SHORT)
        # SHORT: entry giảm = lỗ tiền hơn, phải tính lại
        slippage = expected_entry - actual_entry
        
        # Tính khoảng cách
        distance_sl = expected_sl - expected_entry
        distance_tp = expected_entry - expected_tp
        
        # Áp dụng
        adjusted_sl = actual_entry + distance_sl
        adjusted_tp = actual_entry - distance_tp
    
    return {
        'expected_entry': round_price(expected_entry),
        'actual_entry': round_price(actual_entry),
        'slippage': round_price(slippage),
        'slippage_pct': round((slippage / expected_entry) * 100, 3),
        'expected_sl': round_price(expected_sl),
        'adjusted_sl': round_price(adjusted_sl),
        'expected_tp': round_price(expected_tp),
        'adjusted_tp': round_price(adjusted_tp),
        'side': side.upper(),
    }


def validate_slippage(slippage_pct: float, max_slippage_pct: float = 0.5) -> bool:
    """
    Check whether slippage is within acceptable bounds.

    Args:
        slippage_pct: Slippage percentage
        max_slippage_pct: Maximum allowed slippage percentage (default 0.5%)

    Returns:
        True if slippage is acceptable, False otherwise
    """
    return abs(slippage_pct) <= max_slippage_pct
