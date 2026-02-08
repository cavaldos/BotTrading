"""
Swing indicator - detect swing highs/lows (HH/HL/LH/LL)
=====================================================
Detects swing pivots using lookback `length` (bars on each side).

Matches the simple fractal-style swing logic used in docs and strategy.detect_swing_points.
Provides JSON export for the frontend chart (small labeled markers for pivots).
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List
from dataclasses import dataclass
from numba import njit


@dataclass
class Swing:
    """Represents a swing pivot"""
    type: str       # 'HH', 'HL', 'LH', 'LL'
    price: float
    time: datetime
    index: int
    is_high: bool


@njit(cache=True)
def _check_swing_high_jit(highs: np.ndarray, i: int, length: int) -> bool:
    n = len(highs)
    if i < length or i >= n - length:
        return False
    for j in range(i - length, i + length + 1):
        if j != i and highs[j] >= highs[i]:
            return False
    return True


@njit(cache=True)
def _check_swing_low_jit(lows: np.ndarray, i: int, length: int) -> bool:
    n = len(lows)
    if i < length or i >= n - length:
        return False
    for j in range(i - length, i + length + 1):
        if j != i and lows[j] <= lows[i]:
            return False
    return True


def detect_swings(df: pd.DataFrame, length: int = 21) -> List[Swing]:
    """Detect swings (fractal style) and label them HH/HL/LH/LL.

    Args:
        df: DataFrame with columns Time, High, Low
        length: lookback/forward bars to confirm a pivot

    Returns:
        List of Swing objects sorted by index (ascending)
    """
    swings: List[Swing] = []

    if len(df) < length * 2 + 1:
        return swings

    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    times = df['Time'].values

    last_swing_high = None
    last_swing_low = None

    for i in range(length, len(df) - length):
        if _check_swing_high_jit(highs, i, length):
            # Determine HH or LH
            if last_swing_high is None:
                swing_type = 'HH'
            elif highs[i] > last_swing_high.price:
                swing_type = 'HH'
            else:
                swing_type = 'LH'

            swings.append(Swing(
                type=swing_type,
                price=float(highs[i]),
                time=pd.to_datetime(times[i]).to_pydatetime(),
                index=int(i),
                is_high=True,
            ))
            last_swing_high = swings[-1]

        if _check_swing_low_jit(lows, i, length):
            # Determine HL or LL
            if last_swing_low is None:
                swing_type = 'LL'
            elif lows[i] > last_swing_low.price:
                swing_type = 'HL'
            else:
                swing_type = 'LL'

            swings.append(Swing(
                type=swing_type,
                price=float(lows[i]),
                time=pd.to_datetime(times[i]).to_pydatetime(),
                index=int(i),
                is_high=False,
            ))
            last_swing_low = swings[-1]

    swings.sort(key=lambda x: x.index)
    return swings


def to_json(swings: List[Swing], df: pd.DataFrame = None, times: np.ndarray = None) -> List[dict]:
    """Convert swings to JSON-serializable list for the frontend.

    Each item contains:
      - type: 'HH'|'HL'|'LH'|'LL'
      - price: float
      - time: timestamp (seconds)
      - index: int (bar index)
      - isHigh: bool
      - color: string
    """
    result = []
    if times is None and df is not None:
        times = df['Time'].values

    for s in swings:
        if times is not None and s.index < len(times):
            ts = int(pd.Timestamp(times[s.index]).timestamp())
        else:
            ts = int(s.time.timestamp())

        color = '#ff5252' if s.is_high else '#00bfa5'

        result.append({
            'type': s.type,
            'price': s.price,
            'time': ts,
            'index': s.index,
            'isHigh': s.is_high,
            'color': color,
        })
    return result


# Quick example when run standalone
if __name__ == '__main__':
    import pandas as pd
    import numpy as np

    dates = pd.date_range('2024-01-01', periods=300, freq='H')
    base = 100
    prices = base + np.cumsum(np.random.randn(300) * 0.3)
    df = pd.DataFrame({
        'Time': dates,
        'Open': prices,
        'High': prices + np.abs(np.random.randn(300) * 0.2),
        'Low': prices - np.abs(np.random.randn(300) * 0.2),
        'Close': prices + np.random.randn(300) * 0.05
    })

    sw = detect_swings(df, length=5)
    print(f'Detected {len(sw)} swings')
    print(to_json(sw, times=df['Time'].values))
