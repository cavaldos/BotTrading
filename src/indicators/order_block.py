"""
Order Block (OB) Indicator
============================
Detects Order Blocks from structure breaks using LuxAlgo-style logic.

Ported from OB.pine (LuxAlgo modified).

Key logic:
  - Pivot detection via leg() function (right-side-only confirmation)
  - Pivots only update on leg direction change (zigzag alternation)
  - Structure break via crossover/crossunder with per-pivot crossed flag
  - OB candle: max(parsedHigh) for bearish, min(parsedLow) for bullish
  - High volatility bars (range >= 2*ATR200) swap high/low
  - Real-time mitigation: remove OBs when price crosses through them
  - Supports both Swing and Internal OB streams

Usage:
    from src.indicators.order_block import detect_order_blocks, OrderBlock, to_json

    obs = detect_order_blocks(df, swing_length=50, internal_length=5)
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
    ob_type: str            # 'SWING' or 'INTERNAL'
    break_index: int
    is_mitigated: bool = False
    mitigated_index: int = -1


# ==================== NUMBA JIT FUNCTIONS ====================

@njit(cache=True)
def _calculate_atr_jit(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    """Calculate ATR using Wilder's smoothing (matches ta.atr in Pine)."""
    n = len(highs)
    tr = np.empty(n, dtype=np.float64)
    atr = np.empty(n, dtype=np.float64)

    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        tr[i] = max(tr1, max(tr2, tr3))

    atr[0] = tr[0]
    for i in range(1, n):
        if i < period:
            s = 0.0
            for j in range(i + 1):
                s += tr[j]
            atr[i] = s / (i + 1)
        else:
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    return atr


@njit(cache=True)
def _precompute_parsed_hl_jit(highs: np.ndarray, lows: np.ndarray, atr: np.ndarray):
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
def _find_ob_candle_jit(parsed_highs: np.ndarray, parsed_lows: np.ndarray,
                         start_idx: int, end_idx: int, is_bullish: bool) -> int:
    """
    Find Order Block candle index (pure argmin/argmax, no trending filter).

    For Bullish OB: candle with LOWEST parsed_low in [start_idx, end_idx)
    For Bearish OB: candle with HIGHEST parsed_high in [start_idx, end_idx)
    """
    if start_idx >= end_idx or start_idx < 0:
        return -1

    if is_bullish:
        min_val = parsed_lows[start_idx]
        min_idx = start_idx
        for i in range(start_idx + 1, end_idx):
            if parsed_lows[i] < min_val:
                min_val = parsed_lows[i]
                min_idx = i
        return min_idx
    else:
        max_val = parsed_highs[start_idx]
        max_idx = start_idx
        for i in range(start_idx + 1, end_idx):
            if parsed_highs[i] > max_val:
                max_val = parsed_highs[i]
                max_idx = i
        return max_idx


@njit(cache=True)
def _detect_obs_single_stream_jit(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    parsed_highs: np.ndarray, parsed_lows: np.ndarray,
    size: int, mitigation_mode: int,
    max_ob_store: int,
):
    """
    Detect Order Blocks for a single stream (swing or internal).

    Implements Pine's leg() + getCurrentStructure() + detectStructure() +
    deleteOrderBlocks() logic.

    Args:
        size: leg size (swingsLengthInput for swing, 5 for internal)
        mitigation_mode: 0=HIGHLOW, 1=CLOSE
        max_ob_store: max OBs to keep (100 in Pine)

    Returns:
        ob_highs, ob_lows, ob_indices, ob_biases (1=BULL, -1=BEAR),
        ob_break_indices, ob_mitigated (1=yes, 0=no),
        ob_mitigated_indices, ob_count
    """
    n = len(highs)
    BULLISH_LEG = 1
    BEARISH_LEG = 0

    max_obs = n
    ob_highs = np.empty(max_obs, dtype=np.float64)
    ob_lows = np.empty(max_obs, dtype=np.float64)
    ob_indices = np.empty(max_obs, dtype=np.int64)
    ob_biases = np.empty(max_obs, dtype=np.int8)
    ob_break_indices = np.empty(max_obs, dtype=np.int64)
    ob_mitigated = np.empty(max_obs, dtype=np.int8)
    ob_mitigated_indices = np.empty(max_obs, dtype=np.int64)
    ob_count = 0

    active_ob_highs = np.empty(max_ob_store, dtype=np.float64)
    active_ob_lows = np.empty(max_ob_store, dtype=np.float64)
    active_ob_biases = np.empty(max_ob_store, dtype=np.int8)
    active_ob_result_idx = np.empty(max_ob_store, dtype=np.int64)
    active_count = 0

    leg = -1
    prev_leg = -1

    sh_level = -1e18
    sh_bar_idx = -1
    sh_crossed = True

    sl_level = 1e18
    sl_bar_idx = -1
    sl_crossed = True

    prev_close = closes[0] if n > 0 else 0.0

    for i in range(1, n):
        k = i - size
        if k >= 0 and i < n:
            highest_after = highs[k + 1]
            for j in range(k + 2, i + 1):
                if highs[j] > highest_after:
                    highest_after = highs[j]

            lowest_after = lows[k + 1]
            for j in range(k + 2, i + 1):
                if lows[j] < lowest_after:
                    lowest_after = lows[j]

            new_leg_high = highs[k] > highest_after
            new_leg_low = lows[k] < lowest_after

            prev_leg = leg
            if new_leg_high:
                leg = BEARISH_LEG
            elif new_leg_low:
                leg = BULLISH_LEG

            if prev_leg >= 0 and leg != prev_leg:
                if leg == BULLISH_LEG:
                    sl_level = lows[k]
                    sl_bar_idx = k
                    sl_crossed = False
                else:
                    sh_level = highs[k]
                    sh_bar_idx = k
                    sh_crossed = False

        close_val = closes[i]

        if not sh_crossed and sh_bar_idx >= 0:
            if prev_close <= sh_level and close_val > sh_level:
                sh_crossed = True

                ob_idx = _find_ob_candle_jit(
                    parsed_highs, parsed_lows, sh_bar_idx, i, True
                )
                if ob_idx >= 0:
                    ob_highs[ob_count] = parsed_highs[ob_idx]
                    ob_lows[ob_count] = parsed_lows[ob_idx]
                    ob_indices[ob_count] = ob_idx
                    ob_biases[ob_count] = 1
                    ob_break_indices[ob_count] = i
                    ob_mitigated[ob_count] = 0
                    ob_mitigated_indices[ob_count] = -1

                    if active_count >= max_ob_store:
                        active_count = max_ob_store - 1
                    for j in range(active_count, 0, -1):
                        active_ob_highs[j] = active_ob_highs[j - 1]
                        active_ob_lows[j] = active_ob_lows[j - 1]
                        active_ob_biases[j] = active_ob_biases[j - 1]
                        active_ob_result_idx[j] = active_ob_result_idx[j - 1]
                    active_ob_highs[0] = parsed_highs[ob_idx]
                    active_ob_lows[0] = parsed_lows[ob_idx]
                    active_ob_biases[0] = 1
                    active_ob_result_idx[0] = ob_count
                    active_count = min(active_count + 1, max_ob_store)

                    ob_count += 1

        if not sl_crossed and sl_bar_idx >= 0:
            if prev_close >= sl_level and close_val < sl_level:
                sl_crossed = True

                ob_idx = _find_ob_candle_jit(
                    parsed_highs, parsed_lows, sl_bar_idx, i, False
                )
                if ob_idx >= 0:
                    ob_highs[ob_count] = parsed_highs[ob_idx]
                    ob_lows[ob_count] = parsed_lows[ob_idx]
                    ob_indices[ob_count] = ob_idx
                    ob_biases[ob_count] = -1
                    ob_break_indices[ob_count] = i
                    ob_mitigated[ob_count] = 0
                    ob_mitigated_indices[ob_count] = -1

                    if active_count >= max_ob_store:
                        active_count = max_ob_store - 1
                    for j in range(active_count, 0, -1):
                        active_ob_highs[j] = active_ob_highs[j - 1]
                        active_ob_lows[j] = active_ob_lows[j - 1]
                        active_ob_biases[j] = active_ob_biases[j - 1]
                        active_ob_result_idx[j] = active_ob_result_idx[j - 1]
                    active_ob_highs[0] = parsed_highs[ob_idx]
                    active_ob_lows[0] = parsed_lows[ob_idx]
                    active_ob_biases[0] = -1
                    active_ob_result_idx[0] = ob_count
                    active_count = min(active_count + 1, max_ob_store)

                    ob_count += 1

        j = 0
        while j < active_count:
            mitigated = False
            if active_ob_biases[j] == -1:
                src = close_val if mitigation_mode == 1 else highs[i]
                if src > active_ob_highs[j]:
                    mitigated = True
            else:
                src = close_val if mitigation_mode == 1 else lows[i]
                if src < active_ob_lows[j]:
                    mitigated = True

            if mitigated:
                result_idx = active_ob_result_idx[j]
                ob_mitigated[result_idx] = 1
                ob_mitigated_indices[result_idx] = i
                for m in range(j, active_count - 1):
                    active_ob_highs[m] = active_ob_highs[m + 1]
                    active_ob_lows[m] = active_ob_lows[m + 1]
                    active_ob_biases[m] = active_ob_biases[m + 1]
                    active_ob_result_idx[m] = active_ob_result_idx[m + 1]
                active_count -= 1
            else:
                j += 1

        prev_close = close_val

    return (ob_highs[:ob_count], ob_lows[:ob_count], ob_indices[:ob_count],
            ob_biases[:ob_count], ob_break_indices[:ob_count],
            ob_mitigated[:ob_count], ob_mitigated_indices[:ob_count],
            np.array([ob_count], dtype=np.int64))


# ==================== MAIN DETECTION FUNCTION ====================

def detect_order_blocks(
    df: pd.DataFrame,
    swing_length: int = 50,
    internal_length: int = 5,
    atr_period: int = 200,
    mitigation_mode: str = 'HIGHLOW',
    detect_swing: bool = True,
    detect_internal: bool = True,
    max_ob_store: int = 100,
) -> List[OrderBlock]:
    """
    Detect Order Blocks from structure breaks using LuxAlgo logic.

    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        swing_length: Leg size for swing pivots (default 50, matches Pine)
        internal_length: Leg size for internal pivots (default 5)
        atr_period: ATR period for volatility filter (default 200, matches Pine)
        mitigation_mode: 'HIGHLOW' or 'CLOSE'
        detect_swing: Whether to detect swing OBs
        detect_internal: Whether to detect internal OBs
        max_ob_store: Max active OBs to track (default 100, matches Pine)

    Returns:
        List of OrderBlock objects
    """
    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    times = df['Time'].values

    atr = _calculate_atr_jit(highs, lows, closes, atr_period)
    parsed_highs, parsed_lows = _precompute_parsed_hl_jit(highs, lows, atr)

    mit_mode = 1 if mitigation_mode == 'CLOSE' else 0

    results = []

    if detect_swing:
        (ob_h, ob_l, ob_idx, ob_bias, ob_brk,
         ob_mit, ob_mit_idx, cnt) = _detect_obs_single_stream_jit(
            highs, lows, closes, parsed_highs, parsed_lows,
            swing_length, mit_mode, max_ob_store,
        )
        for i in range(len(ob_h)):
            t = pd.Timestamp(times[ob_idx[i]]).to_pydatetime()
            results.append(OrderBlock(
                high=float(ob_h[i]),
                low=float(ob_l[i]),
                time=t,
                index=int(ob_idx[i]),
                bias='BULLISH' if ob_bias[i] == 1 else 'BEARISH',
                ob_type='SWING',
                break_index=int(ob_brk[i]),
                is_mitigated=bool(ob_mit[i]),
                mitigated_index=int(ob_mit_idx[i]),
            ))

    if detect_internal:
        (ob_h, ob_l, ob_idx, ob_bias, ob_brk,
         ob_mit, ob_mit_idx, cnt) = _detect_obs_single_stream_jit(
            highs, lows, closes, parsed_highs, parsed_lows,
            internal_length, mit_mode, max_ob_store,
        )
        for i in range(len(ob_h)):
            t = pd.Timestamp(times[ob_idx[i]]).to_pydatetime()
            results.append(OrderBlock(
                high=float(ob_h[i]),
                low=float(ob_l[i]),
                time=t,
                index=int(ob_idx[i]),
                bias='BULLISH' if ob_bias[i] == 1 else 'BEARISH',
                ob_type='INTERNAL',
                break_index=int(ob_brk[i]),
                is_mitigated=bool(ob_mit[i]),
                mitigated_index=int(ob_mit_idx[i]),
            ))

    return results


# ==================== JSON EXPORT ====================

def to_json(order_blocks: List[OrderBlock], df: pd.DataFrame, max_bars: int = 50) -> List[dict]:
    """
    Convert Order Blocks to JSON-serializable format for client display.

    OB box ends at whichever comes first: mitigation bar or max_bars after OB candle.
    """
    times = df['Time'].values
    n = len(times)

    result = []
    for ob in order_blocks:
        if ob.index >= n:
            continue

        max_end = min(ob.index + max_bars, n - 1)

        if ob.is_mitigated and 0 <= ob.mitigated_index < n:
            end_idx = min(ob.mitigated_index, max_end)
        else:
            end_idx = max_end

        if ob.ob_type == 'SWING':
            if ob.bias == 'BULLISH':
                color = 'rgba(24, 72, 204, 0.2)'
            else:
                color = 'rgba(178, 40, 51, 0.2)'
        else:
            if ob.bias == 'BULLISH':
                color = 'rgba(49, 121, 245, 0.2)'
            else:
                color = 'rgba(247, 124, 128, 0.2)'

        result.append({
            'startTime': int(pd.Timestamp(times[ob.index]).timestamp()),
            'endTime': int(pd.Timestamp(times[end_idx]).timestamp()),
            'high': ob.high,
            'low': ob.low,
            'bias': ob.bias,
            'obType': ob.ob_type,
            'isMitigated': ob.is_mitigated,
            'color': color,
        })

    return result
