"""
Reversal Candle Patterns - Optimized Combined Version
======================================================
Kết hợp các phiên bản tốt nhất từ backtest.

Pattern Pair IDs:
    0  = all (tất cả patterns)
    1  = doji (CŨ - doji_ratio=0.3)
    2  = hammer / hanging_man (CŨ)
    3  = inverted_hammer / shooting_star (MỚI)
    4  = bullish_engulfing / bearish_engulfing
    5  = [REMOVED - no signals]
    6  = bullish_harami / bearish_harami
    7  = morning_star / evening_star (MỚI - fixed gap)
    8  = tweezer_bottom / tweezer_top
    9  = three_outside_up / three_outside_down
    10 = bullish_belt_hold / bearish_belt_hold
"""

import numpy as np
from numba import njit


# ==================== PATTERN PAIR CONSTANTS ====================
PATTERN_ALL = 0
PATTERN_DOJI = 1
PATTERN_HAMMER_HANGING = 2
PATTERN_INVERTED_SHOOTING = 3
PATTERN_ENGULFING = 4
# PATTERN_PIERCING_DARKCLOUD = 5  # REMOVED - no signals
PATTERN_HARAMI = 6
PATTERN_STAR = 7
PATTERN_TWEEZER = 8
PATTERN_THREE_OUTSIDE = 9
PATTERN_BELT_HOLD = 10

PATTERN_PAIR_NAMES = {
    0: "all",
    1: "doji",
    2: "hammer_hanging",
    3: "inverted_shooting",
    4: "engulfing",
    # 5: removed
    6: "harami",
    7: "star",
    8: "tweezer",
    9: "three_outside",
    10: "belt_hold",
}


# ==================== SINGLE CANDLE PATTERNS ====================

@njit(cache=True)
def _is_doji_jit(open_price: float, close_price: float, high: float, low: float, 
                  doji_ratio: float = 0.3) -> bool:
    """
    Doji: Body cực nhỏ so với range.
    DÙNG CODE CŨ: doji_ratio=0.3 cho kết quả tốt hơn (51.44% vs 48.17%)
    """
    candle_range = high - low
    if candle_range == 0:
        return False
    
    body = abs(close_price - open_price)
    body_ratio = body / candle_range
    return body_ratio <= doji_ratio


@njit(cache=True)
def _is_hammer_jit(open_price: float, close_price: float, high: float, low: float) -> bool:
    """
    Hammer: Small body at top, long lower shadow, short upper shadow.
    DÙNG CODE CŨ: 44.63% WR, $5178 PnL
    
    Criteria:
    - Body < 30% of range
    - Lower shadow >= 60% of range
    - Upper shadow <= 10% of range
    """
    candle_range = high - low
    if candle_range == 0:
        return False
    
    body = abs(close_price - open_price)
    body_top = max(open_price, close_price)
    body_bottom = min(open_price, close_price)
    lower_shadow = body_bottom - low
    upper_shadow = high - body_top
    
    # Minimum body để không nhầm với Doji
    min_body = 0.05 * candle_range
    if body < min_body:
        return False
    
    return (body / candle_range < 0.3 and 
            lower_shadow >= 0.6 * candle_range and 
            upper_shadow <= 0.1 * candle_range)


@njit(cache=True)
def _is_hanging_man_jit(open_price: float, close_price: float, high: float, low: float) -> bool:
    """
    Hanging Man: Giống Hammer về hình dạng.
    DÙNG CODE CŨ
    """
    return _is_hammer_jit(open_price, close_price, high, low)


@njit(cache=True)
def _is_inverted_hammer_jit(open_price: float, close_price: float, 
                             high: float, low: float) -> bool:
    """
    Inverted Hammer: Small body at bottom, long upper shadow, short lower shadow.
    DÙNG CODE MỚI: 47.57% WR, $12685 PnL (tốt hơn code cũ)
    
    Criteria:
    - Body < 35% của range  
    - Upper shadow >= 2x body
    - Upper shadow >= 50% of range
    - Lower shadow <= 10% của range
    """
    candle_range = high - low
    if candle_range == 0:
        return False
    
    body = abs(close_price - open_price)
    body_top = max(open_price, close_price)
    body_bottom = min(open_price, close_price)
    upper_shadow = high - body_top
    lower_shadow = body_bottom - low
    
    min_body = 0.05 * candle_range
    if body < min_body:
        return False
    
    body_ratio = body / candle_range
    upper_ratio = upper_shadow / candle_range
    lower_ratio = lower_shadow / candle_range
    
    return (body_ratio < 0.35 and 
            upper_shadow >= 2.0 * body and
            upper_ratio >= 0.5 and 
            lower_ratio <= 0.1)


@njit(cache=True)
def _is_shooting_star_jit(open_price: float, close_price: float, 
                           high: float, low: float) -> bool:
    """
    Shooting Star: Giống Inverted Hammer về hình dạng.
    DÙNG CODE MỚI
    """
    return _is_inverted_hammer_jit(open_price, close_price, high, low)


@njit(cache=True)
def _is_bullish_belt_hold_jit(open_price: float, close_price: float, 
                               high: float, low: float) -> bool:
    """
    Bullish Belt Hold: Nến bullish dài, mở cửa tại Low.
    """
    candle_range = high - low
    if candle_range == 0:
        return False
    
    if close_price <= open_price:
        return False
    
    body = close_price - open_price
    lower_shadow = open_price - low
    
    if lower_shadow > 0.05 * candle_range:
        return False
    
    return body >= 0.6 * candle_range


@njit(cache=True)
def _is_bearish_belt_hold_jit(open_price: float, close_price: float, 
                               high: float, low: float) -> bool:
    """
    Bearish Belt Hold: Nến bearish dài, mở cửa tại High.
    """
    candle_range = high - low
    if candle_range == 0:
        return False
    
    if close_price >= open_price:
        return False
    
    body = open_price - close_price
    upper_shadow = high - open_price
    
    if upper_shadow > 0.05 * candle_range:
        return False
    
    return body >= 0.6 * candle_range


# ==================== TWO CANDLE PATTERNS ====================

@njit(cache=True)
def _is_bullish_engulfing_jit(prev_open: float, prev_close: float,
                               curr_open: float, curr_close: float) -> bool:
    """Bullish Engulfing: Nến bearish trước, nến bullish hiện tại nuốt nến trước."""
    if prev_close >= prev_open:
        return False
    
    if curr_close <= curr_open:
        return False
    
    return curr_open <= prev_close and curr_close >= prev_open


@njit(cache=True)
def _is_bearish_engulfing_jit(prev_open: float, prev_close: float,
                               curr_open: float, curr_close: float) -> bool:
    """Bearish Engulfing: Nến bullish trước, nến bearish hiện tại nuốt nến trước."""
    if prev_close <= prev_open:
        return False
    
    if curr_close >= curr_open:
        return False
    
    return curr_open >= prev_close and curr_close <= prev_open


@njit(cache=True)
def _is_bullish_harami_jit(prev_open: float, prev_close: float, 
                            prev_high: float, prev_low: float,
                            curr_open: float, curr_close: float, 
                            curr_high: float, curr_low: float) -> bool:
    """Bullish Harami: Nến bearish dài trước, nến bullish nhỏ nằm trong body trước."""
    prev_body = abs(prev_close - prev_open)
    prev_range = prev_high - prev_low
    if prev_close >= prev_open:
        return False
    if prev_range > 0 and prev_body < 0.5 * prev_range:
        return False
    
    if curr_close <= curr_open:
        return False
    
    curr_body = abs(curr_close - curr_open)
    if curr_body >= 0.5 * prev_body:
        return False
    
    prev_body_low = min(prev_open, prev_close)
    prev_body_high = max(prev_open, prev_close)
    return curr_open >= prev_body_low and curr_close <= prev_body_high


@njit(cache=True)
def _is_bearish_harami_jit(prev_open: float, prev_close: float, 
                            prev_high: float, prev_low: float,
                            curr_open: float, curr_close: float, 
                            curr_high: float, curr_low: float) -> bool:
    """Bearish Harami: Nến bullish dài trước, nến bearish nhỏ nằm trong body trước."""
    prev_body = abs(prev_close - prev_open)
    prev_range = prev_high - prev_low
    if prev_close <= prev_open:
        return False
    if prev_range > 0 and prev_body < 0.5 * prev_range:
        return False
    
    if curr_close >= curr_open:
        return False
    
    curr_body = abs(curr_close - curr_open)
    if curr_body >= 0.5 * prev_body:
        return False
    
    prev_body_low = min(prev_open, prev_close)
    prev_body_high = max(prev_open, prev_close)
    return curr_open <= prev_body_high and curr_close >= prev_body_low


@njit(cache=True)
def _is_tweezer_bottom_jit(prev_open: float, prev_close: float, 
                            prev_high: float, prev_low: float,
                            curr_open: float, curr_close: float, 
                            curr_high: float, curr_low: float,
                            tolerance: float = 0.003) -> bool:
    """Tweezer Bottom: Hai nến có Low gần bằng nhau."""
    if prev_close >= prev_open:
        return False
    
    if curr_close <= curr_open:
        return False
    
    avg_low = (prev_low + curr_low) / 2
    if avg_low == 0:
        return False
    
    return abs(prev_low - curr_low) / avg_low <= tolerance


@njit(cache=True)
def _is_tweezer_top_jit(prev_open: float, prev_close: float, 
                         prev_high: float, prev_low: float,
                         curr_open: float, curr_close: float, 
                         curr_high: float, curr_low: float,
                         tolerance: float = 0.003) -> bool:
    """Tweezer Top: Hai nến có High gần bằng nhau."""
    if prev_close <= prev_open:
        return False
    
    if curr_close >= curr_open:
        return False
    
    avg_high = (prev_high + curr_high) / 2
    if avg_high == 0:
        return False
    
    return abs(prev_high - curr_high) / avg_high <= tolerance


# ==================== THREE CANDLE PATTERNS ====================

@njit(cache=True)
def _is_morning_star_jit(opens: np.ndarray, closes: np.ndarray, 
                          highs: np.ndarray, lows: np.ndarray, idx: int) -> bool:
    """
    Morning Star: Ba nến - bearish dài, star nhỏ, bullish dài.
    DÙNG CODE MỚI: Fixed gap condition (cho phép overlap 10%)
    """
    if idx < 2:
        return False
    
    o1, c1, h1, l1 = opens[idx - 2], closes[idx - 2], highs[idx - 2], lows[idx - 2]
    o2, c2, h2, l2 = opens[idx - 1], closes[idx - 1], highs[idx - 1], lows[idx - 1]
    o3, c3, h3, l3 = opens[idx], closes[idx], highs[idx], lows[idx]
    
    # First candle: bearish with substantial body
    body1 = abs(c1 - o1)
    range1 = h1 - l1
    if c1 >= o1:
        return False
    if range1 > 0 and body1 < 0.5 * range1:
        return False
    
    # Second candle: small body (star)
    range2 = h2 - l2
    body2 = abs(c2 - o2)
    if range2 == 0:
        return False
    if body2 / range2 > 0.3:
        return False
    
    # Third candle: bullish with substantial body
    body3 = abs(c3 - o3)
    range3 = h3 - l3
    if c3 <= o3:
        return False
    if range3 > 0 and body3 < 0.5 * range3:
        return False
    
    # Gap condition: Star body should be near/below first candle's close
    # Cho phép overlap 10% thay vì yêu cầu gap hoàn toàn
    star_body_high = max(o2, c2)
    if star_body_high > c1 + 0.1 * body1:
        return False
    
    # Third closes above 50% of first body
    mid1 = (o1 + c1) / 2
    return c3 > mid1


@njit(cache=True)
def _is_evening_star_jit(opens: np.ndarray, closes: np.ndarray, 
                          highs: np.ndarray, lows: np.ndarray, idx: int) -> bool:
    """
    Evening Star: Ba nến - bullish dài, star nhỏ, bearish dài.
    DÙNG CODE MỚI: Fixed gap condition
    """
    if idx < 2:
        return False
    
    o1, c1, h1, l1 = opens[idx - 2], closes[idx - 2], highs[idx - 2], lows[idx - 2]
    o2, c2, h2, l2 = opens[idx - 1], closes[idx - 1], highs[idx - 1], lows[idx - 1]
    o3, c3, h3, l3 = opens[idx], closes[idx], highs[idx], lows[idx]
    
    # First candle: bullish with substantial body
    body1 = abs(c1 - o1)
    range1 = h1 - l1
    if c1 <= o1:
        return False
    if range1 > 0 and body1 < 0.5 * range1:
        return False
    
    # Second candle: small body (star)
    range2 = h2 - l2
    body2 = abs(c2 - o2)
    if range2 == 0:
        return False
    if body2 / range2 > 0.3:
        return False
    
    # Third candle: bearish with substantial body
    body3 = abs(c3 - o3)
    range3 = h3 - l3
    if c3 >= o3:
        return False
    if range3 > 0 and body3 < 0.5 * range3:
        return False
    
    # Gap condition: Star body should be near/above first candle's close
    star_body_low = min(o2, c2)
    if star_body_low < c1 - 0.1 * body1:
        return False
    
    # Third closes below 50% of first body
    mid1 = (o1 + c1) / 2
    return c3 < mid1


@njit(cache=True)
def _is_three_outside_up_jit(opens: np.ndarray, closes: np.ndarray, idx: int) -> bool:
    """Three Outside Up: Bullish Engulfing + nến xác nhận."""
    if idx < 2:
        return False
    
    o1, c1 = opens[idx - 2], closes[idx - 2]
    o2, c2 = opens[idx - 1], closes[idx - 1]
    o3, c3 = opens[idx], closes[idx]
    
    if c1 >= o1:
        return False
    
    if c2 <= o2:
        return False
    if not (o2 <= c1 and c2 >= o1):
        return False
    
    if c3 <= o3:
        return False
    
    return c3 > c2


@njit(cache=True)
def _is_three_outside_down_jit(opens: np.ndarray, closes: np.ndarray, idx: int) -> bool:
    """Three Outside Down: Bearish Engulfing + nến xác nhận."""
    if idx < 2:
        return False
    
    o1, c1 = opens[idx - 2], closes[idx - 2]
    o2, c2 = opens[idx - 1], closes[idx - 1]
    o3, c3 = opens[idx], closes[idx]
    
    if c1 <= o1:
        return False
    
    if c2 >= o2:
        return False
    if not (o2 >= c1 and c2 <= o1):
        return False
    
    if c3 >= o3:
        return False
    
    return c3 < c2


# ==================== MAIN ENTRY POINTS ====================

@njit(cache=True)
def check_reversal_pattern_jit(opens: np.ndarray, closes: np.ndarray, 
                                highs: np.ndarray, lows: np.ndarray,
                                current_idx: int, is_bullish: bool) -> bool:
    """
    Check if current candle shows a reversal pattern.
    ĐÃ LOẠI BỎ: Piercing Line / Dark Cloud Cover (ID 5) - không có tín hiệu
    """
    o_curr = opens[current_idx]
    c_curr = closes[current_idx]
    h_curr = highs[current_idx]
    l_curr = lows[current_idx]
    
    # Doji (doji_ratio=0.3 từ code cũ)
    if _is_doji_jit(o_curr, c_curr, h_curr, l_curr, 0.3):
        return True
    
    if is_bullish:
        # ===== BULLISH REVERSAL PATTERNS =====
        
        # Single candle
        if _is_hammer_jit(o_curr, c_curr, h_curr, l_curr):
            return True
        if _is_inverted_hammer_jit(o_curr, c_curr, h_curr, l_curr):
            return True
        if _is_bullish_belt_hold_jit(o_curr, c_curr, h_curr, l_curr):
            return True
        
        # Two candle patterns
        if current_idx > 0:
            o_prev = opens[current_idx - 1]
            c_prev = closes[current_idx - 1]
            h_prev = highs[current_idx - 1]
            l_prev = lows[current_idx - 1]
            
            if _is_bullish_engulfing_jit(o_prev, c_prev, o_curr, c_curr):
                return True
            # REMOVED: _is_piercing_line_jit - no signals
            if _is_bullish_harami_jit(o_prev, c_prev, h_prev, l_prev, 
                                       o_curr, c_curr, h_curr, l_curr):
                return True
            if _is_tweezer_bottom_jit(o_prev, c_prev, h_prev, l_prev, 
                                       o_curr, c_curr, h_curr, l_curr, 0.003):
                return True
        
        # Three candle patterns
        if current_idx > 1:
            if _is_morning_star_jit(opens, closes, highs, lows, current_idx):
                return True
            if _is_three_outside_up_jit(opens, closes, current_idx):
                return True
    
    else:
        # ===== BEARISH REVERSAL PATTERNS =====
        
        # Single candle
        if _is_shooting_star_jit(o_curr, c_curr, h_curr, l_curr):
            return True
        if _is_hanging_man_jit(o_curr, c_curr, h_curr, l_curr):
            return True
        if _is_bearish_belt_hold_jit(o_curr, c_curr, h_curr, l_curr):
            return True
        
        # Two candle patterns
        if current_idx > 0:
            o_prev = opens[current_idx - 1]
            c_prev = closes[current_idx - 1]
            h_prev = highs[current_idx - 1]
            l_prev = lows[current_idx - 1]
            
            if _is_bearish_engulfing_jit(o_prev, c_prev, o_curr, c_curr):
                return True
            # REMOVED: _is_dark_cloud_cover_jit - no signals
            if _is_bearish_harami_jit(o_prev, c_prev, h_prev, l_prev,
                                       o_curr, c_curr, h_curr, l_curr):
                return True
            if _is_tweezer_top_jit(o_prev, c_prev, h_prev, l_prev,
                                    o_curr, c_curr, h_curr, l_curr, 0.003):
                return True
        
        # Three candle patterns
        if current_idx > 1:
            if _is_evening_star_jit(opens, closes, highs, lows, current_idx):
                return True
            if _is_three_outside_down_jit(opens, closes, current_idx):
                return True
    
    return False


@njit(cache=True)
def check_pattern_by_ids_jit(opens: np.ndarray, closes: np.ndarray, 
                              highs: np.ndarray, lows: np.ndarray,
                              current_idx: int, is_bullish: bool,
                              pattern_ids: np.ndarray) -> bool:
    """
    Check reversal patterns theo mảng IDs được chỉ định.
    
    Pattern Pair IDs (đã tối ưu):
        0  = all (tất cả patterns)
        1  = doji (doji_ratio=0.3)
        2  = hammer / hanging_man
        3  = inverted_hammer / shooting_star (code mới)
        4  = bullish_engulfing / bearish_engulfing
        5  = [REMOVED - no signals]
        6  = bullish_harami / bearish_harami
        7  = morning_star / evening_star (code mới - fixed gap)
        8  = tweezer_bottom / tweezer_top
        9  = three_outside_up / three_outside_down
        10 = bullish_belt_hold / bearish_belt_hold
    """
    # Check for "all" pattern
    for i in range(len(pattern_ids)):
        if pattern_ids[i] == 0:
            return check_reversal_pattern_jit(opens, closes, highs, lows, 
                                               current_idx, is_bullish)
    
    # Get current OHLC
    o_curr = opens[current_idx]
    c_curr = closes[current_idx]
    h_curr = highs[current_idx]
    l_curr = lows[current_idx]
    
    # Get previous OHLC if available
    has_prev = current_idx > 0
    if has_prev:
        o_prev = opens[current_idx - 1]
        c_prev = closes[current_idx - 1]
        h_prev = highs[current_idx - 1]
        l_prev = lows[current_idx - 1]
    else:
        o_prev = c_prev = h_prev = l_prev = 0.0
    
    # Check each pattern ID
    for i in range(len(pattern_ids)):
        pid = pattern_ids[i]
        
        # ID 1: Doji (doji_ratio=0.3 từ code cũ)
        if pid == 1:
            if _is_doji_jit(o_curr, c_curr, h_curr, l_curr, 0.3):
                return True
        
        # ID 2: Hammer / Hanging Man (code cũ)
        elif pid == 2:
            if is_bullish:
                if _is_hammer_jit(o_curr, c_curr, h_curr, l_curr):
                    return True
            else:
                if _is_hanging_man_jit(o_curr, c_curr, h_curr, l_curr):
                    return True
        
        # ID 3: Inverted Hammer / Shooting Star (code mới)
        elif pid == 3:
            if is_bullish:
                if _is_inverted_hammer_jit(o_curr, c_curr, h_curr, l_curr):
                    return True
            else:
                if _is_shooting_star_jit(o_curr, c_curr, h_curr, l_curr):
                    return True
        
        # ID 4: Engulfing patterns
        elif pid == 4:
            if has_prev:
                if is_bullish:
                    if _is_bullish_engulfing_jit(o_prev, c_prev, o_curr, c_curr):
                        return True
                else:
                    if _is_bearish_engulfing_jit(o_prev, c_prev, o_curr, c_curr):
                        return True
        
        # ID 5: REMOVED (Piercing Line / Dark Cloud Cover - no signals)
        # elif pid == 5:
        #     pass  # Skip - no signals in backtest
        
        # ID 6: Harami patterns
        elif pid == 6:
            if has_prev:
                if is_bullish:
                    if _is_bullish_harami_jit(o_prev, c_prev, h_prev, l_prev,
                                               o_curr, c_curr, h_curr, l_curr):
                        return True
                else:
                    if _is_bearish_harami_jit(o_prev, c_prev, h_prev, l_prev,
                                               o_curr, c_curr, h_curr, l_curr):
                        return True
        
        # ID 7: Star patterns (code mới - fixed gap)
        elif pid == 7:
            if current_idx > 1:
                if is_bullish:
                    if _is_morning_star_jit(opens, closes, highs, lows, current_idx):
                        return True
                else:
                    if _is_evening_star_jit(opens, closes, highs, lows, current_idx):
                        return True
        
        # ID 8: Tweezer patterns
        elif pid == 8:
            if has_prev:
                if is_bullish:
                    if _is_tweezer_bottom_jit(o_prev, c_prev, h_prev, l_prev,
                                               o_curr, c_curr, h_curr, l_curr, 0.003):
                        return True
                else:
                    if _is_tweezer_top_jit(o_prev, c_prev, h_prev, l_prev,
                                            o_curr, c_curr, h_curr, l_curr, 0.003):
                        return True
        
        # ID 9: Three Outside patterns
        elif pid == 9:
            if current_idx > 1:
                if is_bullish:
                    if _is_three_outside_up_jit(opens, closes, current_idx):
                        return True
                else:
                    if _is_three_outside_down_jit(opens, closes, current_idx):
                        return True
        
        # ID 10: Belt Hold patterns
        elif pid == 10:
            if is_bullish:
                if _is_bullish_belt_hold_jit(o_curr, c_curr, h_curr, l_curr):
                    return True
            else:
                if _is_bearish_belt_hold_jit(o_curr, c_curr, h_curr, l_curr):
                    return True
    
    return False


# ==================== UTILITY FUNCTIONS ====================

def get_pattern_name(pattern_id: int) -> str:
    """Get pattern pair name from ID."""
    return PATTERN_PAIR_NAMES.get(pattern_id, "unknown")


def get_valid_pattern_ids() -> list:
    """Get list of valid pattern IDs (excluding removed ones)."""
    return [0, 1, 2, 3, 4, 6, 7, 8, 9, 10]  # Bỏ ID 5


def validate_pattern_ids(pattern_ids: list) -> np.ndarray:
    """Validate and convert pattern IDs to numpy array."""
    valid_ids = get_valid_pattern_ids()
    result = []
    
    for pid in pattern_ids:
        if pid in valid_ids:
            result.append(pid)
        elif pid == 5:
            print(f"Warning: Pattern ID 5 (piercing/darkcloud) removed - no signals")
        else:
            print(f"Warning: Invalid pattern ID {pid} ignored")
    
    if not result:
        result = [0]  # Default to all patterns
    
    return np.array(result, dtype=np.int64)