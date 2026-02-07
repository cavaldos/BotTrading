"""
Order Block (OB) Indicator
============================
Detects Order Blocks from structure breaks (BOS/CHoCH) using
LuxAlgo-style extreme candle logic + trending candle filter.

For Bullish OB: Find candle with LOWEST parsed_low in range from pivot to break
For Bearish OB: Find candle with HIGHEST parsed_high in range from pivot to break
High volatility bars (range >= 2*ATR) have their high/low swapped.

Trending Candle Filter: Body > body_ratio * range

Uses Numba JIT for performance-critical functions.

Usage:
    from src.indicators.order_block import detect_order_blocks, OrderBlock, to_json
    
    obs = detect_order_blocks(df, swing_length=10, ob_body_ratio=0.35)
    json_data = to_json(obs, df)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Tuple, Optional
from dataclasses import dataclass
from numba import njit


# ==================== DATA CLASS ====================

@dataclass
class OrderBlock:
    """Represents an Order Block zone."""
    high: float
    low: float
    time: datetime
    index: int
    bias: str               # 'BULLISH' or 'BEARISH'
    structure_type: str     # 'CHoCH' or 'BOS'
    break_index: int        # Index where the structure break occurred
    is_mitigated: bool = False


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
def _precompute_parsed_hl_jit(highs: np.ndarray, lows: np.ndarray, atr: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Pre-compute parsed high/low for high volatility bars (LuxAlgo logic).
    If candle range >= 2 * ATR, swap high/low.
    """
    n = len(highs)
    parsed_highs = np.empty(n, dtype=np.float64)
    parsed_lows = np.empty(n, dtype=np.float64)
    
    for i in range(n):
        candle_range = highs[i] - lows[i]
        if candle_range >= 2.0 * atr[i]:
            parsed_highs[i] = lows[i]
            parsed_lows[i] = highs[i]
        else:
            parsed_highs[i] = highs[i]
            parsed_lows[i] = lows[i]
    
    return parsed_highs, parsed_lows


@njit(cache=True)
def _check_trending_candle_jit(opens: np.ndarray, closes: np.ndarray, highs: np.ndarray,
                                lows: np.ndarray, idx: int, body_ratio_threshold: float) -> bool:
    """Check if a candle is a trending candle (body > ratio * range)."""
    body = abs(closes[idx] - opens[idx])
    candle_range = highs[idx] - lows[idx]
    if candle_range == 0:
        return False
    return (body / candle_range) > body_ratio_threshold


@njit(cache=True)
def _find_ob_index_jit(parsed_highs: np.ndarray, parsed_lows: np.ndarray,
                        start_idx: int, end_idx: int, is_bullish: bool,
                        opens: np.ndarray, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                        body_ratio_threshold: float) -> int:
    """
    Find Order Block candle index using LuxAlgo logic + trending candle filter.
    
    For Bullish OB: Find candle with LOWEST parsed_low (must be trending candle)
    For Bearish OB: Find candle with HIGHEST parsed_high (must be trending candle)
    """
    if start_idx >= end_idx or start_idx < 0:
        return -1
    
    if is_bullish:
        min_val = parsed_lows[start_idx]
        min_idx = start_idx
        for i in range(start_idx + 1, end_idx):
            if parsed_lows[i] < min_val and _check_trending_candle_jit(opens, closes, highs, lows, i, body_ratio_threshold):
                min_val = parsed_lows[i]
                min_idx = i
        if _check_trending_candle_jit(opens, closes, highs, lows, min_idx, body_ratio_threshold):
            return min_idx
        return -1
    else:
        max_val = parsed_highs[start_idx]
        max_idx = start_idx
        for i in range(start_idx + 1, end_idx):
            if parsed_highs[i] > max_val and _check_trending_candle_jit(opens, closes, highs, lows, i, body_ratio_threshold):
                max_val = parsed_highs[i]
                max_idx = i
        if _check_trending_candle_jit(opens, closes, highs, lows, max_idx, body_ratio_threshold):
            return max_idx
        return -1


@njit(cache=True)
def _detect_structures_and_obs_jit(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, opens: np.ndarray,
    atr: np.ndarray, swing_length: int, body_ratio: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Detect all structures (BOS/CHoCH) and their Order Blocks in one pass.
    
    Returns:
        ob_highs, ob_lows, ob_indices, ob_biases (1=BULL, 0=BEAR),
        ob_struct_types (1=BOS, 0=CHoCH), ob_break_indices, count
    """
    n = len(highs)
    parsed_highs, parsed_lows = _precompute_parsed_hl_jit(highs, lows, atr)
    
    # Pre-compute swings
    sh_prices = np.empty(n, dtype=np.float64)
    sh_idxs = np.empty(n, dtype=np.int64)
    sh_count = 0
    sl_prices = np.empty(n, dtype=np.float64)
    sl_idxs = np.empty(n, dtype=np.int64)
    sl_count = 0
    
    for i in range(swing_length, n - swing_length):
        if _check_swing_high_jit(highs, i, swing_length):
            sh_prices[sh_count] = highs[i]
            sh_idxs[sh_count] = i
            sh_count += 1
        if _check_swing_low_jit(lows, i, swing_length):
            sl_prices[sl_count] = lows[i]
            sl_idxs[sl_count] = i
            sl_count += 1
    
    max_obs = n
    ob_highs = np.empty(max_obs, dtype=np.float64)
    ob_lows = np.empty(max_obs, dtype=np.float64)
    ob_indices = np.empty(max_obs, dtype=np.int64)
    ob_biases = np.empty(max_obs, dtype=np.int8)
    ob_struct_types = np.empty(max_obs, dtype=np.int8)
    ob_break_indices = np.empty(max_obs, dtype=np.int64)
    ob_count = 0
    
    if sh_count < 1 or sl_count < 1:
        return (ob_highs[:0], ob_lows[:0], ob_indices[:0], ob_biases[:0],
                ob_struct_types[:0], ob_break_indices[:0], np.array([0], dtype=np.int64))
    
    if sh_idxs[0] > sl_idxs[0]:
        current_trend = 1
    else:
        current_trend = 0
    
    curr_sh = 0
    curr_sl = 0
    last_broken_high = -1
    last_broken_low = -1
    start = max(sh_idxs[0], sl_idxs[0]) + 1
    
    for i in range(start, n):
        close = closes[i]
        
        while curr_sh < sh_count - 1 and sh_idxs[curr_sh + 1] < i:
            curr_sh += 1
        while curr_sl < sl_count - 1 and sl_idxs[curr_sl + 1] < i:
            curr_sl += 1
        
        # Break of swing high
        if sh_idxs[curr_sh] > last_broken_high and close > sh_prices[curr_sh]:
            struct_type = 1 if current_trend == 1 else 0  # BOS=1, CHoCH=0
            pivot_idx = sh_idxs[curr_sh]
            
            ob_idx = _find_ob_index_jit(
                parsed_highs, parsed_lows, pivot_idx, i,
                True, opens, closes, highs, lows, body_ratio
            )
            
            if ob_idx >= 0:
                ob_highs[ob_count] = parsed_highs[ob_idx]
                ob_lows[ob_count] = parsed_lows[ob_idx]
                ob_indices[ob_count] = ob_idx
                ob_biases[ob_count] = 1  # BULLISH
                ob_struct_types[ob_count] = struct_type
                ob_break_indices[ob_count] = i
                ob_count += 1
            
            current_trend = 1
            last_broken_high = sh_idxs[curr_sh]
            if curr_sh < sh_count - 1:
                curr_sh += 1
        
        # Break of swing low
        if sl_idxs[curr_sl] > last_broken_low and close < sl_prices[curr_sl]:
            struct_type = 1 if current_trend == 0 else 0
            pivot_idx = sl_idxs[curr_sl]
            
            ob_idx = _find_ob_index_jit(
                parsed_highs, parsed_lows, pivot_idx, i,
                False, opens, closes, highs, lows, body_ratio
            )
            
            if ob_idx >= 0:
                ob_highs[ob_count] = parsed_highs[ob_idx]
                ob_lows[ob_count] = parsed_lows[ob_idx]
                ob_indices[ob_count] = ob_idx
                ob_biases[ob_count] = 0  # BEARISH
                ob_struct_types[ob_count] = struct_type
                ob_break_indices[ob_count] = i
                ob_count += 1
            
            current_trend = 0
            last_broken_low = sl_idxs[curr_sl]
            if curr_sl < sl_count - 1:
                curr_sl += 1
    
    return (ob_highs[:ob_count], ob_lows[:ob_count], ob_indices[:ob_count],
            ob_biases[:ob_count], ob_struct_types[:ob_count], ob_break_indices[:ob_count],
            np.array([ob_count], dtype=np.int64))


# ==================== MAIN DETECTION FUNCTION ====================

def detect_order_blocks(df: pd.DataFrame, swing_length: int = 10,
                         ob_body_ratio: float = 0.35) -> List[OrderBlock]:
    """
    Detect Order Blocks from structure breaks using LuxAlgo logic.
    
    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        swing_length: Lookback period for swing detection
        ob_body_ratio: Minimum body/range ratio for trending candle
    
    Returns:
        List of OrderBlock objects
    """
    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    opens = df['Open'].values.astype(np.float64)
    times = df['Time'].values
    
    atr = _calculate_atr_jit(highs, lows, closes, 14)
    
    (ob_h, ob_l, ob_idx, ob_bias, ob_st, ob_brk, cnt) = _detect_structures_and_obs_jit(
        highs, lows, closes, opens, atr, swing_length, ob_body_ratio
    )
    
    results = []
    for i in range(len(ob_h)):
        t = pd.Timestamp(times[ob_idx[i]]).to_pydatetime()
        results.append(OrderBlock(
            high=float(ob_h[i]),
            low=float(ob_l[i]),
            time=t,
            index=int(ob_idx[i]),
            bias='BULLISH' if ob_bias[i] == 1 else 'BEARISH',
            structure_type='BOS' if ob_st[i] == 1 else 'CHoCH',
            break_index=int(ob_brk[i]),
        ))
    
    return results


# ==================== JSON EXPORT ====================

def to_json(order_blocks: List[OrderBlock], df: pd.DataFrame, max_bars: int = 20) -> List[dict]:
    """
    Convert Order Blocks to JSON-serializable format for client display.
    
    Includes mitigation detection: OB box ends when price goes through it.
    
    Args:
        order_blocks: List of OrderBlock objects
        df: DataFrame with price data (for mitigation detection)
        max_bars: Maximum bars to extend OB box
    
    Format compatible with TradingView lightweight-charts.
    """
    times = df['Time'].values
    highs = df['High'].values
    lows = df['Low'].values
    n = len(times)
    
    result = []
    for ob in order_blocks:
        if ob.index >= n:
            continue
        
        # Determine end of OB box (mitigated or max_bars)
        end_idx = min(ob.index + max_bars, n - 1)
        for i in range(ob.index + 1, min(ob.index + max_bars + 1, n)):
            if ob.bias == 'BULLISH' and lows[i] < ob.low:
                end_idx = i
                break
            elif ob.bias == 'BEARISH' and highs[i] > ob.high:
                end_idx = i
                break
        
        result.append({
            'startTime': int(pd.Timestamp(times[ob.index]).timestamp()),
            'endTime': int(pd.Timestamp(times[end_idx]).timestamp()),
            'high': ob.high,
            'low': ob.low,
            'bias': ob.bias,
            'structureType': ob.structure_type,
            'color': 'rgba(38, 166, 154, 0.3)' if ob.bias == 'BULLISH' else 'rgba(239, 83, 80, 0.3)',
        })
    
    return result
