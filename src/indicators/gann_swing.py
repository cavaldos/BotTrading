"""
Gann Swing indicator - detect HH/HL/LH/LL using leg-based structure pivots.

Ported from the swing-structure logic in docs/strategy/GannSwing.pine:
    leg(size):
      - newLegHigh = high[size] > ta.highest(size)
      - newLegLow  = low[size]  < ta.lowest(size)
    On leg change:
      - bullish leg start  -> new swing low (HL/LL)
      - bearish leg start  -> new swing high (HH/LH)

This differs from fractal pivots (ta.pivothigh/pivotlow): it confirms pivots from
leg transitions, matching LuxAlgo structure behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from numba import njit


BULLISH_LEG = 1
BEARISH_LEG = 0

TYPE_HH = 1
TYPE_LH = 2
TYPE_HL = 3
TYPE_LL = 4


@dataclass
class GannSwing:
    """Represents a leg-based swing pivot label."""

    type: str       # 'HH' | 'HL' | 'LH' | 'LL'
    price: float
    time: datetime
    index: int
    is_high: bool


@njit(cache=True)
def _detect_gann_swings_jit(
    highs: np.ndarray,
    lows: np.ndarray,
    size: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Detect Gann-style swings from leg transitions.

    Returns:
        pivot_indices, pivot_prices, type_codes, is_high_flags
    """
    n = len(highs)
    max_events = n

    pivot_indices = np.empty(max_events, dtype=np.int64)
    pivot_prices = np.empty(max_events, dtype=np.float64)
    type_codes = np.empty(max_events, dtype=np.int8)
    is_high_flags = np.empty(max_events, dtype=np.int8)
    count = 0

    leg = BEARISH_LEG
    prev_leg = BEARISH_LEG

    last_high = 0.0
    has_last_high = False
    last_low = 0.0
    has_last_low = False

    for i in range(1, n):
        k = i - size
        if k < 0:
            continue

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

        if leg == prev_leg:
            continue

        if leg == BULLISH_LEG:
            # New swing low at bar k
            price = lows[k]
            pivot_indices[count] = k
            pivot_prices[count] = price
            is_high_flags[count] = 0

            if not has_last_low:
                type_codes[count] = TYPE_LL
            elif price < last_low:
                type_codes[count] = TYPE_LL
            else:
                type_codes[count] = TYPE_HL

            last_low = price
            has_last_low = True
        else:
            # New swing high at bar k
            price = highs[k]
            pivot_indices[count] = k
            pivot_prices[count] = price
            is_high_flags[count] = 1

            if not has_last_high:
                type_codes[count] = TYPE_HH
            elif price > last_high:
                type_codes[count] = TYPE_HH
            else:
                type_codes[count] = TYPE_LH

            last_high = price
            has_last_high = True

        count += 1

    return (
        pivot_indices[:count],
        pivot_prices[:count],
        type_codes[:count],
        is_high_flags[:count],
    )


def detect_gann_swings(df: pd.DataFrame, length: int = 3) -> List[GannSwing]:
    """Detect Gann-style swings from OHLC data.

    Args:
        df: DataFrame with columns Time, High, Low
        length: Swing structure length (same role as `swingsLengthInput` in Pine)

    Returns:
        List of GannSwing objects sorted by index.
    """
    swings: List[GannSwing] = []

    if length < 1 or len(df) <= length:
        return swings

    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    times = df['Time'].values

    idxs, prices, type_codes, is_high_flags = _detect_gann_swings_jit(highs, lows, length)

    type_map = {
        TYPE_HH: 'HH',
        TYPE_LH: 'LH',
        TYPE_HL: 'HL',
        TYPE_LL: 'LL',
    }

    for i in range(len(idxs)):
        idx = int(idxs[i])
        swings.append(
            GannSwing(
                type=type_map.get(int(type_codes[i]), 'HL'),
                price=float(prices[i]),
                time=pd.to_datetime(times[idx]).to_pydatetime(),
                index=idx,
                is_high=bool(is_high_flags[i]),
            )
        )

    swings.sort(key=lambda x: x.index)
    return swings


def to_json(
    swings: List[GannSwing],
    df: Optional[pd.DataFrame] = None,
    times: Optional[Sequence[Any]] = None,
) -> List[dict]:
    """Convert Gann swings to JSON-serializable list for frontend rendering."""
    result = []
    if times is None and df is not None:
        times = list(df['Time'].values)

    for s in swings:
        fallback_ts = int(s.time.timestamp())
        if times is not None and s.index < len(times):
            ts_val = times[s.index]
            parsed = pd.Timestamp(ts_val)
            ts = fallback_ts if pd.isna(parsed) else int(parsed.value // 10**9)
        else:
            ts = fallback_ts

        color = '#ff7043' if s.is_high else '#29b6f6'

        result.append({
            'type': s.type,
            'price': s.price,
            'time': ts,
            'index': s.index,
            'isHigh': s.is_high,
            'color': color,
            'indicator': 'GANN_SWING',
        })

    return result


if __name__ == '__main__':
    dates = pd.date_range('2024-01-01', periods=400, freq='h')
    base = 100.0
    prices = base + np.cumsum(np.random.randn(400) * 0.5)
    df = pd.DataFrame(
        {
            'Time': dates,
            'Open': prices,
            'High': prices + np.abs(np.random.randn(400) * 0.4),
            'Low': prices - np.abs(np.random.randn(400) * 0.4),
            'Close': prices + np.random.randn(400) * 0.1,
        }
    )

    detected = detect_gann_swings(df, length=3)
    print(f'Detected {len(detected)} gann swings')
    print(to_json(detected, times=list(df['Time'].values))[:5])
