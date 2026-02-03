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
    Điều chỉnh SL/TP để DUY TRÌ risk distance cố định dựa trên actual entry.
    
    QUAN TRỌNG: Luôn di chuyển SL theo actual entry để đảm bảo:
    - Khoảng cách entry → SL luôn = original_distance
    - Actual risk = Expected risk (vì quantity đã được tính từ original_distance)
    
    Ví dụ LONG: entry=100, sl=95, risk_distance=5, RR=2
    - Entry=102 (xấu): SL=102-5=97, TP=102+(5*2)=112, actual_risk vẫn = expected
    - Entry=98 (tốt): SL=98-5=93, TP=98+(5*2)=108
    
    Ví dụ SHORT: entry=100, sl=105, risk_distance=5, RR=2
    - Entry=98 (xấu): SL=98+5=103, TP=98-(5*2)=88
    - Entry=102 (tốt): SL=102+5=107, TP=102-(5*2)=92
    
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
        
        # LUÔN duy trì khoảng cách risk cố định
        # SL = actual_entry - original_distance
        adjusted_sl = actual_entry - original_distance
        new_risk_distance = original_distance
        
        # Tính TP theo RR ratio
        adjusted_tp = actual_entry + (new_risk_distance * rr_ratio)
        
    else:  # SHORT
        slippage = expected_entry - actual_entry
        
        # LUÔN duy trì khoảng cách risk cố định
        # SL = actual_entry + original_distance
        adjusted_sl = actual_entry + original_distance
        new_risk_distance = original_distance
        
        # Tính TP theo RR ratio
        adjusted_tp = actual_entry - (new_risk_distance * rr_ratio)
    
    slippage_pct = 0
    if expected_entry and expected_entry != 0:
        slippage_pct = round((slippage / expected_entry) * 100, 3)
    
    # Tính actual risk so với expected (để tracking)
    # Vì quantity = risk_per_trade / original_distance
    # actual_risk = quantity * new_risk_distance = risk_per_trade (khi new_risk_distance = original_distance)
    risk_maintained = abs(new_risk_distance - original_distance) < 0.0001
    
    return {
        'expected_entry': round_price(expected_entry),
        'actual_entry': round_price(actual_entry),
        'slippage': round_price(slippage),
        'slippage_pct': slippage_pct,
        'expected_sl': round_price(expected_sl),
        'adjusted_sl': round_price(adjusted_sl),
        'adjusted_tp': round_price(adjusted_tp),
        'new_risk_distance': round_price(new_risk_distance),
        'original_distance': round_price(original_distance),
        'risk_maintained': risk_maintained,
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
