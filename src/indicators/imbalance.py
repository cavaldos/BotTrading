"""
Imbalance Indicator
=====================
Detects price imbalance zones - areas where there is a strong
directional move indicating supply/demand imbalance.

An imbalance candle is one with:
- Large body relative to range (body > 70% of range)
- Significant volume compared to average
- Creates a gap or displacement in price

Unlike FVG which requires a gap between 3 candles, Imbalance detects
single candles with extreme directional momentum.

Uses Numba JIT for performance.

Usage:
    from src.indicators.imbalance import detect_imbalance, Imbalance, to_json
    
    imbalances = detect_imbalance(df, body_ratio=0.7)
    json_data = to_json(imbalances)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Tuple
from dataclasses import dataclass
from numba import njit


# ==================== DATA CLASS ====================

@dataclass
class Imbalance:
    """Represents a price imbalance zone."""
    high: float
    low: float
    time: datetime
    index: int
    bias: str           # 'BULLISH' or 'BEARISH'
    strength: float     # Body size relative to ATR (higher = stronger)
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
def _detect_imbalance_jit(
    opens: np.ndarray, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
    atr: np.ndarray, body_ratio: float, atr_multiplier: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Detect all imbalance candles using JIT.
    
    An imbalance candle:
    1. Body > body_ratio * range (strong directional candle)
    2. Range > atr_multiplier * ATR (significant size)
    
    Returns:
        indices, highs, lows, biases (1=BULLISH, 0=BEARISH), strengths
    """
    n = len(opens)
    max_events = n
    
    idx_arr = np.empty(max_events, dtype=np.int64)
    high_arr = np.empty(max_events, dtype=np.float64)
    low_arr = np.empty(max_events, dtype=np.float64)
    bias_arr = np.empty(max_events, dtype=np.int8)
    strength_arr = np.empty(max_events, dtype=np.float64)
    count = 0
    
    for i in range(1, n):
        candle_range = highs[i] - lows[i]
        if candle_range == 0:
            continue
        
        body = abs(closes[i] - opens[i])
        b_ratio = body / candle_range
        
        # Must be a strong directional candle
        if b_ratio < body_ratio:
            continue
        
        # Must be significantly larger than average
        if atr[i] == 0:
            continue
        if candle_range < atr_multiplier * atr[i]:
            continue
        
        # Determine bias
        if closes[i] > opens[i]:
            bias = 1  # BULLISH
        else:
            bias = 0  # BEARISH
        
        # Strength = body / ATR (how many ATRs this candle spans)
        strength = body / atr[i]
        
        idx_arr[count] = i
        high_arr[count] = highs[i]
        low_arr[count] = lows[i]
        bias_arr[count] = bias
        strength_arr[count] = strength
        count += 1
    
    return (idx_arr[:count].copy(), high_arr[:count].copy(), low_arr[:count].copy(),
            bias_arr[:count].copy(), strength_arr[:count].copy())


@njit(cache=True)
def _check_imbalance_filled_jit(
    high: float, low: float, bias: int, start_idx: int,
    highs: np.ndarray, lows: np.ndarray, n: int,
) -> bool:
    """
    Check if an imbalance zone has been filled by subsequent price action.
    
    Bullish imbalance filled when price retraces below 50% of the candle.
    Bearish imbalance filled when price retraces above 50% of the candle.
    """
    mid = (high + low) / 2.0
    for i in range(start_idx + 1, n):
        if bias == 1:  # BULLISH
            if lows[i] <= mid:
                return True
        else:  # BEARISH
            if highs[i] >= mid:
                return True
    return False


# ==================== MAIN DETECTION FUNCTION ====================

def detect_imbalance(df: pd.DataFrame, body_ratio: float = 0.7,
                      atr_multiplier: float = 1.5) -> List[Imbalance]:
    """
    Detect imbalance candles (strong directional moves).
    
    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        body_ratio: Minimum body/range ratio (0.7 = 70%)
        atr_multiplier: Minimum candle range in ATR units
    
    Returns:
        List of Imbalance objects
    """
    opens = df['Open'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    times = df['Time'].values
    n = len(opens)
    
    atr = _calculate_atr_jit(highs, lows, closes, 14)
    
    idx_arr, high_arr, low_arr, bias_arr, strength_arr = _detect_imbalance_jit(
        opens, closes, highs, lows, atr, body_ratio, atr_multiplier
    )
    
    results = []
    for i in range(len(idx_arr)):
        idx = int(idx_arr[i])
        bias_val = int(bias_arr[i])
        h = float(high_arr[i])
        l = float(low_arr[i])
        
        is_filled = _check_imbalance_filled_jit(h, l, bias_val, idx, highs, lows, n)
        
        t = pd.Timestamp(times[idx]).to_pydatetime()
        results.append(Imbalance(
            high=h,
            low=l,
            time=t,
            index=idx,
            bias='BULLISH' if bias_val == 1 else 'BEARISH',
            strength=float(strength_arr[i]),
            is_filled=is_filled,
        ))
    
    return results


# ==================== JSON EXPORT ====================

def to_json(imbalances: List[Imbalance]) -> List[dict]:
    """
    Convert Imbalance list to JSON-serializable format for client display.
    
    Format compatible with TradingView lightweight-charts.
    """
    result = []
    for imb in imbalances:
        result.append({
            # pd.Timestamp reads naive times as UTC like the candles; datetime.timestamp() would use local time
            'time': int(pd.Timestamp(imb.time).timestamp()),
            'high': imb.high,
            'low': imb.low,
            'bias': imb.bias,
            'strength': round(imb.strength, 2),
            'isFilled': imb.is_filled,
            'color': 'rgba(0, 188, 212, 0.25)' if imb.bias == 'BULLISH' else 'rgba(255, 87, 34, 0.25)',
        })
    return result
