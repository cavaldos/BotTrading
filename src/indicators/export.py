"""
Shared helpers to run SMC indicators and convert them to chart JSON.

Used by `src/indicators/main.py` (full export for the Indicators page) and by
the live snapshot feature (`src/live/core/snapshot.py`). Loads no config at
import time, so it is safe to use inside the live container.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable, List, Optional

import numpy as np
import pandas as pd

from src.indicators.BOS import detect_bos, to_json as bos_to_json
from src.indicators.CHOCH import detect_choch, to_json as choch_to_json
from src.indicators.order_block import detect_order_blocks, to_json as ob_to_json
from src.indicators.FVG import detect_fvg, to_json as fvg_to_json
from src.indicators.ifvg import detect_ifvg, to_json as ifvg_to_json
from src.indicators.eqh_eql import detect_eqh_eql, to_json as eqhl_to_json
from src.indicators.imbalance import detect_imbalance, to_json as imbalance_to_json
from src.indicators.breaker_block import detect_breaker_blocks, to_json as breaker_to_json
from src.indicators.mitigation_block import detect_mitigation_blocks, to_json as mitigation_to_json
from src.indicators.retracement import detect_retracements, to_json as retracement_to_json
from src.indicators.swing import detect_swings, to_json as swing_to_json
from src.indicators.gann_swing import detect_gann_swings, to_json as gann_swing_to_json


COLORS = {
    'bullish_candle': '#26a69a',
    'bearish_candle': '#ef5350',
}

# Order matches the Indicators page JSON layout.
ALL_INDICATORS = (
    'bos', 'choch', 'orderBlocks', 'fvg', 'ifvg', 'eqhEql', 'imbalance',
    'breakerBlocks', 'mitigationBlocks', 'retracement', 'swing', 'gannSwing',
)


def datetime_to_timestamp(dt) -> int:
    """Convert datetime to Unix timestamp (seconds)."""
    if dt is None:
        return 0
    if isinstance(dt, (pd.Timestamp, datetime)):
        if pd.isna(dt):
            return 0
        return int(dt.timestamp())
    if isinstance(dt, np.datetime64):
        ts = pd.Timestamp(dt)
        if pd.isna(ts):
            return 0
        return int(ts.value // 1_000_000_000)
    try:
        if pd.isna(dt):
            return 0
    except (TypeError, ValueError):
        pass
    return int(dt)


def export_candles(df: pd.DataFrame) -> List[dict]:
    """Export candlestick data in lightweight-charts format."""
    times = np.asarray(df['Time'].to_numpy())
    opens = df['Open'].values
    highs = df['High'].values
    lows = df['Low'].values
    closes = df['Close'].values
    return [
        {
            'time': datetime_to_timestamp(times[i]),
            'open': float(opens[i]),
            'high': float(highs[i]),
            'low': float(lows[i]),
            'close': float(closes[i]),
        }
        for i in range(len(df))
    ]


def export_volume(df: pd.DataFrame) -> List[dict]:
    """Export volume data in lightweight-charts histogram format."""
    times = np.asarray(df['Time'].to_numpy())
    opens = df['Open'].values
    closes = df['Close'].values
    volumes = df['Volume'].values if 'Volume' in df.columns else np.zeros(len(df))
    return [
        {
            'time': datetime_to_timestamp(times[i]),
            'value': float(volumes[i]),
            'color': COLORS['bullish_candle'] if closes[i] >= opens[i] else COLORS['bearish_candle'],
        }
        for i in range(len(df))
    ]


def run_indicators(
    df: pd.DataFrame,
    include: Optional[Iterable[str]] = None,
    *,
    swing_length: Optional[int] = None,
    internal_length: Optional[int] = None,
    mitigation_mode: Optional[str] = None,
    eqhl_length: Optional[int] = None,
    eqhl_threshold: Optional[float] = None,
    fvg_auto_threshold: Optional[bool] = None,
    swing_pivot_length: Optional[int] = None,
    gann_swing_length: int = 3,
    verbose: bool = False,
) -> dict:
    """
    Run SMC indicators on a DataFrame and return chart-ready JSON lists.

    Parameters left as None fall back to each indicator's own defaults
    (same output as the Indicators page). Pass strategy values to match
    what the live strategy sees.
    """
    wanted = list(ALL_INDICATORS) if include is None else [k for k in ALL_INDICATORS if k in set(include)]
    times = np.asarray(df['Time'].to_numpy())

    structure_kwargs = {}
    if swing_length is not None:
        structure_kwargs['swing_length'] = int(swing_length)
    if internal_length is not None:
        structure_kwargs['internal_length'] = int(internal_length)

    ob_kwargs = dict(structure_kwargs)
    if mitigation_mode is not None:
        ob_kwargs['mitigation_mode'] = mitigation_mode

    eqhl_kwargs = {}
    if eqhl_length is not None:
        eqhl_kwargs['bars_confirmation'] = int(eqhl_length)
    if eqhl_threshold is not None:
        eqhl_kwargs['threshold'] = float(eqhl_threshold)

    gap_kwargs = {}
    if fvg_auto_threshold is not None:
        gap_kwargs['auto_threshold'] = bool(fvg_auto_threshold)

    swing_kwargs = {}
    if swing_pivot_length is not None:
        swing_kwargs['length'] = int(swing_pivot_length)

    runners: dict[str, tuple[str, Callable[[], tuple[int, list]]]] = {
        'bos': ('BOS', lambda: _run(detect_bos(df, **structure_kwargs), lambda r: bos_to_json(r, times=times))),
        'choch': ('CHoCH', lambda: _run(detect_choch(df, **structure_kwargs), lambda r: choch_to_json(r, times=times))),
        'orderBlocks': ('Order Blocks', lambda: _run(detect_order_blocks(df, **ob_kwargs), lambda r: ob_to_json(r, df=df))),
        'fvg': ('FVG', lambda: _run(detect_fvg(df, **gap_kwargs), lambda r: fvg_to_json(r, times=times))),
        'ifvg': ('IFVG', lambda: _run(detect_ifvg(df, **gap_kwargs), lambda r: ifvg_to_json(r, times=times))),
        'eqhEql': ('EQH/EQL', lambda: _run(detect_eqh_eql(df, **eqhl_kwargs), lambda r: eqhl_to_json(r, times=times))),
        'imbalance': ('Imbalance', lambda: _run(detect_imbalance(df), imbalance_to_json)),
        'breakerBlocks': ('Breaker Blocks', lambda: _run(detect_breaker_blocks(df), lambda r: breaker_to_json(r, df=df))),
        'mitigationBlocks': ('Mitigation Blocks', lambda: _run(detect_mitigation_blocks(df), lambda r: mitigation_to_json(r, df=df))),
        'retracement': ('Retracement', lambda: _run(detect_retracements(df), lambda r: retracement_to_json(r, times=times))),
        'swing': ('Swings', lambda: _run(detect_swings(df, **swing_kwargs), lambda r: swing_to_json(r, times=times))),
        'gannSwing': (
            f'Gann Swing (len={gann_swing_length})',
            lambda: _run(detect_gann_swings(df, length=gann_swing_length), lambda r: gann_swing_to_json(r, times=list(times))),
        ),
    }

    results: dict = {}
    for key in wanted:
        label, runner = runners[key]
        if verbose:
            print(f"    {label}...", end='', flush=True)
        count, payload = runner()
        results[key] = payload
        if verbose:
            print(f" {count} found")
    return results


def _run(raw: list, convert: Callable[[list], list]) -> tuple[int, list]:
    return len(raw), convert(raw)
