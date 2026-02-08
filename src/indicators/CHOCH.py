"""
CHoCH (Change of Character) Indicator
======================================
Detects Change of Character events only (reversal), matching choch_indicator.pine.

Ported from docs/strategy/choch_indicator.pine.

Key logic (same leg/updatePivots as BOS; different emit condition):
  - Pivot detection via leg(size): high[size] > ta.highest(size) / low[size] < ta.lowest(size)
  - Pivots update on leg change: startOfNewLeg → update swingHigh/swingLow at bar [size]
  - CHoCH = crossover/crossunder with pivot.crossed; only when trend REVERSES (bias was BEARISH→bullish break, BULLISH→bearish break)
  - Supports both Swing and Internal streams independently

Usage:
    from src.indicators.CHOCH import detect_choch, CHoCH, to_json

    events = detect_choch(df, swing_length=50, internal_length=5)
    json_data = to_json(events, times=df['Time'].values)
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

# Event types (CHoCH only)
CHOCH_BULLISH = 1
CHOCH_BEARISH = -1


# ==================== DATA CLASS ====================

@dataclass
class CHoCH:
    """Represents a Change of Character event (reversal only)."""
    event_type: str         # 'CHOCH_BULLISH' or 'CHOCH_BEARISH'
    bias: str               # 'BULLISH' or 'BEARISH' (new trend after reversal)
    level: float            # Price level that was broken
    break_time: datetime
    break_index: int
    pivot_time: datetime
    pivot_index: int
    choch_type: str         # 'SWING' or 'INTERNAL'


# ==================== NUMBA JIT FUNCTIONS ====================

@njit(cache=True)
def _detect_choch_single_stream_jit(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    size: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Detect CHoCH events for a single stream (swing or internal).
    Same leg() + updatePivots() as BOS; detectChoch() only emits on reversal.

    Pine logic reference (choch_indicator.pine):
        leg(size), updatePivots(size) — identical to BOS
        detectChoch(): only emit when trend reverses
            crossover(close, pH.currentLevel) and not crossed → if t.bias == BEARISH then isBullishChoch; t.bias := BULLISH
            crossunder(close, pL.currentLevel) and not crossed → if t.bias == BULLISH then isBearishChoch; t.bias := BEARISH

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

    # Pivot state (matches Pine pivot UDT)
    sh_level = -1e18
    sh_bar_idx = -1
    sh_crossed = True

    sl_level = 1e18
    sl_bar_idx = -1
    sl_crossed = True

    trend_bias = NEUTRAL
    leg = BEARISH_LEG
    prev_leg = BEARISH_LEG

    prev_close = closes[0] if n > 0 else 0.0

    for i in range(1, n):
        k = i - size
        if k >= 0:
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

            if leg != prev_leg:
                if leg == BULLISH_LEG:
                    sl_level = lows[k]
                    sl_bar_idx = k
                    sl_crossed = False
                else:
                    sh_level = highs[k]
                    sh_bar_idx = k
                    sh_crossed = False

        close_val = closes[i]

        # Bullish breakout: only emit CHoCH when trend was BEARISH (reversal)
        if not sh_crossed and sh_bar_idx >= 0:
            if prev_close <= sh_level and close_val > sh_level:
                sh_crossed = True
                if trend_bias == BEARISH:
                    event_types[count] = CHOCH_BULLISH
                    biases[count] = BULLISH
                    levels[count] = sh_level
                    break_indices[count] = i
                    pivot_indices[count] = sh_bar_idx
                    count += 1
                trend_bias = BULLISH

        # Bearish breakout: only emit CHoCH when trend was BULLISH (reversal)
        if not sl_crossed and sl_bar_idx >= 0:
            if prev_close >= sl_level and close_val < sl_level:
                sl_crossed = True
                if trend_bias == BULLISH:
                    event_types[count] = CHOCH_BEARISH
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
        pivot_indices[:count],
    )


# ==================== MAIN DETECTION FUNCTION ====================

def detect_choch(
    df: pd.DataFrame,
    swing_length: int = 50,
    internal_length: int = 5,
    detect_swing: bool = True,
    detect_internal: bool = True,
) -> List[CHoCH]:
    """
    Detect CHoCH events (reversal only) from structure breaks, matching choch_indicator.pine.

    Two independent streams (Swing and Internal), each with own pivot state and trend bias.

    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        swing_length: Leg size for swing pivots (default 50, matches Pine)
        internal_length: Leg size for internal pivots (default 5, matches Pine)
        detect_swing: Whether to detect swing CHoCH
        detect_internal: Whether to detect internal CHoCH

    Returns:
        List of CHoCH objects sorted by break_index
    """
    df = df.sort_index()

    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    times = df['Time'].values

    results = []

    if detect_swing:
        event_types, biases, levels, break_idxs, pivot_idxs = _detect_choch_single_stream_jit(
            highs, lows, closes, swing_length
        )
        for i in range(len(biases)):
            results.append(_make_choch(
                event_types[i], biases[i], levels[i],
                break_idxs[i], pivot_idxs[i], times, 'SWING'
            ))

    if detect_internal:
        event_types, biases, levels, break_idxs, pivot_idxs = _detect_choch_single_stream_jit(
            highs, lows, closes, internal_length
        )
        for i in range(len(biases)):
            results.append(_make_choch(
                event_types[i], biases[i], levels[i],
                break_idxs[i], pivot_idxs[i], times, 'INTERNAL'
            ))

    results.sort(key=lambda x: x.break_index)
    return results


def _make_choch(
    event_type: int, bias: int, level: float,
    break_idx: int, pivot_idx: int,
    times: np.ndarray, choch_type: str,
) -> CHoCH:
    """Build CHoCH object from JIT output."""
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

    event_type_str = {
        CHOCH_BULLISH: 'CHOCH_BULLISH',
        CHOCH_BEARISH: 'CHOCH_BEARISH',
    }.get(int(event_type), 'UNKNOWN')

    return CHoCH(
        event_type=event_type_str,
        bias='BULLISH' if bias == BULLISH else 'BEARISH',
        level=float(level),
        break_time=bt,
        break_index=break_idx,
        pivot_time=pt,
        pivot_index=pivot_idx,
        choch_type=choch_type,
    )


# ==================== JSON EXPORT ====================

def to_json(choch_list: List[CHoCH], times: np.ndarray = None) -> List[dict]:
    """
    Format compatible with TradingView lightweight-charts.
    If `times` is provided (same as candle Time), breakTime/pivotTime match candle export.
    """
    result = []
    for choch in choch_list:
        color_map = {
            'CHOCH_BULLISH': 'rgba(255, 152, 0, 1)',   # #ff9800 (Pine)
            'CHOCH_BEARISH': 'rgba(255, 152, 0, 1)',
        }
        style_map = {
            'CHOCH_BULLISH': 'dashed',
            'CHOCH_BEARISH': 'dashed',
        }

        if times is not None and len(times) > 0:
            bi, pi = int(choch.break_index), int(choch.pivot_index)
            break_ts = int(pd.Timestamp(times[bi]).timestamp()) if bi < len(times) else int(choch.break_time.timestamp())
            pivot_ts = int(pd.Timestamp(times[pi]).timestamp()) if pi < len(times) else int(choch.pivot_time.timestamp())
        else:
            break_ts = int(choch.break_time.timestamp())
            pivot_ts = int(choch.pivot_time.timestamp())

        result.append({
            'type': 'CHoCH',
            'eventType': choch.event_type,
            'bias': choch.bias,
            'chochType': choch.choch_type,
            'level': choch.level,
            'breakTime': break_ts,
            'breakIndex': choch.break_index,
            'pivotTime': pivot_ts,
            'pivotIndex': choch.pivot_index,
            'color': color_map.get(choch.event_type, 'rgba(255, 152, 0, 0.9)'),
            'style': style_map.get(choch.event_type, 'dashed'),
            'lineStyle': style_map.get(choch.event_type, 'dashed'),
        })
    return result


# ==================== EXAMPLE ====================

if __name__ == "__main__":
    import numpy as np
    import pandas as pd

    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=200, freq='H')
    base = 100
    prices = base + np.cumsum(np.random.randn(200) * 0.5)

    df = pd.DataFrame({
        'Time': dates,
        'Open': prices,
        'High': prices + np.abs(np.random.randn(200) * 0.3),
        'Low': prices - np.abs(np.random.randn(200) * 0.3),
        'Close': prices + np.random.randn(200) * 0.1
    })

    events = detect_choch(df, swing_length=50, internal_length=5)
    print(f"Detected {len(events)} CHoCH events:")
    for e in events:
        print(f"  {e.event_type:16s} | {e.choch_type:8s} | break bar {e.break_index:4d} | level {e.level:.2f}")

    json_data = to_json(events, times=df['Time'].values)
    print(f"\nJSON export: {len(json_data)} events")
