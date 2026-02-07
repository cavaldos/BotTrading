"""
Breaker Block Indicator
=========================
Detects Breaker Blocks - Order Blocks that have been fully broken (mitigated),
causing the zone to flip from support to resistance (or vice versa).

A Breaker Block forms when:
1. An Order Block is detected (Bullish or Bearish)
2. Price completely breaks through the OB zone (full mitigation)
3. The OB flips bias: Bullish OB becomes Bearish Breaker, Bearish OB becomes Bullish Breaker

Breaker Blocks act as the opposite of their original OB:
- Bullish OB broken -> Bearish Breaker (resistance zone)
- Bearish OB broken -> Bullish Breaker (support zone)

Uses Numba JIT for performance-critical functions.

Usage:
    from src.indicators.breaker_block import detect_breaker_blocks, BreakerBlock, to_json
    
    breakers = detect_breaker_blocks(df, swing_length=10, ob_body_ratio=0.35)
    json_data = to_json(breakers, df, max_bars=30)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Tuple
from dataclasses import dataclass
from numba import njit


# ==================== DATA CLASS ====================

@dataclass
class BreakerBlock:
    """Represents a Breaker Block zone (broken Order Block with flipped bias)."""
    high: float
    low: float
    ob_time: datetime         # Time the original OB formed
    ob_index: int             # Index of the original OB candle
    break_time: datetime      # Time price broke through the OB
    break_index: int          # Index where OB was fully broken
    bias: str                 # 'BULLISH' or 'BEARISH' (FLIPPED from original OB)
    original_ob_bias: str     # Original OB bias before flip
    structure_type: str       # 'BOS' or 'CHoCH' (from original OB)
    is_retested: bool = False # True if price came back to test the breaker


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
def _detect_breaker_blocks_jit(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, opens: np.ndarray,
    atr: np.ndarray, swing_length: int, body_ratio: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Detect all Breaker Blocks in one pass.
    
    1. Detect structures (BOS/CHoCH) and their Order Blocks
    2. For each OB, check if price fully breaks through it
    3. If broken, create a Breaker Block with flipped bias
    
    Returns:
        bb_highs, bb_lows, bb_ob_indices, bb_break_indices,
        bb_biases (1=BULLISH breaker, 0=BEARISH breaker),
        bb_orig_biases (1=original was BULLISH, 0=was BEARISH),
        bb_struct_types (1=BOS, 0=CHoCH), count
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
    
    # Storage for OBs (intermediate)
    max_obs = n
    ob_highs = np.empty(max_obs, dtype=np.float64)
    ob_lows = np.empty(max_obs, dtype=np.float64)
    ob_indices = np.empty(max_obs, dtype=np.int64)
    ob_biases = np.empty(max_obs, dtype=np.int8)
    ob_struct_types = np.empty(max_obs, dtype=np.int8)
    ob_struct_break_indices = np.empty(max_obs, dtype=np.int64)
    ob_count = 0
    
    # Storage for Breaker Blocks (output)
    bb_highs = np.empty(max_obs, dtype=np.float64)
    bb_lows = np.empty(max_obs, dtype=np.float64)
    bb_ob_indices = np.empty(max_obs, dtype=np.int64)
    bb_break_indices = np.empty(max_obs, dtype=np.int64)
    bb_biases = np.empty(max_obs, dtype=np.int8)
    bb_orig_biases = np.empty(max_obs, dtype=np.int8)
    bb_struct_types = np.empty(max_obs, dtype=np.int8)
    bb_count = 0
    
    if sh_count < 1 or sl_count < 1:
        return (bb_highs[:0], bb_lows[:0], bb_ob_indices[:0], bb_break_indices[:0],
                bb_biases[:0], bb_orig_biases[:0], bb_struct_types[:0],
                np.array([0], dtype=np.int64))
    
    if sh_idxs[0] > sl_idxs[0]:
        current_trend = 1
    else:
        current_trend = 0
    
    curr_sh = 0
    curr_sl = 0
    last_broken_high = -1
    last_broken_low = -1
    start = max(sh_idxs[0], sl_idxs[0]) + 1
    
    # Track which OBs have already become breakers
    ob_is_broken = np.zeros(max_obs, dtype=np.int8)
    
    for i in range(start, n):
        close = closes[i]
        
        while curr_sh < sh_count - 1 and sh_idxs[curr_sh + 1] < i:
            curr_sh += 1
        while curr_sl < sl_count - 1 and sl_idxs[curr_sl + 1] < i:
            curr_sl += 1
        
        # --- Step 1: Check existing OBs for full mitigation (becomes Breaker) ---
        for k in range(ob_count):
            if ob_is_broken[k] == 1:
                continue
            
            ob_bias = ob_biases[k]
            ob_h = ob_highs[k]
            ob_l = ob_lows[k]
            ob_idx = ob_indices[k]
            
            # OB must have been created before current bar
            if ob_idx >= i:
                continue
            
            # Check for full break through the OB
            if ob_bias == 1:  # Bullish OB broken -> Bearish Breaker
                # Bullish OB is fully broken when close goes below OB low
                if close < ob_l:
                    ob_is_broken[k] = 1
                    bb_highs[bb_count] = ob_h
                    bb_lows[bb_count] = ob_l
                    bb_ob_indices[bb_count] = ob_idx
                    bb_break_indices[bb_count] = i
                    bb_biases[bb_count] = 0       # Flipped to BEARISH
                    bb_orig_biases[bb_count] = 1  # Originally BULLISH
                    bb_struct_types[bb_count] = ob_struct_types[k]
                    bb_count += 1
            else:  # Bearish OB broken -> Bullish Breaker
                # Bearish OB is fully broken when close goes above OB high
                if close > ob_h:
                    ob_is_broken[k] = 1
                    bb_highs[bb_count] = ob_h
                    bb_lows[bb_count] = ob_l
                    bb_ob_indices[bb_count] = ob_idx
                    bb_break_indices[bb_count] = i
                    bb_biases[bb_count] = 1       # Flipped to BULLISH
                    bb_orig_biases[bb_count] = 0  # Originally BEARISH
                    bb_struct_types[bb_count] = ob_struct_types[k]
                    bb_count += 1
        
        # --- Step 2: Detect new structure breaks and create OBs ---
        # Break of swing high
        if sh_idxs[curr_sh] > last_broken_high and close > sh_prices[curr_sh]:
            struct_type = 1 if current_trend == 1 else 0
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
                ob_struct_break_indices[ob_count] = i
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
                ob_struct_break_indices[ob_count] = i
                ob_count += 1
            
            current_trend = 0
            last_broken_low = sl_idxs[curr_sl]
            if curr_sl < sl_count - 1:
                curr_sl += 1
    
    return (bb_highs[:bb_count], bb_lows[:bb_count], bb_ob_indices[:bb_count],
            bb_break_indices[:bb_count], bb_biases[:bb_count], bb_orig_biases[:bb_count],
            bb_struct_types[:bb_count], np.array([bb_count], dtype=np.int64))


@njit(cache=True)
def _check_retest_jit(
    bb_high: float, bb_low: float, bb_bias: int, break_idx: int,
    highs: np.ndarray, lows: np.ndarray, n: int,
) -> bool:
    """
    Check if a Breaker Block has been retested.
    
    Bullish Breaker retested: price drops to touch the zone (low <= bb_high)
    Bearish Breaker retested: price rises to touch the zone (high >= bb_low)
    """
    for i in range(break_idx + 1, n):
        if bb_bias == 1:  # Bullish Breaker (was bearish OB, now support)
            if lows[i] <= bb_high and lows[i] >= bb_low:
                return True
        else:  # Bearish Breaker (was bullish OB, now resistance)
            if highs[i] >= bb_low and highs[i] <= bb_high:
                return True
    return False


# ==================== MAIN DETECTION FUNCTION ====================

def detect_breaker_blocks(df: pd.DataFrame, swing_length: int = 10,
                           ob_body_ratio: float = 0.35) -> List[BreakerBlock]:
    """
    Detect Breaker Blocks (Order Blocks that have been fully broken).
    
    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        swing_length: Lookback period for swing detection
        ob_body_ratio: Minimum body/range ratio for trending candle
    
    Returns:
        List of BreakerBlock objects
    """
    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    opens = df['Open'].values.astype(np.float64)
    times = df['Time'].values
    n = len(highs)
    
    atr = _calculate_atr_jit(highs, lows, closes, 14)
    
    (bb_h, bb_l, bb_ob_idx, bb_brk_idx, bb_bias, bb_orig,
     bb_st, cnt) = _detect_breaker_blocks_jit(
        highs, lows, closes, opens, atr, swing_length, ob_body_ratio
    )
    
    results = []
    for i in range(len(bb_h)):
        ob_t = pd.Timestamp(times[bb_ob_idx[i]]).to_pydatetime()
        brk_t = pd.Timestamp(times[bb_brk_idx[i]]).to_pydatetime()
        
        is_retested = _check_retest_jit(
            float(bb_h[i]), float(bb_l[i]), int(bb_bias[i]),
            int(bb_brk_idx[i]), highs, lows, n
        )
        
        results.append(BreakerBlock(
            high=float(bb_h[i]),
            low=float(bb_l[i]),
            ob_time=ob_t,
            ob_index=int(bb_ob_idx[i]),
            break_time=brk_t,
            break_index=int(bb_brk_idx[i]),
            bias='BULLISH' if bb_bias[i] == 1 else 'BEARISH',
            original_ob_bias='BULLISH' if bb_orig[i] == 1 else 'BEARISH',
            structure_type='BOS' if bb_st[i] == 1 else 'CHoCH',
            is_retested=is_retested,
        ))
    
    return results


# ==================== JSON EXPORT ====================

def to_json(breaker_blocks: List[BreakerBlock], df: pd.DataFrame, max_bars: int = 30) -> List[dict]:
    """
    Convert Breaker Blocks to JSON-serializable format for client display.
    
    Box starts at break_index (when OB was broken) and extends max_bars
    or until the breaker itself is broken.
    
    Format compatible with TradingView lightweight-charts.
    """
    times = df['Time'].values
    highs = df['High'].values
    lows = df['Low'].values
    n = len(times)
    
    result = []
    for bb in breaker_blocks:
        if bb.break_index >= n:
            continue
        
        # Breaker box starts at break point
        start_idx = bb.break_index
        end_idx = min(start_idx + max_bars, n - 1)
        
        # Check if the breaker itself gets broken
        for i in range(start_idx + 1, min(start_idx + max_bars + 1, n)):
            if bb.bias == 'BULLISH' and lows[i] < bb.low:
                end_idx = i
                break
            elif bb.bias == 'BEARISH' and highs[i] > bb.high:
                end_idx = i
                break
        
        result.append({
            'startTime': int(pd.Timestamp(times[start_idx]).timestamp()),
            'endTime': int(pd.Timestamp(times[end_idx]).timestamp()),
            'obTime': int(pd.Timestamp(times[bb.ob_index]).timestamp()),
            'high': bb.high,
            'low': bb.low,
            'bias': bb.bias,
            'originalBias': bb.original_ob_bias,
            'structureType': bb.structure_type,
            'isRetested': bb.is_retested,
            'color': 'rgba(76, 175, 80, 0.3)' if bb.bias == 'BULLISH' else 'rgba(244, 67, 54, 0.3)',
            'borderColor': 'rgba(76, 175, 80, 0.8)' if bb.bias == 'BULLISH' else 'rgba(244, 67, 54, 0.8)',
        })
    
    return result
