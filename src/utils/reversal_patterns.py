"""
Reversal Candle Patterns - Numba JIT Optimized
===============================================
All candlestick reversal pattern detection functions optimized with @njit.

Patterns included:
- Single candle: Doji, Hammer, Inverted Hammer, Shooting Star, Hanging Man, Belt Hold
- Two candle: Bullish/Bearish Engulfing, Piercing Line, Dark Cloud Cover, 
              Bullish/Bearish Harami, Tweezer Top/Bottom
- Three candle: Morning Star, Evening Star, Three Outside Up/Down

Usage:
    from reversal_patterns import check_reversal_pattern_jit, check_pattern_by_ids_jit
    
    # Check all patterns
    is_reversal = check_reversal_pattern_jit(
        opens, closes, highs, lows, current_idx, is_bullish=True
    )
    
    # Check specific pattern pairs by IDs
    pattern_ids = np.array([1, 2], dtype=np.int64)  # Doji + Hammer/Hanging
    is_reversal = check_pattern_by_ids_jit(
        opens, closes, highs, lows, current_idx, is_bullish=True, pattern_ids=pattern_ids
    )

Pattern Pair IDs:
    0  = all (tất cả patterns)
    1  = doji
    2  = hammer / hanging_man
    3  = inverted_hammer / shooting_star
    4  = bullish_engulfing / bearish_engulfing
    5  = piercing_line / dark_cloud_cover
    6  = bullish_harami / bearish_harami
    7  = morning_star / evening_star
    8  = tweezer_bottom / tweezer_top
    9  = three_outside_up / three_outside_down
    10 = bullish_belt_hold / bearish_belt_hold
"""

import numpy as np
from numba import njit
from numba.typed import List as NumbaList


# ==================== PATTERN PAIR CONSTANTS ====================
# Mỗi ID đại diện cho một cặp pattern đối nghịch (bullish/bearish)

PATTERN_ALL = 0
PATTERN_DOJI = 1
PATTERN_HAMMER_HANGING = 2
PATTERN_INVERTED_SHOOTING = 3
PATTERN_ENGULFING = 4
PATTERN_PIERCING_DARKCLOUD = 5
PATTERN_HARAMI = 6
PATTERN_STAR = 7
PATTERN_TWEEZER = 8
PATTERN_THREE_OUTSIDE = 9
PATTERN_BELT_HOLD = 10

# Danh sách tên các cặp pattern để reference
PATTERN_PAIR_NAMES = [
    "all",                      # 0
    "doji",                     # 1
    "hammer_hanging",           # 2
    "inverted_shooting",        # 3
    "engulfing",                # 4
    "piercing_darkcloud",       # 5
    "harami",                   # 6
    "star",                     # 7
    "tweezer",                  # 8
    "three_outside",            # 9
    "belt_hold",                # 10
]


# ==================== SINGLE CANDLE PATTERNS ====================

@njit(cache=True)
def _is_doji_jit(open_price: float, close_price: float, high: float, low: float, 
                  doji_ratio: float = 0.3) -> bool:
    """
    Doji: Body is very small relative to the range.
    Reversal signal - indicates indecision.
    
    Args:
        doji_ratio: threshold for body/range ratio (0.3 = body <= 30% of range)
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
    Bullish reversal pattern.
    
    Criteria:
    - Body < 30% of range
    - Lower shadow >= 2x body
    - Upper shadow <= body
    """
    candle_range = high - low
    if candle_range == 0:
        return False
    
    body = abs(close_price - open_price)
    body_top = max(open_price, close_price)
    body_bottom = min(open_price, close_price)
    lower_shadow = body_bottom - low
    upper_shadow = high - body_top
    
    min_body = 0.05 * candle_range
    if body < min_body:
        return False
    
    return (body / candle_range < 0.3 and 
            lower_shadow >= 0.6 * candle_range and 
            upper_shadow <= 0.1 * candle_range)


@njit(cache=True)
def _is_inverted_hammer_jit(open_price: float, close_price: float, high: float, low: float) -> bool:
    """
    Inverted Hammer: Small body at bottom, long upper shadow, short lower shadow.
    Bullish reversal pattern.
    
    Criteria:
    - Body < 30% of range
    - Upper shadow >= 2x body
    - Lower shadow <= body
    """
    candle_range = high - low
    if candle_range == 0:
        return False
    
    body = abs(close_price - open_price)
    body_top = max(open_price, close_price)
    body_bottom = min(open_price, close_price)
    lower_shadow = body_bottom - low
    upper_shadow = high - body_top
    
    min_body = 0.05 * candle_range
    if body < min_body:
        return False
    
    return (body / candle_range < 0.3 and 
            upper_shadow >= 0.6 * candle_range and 
            lower_shadow <= 0.1 * candle_range)


@njit(cache=True)
def _is_shooting_star_jit(open_price: float, close_price: float, high: float, low: float) -> bool:
    """
    Shooting Star: Small body at bottom, long upper shadow, short lower shadow.
    Bearish reversal pattern.
    
    Criteria:
    - Body < 30% of range
    - Upper shadow >= 2x body
    - Lower shadow <= body
    """
    candle_range = high - low
    if candle_range == 0:
        return False
    
    body = abs(close_price - open_price)
    body_top = max(open_price, close_price)
    body_bottom = min(open_price, close_price)
    lower_shadow = body_bottom - low
    upper_shadow = high - body_top
    
    min_body = 0.05 * candle_range
    if body < min_body:
        return False
    
    return (body / candle_range < 0.3 and 
            upper_shadow >= 0.6 * candle_range and 
            lower_shadow <= 0.1 * candle_range)


@njit(cache=True)
def _is_hanging_man_jit(open_price: float, close_price: float, high: float, low: float) -> bool:
    """
    Hanging Man: Small body at top, long lower shadow, short upper shadow.
    Bearish reversal pattern.
    
    Criteria:
    - Body < 30% of range
    - Lower shadow >= 2x body
    - Upper shadow <= body
    """
    candle_range = high - low
    if candle_range == 0:
        return False
    
    body = abs(close_price - open_price)
    body_top = max(open_price, close_price)
    body_bottom = min(open_price, close_price)
    lower_shadow = body_bottom - low
    upper_shadow = high - body_top
    
    min_body = 0.05 * candle_range
    if body < min_body:
        return False
    
    return (body / candle_range < 0.3 and 
            lower_shadow >= 0.6 * candle_range and 
            upper_shadow <= 0.1 * candle_range)


@njit(cache=True)
def _is_bullish_belt_hold_jit(open_price: float, close_price: float, 
                               high: float, low: float) -> bool:
    """
    Bullish Belt Hold (Yorikiri): Long bullish candle that opens at its low.
    Bullish reversal pattern.
    
    Criteria:
    - Bullish candle (close > open)
    - Open = Low (no lower shadow, or very small)
    - Long body (>= 60% of range)
    """
    candle_range = high - low
    if candle_range == 0:
        return False
    
    # Must be bullish
    if close_price <= open_price:
        return False
    
    body = close_price - open_price
    lower_shadow = open_price - low
    
    # Open should be at or very near Low (lower shadow < 5% of range)
    if lower_shadow > 0.05 * candle_range:
        return False
    
    # Body should be long
    return body >= 0.6 * candle_range


@njit(cache=True)
def _is_bearish_belt_hold_jit(open_price: float, close_price: float, 
                               high: float, low: float) -> bool:
    """
    Bearish Belt Hold (Yorikiri): Long bearish candle that opens at its high.
    Bearish reversal pattern.
    
    Criteria:
    - Bearish candle (close < open)
    - Open = High (no upper shadow, or very small)
    - Long body (>= 60% of range)
    """
    candle_range = high - low
    if candle_range == 0:
        return False
    
    # Must be bearish
    if close_price >= open_price:
        return False
    
    body = open_price - close_price
    upper_shadow = high - open_price
    
    # Open should be at or very near High (upper shadow < 5% of range)
    if upper_shadow > 0.05 * candle_range:
        return False
    
    # Body should be long
    return body >= 0.6 * candle_range


# ==================== TWO CANDLE PATTERNS ====================

@njit(cache=True)
def _is_tweezer_bottom_jit(prev_open: float, prev_close: float, prev_high: float, prev_low: float,
                            curr_open: float, curr_close: float, curr_high: float, curr_low: float,
                            tolerance: float = 0.003) -> bool:
    """
    Tweezer Bottom: Two candles with matching lows - first bearish, second bullish.
    Bullish reversal pattern.
    
    Criteria:
    - First candle: bearish
    - Second candle: bullish  
    - Lows are approximately equal (within tolerance)
    """
    # First must be bearish
    if prev_close >= prev_open:
        return False
    
    # Second must be bullish
    if curr_close <= curr_open:
        return False
    
    # Lows should be approximately equal
    avg_low = (prev_low + curr_low) / 2
    if avg_low == 0:
        return False
    
    return abs(prev_low - curr_low) / avg_low <= tolerance


@njit(cache=True)
def _is_tweezer_top_jit(prev_open: float, prev_close: float, prev_high: float, prev_low: float,
                          curr_open: float, curr_close: float, curr_high: float, curr_low: float,
                          tolerance: float = 0.003) -> bool:
    """
    Tweezer Top: Two candles with matching highs - first bullish, second bearish.
    Bearish reversal pattern.
    
    Criteria:
    - First candle: bullish
    - Second candle: bearish
    - Highs are approximately equal (within tolerance)
    """
    # First must be bullish
    if prev_close <= prev_open:
        return False
    
    # Second must be bearish
    if curr_close >= curr_open:
        return False
    
    # Highs should be approximately equal
    avg_high = (prev_high + curr_high) / 2
    if avg_high == 0:
        return False
    
    return abs(prev_high - curr_high) / avg_high <= tolerance



@njit(cache=True)
def _is_bullish_engulfing_jit(prev_open: float, prev_close: float,
                               curr_open: float, curr_close: float) -> bool:
    """
    Bullish Engulfing: Previous bearish candle, current bullish candle that engulfs previous.
    Bullish reversal pattern.
    
    Criteria:
    - Previous: bearish (open > close)
    - Current: bullish (close > open)
    - Current body engulfs previous body
    """
    # Previous must be bearish
    if prev_close >= prev_open:
        return False
    
    # Current must be bullish
    if curr_close <= curr_open:
        return False
    
    # Current engulfs previous body
    return curr_open <= prev_close and curr_close >= prev_open


@njit(cache=True)
def _is_bearish_engulfing_jit(prev_open: float, prev_close: float,
                               curr_open: float, curr_close: float) -> bool:
    """
    Bearish Engulfing: Previous bullish candle, current bearish candle that engulfs previous.
    Bearish reversal pattern.
    
    Criteria:
    - Previous: bullish (close > open)
    - Current: bearish (open > close)
    - Current body engulfs previous body
    """
    # Previous must be bullish
    if prev_close <= prev_open:
        return False
    
    # Current must be bearish
    if curr_close >= curr_open:
        return False
    
    # Current engulfs previous body
    return curr_open >= prev_close and curr_close <= prev_open


@njit(cache=True)
def _is_piercing_line_jit(prev_open: float, prev_close: float, 
                           curr_open: float, curr_close: float) -> bool:
    """
    Piercing Line: Previous bearish, current bullish opens below prev close, 
    closes above 50% of prev body.
    Bullish reversal pattern.
    """
    # Previous must be bearish
    if prev_close >= prev_open:
        return False
    
    # Current must be bullish
    if curr_close <= curr_open:
        return False
    
    # Current opens below previous close (gap down)
    if curr_open >= prev_close:
        return False
    
    # Current closes above 50% of previous body but below previous open
    prev_mid = (prev_open + prev_close) / 2
    return curr_close > prev_mid and curr_close < prev_open


@njit(cache=True)
def _is_dark_cloud_cover_jit(prev_open: float, prev_close: float,
                              curr_open: float, curr_close: float) -> bool:
    """
    Dark Cloud Cover: Previous bullish, current bearish opens above prev close,
    closes below 50% of prev body.
    Bearish reversal pattern.
    """
    # Previous must be bullish
    if prev_close <= prev_open:
        return False
    
    # Current must be bearish
    if curr_close >= curr_open:
        return False
    
    # Current opens above previous close (gap up)
    if curr_open <= prev_close:
        return False
    
    # Current closes below 50% of previous body but above previous open
    prev_mid = (prev_open + prev_close) / 2
    return curr_close < prev_mid and curr_close > prev_open


@njit(cache=True)
def _is_bullish_harami_jit(prev_open: float, prev_close: float, prev_high: float, prev_low: float,
                            curr_open: float, curr_close: float, curr_high: float, curr_low: float) -> bool:
    """
    Bullish Harami: Previous bearish (long), current bullish (small) contained within prev body.
    Bullish reversal pattern.
    """
    # Previous must be bearish with substantial body
    prev_body = abs(prev_close - prev_open)
    prev_range = prev_high - prev_low
    if prev_close >= prev_open or (prev_range > 0 and prev_body < 0.5 * prev_range):
        return False
    
    # Current must be bullish
    if curr_close <= curr_open:
        return False
    
    curr_body = abs(curr_close - curr_open)
    if curr_body >= 0.5 * prev_body:
        return False
    
    # Current contained within previous body
    prev_body_low = min(prev_open, prev_close)
    prev_body_high = max(prev_open, prev_close)
    return curr_open >= prev_body_low and curr_close <= prev_body_high


@njit(cache=True)
def _is_bearish_harami_jit(prev_open: float, prev_close: float, prev_high: float, prev_low: float,
                            curr_open: float, curr_close: float, curr_high: float, curr_low: float) -> bool:
    """
    Bearish Harami: Previous bullish (long), current bearish (small) contained within prev body.
    Bearish reversal pattern.
    """
    # Previous must be bullish with substantial body
    prev_body = abs(prev_close - prev_open)
    prev_range = prev_high - prev_low
    if prev_close <= prev_open or (prev_range > 0 and prev_body < 0.5 * prev_range):
        return False
    
    # Current must be bearish
    if curr_close >= curr_open:
        return False
    
    curr_body = abs(curr_close - curr_open)
    if curr_body >= 0.5 * prev_body:
        return False
    
    # Current contained within previous body
    prev_body_low = min(prev_open, prev_close)
    prev_body_high = max(prev_open, prev_close)
    return curr_open <= prev_body_high and curr_close >= prev_body_low


# ==================== THREE CANDLE PATTERNS ====================

@njit(cache=True)
def _is_three_outside_up_jit(opens: np.ndarray, closes: np.ndarray, idx: int) -> bool:
    """
    Three Outside Up: Bullish Engulfing + confirmation candle closing higher.
    Strong bullish reversal pattern.
    
    Criteria:
    - First candle: bearish
    - Second candle: bullish engulfing first candle
    - Third candle: bullish, closes above second candle's close
    """
    if idx < 2:
        return False
    
    o1, c1 = opens[idx - 2], closes[idx - 2]
    o2, c2 = opens[idx - 1], closes[idx - 1]
    o3, c3 = opens[idx], closes[idx]
    
    # First candle: bearish
    if c1 >= o1:
        return False
    
    # Second candle: bullish engulfing first
    if c2 <= o2:  # must be bullish
        return False
    if not (o2 <= c1 and c2 >= o1):  # must engulf
        return False
    
    # Third candle: bullish and closes above second's close
    if c3 <= o3:  # must be bullish
        return False
    
    return c3 > c2


@njit(cache=True)
def _is_three_outside_down_jit(opens: np.ndarray, closes: np.ndarray, idx: int) -> bool:
    """
    Three Outside Down: Bearish Engulfing + confirmation candle closing lower.
    Strong bearish reversal pattern.
    
    Criteria:
    - First candle: bullish
    - Second candle: bearish engulfing first candle
    - Third candle: bearish, closes below second candle's close
    """
    if idx < 2:
        return False
    
    o1, c1 = opens[idx - 2], closes[idx - 2]
    o2, c2 = opens[idx - 1], closes[idx - 1]
    o3, c3 = opens[idx], closes[idx]
    
    # First candle: bullish
    if c1 <= o1:
        return False
    
    # Second candle: bearish engulfing first
    if c2 >= o2:  # must be bearish
        return False
    if not (o2 >= c1 and c2 <= o1):  # must engulf
        return False
    
    # Third candle: bearish and closes below second's close
    if c3 >= o3:  # must be bearish
        return False
    
    return c3 < c2


@njit(cache=True)
def _is_morning_star_jit(opens: np.ndarray, closes: np.ndarray, 
                          highs: np.ndarray, lows: np.ndarray, idx: int) -> bool:
    """
    Morning Star: Three candles - bearish, small (gap down), bullish.
    Bullish reversal pattern.
    """
    if idx < 2:
        return False
    
    o1, c1, h1, l1 = opens[idx - 2], closes[idx - 2], highs[idx - 2], lows[idx - 2]
    o2, c2, h2, l2 = opens[idx - 1], closes[idx - 1], highs[idx - 1], lows[idx - 1]
    o3, c3 = opens[idx], closes[idx]
    
    # First bearish with substantial body
    body1 = abs(c1 - o1)
    range1 = h1 - l1
    if c1 >= o1 or (range1 > 0 and body1 < 0.5 * range1):
        return False
    
    # Second small (star)
    range2 = h2 - l2
    body2 = abs(c2 - o2)
    if range2 == 0 or body2 / range2 > 0.3:
        return False
    
    # Third bullish with substantial body
    body3 = abs(c3 - o3)
    range3 = highs[idx] - lows[idx]
    if c3 <= o3 or (range3 > 0 and body3 < 0.5 * range3):
        return False
    
    # Second gaps down below first close
    star_body_high = max(o2, c2)
    if star_body_high >= c1:
        return False
    
    # Third closes above 50% of first body
    mid1 = (o1 + c1) / 2
    return c3 > mid1


@njit(cache=True)
def _is_evening_star_jit(opens: np.ndarray, closes: np.ndarray, 
                          highs: np.ndarray, lows: np.ndarray, idx: int) -> bool:
    """
    Evening Star: Three candles - bullish, small (gap up), bearish.
    Bearish reversal pattern.
    """
    if idx < 2:
        return False
    
    o1, c1, h1, l1 = opens[idx - 2], closes[idx - 2], highs[idx - 2], lows[idx - 2]
    o2, c2, h2, l2 = opens[idx - 1], closes[idx - 1], highs[idx - 1], lows[idx - 1]
    o3, c3 = opens[idx], closes[idx]
    
    # First bullish with substantial body
    body1 = abs(c1 - o1)
    range1 = h1 - l1
    if c1 <= o1 or (range1 > 0 and body1 < 0.5 * range1):
        return False
    
    # Second small (star)
    range2 = h2 - l2
    body2 = abs(c2 - o2)
    if range2 == 0 or body2 / range2 > 0.3:
        return False
    
    # Third bearish with substantial body
    body3 = abs(c3 - o3)
    range3 = highs[idx] - lows[idx]
    if c3 >= o3 or (range3 > 0 and body3 < 0.5 * range3):
        return False
    
    # Second gaps up above first close
    star_body_low = min(o2, c2)
    if star_body_low <= c1:
        return False
    
    # Third closes below 50% of first body
    mid1 = (o1 + c1) / 2
    return c3 < mid1


# ==================== MAIN ENTRY POINT ====================

@njit(cache=True)
def check_reversal_pattern_jit(opens: np.ndarray, closes: np.ndarray, 
                                highs: np.ndarray, lows: np.ndarray,
                                current_idx: int, is_bullish: bool) -> bool:
    """
    Check if current candle shows a reversal pattern.
    
    Args:
        opens, closes, highs, lows: Price arrays (float64)
        current_idx: Index of current candle to check
        is_bullish: True for bullish OB (check bullish reversal patterns),
                    False for bearish OB (check bearish reversal patterns)
    
    Returns:
        True if ANY reversal pattern is detected
    
    Patterns checked (OR logic):
    - Doji (always checked regardless of bias)
    - Bullish: Hammer, Inverted Hammer, Belt Hold,
               Bullish Engulfing, Piercing Line, Bullish Harami, Tweezer Bottom,
               Morning Star, Three Outside Up
    - Bearish: Shooting Star, Hanging Man, Belt Hold,
               Bearish Engulfing, Dark Cloud Cover, Bearish Harami, Tweezer Top,
               Evening Star, Three Outside Down
    """
    o_curr = opens[current_idx]
    c_curr = closes[current_idx]
    h_curr = highs[current_idx]
    l_curr = lows[current_idx]
    
    # Check Doji first (valid for both bullish and bearish)
    if _is_doji_jit(o_curr, c_curr, h_curr, l_curr, 0.3):
        return True
    
    if is_bullish:
        # ===== BULLISH REVERSAL PATTERNS =====
        
        # Single candle patterns
        if _is_hammer_jit(o_curr, c_curr, h_curr, l_curr):
            return True
        if _is_inverted_hammer_jit(o_curr, c_curr, h_curr, l_curr):
            return True
        if _is_bullish_belt_hold_jit(o_curr, c_curr, h_curr, l_curr):
            return True
        
        # Two candle patterns (need previous candle)
        if current_idx > 0:
            o_prev = opens[current_idx - 1]
            c_prev = closes[current_idx - 1]
            h_prev = highs[current_idx - 1]
            l_prev = lows[current_idx - 1]
            
            if _is_bullish_engulfing_jit(o_prev, c_prev, o_curr, c_curr):
                return True
            if _is_piercing_line_jit(o_prev, c_prev, o_curr, c_curr):
                return True
            if _is_bullish_harami_jit(o_prev, c_prev, h_prev, l_prev, o_curr, c_curr, h_curr, l_curr):
                return True
            if _is_tweezer_bottom_jit(o_prev, c_prev, h_prev, l_prev, 
                                      o_curr, c_curr, h_curr, l_curr, 0.003):
                return True
        
        # Three candle patterns (need 2 previous candles)
        if current_idx > 1:
            if _is_morning_star_jit(opens, closes, highs, lows, current_idx):
                return True
            if _is_three_outside_up_jit(opens, closes, current_idx):
                return True
    
    else:
        # ===== BEARISH REVERSAL PATTERNS =====
        
        # Single candle patterns
        if _is_shooting_star_jit(o_curr, c_curr, h_curr, l_curr):
            return True
        if _is_hanging_man_jit(o_curr, c_curr, h_curr, l_curr):
            return True
        if _is_bearish_belt_hold_jit(o_curr, c_curr, h_curr, l_curr):
            return True
        
        # Two candle patterns (need previous candle)
        if current_idx > 0:
            o_prev = opens[current_idx - 1]
            c_prev = closes[current_idx - 1]
            h_prev = highs[current_idx - 1]
            l_prev = lows[current_idx - 1]
            
            if _is_bearish_engulfing_jit(o_prev, c_prev, o_curr, c_curr):
                return True
            if _is_dark_cloud_cover_jit(o_prev, c_prev, o_curr, c_curr):
                return True
            if _is_bearish_harami_jit(o_prev, c_prev, h_prev, l_prev, o_curr, c_curr, h_curr, l_curr):
                return True
            if _is_tweezer_top_jit(o_prev, c_prev, h_prev, l_prev,
                                   o_curr, c_curr, h_curr, l_curr, 0.003):
                return True
        
        # Three candle patterns (need 2 previous candles)
        if current_idx > 1:
            if _is_evening_star_jit(opens, closes, highs, lows, current_idx):
                return True
            if _is_three_outside_down_jit(opens, closes, current_idx):
                return True
    
    return False


# ==================== PATTERN BY IDS ENTRY POINT ====================

@njit(cache=True)
def check_pattern_by_ids_jit(opens: np.ndarray, closes: np.ndarray, 
                              highs: np.ndarray, lows: np.ndarray,
                              current_idx: int, is_bullish: bool,
                              pattern_ids: np.ndarray) -> bool:
    """
    Check reversal patterns theo mảng IDs được chỉ định.
    
    Args:
        opens, closes, highs, lows: Price arrays (float64)
        current_idx: Index of current candle to check
        is_bullish: True for bullish OB, False for bearish OB
        pattern_ids: numpy array of int64 chứa các ID cặp pattern muốn check
                     Ví dụ: np.array([1, 2]) = check Doji + Hammer/Hanging
    
    Pattern Pair IDs:
        0  = all (tất cả patterns)
        1  = doji
        2  = hammer / hanging_man
        3  = inverted_hammer / shooting_star
        4  = bullish_engulfing / bearish_engulfing
        5  = piercing_line / dark_cloud_cover
        6  = bullish_harami / bearish_harami
        7  = morning_star / evening_star
        8  = tweezer_bottom / tweezer_top
        9  = three_outside_up / three_outside_down
        10 = bullish_belt_hold / bearish_belt_hold
    
    Returns:
        True if ANY pattern trong danh sách được phát hiện
    """
    # Nếu có 0 trong mảng -> check tất cả patterns
    for i in range(len(pattern_ids)):
        if pattern_ids[i] == 0:
            return check_reversal_pattern_jit(opens, closes, highs, lows, current_idx, is_bullish)
    
    # Lấy giá trị OHLC hiện tại
    o_curr = opens[current_idx]
    c_curr = closes[current_idx]
    h_curr = highs[current_idx]
    l_curr = lows[current_idx]
    
    # Lấy giá trị OHLC previous (nếu có)
    has_prev = current_idx > 0
    o_prev = opens[current_idx - 1] if has_prev else 0.0
    c_prev = closes[current_idx - 1] if has_prev else 0.0
    h_prev = highs[current_idx - 1] if has_prev else 0.0
    l_prev = lows[current_idx - 1] if has_prev else 0.0
    
    # Check từng pattern ID trong mảng
    for i in range(len(pattern_ids)):
        pid = pattern_ids[i]
        
        # ID 1: Doji (cả bullish và bearish)
        if pid == 1:
            if _is_doji_jit(o_curr, c_curr, h_curr, l_curr, 0.3):
                return True
        
        # ID 2: Hammer (bullish) / Hanging Man (bearish)
        elif pid == 2:
            if is_bullish:
                if _is_hammer_jit(o_curr, c_curr, h_curr, l_curr):
                    return True
            else:
                if _is_hanging_man_jit(o_curr, c_curr, h_curr, l_curr):
                    return True
        
        # ID 3: Inverted Hammer (bullish) / Shooting Star (bearish)
        elif pid == 3:
            if is_bullish:
                if _is_inverted_hammer_jit(o_curr, c_curr, h_curr, l_curr):
                    return True
            else:
                if _is_shooting_star_jit(o_curr, c_curr, h_curr, l_curr):
                    return True
        
        # ID 4: Bullish Engulfing / Bearish Engulfing
        elif pid == 4:
            if has_prev:
                if is_bullish:
                    if _is_bullish_engulfing_jit(o_prev, c_prev, o_curr, c_curr):
                        return True
                else:
                    if _is_bearish_engulfing_jit(o_prev, c_prev, o_curr, c_curr):
                        return True
        
        # ID 5: Piercing Line (bullish) / Dark Cloud Cover (bearish)
        elif pid == 5:
            if has_prev:
                if is_bullish:
                    if _is_piercing_line_jit(o_prev, c_prev, o_curr, c_curr):
                        return True
                else:
                    if _is_dark_cloud_cover_jit(o_prev, c_prev, o_curr, c_curr):
                        return True
        
        # ID 6: Bullish Harami / Bearish Harami
        elif pid == 6:
            if has_prev:
                if is_bullish:
                    if _is_bullish_harami_jit(o_prev, c_prev, h_prev, l_prev, o_curr, c_curr, h_curr, l_curr):
                        return True
                else:
                    if _is_bearish_harami_jit(o_prev, c_prev, h_prev, l_prev, o_curr, c_curr, h_curr, l_curr):
                        return True
        
        # ID 7: Morning Star (bullish) / Evening Star (bearish)
        elif pid == 7:
            if current_idx > 1:
                if is_bullish:
                    if _is_morning_star_jit(opens, closes, highs, lows, current_idx):
                        return True
                else:
                    if _is_evening_star_jit(opens, closes, highs, lows, current_idx):
                        return True
        
        # ID 8: Tweezer Bottom (bullish) / Tweezer Top (bearish)
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
        
        # ID 9: Three Outside Up (bullish) / Three Outside Down (bearish)
        elif pid == 9:
            if current_idx > 1:
                if is_bullish:
                    if _is_three_outside_up_jit(opens, closes, current_idx):
                        return True
                else:
                    if _is_three_outside_down_jit(opens, closes, current_idx):
                        return True
        
        # ID 10: Bullish Belt Hold / Bearish Belt Hold
        elif pid == 10:
            if is_bullish:
                if _is_bullish_belt_hold_jit(o_curr, c_curr, h_curr, l_curr):
                    return True
            else:
                if _is_bearish_belt_hold_jit(o_curr, c_curr, h_curr, l_curr):
                    return True
    
    return False
