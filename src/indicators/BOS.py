"""
BOS (Break of Structure) Indicator
==================================
Detects Break of Structure events only (no CHoCH), matching bos_indicator.pine.

Ported from docs/strategy/bos_indicator.pine.

Key logic:
  - Pivot detection via leg(size): high[size] > ta.highest(size) / low[size] < ta.lowest(size)
  - Pivots update on leg change: startOfNewLeg → update swingHigh/swingLow at bar [size]
  - BOS = crossover/crossunder with pivot.crossed flag; only when trend continues (bias == BULLISH/BEARISH or 0)
  - Supports both Swing and Internal streams independently

Usage:
    from src.indicators.BOS import detect_bos, BOS, to_json

    events = detect_bos(df, swing_length=50, internal_length=5)
    json_data = to_json(events)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Tuple
from dataclasses import dataclass
from numba import njit


# ==================== CONSTANTS ====================

BULLISH = 1
BEARISH = -1
NEUTRAL = 0
BULLISH_LEG = 1
BEARISH_LEG = 0

# Event types (BOS only; Pine script does not emit CHoCH)
BOS_BULLISH = 1
BOS_BEARISH = -1


# ==================== DATA CLASS ====================

@dataclass
class BOS:
    """Represents a Break of Structure event (BOS only, no CHoCH)."""
    event_type: str         # 'BOS_BULLISH' or 'BOS_BEARISH'
    bias: str               # 'BULLISH' or 'BEARISH' (new trend after break)
    level: float            # Price level that was broken
    break_time: datetime
    break_index: int
    pivot_time: datetime
    pivot_index: int
    bos_type: str           # 'SWING' or 'INTERNAL'


# ==================== NUMBA JIT FUNCTIONS ====================

@njit(cache=True)
def _detect_bos_single_stream_jit(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    size: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Detect BOS/CHoCH events for a single stream (swing or internal).

    Implements Pine's leg() + updatePivots() + detectBosChoch() in a single pass,
    matching the same pattern used in order_block.py.

    Pine logic reference (bos_indicator.pine - BOS only):
        leg(size): var leg = 0; newLegHigh/newLegLow → leg := 0 or 1
        updatePivots: on startOfNewLeg, if startOfBullishLeg → swingLow, else → swingHigh
        detectBos(): only emit when trend continues (bias == BULLISH/BEARISH or 0), else no event
            crossover(close, swingHigh) and not crossed → if t.bias==BULLISH or 0 then isBullishBos; t.bias:=BULLISH
            crossunder(close, swingLow) and not crossed → if t.bias==BEARISH or 0 then isBearishBos; t.bias:=BEARISH

    Args:
        size: leg size (swingLength for swing, internalLength for internal)

    Returns:
        event_types, biases, levels, break_indices, pivot_indices
    """
    n = len(highs)
    max_events = n
    event_types = np.empty(max_events, dtype=np.int8)
    biases = np.empty(max_events, dtype=np.int8)
    levels = np.empty(max_events, dtype=np.float64)
    break_indices = np.empty(max_events, dtype=np.int64)
    pivot_indices = np.empty(max_events, dtype=np.int64)
    count = 0

    # Pivot state (matches Pine's pivot.new(na, na, false))
    # Use sentinel values similar to order_block.py
    sh_level = -1e18       # swing high level (effectively na)
    sh_bar_idx = -1        # swing high bar index
    sh_crossed = True      # swing high already crossed (prevent false fire on init)

    sl_level = 1e18        # swing low level (effectively na)
    sl_bar_idx = -1        # swing low bar index
    sl_crossed = True      # swing low already crossed

    # Trend bias: 0=NEUTRAL, 1=BULLISH, -1=BEARISH
    trend_bias = NEUTRAL

    # Leg state (matches Pine's var leg = 0)
    leg = BEARISH_LEG   # 0 in Pine
    prev_leg = BEARISH_LEG

    prev_close = closes[0] if n > 0 else 0.0

    for i in range(1, n):
        # ── STEP 1: LEG DETECTION (same as order_block.py) ──
        k = i - size
        if k >= 0:
            # ta.highest(size) = max(high[i], high[i-1], ..., high[i-size+1])
            # In array terms: max(highs[k+1]..highs[i])
            highest_after = highs[k + 1]
            for j in range(k + 2, i + 1):
                if highs[j] > highest_after:
                    highest_after = highs[j]

            # ta.lowest(size) = min(low[i], low[i-1], ..., low[i-size+1])
            lowest_after = lows[k + 1]
            for j in range(k + 2, i + 1):
                if lows[j] < lowest_after:
                    lowest_after = lows[j]

            # high[size] > ta.highest(size) → new high pivot found → bearish leg starts
            new_leg_high = highs[k] > highest_after
            # low[size] < ta.lowest(size) → new low pivot found → bullish leg starts
            new_leg_low = lows[k] < lowest_after

            prev_leg = leg
            if new_leg_high:
                leg = BEARISH_LEG
            elif new_leg_low:
                leg = BULLISH_LEG

            # ── STEP 2: UPDATE PIVOTS ON LEG CHANGE (startOfNewLeg in Pine) ──
            if leg != prev_leg:
                if leg == BULLISH_LEG:
                    # startOfBullishLeg: new low pivot found at bar k
                    sl_level = lows[k]
                    sl_bar_idx = k
                    sl_crossed = False
                else:
                    # startOfBearishLeg: new high pivot found at bar k
                    sh_level = highs[k]
                    sh_bar_idx = k
                    sh_crossed = False

        # ── STEP 3: DETECT STRUCTURE BREAKS (crossover/crossunder) ──
        close_val = closes[i]

        # Bullish breakout: ta.crossover(close, pH.currentLevel) and not pH.crossed (BOS only)
        if not sh_crossed and sh_bar_idx >= 0:
            if prev_close <= sh_level and close_val > sh_level:
                sh_crossed = True
                # Only emit event when trend was BULLISH or 0 (BOS), not on reversal (Pine: isBullishBos only then)
                if trend_bias == BULLISH or trend_bias == NEUTRAL:
                    event_types[count] = BOS_BULLISH
                    biases[count] = BULLISH
                    levels[count] = sh_level
                    break_indices[count] = i
                    pivot_indices[count] = sh_bar_idx
                    count += 1
                trend_bias = BULLISH

        # Bearish breakout: ta.crossunder(close, pL.currentLevel) and not pL.crossed (BOS only)
        if not sl_crossed and sl_bar_idx >= 0:
            if prev_close >= sl_level and close_val < sl_level:
                sl_crossed = True
                # Only emit event when trend was BEARISH or 0 (BOS), not on reversal
                if trend_bias == BEARISH or trend_bias == NEUTRAL:
                    event_types[count] = BOS_BEARISH
                    biases[count] = BEARISH
                    levels[count] = sl_level
                    break_indices[count] = i
                    pivot_indices[count] = sl_bar_idx
                    count += 1
                trend_bias = BEARISH

        prev_close = close_val

    return (
        event_types[:count],
        biases[:count],
        levels[:count],
        break_indices[:count],
        pivot_indices[:count]
    )


# ==================== MAIN DETECTION FUNCTION ====================

def detect_bos(
    df: pd.DataFrame,
    swing_length: int = 50,
    internal_length: int = 5,
    detect_swing: bool = True,
    detect_internal: bool = True,
) -> List[BOS]:
    """
    Detect BOS/CHoCH events from structure breaks using LuxAlgo logic.

    Processes two independent streams (Swing and Internal), each with their
    own pivot state and trend bias, exactly like the Pine Script.

    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        swing_length: Leg size for swing pivots (default 50, matches Pine)
        internal_length: Leg size for internal pivots (default 5, matches Pine)
        detect_swing: Whether to detect swing BOS/CHoCH
        detect_internal: Whether to detect internal BOS/CHoCH

    Returns:
        List of BOS objects sorted by break_index
    """
    # Ensure chronological order (oldest to newest)
    df = df.sort_index()

    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    times = df['Time'].values

    results = []

    if detect_swing:
        event_types, biases, levels, break_idxs, pivot_idxs = _detect_bos_single_stream_jit(
            highs, lows, closes, swing_length
        )
        for i in range(len(biases)):
            results.append(_make_bos(
                event_types[i], biases[i], levels[i],
                break_idxs[i], pivot_idxs[i], times, 'SWING'
            ))

    if detect_internal:
        event_types, biases, levels, break_idxs, pivot_idxs = _detect_bos_single_stream_jit(
            highs, lows, closes, internal_length
        )
        for i in range(len(biases)):
            results.append(_make_bos(
                event_types[i], biases[i], levels[i],
                break_idxs[i], pivot_idxs[i], times, 'INTERNAL'
            ))

    # Sort by break_index for chronological output
    results.sort(key=lambda x: x.break_index)

    return results


def _make_bos(
    event_type: int, bias: int, level: float,
    break_idx: int, pivot_idx: int,
    times: np.ndarray, bos_type: str,
) -> BOS:
    """Helper to construct a BOS object from raw JIT output."""
    break_idx = int(break_idx)
    pivot_idx = int(pivot_idx)

    break_ts = times[break_idx]
    pivot_ts = times[pivot_idx]

    if isinstance(break_ts, (int, np.integer)):
        bt = datetime.fromtimestamp(int(break_ts))
    else:
        bt = pd.Timestamp(break_ts).to_pydatetime()

    if isinstance(pivot_ts, (int, np.integer)):
        pt = datetime.fromtimestamp(int(pivot_ts))
    else:
        pt = pd.Timestamp(pivot_ts).to_pydatetime()

    event_type_int = int(event_type)
    event_type_str = {
        BOS_BULLISH: 'BOS_BULLISH',
        BOS_BEARISH: 'BOS_BEARISH',
    }.get(event_type_int, 'UNKNOWN')

    return BOS(
        event_type=event_type_str,
        bias='BULLISH' if bias == BULLISH else 'BEARISH',
        level=float(level),
        break_time=bt,
        break_index=break_idx,
        pivot_time=pt,
        pivot_index=pivot_idx,
        bos_type=bos_type,
    )


# ==================== JSON EXPORT ====================

def to_json(bos_list: List[BOS], times: np.ndarray = None) -> List[dict]:
    """
    Format compatible with TradingView lightweight-charts.
    If `times` is provided (same array as candle Time), breakTime/pivotTime are
    computed with the same conversion as candle export for exact alignment.
    """
    result = []
    for bos in bos_list:
        color_map = {
            'BOS_BULLISH': 'rgba(41, 98, 255, 1)',    # #2962ff (Pine Swing)
            'BOS_BEARISH': 'rgba(41, 98, 255, 1)',
        }
        style_map = {
            'BOS_BULLISH': 'dashed',
            'BOS_BEARISH': 'dashed',
        }
        # Use same timestamp conversion as export_candles for alignment with candle time scale
        if times is not None and len(times) > 0:
            bi, pi = int(bos.break_index), int(bos.pivot_index)
            break_ts = int(pd.Timestamp(times[bi]).timestamp()) if bi < len(times) else int(bos.break_time.timestamp())
            pivot_ts = int(pd.Timestamp(times[pi]).timestamp()) if pi < len(times) else int(bos.pivot_time.timestamp())
        else:
            break_ts = int(bos.break_time.timestamp())
            pivot_ts = int(bos.pivot_time.timestamp())

        result.append({
            'type': 'BOS',
            'eventType': bos.event_type,
            'bias': bos.bias,
            'bosType': bos.bos_type,
            'level': bos.level,
            'breakTime': break_ts,
            'breakIndex': bos.break_index,
            'pivotTime': pivot_ts,
            'pivotIndex': bos.pivot_index,
            'color': color_map.get(bos.event_type, 'rgba(128, 128, 128, 0.8)'),
            'style': style_map.get(bos.event_type, 'dashed'),
            'lineStyle': style_map.get(bos.event_type, 'dashed'),
        })
    return result


# ==================== EXAMPLE USAGE ====================

if __name__ == "__main__":
    import numpy as np
    import pandas as pd

    # Generate test data
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=200, freq='H')
    base = 100
    prices = base + np.cumsum(np.random.randn(200) * 0.5)

    # Create a clear BOS pattern
    prices[30:50] = np.linspace(95, 105, 20)   # Downtrend
    prices[50:80] = np.linspace(105, 115, 30)   # Uptrend (BOS)
    prices[80:100] = np.linspace(115, 105, 20)  # Downtrend (CHoCH)

    df = pd.DataFrame({
        'Time': dates,
        'Open': prices,
        'High': prices + np.abs(np.random.randn(200) * 0.3),
        'Low': prices - np.abs(np.random.randn(200) * 0.3),
        'Close': prices + np.random.randn(200) * 0.1
    })

    # Detect BOS events (both swing and internal)
    bos_events = detect_bos(df, swing_length=20, internal_length=5)

    print(f"Detected {len(bos_events)} BOS events:")
    for bos in bos_events:
        print(f"{bos.event_type:20s} | {bos.bos_type:8s} | Break at bar {bos.break_index:4d} | Level: {bos.level:.2f}")

    # Export to JSON for TradingView
    json_data = to_json(bos_events)
    print(f"\nJSON export ready with {len(json_data)} events")
