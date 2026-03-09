"""
Inverse Fair Value Gap (IFVG) Indicator
======================================
Detects FVG zones that later get broken and flip bias.

Bullish IFVG:
  - source zone is a bearish FVG
  - a later candle closes above the source zone upper bound

Bearish IFVG:
  - source zone is a bullish FVG
  - a later candle closes below the source zone lower bound

The IFVG keeps the original FVG price range, but its activation time is the bar
that flips the zone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import numpy as np
import pandas as pd
from numba import njit

from src.indicators.FVG import FairValueGap, detect_fvg


@dataclass
class InverseFairValueGap:
    """Represents an FVG that has flipped into an IFVG."""

    top: float
    bottom: float
    bias: str
    time: datetime
    index: int
    start_time: datetime
    start_index: int
    source_time: datetime
    source_index: int
    source_bias: str
    is_filled: bool = False


@njit(cache=True)
def _check_ifvg_filled_jit(
    zone_top: float,
    zone_bottom: float,
    zone_bias: int,
    start_idx: int,
    highs: np.ndarray,
    lows: np.ndarray,
    n: int,
) -> bool:
    """Check whether an activated IFVG gets mitigated later."""
    upper = max(zone_top, zone_bottom)
    lower = min(zone_top, zone_bottom)

    for i in range(start_idx + 1, n):
        if zone_bias == 1:
            if lows[i] < lower:
                return True
        else:
            if highs[i] > upper:
                return True
    return False


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, np.integer)):
        return datetime.fromtimestamp(int(value))
    if pd.isna(value):
        return datetime.fromtimestamp(0)
    ts = pd.Timestamp(value)
    return datetime.fromtimestamp(int(ts.value // 1_000_000_000))


def _to_unix_timestamp(value) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, (int, np.integer)):
        return int(value)
    if pd.isna(value):
        return 0
    ts = pd.Timestamp(value)
    return int(ts.value // 1_000_000_000)


def _find_flip_index(
    fvg: FairValueGap,
    closes: np.ndarray,
    n: int,
) -> int:
    """Return first bar index where the source FVG flips into an IFVG."""
    upper = max(fvg.top, fvg.bottom)
    lower = min(fvg.top, fvg.bottom)

    for i in range(int(fvg.index) + 1, n):
        if fvg.bias == 'BEARISH' and closes[i] > upper:
            return i
        if fvg.bias == 'BULLISH' and closes[i] < lower:
            return i
    return -1


def detect_ifvg(
    df: pd.DataFrame,
    auto_threshold: bool = True,
) -> List[InverseFairValueGap]:
    """Detect inverse fair value gaps from source FVGs."""
    df = df.sort_index()

    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    times = np.asarray(df['Time'].to_numpy())
    n = len(df)

    fvg_list = detect_fvg(df, auto_threshold=auto_threshold)
    results: List[InverseFairValueGap] = []

    for fvg in fvg_list:
        flip_index = _find_flip_index(fvg, closes, n)
        if flip_index < 0:
            continue

        result_bias = 'BULLISH' if fvg.bias == 'BEARISH' else 'BEARISH'
        start_index = max(0, int(fvg.index) - 2)
        bias_code = 1 if result_bias == 'BULLISH' else 0
        is_filled = _check_ifvg_filled_jit(
            float(fvg.top),
            float(fvg.bottom),
            bias_code,
            flip_index,
            highs,
            lows,
            n,
        )

        results.append(
            InverseFairValueGap(
                top=float(fvg.top),
                bottom=float(fvg.bottom),
                bias=result_bias,
                time=_to_datetime(times[flip_index]),
                index=flip_index,
                start_time=_to_datetime(times[start_index]),
                start_index=start_index,
                source_time=fvg.time,
                source_index=int(fvg.index),
                source_bias=fvg.bias,
                is_filled=is_filled,
            )
        )

    return results


def to_json(
    ifvg_list: List[InverseFairValueGap],
    times: Optional[np.ndarray] = None,
) -> List[dict]:
    """Convert IFVG items to chart-friendly JSON."""
    result = []

    for ifvg in ifvg_list:
        if times is not None and len(times) > 0:
            time_ts = _to_unix_timestamp(times[ifvg.index]) if ifvg.index < len(times) else _to_unix_timestamp(ifvg.time)
            start_ts = _to_unix_timestamp(times[ifvg.start_index]) if ifvg.start_index < len(times) else _to_unix_timestamp(ifvg.start_time)
            source_ts = _to_unix_timestamp(times[ifvg.source_index]) if ifvg.source_index < len(times) else _to_unix_timestamp(ifvg.source_time)
        else:
            time_ts = _to_unix_timestamp(ifvg.time)
            start_ts = _to_unix_timestamp(ifvg.start_time)
            source_ts = _to_unix_timestamp(ifvg.source_time)

        is_bullish = ifvg.bias == 'BULLISH'
        result.append({
            'time': time_ts,
            'startTime': start_ts,
            'sourceTime': source_ts,
            'top': ifvg.top,
            'bottom': ifvg.bottom,
            'bias': ifvg.bias,
            'sourceBias': ifvg.source_bias,
            'isFilled': ifvg.is_filled,
            'color': 'rgba(189, 189, 189, 0.12)',
            'borderColor': 'rgba(76, 175, 80, 0.70)' if is_bullish else 'rgba(255, 112, 67, 0.75)',
            'labelColor': 'rgba(76, 175, 80, 0.95)' if is_bullish else 'rgba(255, 112, 67, 0.95)',
        })

    return result


if __name__ == '__main__':
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=250, freq='H')
    base = 100.0
    prices = base + np.cumsum(np.random.randn(250) * 0.45)
    df = pd.DataFrame(
        {
            'Time': dates,
            'Open': prices,
            'High': prices + np.abs(np.random.randn(250) * 0.35),
            'Low': prices - np.abs(np.random.randn(250) * 0.35),
            'Close': prices + np.random.randn(250) * 0.12,
        }
    )

    items = detect_ifvg(df)
    print(f'Detected {len(items)} IFVG events')
    for item in items[:5]:
        print(f'  {item.bias} source={item.source_index} flip={item.index}')
