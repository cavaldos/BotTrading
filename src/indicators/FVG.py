"""
FVG (Fair Value Gap) Indicator
================================
Detects Fair Value Gaps - imbalanced price action between 3 consecutive candles.

Bullish FVG: Low[current] > High[2 bars ago] (gap up, buyers dominant)
Bearish FVG: High[current] < Low[2 bars ago] (gap down, sellers dominant)

Optionally filters small FVGs using ATR threshold.
Uses Numba JIT for performance.

Usage:
    from src.indicators.FVG import detect_fvg, FairValueGap, to_json
    
    fvgs = detect_fvg(df, use_atr_filter=True)
    json_data = to_json(fvgs)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Tuple
from dataclasses import dataclass
from numba import njit


# ==================== DATA CLASS ====================

@dataclass
class FairValueGap:
    """Represents a Fair Value Gap."""
    top: float
    bottom: float
    bias: str           # 'BULLISH' or 'BEARISH'
    time: datetime
    index: int
    is_filled: bool = False


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
def _detect_fvg_jit(
    highs: np.ndarray, lows: np.ndarray, atr: np.ndarray, use_filter: bool,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Detect all FVGs at once using JIT.
    
    Returns:
        fvg_indices, fvg_tops, fvg_bottoms, fvg_biases (1=BULLISH, 0=BEARISH)
    """
    n = len(highs)
    max_fvg = n
    
    fvg_indices = np.empty(max_fvg, dtype=np.int64)
    fvg_tops = np.empty(max_fvg, dtype=np.float64)
    fvg_bottoms = np.empty(max_fvg, dtype=np.float64)
    fvg_biases = np.empty(max_fvg, dtype=np.int8)
    count = 0
    
    for i in range(2, n):
        # Bullish FVG: current low > high 2 bars ago
        if lows[i] > highs[i - 2]:
            gap_size = lows[i] - highs[i - 2]
            if not use_filter or gap_size >= 0.1 * atr[i]:
                fvg_indices[count] = i
                fvg_tops[count] = lows[i]
                fvg_bottoms[count] = highs[i - 2]
                fvg_biases[count] = 1
                count += 1
        
        # Bearish FVG: current high < low 2 bars ago
        if highs[i] < lows[i - 2]:
            gap_size = lows[i - 2] - highs[i]
            if not use_filter or gap_size >= 0.1 * atr[i]:
                fvg_indices[count] = i
                fvg_tops[count] = lows[i - 2]
                fvg_bottoms[count] = highs[i]
                fvg_biases[count] = 0
                count += 1
    
    return fvg_indices[:count].copy(), fvg_tops[:count].copy(), fvg_bottoms[:count].copy(), fvg_biases[:count].copy()


@njit(cache=True)
def _check_fvg_filled_jit(
    fvg_top: float, fvg_bottom: float, fvg_bias: int, fvg_index: int,
    highs: np.ndarray, lows: np.ndarray, n: int,
) -> bool:
    """
    Check if an FVG has been filled by subsequent price action.
    
    Bullish FVG filled when price drops below FVG bottom.
    Bearish FVG filled when price rises above FVG top.
    """
    for i in range(fvg_index + 1, n):
        if fvg_bias == 1:  # BULLISH
            if lows[i] <= fvg_bottom:
                return True
        else:  # BEARISH
            if highs[i] >= fvg_top:
                return True
    return False


# ==================== MAIN DETECTION FUNCTION ====================

def detect_fvg(df: pd.DataFrame, use_atr_filter: bool = True) -> List[FairValueGap]:
    """
    Detect Fair Value Gaps.
    
    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        use_atr_filter: If True, filter out small FVGs using ATR
    
    Returns:
        List of FairValueGap objects
    """
    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    times = df['Time'].values
    n = len(highs)
    
    atr = _calculate_atr_jit(highs, lows, closes, 14)
    
    fvg_indices, fvg_tops, fvg_bottoms, fvg_biases = _detect_fvg_jit(
        highs, lows, atr, use_atr_filter
    )
    
    results = []
    for i in range(len(fvg_indices)):
        idx = int(fvg_indices[i])
        bias_val = int(fvg_biases[i])
        top_val = float(fvg_tops[i])
        bottom_val = float(fvg_bottoms[i])
        
        is_filled = _check_fvg_filled_jit(top_val, bottom_val, bias_val, idx, highs, lows, n)
        
        t = pd.Timestamp(times[idx]).to_pydatetime()
        results.append(FairValueGap(
            top=top_val,
            bottom=bottom_val,
            bias='BULLISH' if bias_val == 1 else 'BEARISH',
            time=t,
            index=idx,
            is_filled=is_filled,
        ))
    
    return results


# ==================== JSON EXPORT ====================

def to_json(fvg_list: List[FairValueGap]) -> List[dict]:
    """
    Convert FVG list to JSON-serializable format for client display.
    
    Format compatible with TradingView lightweight-charts.
    """
    result = []
    for fvg in fvg_list:
        result.append({
            'time': int(fvg.time.timestamp()),
            'top': fvg.top,
            'bottom': fvg.bottom,
            'bias': fvg.bias,
            'isFilled': fvg.is_filled,
            'color': 'rgba(38, 166, 154, 0.2)' if fvg.bias == 'BULLISH' else 'rgba(239, 83, 80, 0.2)',
        })
    return result
