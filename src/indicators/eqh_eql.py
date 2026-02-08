"""
EQH/EQL (Equal High / Equal Low) Indicator
===========================================
Detects Equal Highs and Equal Lows using LuxAlgo-style leg-based pivots.
When two consecutive swing highs (or lows) are within threshold * ATR, draw EQH (or EQL).

Ported from docs/strategy/eqh_eql.pine.

Key logic:
  - Pivot detection via leg(size): same as BOS/CHoCH (high[size] > ta.highest(size), etc.)
  - On leg change: compare NEW pivot level with PREVIOUS pivot level of same type.
  - If |prev_level - new_level| < threshold * ATR(current bar) → emit EQH or EQL.
  - ATR uses period 200 (Pine: ta.atr(200)), RMA smoothing.
  - Default: bars_confirmation=3, threshold=0.1.

Usage:
    from src.indicators.eqh_eql import detect_eqh_eql, EqualHL, to_json

    eqhl_list = detect_eqh_eql(df, bars_confirmation=3, threshold=0.1)
    json_data = to_json(eqhl_list, times=df['Time'].values)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Tuple
from dataclasses import dataclass
from numba import njit


# ==================== CONSTANTS ====================

BULLISH_LEG = 1
BEARISH_LEG = 0
EQH = 1
EQL = 0


# ==================== DATA CLASS ====================

@dataclass
class EqualHL:
    """Equal High or Equal Low (two consecutive pivots within threshold * ATR)."""
    type: str               # 'EQH' or 'EQL'
    level: float            # Level used for line (second pivot level, matching Pine draw)
    start_time: datetime
    end_time: datetime
    first_pivot_index: int
    second_pivot_index: int


# ==================== NUMBA JIT FUNCTIONS ====================

@njit(cache=True)
def _atr_rma_jit(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    """ATR using RMA (Wilder) of True Range, matching Pine ta.atr(period)."""
    n = len(highs)
    tr = np.empty(n, dtype=np.float64)
    atr = np.empty(n, dtype=np.float64)

    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        tr[i] = max(tr1, max(tr2, tr3))

    # RMA: first 'period' bars use SMA of TR, then (x + (period-1)*rma[1])/period
    cum = 0.0
    for i in range(n):
        cum += tr[i]
        if i < period:
            atr[i] = cum / (i + 1)
        else:
            atr[i] = (tr[i] + (period - 1) * atr[i - 1]) / period
    return atr


@njit(cache=True)
def _detect_eqh_eql_jit(
    highs: np.ndarray,
    lows: np.ndarray,
    atr: np.ndarray,
    size: int,
    threshold: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Detect EQH/EQL: leg-based pivots (same as BOS); on each new pivot,
    if previous pivot of same type is within threshold*ATR, emit event.

    Pine logic:
        size = bars confirmation, currentLeg = leg(size), newPivot = startOfNewLeg
        if newPivot and pivotLow: if |equalLow.currentLevel - low[size]| < threshold*atr → draw EQL; then update equalLow
        if newPivot and pivotHigh: if |equalHigh.currentLevel - high[size]| < threshold*atr → draw EQH; then update equalHigh

    Returns:
        types (1=EQH, 0=EQL), levels, first_pivot_indices, second_pivot_indices
    """
    n = len(highs)
    max_events = n
    types = np.empty(max_events, dtype=np.int8)
    levels = np.empty(max_events, dtype=np.float64)
    first_pivots = np.empty(max_events, dtype=np.int64)
    second_pivots = np.empty(max_events, dtype=np.int64)
    count = 0

    # Pivot state (previous swing high/low level and bar)
    sh_level = -1e18
    sh_bar_idx = -1

    sl_level = 1e18
    sl_bar_idx = -1

    leg = BEARISH_LEG
    prev_leg = BEARISH_LEG

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
                atr_val = atr[i]
                if atr_val <= 0:
                    atr_val = 1e-10

                if leg == BULLISH_LEG:
                    # New swing low: check EQL (previous low within threshold*ATR of new low)
                    if sl_bar_idx >= 0 and abs(sl_level - lows[k]) < threshold * atr_val:
                        types[count] = EQL
                        levels[count] = lows[k]
                        first_pivots[count] = sl_bar_idx
                        second_pivots[count] = k
                        count += 1
                    sl_level = lows[k]
                    sl_bar_idx = k
                else:
                    # New swing high: check EQH
                    if sh_bar_idx >= 0 and abs(sh_level - highs[k]) < threshold * atr_val:
                        types[count] = EQH
                        levels[count] = highs[k]
                        first_pivots[count] = sh_bar_idx
                        second_pivots[count] = k
                        count += 1
                    sh_level = highs[k]
                    sh_bar_idx = k

    return (
        types[:count],
        levels[:count],
        first_pivots[:count],
        second_pivots[:count],
    )


# ==================== MAIN DETECTION FUNCTION ====================

def detect_eqh_eql(
    df: pd.DataFrame,
    bars_confirmation: int = 3,
    threshold: float = 0.1,
    atr_period: int = 200,
) -> List[EqualHL]:
    """
    Detect Equal Highs and Equal Lows (consecutive pivots within threshold*ATR).
    Matches docs/strategy/eqh_eql.pine (leg-based pivots, ATR 200).

    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        bars_confirmation: Leg size for pivot confirmation (default 3, Pine: Bars Confirmation)
        threshold: ATR multiplier for "equal" (default 0.1, Pine: 0–0.5)
        atr_period: ATR period (default 200, Pine: ta.atr(200))

    Returns:
        List of EqualHL objects (order of detection)
    """
    df = df.sort_index()

    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    times = df['Time'].values

    atr = _atr_rma_jit(highs, lows, closes, atr_period)

    types, levels, first_idxs, second_idxs = _detect_eqh_eql_jit(
        highs, lows, atr, bars_confirmation, threshold
    )

    results = []
    for i in range(len(types)):
        fp = int(first_idxs[i])
        sp = int(second_idxs[i])
        st = times[fp]
        et = times[sp]
        if isinstance(st, (int, np.integer)):
            start_dt = datetime.fromtimestamp(int(st))
        else:
            start_dt = pd.Timestamp(st).to_pydatetime()
        if isinstance(et, (int, np.integer)):
            end_dt = datetime.fromtimestamp(int(et))
        else:
            end_dt = pd.Timestamp(et).to_pydatetime()

        results.append(EqualHL(
            type='EQH' if types[i] == EQH else 'EQL',
            level=float(levels[i]),
            start_time=start_dt,
            end_time=end_dt,
            first_pivot_index=fp,
            second_pivot_index=sp,
        ))

    return results


# ==================== JSON EXPORT ====================

def to_json(eqhl_list: List[EqualHL], df: pd.DataFrame = None, times: np.ndarray = None) -> List[dict]:
    """
    Format for lightweight-charts. If `times` is provided, use same timestamp
    conversion as candle export for alignment.
    """
    if times is None and df is not None:
        times = df['Time'].values

    result = []
    for eqhl in eqhl_list:
        if times is not None and len(times) > 0:
            fp, sp = eqhl.first_pivot_index, eqhl.second_pivot_index
            start_ts = int(pd.Timestamp(times[fp]).timestamp()) if fp < len(times) else int(eqhl.start_time.timestamp())
            end_ts = int(pd.Timestamp(times[sp]).timestamp()) if sp < len(times) else int(eqhl.end_time.timestamp())
        else:
            start_ts = int(eqhl.start_time.timestamp())
            end_ts = int(eqhl.end_time.timestamp())

        # Pine: EQH = bearish color #F23645, EQL = bullish #089981
        color = 'rgba(242, 54, 69, 0.85)' if eqhl.type == 'EQH' else 'rgba(8, 153, 129, 0.85)'

        result.append({
            'type': eqhl.type,
            'level': eqhl.level,
            'startTime': start_ts,
            'endTime': end_ts,
            'firstPivotIndex': eqhl.first_pivot_index,
            'secondPivotIndex': eqhl.second_pivot_index,
            'color': color,
            'lineStyle': 'dotted',
        })
    return result


# ==================== EXAMPLE ====================

if __name__ == "__main__":
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=500, freq='H')
    base = 100
    prices = base + np.cumsum(np.random.randn(500) * 0.3)

    df = pd.DataFrame({
        'Time': dates,
        'Open': prices,
        'High': prices + np.abs(np.random.randn(500) * 0.2),
        'Low': prices - np.abs(np.random.randn(500) * 0.2),
        'Close': prices + np.random.randn(500) * 0.05
    })

    events = detect_eqh_eql(df, bars_confirmation=3, threshold=0.1)
    print(f"Detected {len(events)} EQH/EQL events:")
    for e in events:
        print(f"  {e.type} | level {e.level:.2f} | bars {e.first_pivot_index} -> {e.second_pivot_index}")

    json_data = to_json(events, times=df['Time'].values)
    print(f"\nJSON export: {len(json_data)} events")
