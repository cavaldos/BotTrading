"""
Fibonacci Retracement Indicator
==================================
Detects and calculates Fibonacci retracement levels using auto-detected
swing highs and swing lows.

Supports three types of retracement:
1. Standard Fibonacci: 0.236, 0.382, 0.5, 0.618, 0.786
2. OTE (Optimal Trade Entry): 0.5, 0.618, 0.786 (ICT Sweet Spot)
3. Auto: Automatically detects nearest swing high/low pair

Each retracement set is drawn between a swing high and swing low pair,
with levels calculated based on the swing range.

Uses Numba JIT for performance.

Usage:
    from src.indicators.retracement import detect_retracements, Retracement, to_json
    
    retracements = detect_retracements(df, swing_length=10)
    json_data = to_json(retracements)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Tuple
from dataclasses import dataclass, field
from numba import njit


# ==================== CONSTANTS ====================

STANDARD_FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
OTE_LEVELS = [0.5, 0.618, 0.786]
FIB_LEVEL_NAMES = {
    0.0: '0%',
    0.236: '23.6%',
    0.382: '38.2%',
    0.5: '50%',
    0.618: '61.8%',
    0.786: '78.6%',
    1.0: '100%',
}


# ==================== DATA CLASS ====================

@dataclass
class FibLevel:
    """A single Fibonacci level."""
    ratio: float        # e.g. 0.618
    price: float        # Actual price at this level
    label: str          # e.g. '61.8%'


@dataclass
class Retracement:
    """Represents a set of Fibonacci retracement levels between two swing points."""
    swing_high: float
    swing_low: float
    swing_high_time: datetime
    swing_low_time: datetime
    swing_high_index: int
    swing_low_index: int
    direction: str              # 'UP' (low to high) or 'DOWN' (high to low)
    retracement_type: str       # 'STANDARD', 'OTE', or 'AUTO'
    levels: List[FibLevel] = field(default_factory=list)


# ==================== NUMBA JIT FUNCTIONS ====================

@njit(cache=True)
def _calculate_atr_jit(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    """Calculate Average True Range (ATR)."""
    n = len(highs)
    tr = np.empty(n, dtype=np.float64)
    atr = np.empty(n, dtype=np.float64)
    
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        tr[i] = max(tr1, max(tr2, tr3))
    
    cumsum = 0.0
    for i in range(n):
        cumsum += tr[i]
        if i < period:
            atr[i] = cumsum / (i + 1)
        else:
            cumsum -= tr[i - period]
            atr[i] = cumsum / period
    
    return atr


@njit(cache=True)
def _check_swing_high_jit(highs: np.ndarray, i: int, length: int) -> bool:
    """Check if index i is a swing high."""
    n = len(highs)
    if i < length or i >= n - length:
        return False
    for j in range(i - length, i + length + 1):
        if j != i and highs[j] >= highs[i]:
            return False
    return True


@njit(cache=True)
def _check_swing_low_jit(lows: np.ndarray, i: int, length: int) -> bool:
    """Check if index i is a swing low."""
    n = len(lows)
    if i < length or i >= n - length:
        return False
    for j in range(i - length, i + length + 1):
        if j != i and lows[j] <= lows[i]:
            return False
    return True


@njit(cache=True)
def _find_swing_pairs_jit(
    highs: np.ndarray, lows: np.ndarray, length: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Find all alternating swing high/low pairs for retracement drawing.
    
    Returns alternating pairs: each pair is (swing_high_idx, swing_low_idx)
    with the direction indicating which came first.
    
    Returns:
        sh_indices, sh_prices, sl_indices, sl_prices,
        pair_sh_idx, pair_sl_idx (matched pairs)
    """
    n = len(highs)
    
    # Collect all swing points in order
    swing_indices = np.empty(n, dtype=np.int64)
    swing_prices = np.empty(n, dtype=np.float64)
    swing_types = np.empty(n, dtype=np.int8)  # 1=high, 0=low
    swing_count = 0
    
    for i in range(length, n - length):
        is_sh = _check_swing_high_jit(highs, i, length)
        is_sl = _check_swing_low_jit(lows, i, length)
        
        if is_sh:
            swing_indices[swing_count] = i
            swing_prices[swing_count] = highs[i]
            swing_types[swing_count] = 1
            swing_count += 1
        if is_sl:
            swing_indices[swing_count] = i
            swing_prices[swing_count] = lows[i]
            swing_types[swing_count] = 0
            swing_count += 1
    
    # Sort by index (in case sh and sl at same index)
    for i in range(swing_count):
        for j in range(i + 1, swing_count):
            if swing_indices[j] < swing_indices[i]:
                swing_indices[i], swing_indices[j] = swing_indices[j], swing_indices[i]
                swing_prices[i], swing_prices[j] = swing_prices[j], swing_prices[i]
                swing_types[i], swing_types[j] = swing_types[j], swing_types[i]
    
    # Build alternating pairs: find consecutive high-low or low-high pairs
    max_pairs = swing_count
    pair_sh_indices = np.empty(max_pairs, dtype=np.int64)
    pair_sl_indices = np.empty(max_pairs, dtype=np.int64)
    pair_sh_prices = np.empty(max_pairs, dtype=np.float64)
    pair_sl_prices = np.empty(max_pairs, dtype=np.float64)
    pair_directions = np.empty(max_pairs, dtype=np.int8)  # 1=UP (low first), 0=DOWN (high first)
    pair_count = 0
    
    i = 0
    while i < swing_count - 1:
        if swing_types[i] == 1 and swing_types[i + 1] == 0:
            # High then Low -> DOWN move
            pair_sh_indices[pair_count] = swing_indices[i]
            pair_sl_indices[pair_count] = swing_indices[i + 1]
            pair_sh_prices[pair_count] = swing_prices[i]
            pair_sl_prices[pair_count] = swing_prices[i + 1]
            pair_directions[pair_count] = 0  # DOWN
            pair_count += 1
            i += 2
        elif swing_types[i] == 0 and swing_types[i + 1] == 1:
            # Low then High -> UP move
            pair_sh_indices[pair_count] = swing_indices[i + 1]
            pair_sl_indices[pair_count] = swing_indices[i]
            pair_sh_prices[pair_count] = swing_prices[i + 1]
            pair_sl_prices[pair_count] = swing_prices[i]
            pair_directions[pair_count] = 1  # UP
            pair_count += 1
            i += 2
        else:
            # Same type consecutive, skip first
            i += 1
    
    return (pair_sh_indices[:pair_count], pair_sh_prices[:pair_count],
            pair_sl_indices[:pair_count], pair_sl_prices[:pair_count],
            pair_directions[:pair_count], np.array([pair_count], dtype=np.int64))


# ==================== HELPER FUNCTIONS ====================

def _calculate_fib_levels(swing_high: float, swing_low: float,
                           direction: str, fib_ratios: List[float]) -> List[FibLevel]:
    """
    Calculate Fibonacci retracement levels between swing high and swing low.
    
    For UP direction (retracement from high going down):
        Level = swing_high - ratio * (swing_high - swing_low)
    For DOWN direction (retracement from low going up):
        Level = swing_low + ratio * (swing_high - swing_low)
    """
    levels = []
    swing_range = swing_high - swing_low
    
    if swing_range <= 0:
        return levels
    
    for ratio in fib_ratios:
        if direction == 'UP':
            # Retracement from the high (price came up, now retracing down)
            price = swing_high - ratio * swing_range
        else:
            # Retracement from the low (price came down, now retracing up)
            price = swing_low + ratio * swing_range
        
        label = FIB_LEVEL_NAMES.get(ratio, f'{ratio * 100:.1f}%')
        levels.append(FibLevel(ratio=ratio, price=price, label=label))
    
    return levels


# ==================== MAIN DETECTION FUNCTION ====================

def detect_retracements(df: pd.DataFrame, swing_length: int = 10,
                         max_pairs: int = 50) -> List[Retracement]:
    """
    Detect Fibonacci retracement levels from auto-detected swing pairs.
    
    For each swing pair, generates three types of retracement:
    - STANDARD: Full Fibonacci levels (0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
    - OTE: Optimal Trade Entry levels (0.5, 0.618, 0.786)
    - AUTO: Same as STANDARD but only the most recent pair
    
    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        swing_length: Lookback period for swing detection
        max_pairs: Maximum number of swing pairs to process
    
    Returns:
        List of Retracement objects (3 per swing pair: STANDARD, OTE, AUTO for last)
    """
    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    times = df['Time'].values
    
    (p_sh_idx, p_sh_prices, p_sl_idx, p_sl_prices,
     p_dirs, cnt) = _find_swing_pairs_jit(highs, lows, swing_length)
    
    num_pairs = int(cnt[0])
    if num_pairs == 0:
        return []
    
    # Limit to most recent pairs
    start = max(0, num_pairs - max_pairs)
    
    results = []
    
    for i in range(start, num_pairs):
        sh_idx = int(p_sh_idx[i])
        sl_idx = int(p_sl_idx[i])
        sh_price = float(p_sh_prices[i])
        sl_price = float(p_sl_prices[i])
        direction = 'UP' if p_dirs[i] == 1 else 'DOWN'
        
        sh_time = pd.Timestamp(times[sh_idx]).to_pydatetime()
        sl_time = pd.Timestamp(times[sl_idx]).to_pydatetime()
        
        # Standard Fibonacci
        std_levels = _calculate_fib_levels(sh_price, sl_price, direction, STANDARD_FIB_LEVELS)
        results.append(Retracement(
            swing_high=sh_price,
            swing_low=sl_price,
            swing_high_time=sh_time,
            swing_low_time=sl_time,
            swing_high_index=sh_idx,
            swing_low_index=sl_idx,
            direction=direction,
            retracement_type='STANDARD',
            levels=std_levels,
        ))
        
        # OTE (Optimal Trade Entry)
        ote_levels = _calculate_fib_levels(sh_price, sl_price, direction, OTE_LEVELS)
        results.append(Retracement(
            swing_high=sh_price,
            swing_low=sl_price,
            swing_high_time=sh_time,
            swing_low_time=sl_time,
            swing_high_index=sh_idx,
            swing_low_index=sl_idx,
            direction=direction,
            retracement_type='OTE',
            levels=ote_levels,
        ))
    
    # AUTO: Mark the last pair's STANDARD retracement as AUTO type
    if num_pairs > 0:
        last_idx = num_pairs - 1
        sh_idx = int(p_sh_idx[last_idx])
        sl_idx = int(p_sl_idx[last_idx])
        sh_price = float(p_sh_prices[last_idx])
        sl_price = float(p_sl_prices[last_idx])
        direction = 'UP' if p_dirs[last_idx] == 1 else 'DOWN'
        
        sh_time = pd.Timestamp(times[sh_idx]).to_pydatetime()
        sl_time = pd.Timestamp(times[sl_idx]).to_pydatetime()
        
        auto_levels = _calculate_fib_levels(sh_price, sl_price, direction, STANDARD_FIB_LEVELS)
        results.append(Retracement(
            swing_high=sh_price,
            swing_low=sl_price,
            swing_high_time=sh_time,
            swing_low_time=sl_time,
            swing_high_index=sh_idx,
            swing_low_index=sl_idx,
            direction=direction,
            retracement_type='AUTO',
            levels=auto_levels,
        ))
    
    return results


# ==================== JSON EXPORT ====================

def to_json(retracements: List[Retracement]) -> List[dict]:
    """
    Convert Retracement list to JSON-serializable format for client display.
    
    Format compatible with TradingView lightweight-charts.
    """
    result = []
    for ret in retracements:
        levels_json = []
        for lvl in ret.levels:
            levels_json.append({
                'ratio': lvl.ratio,
                'price': lvl.price,
                'label': lvl.label,
            })
        
        # Color scheme based on retracement type
        if ret.retracement_type == 'OTE':
            color = 'rgba(156, 39, 176, 0.4)'    # Purple for OTE
            line_color = 'rgba(156, 39, 176, 0.8)'
        elif ret.retracement_type == 'AUTO':
            color = 'rgba(255, 193, 7, 0.3)'     # Amber for AUTO
            line_color = 'rgba(255, 193, 7, 0.8)'
        else:
            color = 'rgba(33, 150, 243, 0.2)'    # Blue for STANDARD
            line_color = 'rgba(33, 150, 243, 0.6)'
        
        result.append({
            'swingHigh': ret.swing_high,
            'swingLow': ret.swing_low,
            'swingHighTime': int(ret.swing_high_time.timestamp()),
            'swingLowTime': int(ret.swing_low_time.timestamp()),
            'swingHighIndex': ret.swing_high_index,
            'swingLowIndex': ret.swing_low_index,
            'direction': ret.direction,
            'type': ret.retracement_type,
            'levels': levels_json,
            'color': color,
            'lineColor': line_color,
        })
    
    return result
