"""
BOS (Break of Structure) Indicator
====================================
Detects when price breaks a swing high/low in the SAME direction
as the current trend, confirming trend continuation.

- In BULLISH trend: Price breaks above swing high = BOS Bullish
- In BEARISH trend: Price breaks below swing low = BOS Bearish

Uses Numba JIT for performance-critical swing detection.

Usage:
    from src.indicators.BOS import detect_bos, BOS, to_json
    
    bos_list = detect_bos(df, swing_length=10)
    json_data = to_json(bos_list)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Tuple
from dataclasses import dataclass
from numba import njit


# ==================== DATA CLASS ====================

@dataclass
class BOS:
    """Represents a Break of Structure event."""
    bias: str               # 'BULLISH' or 'BEARISH'
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
def _detect_bos_jit(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    length: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Detect all BOS events using JIT.
    
    Returns:
        - biases: 1=BULLISH, 0=BEARISH
        - levels: price levels broken
        - break_indices: where the break happened
        - pivot_indices: the swing point that was broken
        - count: number of BOS events
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
        return biases[:0], levels[:0], break_indices[:0], pivot_indices[:0], np.array([0], dtype=np.int64)
    
    # Determine initial trend
    current_trend = 1  # 1=BULLISH, 0=BEARISH
    if sh_indices_arr[0] > sl_indices_arr[0]:
        current_trend = 1
    else:
        current_trend = 0
    
    # Track current unbroken swing
    curr_sh_idx = 0
    curr_sl_idx = 0
    last_broken_high = -1
    last_broken_low = -1
    
    start = max(sh_indices_arr[0], sl_indices_arr[0]) + 1
    
    for i in range(start, n):
        close = closes[i]
        
        # Update swing pointers
        while curr_sh_idx < sh_count - 1 and sh_indices_arr[curr_sh_idx + 1] < i:
            curr_sh_idx += 1
        while curr_sl_idx < sl_count - 1 and sl_indices_arr[curr_sl_idx + 1] < i:
            curr_sl_idx += 1
        
        # Check break of swing high (BOS only when trend is BULLISH)
        sh_price = sh_prices[curr_sh_idx]
        sh_pidx = sh_indices_arr[curr_sh_idx]
        
        if sh_pidx > last_broken_high and close > sh_price:
            if current_trend == 1:  # BOS (same direction)
                biases[count] = 1  # BULLISH
                levels[count] = sh_price
                break_indices[count] = i
                pivot_indices[count] = sh_pidx
                count += 1
            # CHoCH would be when current_trend == 0, handled in CHOCH.py
            current_trend = 1
            last_broken_high = sh_pidx
            if curr_sh_idx < sh_count - 1:
                curr_sh_idx += 1
        
        # Check break of swing low (BOS only when trend is BEARISH)
        sl_price = sl_prices[curr_sl_idx]
        sl_pidx = sl_indices_arr[curr_sl_idx]
        
        if sl_pidx > last_broken_low and close < sl_price:
            if current_trend == 0:  # BOS (same direction)
                biases[count] = 0  # BEARISH
                levels[count] = sl_price
                break_indices[count] = i
                pivot_indices[count] = sl_pidx
                count += 1
            current_trend = 0
            last_broken_low = sl_pidx
            if curr_sl_idx < sl_count - 1:
                curr_sl_idx += 1
    
    return biases[:count], levels[:count], break_indices[:count], pivot_indices[:count], np.array([count], dtype=np.int64)


# ==================== MAIN DETECTION FUNCTION ====================

def detect_bos(df: pd.DataFrame, swing_length: int = 10) -> List[BOS]:
    """
    Detect Break of Structure events.
    
    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        swing_length: Lookback period for swing detection
    
    Returns:
        List of BOS events sorted by break_index
    """
    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    times = df['Time'].values
    
    biases, levels, break_idxs, pivot_idxs, cnt = _detect_bos_jit(
        highs, lows, closes, swing_length
    )
    
    results = []
    for i in range(len(biases)):
        bt = pd.Timestamp(times[break_idxs[i]]).to_pydatetime()
        pt = pd.Timestamp(times[pivot_idxs[i]]).to_pydatetime()
        results.append(BOS(
            bias='BULLISH' if biases[i] == 1 else 'BEARISH',
            level=float(levels[i]),
            break_time=bt,
            break_index=int(break_idxs[i]),
            pivot_time=pt,
            pivot_index=int(pivot_idxs[i]),
        ))
    
    return results


# ==================== JSON EXPORT ====================

def to_json(bos_list: List[BOS]) -> List[dict]:
    """
    Convert BOS list to JSON-serializable format for client display.
    
    Format compatible with TradingView lightweight-charts.
    """
    result = []
    for bos in bos_list:
        result.append({
            'type': 'BOS',
            'bias': bos.bias,
            'level': bos.level,
            'breakTime': int(bos.break_time.timestamp()),
            'breakIndex': bos.break_index,
            'pivotTime': int(bos.pivot_time.timestamp()),
            'pivotIndex': bos.pivot_index,
            'color': 'rgba(38, 166, 154, 0.8)' if bos.bias == 'BULLISH' else 'rgba(239, 83, 80, 0.8)',
        })
    return result
