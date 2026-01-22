"""
Risk management utilities
- Symbol cleaning
- Slippage calculation
- Risk validation
"""

import re


def clean_symbol(symbol: str) -> str:
    """
    Loại bỏ timeframe suffix
    Ví dụ: BTCUSDT_1m -> BTCUSDT, BTC/USDT:USDT_5m -> BTC/USDT:USDT
    
    Args:
        symbol: Symbol với hoặc không có timeframe suffix
    
    Returns:
        Symbol đã được làm sạch
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
    Điều chỉnh SL/TP dựa trên giá fill thực tế so với giá entry dự tính
    
    Args:
        expected_entry: Giá entry dự tính ban đầu
        actual_entry: Giá fill thực tế từ market order
        expected_sl: SL dự tính ban đầu
        expected_tp: TP dự tính ban đầu
        side: 'buy' hoặc 'sell'
    
    Returns:
        dict: Adjusted SL, TP và slippage info
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
    Kiểm tra slippage có chấp nhận được không
    
    Args:
        slippage_pct: Phần trăm slippage
        max_slippage_pct: Mức slippage tối đa cho phép (mặc định 0.5%)
    
    Returns:
        True nếu slippage chấp nhận được, False nếu vượt quá
    """
    return abs(slippage_pct) <= max_slippage_pct
