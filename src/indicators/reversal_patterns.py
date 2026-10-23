"""
Reversal Candle Patterns - Optimized Combined Version
======================================================
Kết hợp các phiên bản tốt nhất từ backtest.

Pattern Pair IDs (LONG / SHORT):
    0  = all (tất cả patterns bên dưới)
    1  = doji (body <= doji_ratio * range, mặc định 0.1)
    2  = hammer (chỉ LONG - râu dưới dài = từ chối giá thấp tại vùng cầu)
    3  = shooting_star (chỉ SHORT - râu trên dài = từ chối giá cao tại vùng cung)
    4  = bullish_engulfing / bearish_engulfing
    5  = [REMOVED - no signals]
    6  = bullish_harami / bearish_harami
    7  = morning_star / evening_star (fixed gap)
    8  = tweezer_bottom / tweezer_top (sai số tính theo biên độ nến)
    9  = three_outside_up / three_outside_down
    10 = bullish_belt_hold / bearish_belt_hold

Hanging man (râu dưới dài cho SHORT) và inverted hammer (râu trên dài cho LONG)
đã bị loại: tại vùng OB, hình dạng đó thể hiện lực ngược hướng lệnh chứ không
phải sự từ chối giá.
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

DEFAULT_DOJI_RATIO = 0.1
DEFAULT_TWEEZER_TOLERANCE = 0.1  # max |low1 - low2| as a fraction of the average candle range

PATTERN_PAIR_NAMES = {
    0: "all",
    1: "doji",
    2: "hammer",
    3: "shooting_star",
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
                  doji_ratio: float = 0.1) -> bool:
    """
    Doji: Body cực nhỏ so với range (mặc định body <= 10% range).
    Ngưỡng cũ 0.3 quá rộng: ~23% nến 1m được tính là doji và nó nuốt luôn
    hammer/shooting star. Con số WR cũ (51.44%) đo trên backtest có lỗi khớp
    lệnh nhìn trước tương lai, nên không còn dùng làm căn cứ.
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
                            tolerance: float = 0.1) -> bool:
    """
    Tweezer Bottom: nến giảm rồi nến tăng, hai Low gần bằng nhau.
    Sai số tính theo biên độ nến trung bình (không theo % giá) để không
    biến thành "nến đỏ rồi nến xanh" trên khung thời gian nhỏ.
    """
    if prev_close >= prev_open:
        return False

    if curr_close <= curr_open:
        return False

    avg_range = ((prev_high - prev_low) + (curr_high - curr_low)) / 2
    if avg_range <= 0:
        return False

    return abs(prev_low - curr_low) <= tolerance * avg_range


@njit(cache=True)
def _is_tweezer_top_jit(prev_open: float, prev_close: float, 
                         prev_high: float, prev_low: float,
                         curr_open: float, curr_close: float,
                         curr_high: float, curr_low: float,
                         tolerance: float = 0.1) -> bool:
    """Tweezer Top: nến tăng rồi nến giảm, hai High gần bằng nhau (sai số theo biên độ nến)."""
    if prev_close <= prev_open:
        return False

    if curr_close >= curr_open:
        return False

    avg_range = ((prev_high - prev_low) + (curr_high - curr_low)) / 2
    if avg_range <= 0:
        return False

    return abs(prev_high - curr_high) <= tolerance * avg_range


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
def _family_enabled(pattern_ids: np.ndarray, family_id: int) -> bool:
    for i in range(len(pattern_ids)):
        if pattern_ids[i] == PATTERN_ALL or pattern_ids[i] == family_id:
            return True
    return False


@njit(cache=True)
def count_reversal_patterns_jit(opens: np.ndarray, closes: np.ndarray,
                                highs: np.ndarray, lows: np.ndarray,
                                current_idx: int, is_bullish: bool,
                                pattern_ids: np.ndarray,
                                doji_ratio: float = DEFAULT_DOJI_RATIO,
                                tweezer_tolerance: float = DEFAULT_TWEEZER_TOLERANCE) -> int:
    """
    Count how many enabled pattern families match the candle at current_idx.

    pattern_ids containing 0 enables every family. Each family counts at most
    once, so the result can be compared with a "min matches" threshold.
    """
    o_curr = opens[current_idx]
    c_curr = closes[current_idx]
    h_curr = highs[current_idx]
    l_curr = lows[current_idx]

    has_prev = current_idx > 0
    o_prev = c_prev = h_prev = l_prev = 0.0
    if has_prev:
        o_prev = opens[current_idx - 1]
        c_prev = closes[current_idx - 1]
        h_prev = highs[current_idx - 1]
        l_prev = lows[current_idx - 1]
    has_two_prev = current_idx > 1

    count = 0

    # ID 1: Doji (neutral, valid for both sides)
    if _family_enabled(pattern_ids, PATTERN_DOJI):
        if _is_doji_jit(o_curr, c_curr, h_curr, l_curr, doji_ratio):
            count += 1

    if is_bullish:
        # ID 2: Hammer (LONG only)
        if _family_enabled(pattern_ids, PATTERN_HAMMER_HANGING):
            if _is_hammer_jit(o_curr, c_curr, h_curr, l_curr):
                count += 1
        # ID 4: Engulfing
        if has_prev and _family_enabled(pattern_ids, PATTERN_ENGULFING):
            if _is_bullish_engulfing_jit(o_prev, c_prev, o_curr, c_curr):
                count += 1
        # ID 6: Harami
        if has_prev and _family_enabled(pattern_ids, PATTERN_HARAMI):
            if _is_bullish_harami_jit(o_prev, c_prev, h_prev, l_prev,
                                      o_curr, c_curr, h_curr, l_curr):
                count += 1
        # ID 7: Morning star
        if has_two_prev and _family_enabled(pattern_ids, PATTERN_STAR):
            if _is_morning_star_jit(opens, closes, highs, lows, current_idx):
                count += 1
        # ID 8: Tweezer bottom
        if has_prev and _family_enabled(pattern_ids, PATTERN_TWEEZER):
            if _is_tweezer_bottom_jit(o_prev, c_prev, h_prev, l_prev,
                                      o_curr, c_curr, h_curr, l_curr, tweezer_tolerance):
                count += 1
        # ID 9: Three outside up
        if has_two_prev and _family_enabled(pattern_ids, PATTERN_THREE_OUTSIDE):
            if _is_three_outside_up_jit(opens, closes, current_idx):
                count += 1
        # ID 10: Belt hold
        if _family_enabled(pattern_ids, PATTERN_BELT_HOLD):
            if _is_bullish_belt_hold_jit(o_curr, c_curr, h_curr, l_curr):
                count += 1
    else:
        # ID 3: Shooting star (SHORT only)
        if _family_enabled(pattern_ids, PATTERN_INVERTED_SHOOTING):
            if _is_shooting_star_jit(o_curr, c_curr, h_curr, l_curr):
                count += 1
        if has_prev and _family_enabled(pattern_ids, PATTERN_ENGULFING):
            if _is_bearish_engulfing_jit(o_prev, c_prev, o_curr, c_curr):
                count += 1
        if has_prev and _family_enabled(pattern_ids, PATTERN_HARAMI):
            if _is_bearish_harami_jit(o_prev, c_prev, h_prev, l_prev,
                                      o_curr, c_curr, h_curr, l_curr):
                count += 1
        if has_two_prev and _family_enabled(pattern_ids, PATTERN_STAR):
            if _is_evening_star_jit(opens, closes, highs, lows, current_idx):
                count += 1
        if has_prev and _family_enabled(pattern_ids, PATTERN_TWEEZER):
            if _is_tweezer_top_jit(o_prev, c_prev, h_prev, l_prev,
                                   o_curr, c_curr, h_curr, l_curr, tweezer_tolerance):
                count += 1
        if has_two_prev and _family_enabled(pattern_ids, PATTERN_THREE_OUTSIDE):
            if _is_three_outside_down_jit(opens, closes, current_idx):
                count += 1
        if _family_enabled(pattern_ids, PATTERN_BELT_HOLD):
            if _is_bearish_belt_hold_jit(o_curr, c_curr, h_curr, l_curr):
                count += 1

    return count


@njit(cache=True)
def check_reversal_pattern_jit(opens: np.ndarray, closes: np.ndarray,
                               highs: np.ndarray, lows: np.ndarray,
                               current_idx: int, is_bullish: bool) -> bool:
    """True if any pattern family matches (all families enabled, default thresholds)."""
    all_ids = np.zeros(1, dtype=np.int64)
    return count_reversal_patterns_jit(opens, closes, highs, lows, current_idx, is_bullish,
                                       all_ids, DEFAULT_DOJI_RATIO, DEFAULT_TWEEZER_TOLERANCE) > 0


@njit(cache=True)
def check_pattern_by_ids_jit(opens: np.ndarray, closes: np.ndarray,
                             highs: np.ndarray, lows: np.ndarray,
                             current_idx: int, is_bullish: bool,
                             pattern_ids: np.ndarray) -> bool:
    """True if any of the selected pattern families matches (default thresholds)."""
    return count_reversal_patterns_jit(opens, closes, highs, lows, current_idx, is_bullish,
                                       pattern_ids, DEFAULT_DOJI_RATIO, DEFAULT_TWEEZER_TOLERANCE) > 0


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