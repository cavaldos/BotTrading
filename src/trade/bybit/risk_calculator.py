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
    
    slippage_pct = 0
    if expected_entry and expected_entry != 0:
        slippage_pct = round((slippage / expected_entry) * 100, 3)
    
    return {
        'expected_entry': round_price(expected_entry),
        'actual_entry': round_price(actual_entry),
        'slippage': round_price(slippage),
        'slippage_pct': slippage_pct,
        'expected_sl': round_price(expected_sl),
        'adjusted_sl': round_price(adjusted_sl),
        'expected_tp': round_price(expected_tp),
        'adjusted_tp': round_price(adjusted_tp),
        'side': side.upper(),
    }


def adjust_levels_for_slippage_v2(
    expected_entry: float,
    actual_entry: float,
    expected_sl: float,
    side: str = 'buy',
    rr_ratio: float = 2.0,
) -> dict:
    """
    Điều chỉnh SL/TP theo logic:
    - Luôn dịch SL để giữ khoảng cách risk cố định với entry thực tế
    - NHƯNG: SL thực tế không được "gần" hơn SL gốc
      + LONG: adjusted_sl <= expected_sl (SL không được cao hơn)
      + SHORT: adjusted_sl >= expected_sl (SL không được thấp hơn)
    
    Ví dụ LONG: entry=20, sl=15, RR=2
    - Entry=21 (xấu): SL lý tưởng=16, nhưng 16>15 nên SL=15, risk=6, TP=21+(6*2)=33
    - Entry=19 (tốt): SL=14 (14<15 OK), risk=5, TP=19+(5*2)=29
    
    Ví dụ SHORT: entry=100, sl=105, RR=2
    - Entry=99 (xấu): SL lý tưởng=104, nhưng 104<105 nên SL=105, risk=6, TP=99-(6*2)=87
    - Entry=101 (tốt): SL=106 (106>105 OK), risk=5, TP=101-(5*2)=91
    
    Args:
        expected_entry: Giá entry dự kiến
        actual_entry: Giá fill thực tế
        expected_sl: SL dự kiến ban đầu
        side: 'buy' (LONG) hoặc 'sell' (SHORT)
        rr_ratio: Tỉ lệ Risk:Reward (mặc định 2.0)
    
    Returns:
        dict: SL/TP đã điều chỉnh và thông tin slippage
    """
    original_distance = abs(expected_entry - expected_sl)
    
    if side.lower() == 'buy':  # LONG
        slippage = actual_entry - expected_entry
        
        # Tính SL lý tưởng (giữ khoảng cách cố định)
        ideal_sl = actual_entry - original_distance
        
        # LONG: SL không được CAO hơn expected_sl
        if ideal_sl > expected_sl:
            # Entry xấu (cao hơn) → SL lý tưởng cao hơn SL gốc → dùng SL gốc
            adjusted_sl = expected_sl
            new_risk_distance = actual_entry - adjusted_sl
        else:
            # Entry tốt hoặc vừa → dùng SL lý tưởng (thấp hơn hoặc bằng SL gốc)
            adjusted_sl = ideal_sl
            new_risk_distance = original_distance
        
        # Tính TP theo RR ratio
        adjusted_tp = actual_entry + (new_risk_distance * rr_ratio)
        
    else:  # SHORT
        slippage = expected_entry - actual_entry
        
        # Tính SL lý tưởng (giữ khoảng cách cố định)
        ideal_sl = actual_entry + original_distance
        
        # SHORT: SL không được THẤP hơn expected_sl
        if ideal_sl < expected_sl:
            # Entry xấu (thấp hơn) → SL lý tưởng thấp hơn SL gốc → dùng SL gốc
            adjusted_sl = expected_sl
            new_risk_distance = adjusted_sl - actual_entry
        else:
            # Entry tốt hoặc vừa → dùng SL lý tưởng (cao hơn hoặc bằng SL gốc)
            adjusted_sl = ideal_sl
            new_risk_distance = original_distance
        
        # Tính TP theo RR ratio
        adjusted_tp = actual_entry - (new_risk_distance * rr_ratio)
    
    slippage_pct = 0
    if expected_entry and expected_entry != 0:
        slippage_pct = round((slippage / expected_entry) * 100, 3)
    
    return {
        'expected_entry': round_price(expected_entry),
        'actual_entry': round_price(actual_entry),
        'slippage': round_price(slippage),
        'slippage_pct': slippage_pct,
        'expected_sl': round_price(expected_sl),
        'adjusted_sl': round_price(adjusted_sl),
        'adjusted_tp': round_price(adjusted_tp),
        'new_risk_distance': round_price(new_risk_distance),
        'rr_ratio': rr_ratio,
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
