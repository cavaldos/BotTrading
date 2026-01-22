"""
Risk management utilities
- Symbol cleaning
- Slippage calculation
- Risk validation
"""

import re


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
        'expected_entry': round(expected_entry, 2),
        'actual_entry': round(actual_entry, 2),
        'slippage': round(slippage, 2),
        'slippage_pct': round((slippage / expected_entry) * 100, 3),
        'expected_sl': round(expected_sl, 2),
        'adjusted_sl': round(adjusted_sl, 2),
        'expected_tp': round(expected_tp, 2),
        'adjusted_tp': round(adjusted_tp, 2),
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
