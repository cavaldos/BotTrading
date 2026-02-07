"""
CHoCH (Change of Character) Indicator
========================================
Detects when price breaks a swing high/low AGAINST the current trend,
signaling a potential trend reversal.

- In BULLISH trend: Price breaks below swing low = CHoCH Bearish (reversal)
- In BEARISH trend: Price breaks above swing high = CHoCH Bullish (reversal)

Uses Numba JIT for performance-critical swing detection.

Usage:
    from src.indicators.CHOCH import detect_choch, CHoCH, to_json
    
    choch_list = detect_choch(df, swing_length=10)
    json_data = to_json(choch_list)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Tuple
from dataclasses import dataclass
from numba import njit


# ==================== DATA CLASS ====================

@dataclass
class CHoCH:
    """Represents a Change of Character event (trend reversal)."""
    bias: str               # 'BULLISH' or 'BEARISH' (new direction after reversal)
    level: float            # Price level that was broken
    break_time: datetime
    break_index: int
    pivot_time: datetime
    pivot_index: int


# ==================== NUMBA JIT FUNCTIONS ====================

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
def _detect_choch_jit(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    length: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Detect all CHoCH events using JIT.
    
    CHoCH happens when price breaks structure AGAINST current trend:
    - Was BULLISH, breaks swing low -> CHoCH BEARISH
    - Was BEARISH, breaks swing high -> CHoCH BULLISH
    
    Returns:
        - biases: 1=BULLISH, 0=BEARISH
        - levels: price levels broken
        - break_indices: where the break happened
        - pivot_indices: the swing point that was broken
    """
    n = len(highs)
    max_events = n
    biases = np.empty(max_events, dtype=np.int8)
    levels = np.empty(max_events, dtype=np.float64)
    break_indices = np.empty(max_events, dtype=np.int64)
    pivot_indices = np.empty(max_events, dtype=np.int64)
    count = 0
    
    # Pre-compute swing points
    sh_prices = np.empty(n, dtype=np.float64)
    sh_indices_arr = np.empty(n, dtype=np.int64)
    sh_count = 0
    
    sl_prices = np.empty(n, dtype=np.float64)
    sl_indices_arr = np.empty(n, dtype=np.int64)
    sl_count = 0
    
    for i in range(length, n - length):
        if _check_swing_high_jit(highs, i, length):
            sh_prices[sh_count] = highs[i]
            sh_indices_arr[sh_count] = i
            sh_count += 1
        if _check_swing_low_jit(lows, i, length):
            sl_prices[sl_count] = lows[i]
            sl_indices_arr[sl_count] = i
            sl_count += 1
    
    if sh_count < 1 or sl_count < 1:
        return biases[:0], levels[:0], break_indices[:0], pivot_indices[:0]
    
    # Determine initial trend
    if sh_indices_arr[0] > sl_indices_arr[0]:
        current_trend = 1  # BULLISH
    else:
        current_trend = 0  # BEARISH
    
    curr_sh_idx = 0
    curr_sl_idx = 0
    last_broken_high = -1
    last_broken_low = -1
    
    start = max(sh_indices_arr[0], sl_indices_arr[0]) + 1
    
    for i in range(start, n):
        close = closes[i]
        
        while curr_sh_idx < sh_count - 1 and sh_indices_arr[curr_sh_idx + 1] < i:
            curr_sh_idx += 1
        while curr_sl_idx < sl_count - 1 and sl_indices_arr[curr_sl_idx + 1] < i:
            curr_sl_idx += 1
        
        # Check break of swing high
        sh_price = sh_prices[curr_sh_idx]
        sh_pidx = sh_indices_arr[curr_sh_idx]
        
        if sh_pidx > last_broken_high and close > sh_price:
            if current_trend == 0:  # Was BEARISH -> CHoCH to BULLISH
                biases[count] = 1
                levels[count] = sh_price
                break_indices[count] = i
                pivot_indices[count] = sh_pidx
                count += 1
            current_trend = 1
            last_broken_high = sh_pidx
            if curr_sh_idx < sh_count - 1:
                curr_sh_idx += 1
        
        # Check break of swing low
        sl_price = sl_prices[curr_sl_idx]
        sl_pidx = sl_indices_arr[curr_sl_idx]
        
        if sl_pidx > last_broken_low and close < sl_price:
            if current_trend == 1:  # Was BULLISH -> CHoCH to BEARISH
                biases[count] = 0
                levels[count] = sl_price
                break_indices[count] = i
                pivot_indices[count] = sl_pidx
                count += 1
            current_trend = 0
            last_broken_low = sl_pidx
            if curr_sl_idx < sl_count - 1:
                curr_sl_idx += 1
    
    return biases[:count], levels[:count], break_indices[:count], pivot_indices[:count]


# ==================== MAIN DETECTION FUNCTION ====================

def detect_choch(df: pd.DataFrame, swing_length: int = 10) -> List[CHoCH]:
    """
    Detect Change of Character events (trend reversals).
    
    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        swing_length: Lookback period for swing detection
    
    Returns:
        List of CHoCH events sorted by break_index
    """
    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    times = df['Time'].values
    
    biases, levels, break_idxs, pivot_idxs = _detect_choch_jit(
        highs, lows, closes, swing_length
    )
    
    results = []
    for i in range(len(biases)):
        bt = pd.Timestamp(times[break_idxs[i]]).to_pydatetime()
        pt = pd.Timestamp(times[pivot_idxs[i]]).to_pydatetime()
        results.append(CHoCH(
            bias='BULLISH' if biases[i] == 1 else 'BEARISH',
            level=float(levels[i]),
            break_time=bt,
            break_index=int(break_idxs[i]),
            pivot_time=pt,
            pivot_index=int(pivot_idxs[i]),
        ))
    
    return results


# ==================== JSON EXPORT ====================

def to_json(choch_list: List[CHoCH]) -> List[dict]:
    """
    Convert CHoCH list to JSON-serializable format for client display.
    
    Format compatible with TradingView lightweight-charts.
    """
    result = []
    for choch in choch_list:
        result.append({
            'type': 'CHoCH',
            'bias': choch.bias,
            'level': choch.level,
            'breakTime': int(choch.break_time.timestamp()),
            'breakIndex': choch.break_index,
            'pivotTime': int(choch.pivot_time.timestamp()),
            'pivotIndex': choch.pivot_index,
            'color': 'rgba(255, 235, 59, 0.9)' if choch.bias == 'BULLISH' else 'rgba(156, 39, 176, 0.9)',
        })
    return result
