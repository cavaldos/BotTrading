"""
EQH/EQL (Equal High / Equal Low) Indicator
=============================================
Detects Equal Highs and Equal Lows - liquidity zones where price
has tested the same level multiple times.

EQH: Two swing highs with prices within threshold * ATR of each other
EQL: Two swing lows with prices within threshold * ATR of each other

These zones represent areas where stop losses cluster, making them
attractive targets for smart money to hunt liquidity.

Uses Numba JIT for performance.

Usage:
    from src.indicators.eqh_eql import detect_eqh_eql, EqualHL, to_json
    
    eqhl_list = detect_eqh_eql(df, swing_length=3, threshold=0.3)
    json_data = to_json(eqhl_list)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Tuple
from dataclasses import dataclass
from numba import njit


# ==================== DATA CLASS ====================

@dataclass
class EqualHL:
    """Represents Equal High or Equal Low (liquidity zone)."""
    type: str           # 'EQH' or 'EQL'
    level: float        # Average price of the two swing points
    time: datetime      # Time of the second (confirming) swing
    index: int          # Index of the second swing
    first_pivot_index: int
    second_pivot_index: int


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
def _detect_eqh_eql_jit(
    highs: np.ndarray, lows: np.ndarray, atr: np.ndarray,
    length: int, threshold: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Detect all EQH/EQL events using JIT.
    
    Returns:
        types (1=EQH, 0=EQL), levels, indices (second pivot),
        first_pivot_indices, second_pivot_indices
    """
    n = len(highs)
    
    # Pre-compute swing points
    sh_prices = np.empty(n, dtype=np.float64)
    sh_idxs = np.empty(n, dtype=np.int64)
    sh_count = 0
    
    sl_prices = np.empty(n, dtype=np.float64)
    sl_idxs = np.empty(n, dtype=np.int64)
    sl_count = 0
    
    for i in range(length, n - length):
        if _check_swing_high_jit(highs, i, length):
            sh_prices[sh_count] = highs[i]
            sh_idxs[sh_count] = i
            sh_count += 1
        if _check_swing_low_jit(lows, i, length):
            sl_prices[sl_count] = lows[i]
            sl_idxs[sl_count] = i
            sl_count += 1
    
    max_events = sh_count * sh_count + sl_count * sl_count
    if max_events == 0:
        max_events = 1
    
    types = np.empty(max_events, dtype=np.int8)
    levels = np.empty(max_events, dtype=np.float64)
    indices = np.empty(max_events, dtype=np.int64)
    first_pivots = np.empty(max_events, dtype=np.int64)
    second_pivots = np.empty(max_events, dtype=np.int64)
    count = 0
    
    # Detect EQH: compare each pair of swing highs
    for i in range(sh_count):
        for j in range(i + 1, sh_count):
            atr_val = atr[sh_idxs[j]]
            if abs(sh_prices[i] - sh_prices[j]) < threshold * atr_val:
                types[count] = 1  # EQH
                levels[count] = (sh_prices[i] + sh_prices[j]) / 2.0
                indices[count] = sh_idxs[j]
                first_pivots[count] = sh_idxs[i]
                second_pivots[count] = sh_idxs[j]
                count += 1
    
    # Detect EQL: compare each pair of swing lows
    for i in range(sl_count):
        for j in range(i + 1, sl_count):
            atr_val = atr[sl_idxs[j]]
            if abs(sl_prices[i] - sl_prices[j]) < threshold * atr_val:
                types[count] = 0  # EQL
                levels[count] = (sl_prices[i] + sl_prices[j]) / 2.0
                indices[count] = sl_idxs[j]
                first_pivots[count] = sl_idxs[i]
                second_pivots[count] = sl_idxs[j]
                count += 1
    
    return types[:count], levels[:count], indices[:count], first_pivots[:count], second_pivots[:count]


# ==================== MAIN DETECTION FUNCTION ====================

def detect_eqh_eql(df: pd.DataFrame, swing_length: int = 3,
                    threshold: float = 0.3) -> List[EqualHL]:
    """
    Detect Equal Highs and Equal Lows (liquidity zones).
    
    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        swing_length: Lookback period for internal swing detection
        threshold: ATR multiplier for price similarity
    
    Returns:
        List of EqualHL objects sorted by index
    """
    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    times = df['Time'].values
    
    atr = _calculate_atr_jit(highs, lows, closes, 14)
    
    types, levels, idxs, fp, sp = _detect_eqh_eql_jit(
        highs, lows, atr, swing_length, threshold
    )
    
    results = []
    for i in range(len(types)):
        idx = int(idxs[i])
        t = pd.Timestamp(times[idx]).to_pydatetime()
        results.append(EqualHL(
            type='EQH' if types[i] == 1 else 'EQL',
            level=float(levels[i]),
            time=t,
            index=idx,
            first_pivot_index=int(fp[i]),
            second_pivot_index=int(sp[i]),
        ))
    
    # Sort by index
    results.sort(key=lambda x: x.index)
    return results


# ==================== JSON EXPORT ====================

def to_json(eqhl_list: List[EqualHL], df: pd.DataFrame) -> List[dict]:
    """
    Convert EQH/EQL list to JSON-serializable format for client display.
    
    Format compatible with TradingView lightweight-charts.
    Draws a horizontal line between the two pivot points.
    """
    times = df['Time'].values
    
    result = []
    for eqhl in eqhl_list:
        start_time = int(pd.Timestamp(times[eqhl.first_pivot_index]).timestamp())
        end_time = int(pd.Timestamp(times[eqhl.second_pivot_index]).timestamp())
        
        result.append({
            'type': eqhl.type,
            'level': eqhl.level,
            'startTime': start_time,
            'endTime': end_time,
            'firstPivotIndex': eqhl.first_pivot_index,
            'secondPivotIndex': eqhl.second_pivot_index,
            'color': 'rgba(255, 152, 0, 0.7)' if eqhl.type == 'EQH' else 'rgba(33, 150, 243, 0.7)',
            'lineStyle': 'dashed',
        })
    return result
