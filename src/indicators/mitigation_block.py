"""
Mitigation Block Indicator
=============================
Detects Mitigation Blocks - Order Blocks that have been partially mitigated
(price touched the zone but didn't fully break through).

A Mitigation Block forms when:
1. An Order Block is detected (Bullish or Bearish)
2. Price enters the OB zone (touches it)
3. Price does NOT fully break through (doesn't close beyond the OB)
4. The partially mitigated zone now has reduced strength

Difference from Breaker Block:
- Breaker Block = OB fully broken (price closes beyond), bias flips
- Mitigation Block = OB partially touched but held, same bias retained

Uses Numba JIT for performance-critical functions.

Usage:
    from src.indicators.mitigation_block import detect_mitigation_blocks, MitigationBlock, to_json
    
    mbs = detect_mitigation_blocks(df, swing_length=10, ob_body_ratio=0.35)
    json_data = to_json(mbs, df, max_bars=20)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Tuple
from dataclasses import dataclass
from numba import njit


# ==================== DATA CLASS ====================

@dataclass
class MitigationBlock:
    """Represents a Mitigation Block (partially mitigated Order Block)."""
    high: float
    low: float
    ob_time: datetime           # Time the original OB formed
    ob_index: int               # Index of the original OB candle
    mitigation_time: datetime   # Time of first partial mitigation
    mitigation_index: int       # Index where OB was first touched
    bias: str                   # 'BULLISH' or 'BEARISH' (same as original OB)
    structure_type: str         # 'BOS' or 'CHoCH' (from original OB)
    mitigation_percent: float   # How deep price penetrated the OB (0-100%)
    is_fully_mitigated: bool = False  # True if eventually fully broken


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
def _detect_mitigation_blocks_jit(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, opens: np.ndarray,
    atr: np.ndarray, swing_length: int, body_ratio: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Detect Mitigation Blocks in one pass.
    
    1. Detect structures (BOS/CHoCH) and their Order Blocks
    2. For each OB, check if price touches the zone without fully breaking through
    3. Track mitigation percentage (how deep price penetrated)
    
    Returns:
        mb_highs, mb_lows, mb_ob_indices, mb_mit_indices,
        mb_biases (1=BULLISH, 0=BEARISH), mb_struct_types (1=BOS, 0=CHoCH),
        mb_mit_pcts, mb_fully_mitigated, count
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
    # Intermediate OB storage
    ob_highs = np.empty(max_obs, dtype=np.float64)
    ob_lows = np.empty(max_obs, dtype=np.float64)
    ob_indices = np.empty(max_obs, dtype=np.int64)
    ob_biases = np.empty(max_obs, dtype=np.int8)
    ob_struct_types = np.empty(max_obs, dtype=np.int8)
    ob_count = 0
    
    # Track OB state: 0=untouched, 1=partially mitigated, 2=fully broken
    ob_state = np.zeros(max_obs, dtype=np.int8)
    ob_first_touch_idx = np.full(max_obs, -1, dtype=np.int64)
    ob_max_penetration = np.zeros(max_obs, dtype=np.float64)
    
    # Output arrays
    mb_highs = np.empty(max_obs, dtype=np.float64)
    mb_lows = np.empty(max_obs, dtype=np.float64)
    mb_ob_indices = np.empty(max_obs, dtype=np.int64)
    mb_mit_indices = np.empty(max_obs, dtype=np.int64)
    mb_biases = np.empty(max_obs, dtype=np.int8)
    mb_struct_types = np.empty(max_obs, dtype=np.int8)
    mb_mit_pcts = np.empty(max_obs, dtype=np.float64)
    mb_fully_mitigated = np.empty(max_obs, dtype=np.int8)
    mb_count = 0
    
    if sh_count < 1 or sl_count < 1:
        return (mb_highs[:0], mb_lows[:0], mb_ob_indices[:0], mb_mit_indices[:0],
                mb_biases[:0], mb_struct_types[:0], mb_mit_pcts[:0], mb_fully_mitigated[:0],
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
    
    for i in range(start, n):
        close = closes[i]
        
        while curr_sh < sh_count - 1 and sh_idxs[curr_sh + 1] < i:
            curr_sh += 1
        while curr_sl < sl_count - 1 and sl_idxs[curr_sl + 1] < i:
            curr_sl += 1
        
        # --- Step 1: Check existing OBs for partial mitigation ---
        for k in range(ob_count):
            if ob_state[k] == 2:  # Already fully broken
                continue
            
            ob_bias = ob_biases[k]
            ob_h = ob_highs[k]
            ob_l = ob_lows[k]
            ob_idx = ob_indices[k]
            ob_range = ob_h - ob_l
            
            if ob_idx >= i or ob_range == 0:
                continue
            
            if ob_bias == 1:  # Bullish OB
                # Touched: price low enters the OB zone
                if lows[i] <= ob_h and lows[i] >= ob_l:
                    # Calculate penetration percentage
                    penetration = (ob_h - lows[i]) / ob_range * 100.0
                    if penetration > ob_max_penetration[k]:
                        ob_max_penetration[k] = penetration
                    
                    if ob_state[k] == 0:
                        ob_state[k] = 1  # Mark as partially mitigated
                        ob_first_touch_idx[k] = i
                
                # Fully broken: close below OB low
                if close < ob_l:
                    if ob_state[k] == 1:
                        # Was partially mitigated, now fully broken
                        # Record it as a mitigation block that got fully mitigated
                        mb_highs[mb_count] = ob_h
                        mb_lows[mb_count] = ob_l
                        mb_ob_indices[mb_count] = ob_idx
                        mb_mit_indices[mb_count] = ob_first_touch_idx[k]
                        mb_biases[mb_count] = ob_bias
                        mb_struct_types[mb_count] = ob_struct_types[k]
                        mb_mit_pcts[mb_count] = ob_max_penetration[k]
                        mb_fully_mitigated[mb_count] = 1
                        mb_count += 1
                    ob_state[k] = 2
            
            else:  # Bearish OB
                # Touched: price high enters the OB zone
                if highs[i] >= ob_l and highs[i] <= ob_h:
                    penetration = (highs[i] - ob_l) / ob_range * 100.0
                    if penetration > ob_max_penetration[k]:
                        ob_max_penetration[k] = penetration
                    
                    if ob_state[k] == 0:
                        ob_state[k] = 1
                        ob_first_touch_idx[k] = i
                
                # Fully broken: close above OB high
                if close > ob_h:
                    if ob_state[k] == 1:
                        mb_highs[mb_count] = ob_h
                        mb_lows[mb_count] = ob_l
                        mb_ob_indices[mb_count] = ob_idx
                        mb_mit_indices[mb_count] = ob_first_touch_idx[k]
                        mb_biases[mb_count] = ob_bias
                        mb_struct_types[mb_count] = ob_struct_types[k]
                        mb_mit_pcts[mb_count] = ob_max_penetration[k]
                        mb_fully_mitigated[mb_count] = 1
                        mb_count += 1
                    ob_state[k] = 2
        
        # --- Step 2: Detect new structure breaks and create OBs ---
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
                ob_biases[ob_count] = 1
                ob_struct_types[ob_count] = struct_type
                ob_count += 1
            
            current_trend = 1
            last_broken_high = sh_idxs[curr_sh]
            if curr_sh < sh_count - 1:
                curr_sh += 1
        
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
                ob_biases[ob_count] = 0
                ob_struct_types[ob_count] = struct_type
                ob_count += 1
            
            current_trend = 0
            last_broken_low = sl_idxs[curr_sl]
            if curr_sl < sl_count - 1:
                curr_sl += 1
    
    # --- Step 3: Collect OBs that were partially mitigated but NOT fully broken ---
    for k in range(ob_count):
        if ob_state[k] == 1:  # Partially mitigated, still holding
            mb_highs[mb_count] = ob_highs[k]
            mb_lows[mb_count] = ob_lows[k]
            mb_ob_indices[mb_count] = ob_indices[k]
            mb_mit_indices[mb_count] = ob_first_touch_idx[k]
            mb_biases[mb_count] = ob_biases[k]
            mb_struct_types[mb_count] = ob_struct_types[k]
            mb_mit_pcts[mb_count] = ob_max_penetration[k]
            mb_fully_mitigated[mb_count] = 0
            mb_count += 1
    
    return (mb_highs[:mb_count], mb_lows[:mb_count], mb_ob_indices[:mb_count],
            mb_mit_indices[:mb_count], mb_biases[:mb_count], mb_struct_types[:mb_count],
            mb_mit_pcts[:mb_count], mb_fully_mitigated[:mb_count],
            np.array([mb_count], dtype=np.int64))


# ==================== MAIN DETECTION FUNCTION ====================

def detect_mitigation_blocks(df: pd.DataFrame, swing_length: int = 10,
                              ob_body_ratio: float = 0.35) -> List[MitigationBlock]:
    """
    Detect Mitigation Blocks (partially mitigated Order Blocks).
    
    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        swing_length: Lookback period for swing detection
        ob_body_ratio: Minimum body/range ratio for trending candle
    
    Returns:
        List of MitigationBlock objects
    """
    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    opens = df['Open'].values.astype(np.float64)
    times = df['Time'].values
    
    atr = _calculate_atr_jit(highs, lows, closes, 14)
    
    (mb_h, mb_l, mb_ob_idx, mb_mit_idx, mb_bias, mb_st,
     mb_pct, mb_full, cnt) = _detect_mitigation_blocks_jit(
        highs, lows, closes, opens, atr, swing_length, ob_body_ratio
    )
    
    results = []
    for i in range(len(mb_h)):
        ob_t = pd.Timestamp(times[mb_ob_idx[i]]).to_pydatetime()
        mit_t = pd.Timestamp(times[mb_mit_idx[i]]).to_pydatetime()
        
        results.append(MitigationBlock(
            high=float(mb_h[i]),
            low=float(mb_l[i]),
            ob_time=ob_t,
            ob_index=int(mb_ob_idx[i]),
            mitigation_time=mit_t,
            mitigation_index=int(mb_mit_idx[i]),
            bias='BULLISH' if mb_bias[i] == 1 else 'BEARISH',
            structure_type='BOS' if mb_st[i] == 1 else 'CHoCH',
            mitigation_percent=float(mb_pct[i]),
            is_fully_mitigated=bool(mb_full[i] == 1),
        ))
    
    return results


# ==================== JSON EXPORT ====================

def to_json(mitigation_blocks: List[MitigationBlock], df: pd.DataFrame, max_bars: int = 20) -> List[dict]:
    """
    Convert Mitigation Blocks to JSON-serializable format for client display.
    
    Box starts at OB index and extends to mitigation point or max_bars.
    
    Format compatible with TradingView lightweight-charts.
    """
    times = df['Time'].values
    highs = df['High'].values
    lows = df['Low'].values
    n = len(times)
    
    result = []
    for mb in mitigation_blocks:
        if mb.ob_index >= n:
            continue
        
        # Box starts at OB creation
        start_idx = mb.ob_index
        end_idx = min(mb.mitigation_index + max_bars, n - 1)
        
        # Check if the zone gets fully broken after mitigation
        for i in range(mb.mitigation_index + 1, min(mb.mitigation_index + max_bars + 1, n)):
            if mb.bias == 'BULLISH' and lows[i] < mb.low:
                end_idx = i
                break
            elif mb.bias == 'BEARISH' and highs[i] > mb.high:
                end_idx = i
                break
        
        result.append({
            'startTime': int(pd.Timestamp(times[start_idx]).timestamp()),
            'endTime': int(pd.Timestamp(times[end_idx]).timestamp()),
            'mitigationTime': int(pd.Timestamp(times[mb.mitigation_index]).timestamp()),
            'high': mb.high,
            'low': mb.low,
            'bias': mb.bias,
            'structureType': mb.structure_type,
            'mitigationPercent': round(mb.mitigation_percent, 1),
            'isFullyMitigated': mb.is_fully_mitigated,
            'color': 'rgba(38, 166, 154, 0.15)' if mb.bias == 'BULLISH' else 'rgba(239, 83, 80, 0.15)',
            'borderColor': 'rgba(38, 166, 154, 0.5)' if mb.bias == 'BULLISH' else 'rgba(239, 83, 80, 0.5)',
        })
    
    return result
