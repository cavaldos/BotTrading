"""
Key price levels of higher timeframes (e.g. 15m, then 10m) used for SL / TP.

Levels of one timeframe, built from the entry-timeframe (1m) candles:
- swing  swing highs / lows: a candle whose high (low) is above (below) the
         `swing_length` candles on each side
- eqhl   equal highs / lows (src/indicators/eqh_eql.py)
- ob     order blocks (src/indicators/order_block.py)
- fvg    fair value gaps (src/indicators/FVG.py)

Support = swing lows, EQL, bullish OBs, bullish FVGs. Resistance = swing highs,
EQH, bearish OBs, bearish FVGs. Each level is a price range [low, high]
(a swing or EQH/EQL has low == high).

A level is known from the close of the candle that confirms it (`start`) and is
used until a source candle trades through it (`end` = open time of the first
candle whose low goes below a support's low, or whose high goes above a
resistance's high). At a decision time only levels with
start <= decision <= end are used, so nothing from the future leaks in.

SL (LONG): beyond the nearest support below the entry (at least min_sl_pct away).
TP (LONG): at the nearest resistance above the entry that gives RR >= min_rr.
SHORT is the mirror.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
from numba import njit

from src.core.timeframe import convert_timeframe, timeframe_to_minutes
from src.indicators.eqh_eql import detect_eqh_eql
from src.indicators.FVG import detect_fvg
from src.indicators.order_block import detect_order_blocks

NEVER_NS = np.iinfo(np.int64).max
MINUTE_NS = 60 * 1_000_000_000
LEVEL_SOURCES = ("swing", "eqhl", "ob", "fvg")

SWING, EQHL, OB, FVG = 0, 1, 2, 3
_SUPPORT_NAMES = {SWING: "swing low", EQHL: "EQL", OB: "OB", FVG: "FVG"}
_RESISTANCE_NAMES = {SWING: "swing high", EQHL: "EQH", OB: "OB", FVG: "FVG"}

_BLOCK = 64          # level-timeframe candles per block when searching for the end time
_CACHE_SIZE = 8      # built level sets kept (one strategy per signal timeframe reuses them)


@dataclass(frozen=True)
class KeyLevelParams:
    """Settings that change which levels are found (also the cache key)."""
    sources: Tuple[str, ...] = LEVEL_SOURCES
    swing_length: int = 5
    ob_swing_length: int = 10
    ob_internal_length: int = 3
    ob_body_ratio: float = 0.0
    fvg_auto_threshold: bool = True
    eqhl_length: int = 3
    eqhl_threshold: float = 0.3


@dataclass
class LevelSide:
    """Support or resistance levels of one timeframe, sorted by start time (int64 ns)."""
    low: np.ndarray
    high: np.ndarray
    kind: np.ndarray
    start_ns: np.ndarray
    end_ns: np.ndarray

    def active(self, decision_ns: int, lookback_ns: int = 0) -> np.ndarray:
        """Indices of the levels known and not yet traded through at decision_ns."""
        end = int(np.searchsorted(self.start_ns, decision_ns, side="right"))
        start = 0
        if lookback_ns > 0:
            start = int(np.searchsorted(self.start_ns, decision_ns - lookback_ns, side="left"))
        if end <= start:
            return np.empty(0, dtype=np.int64)
        # traded through at or after the decision time = still there when the decision is made
        keep = self.end_ns[start:end] >= decision_ns
        return np.arange(start, end, dtype=np.int64)[keep]


@dataclass
class KeyLevels:
    timeframe: str
    bar_ns: int
    support: LevelSide
    resistance: LevelSide


# ==================== NUMBA HELPERS ====================

@njit(cache=True)
def _pivot_flags_jit(values: np.ndarray, length: int, is_high: bool) -> np.ndarray:
    """True where the value is strictly above (below) the `length` values on each side."""
    n = len(values)
    flags = np.zeros(n, dtype=np.bool_)
    for i in range(length, n - length):
        value = values[i]
        ok = True
        for j in range(i - length, i + length + 1):
            if j == i:
                continue
            if is_high:
                if values[j] >= value:
                    ok = False
                    break
            elif values[j] <= value:
                ok = False
                break
        flags[i] = ok
    return flags


@njit(cache=True)
def _end_times_jit(
    levels: np.ndarray,
    first_bars: np.ndarray,
    is_high: bool,
    bar_values: np.ndarray,
    block_values: np.ndarray,
    block: int,
    bar_start_src: np.ndarray,
    bar_end_src: np.ndarray,
    src_times: np.ndarray,
    src_values: np.ndarray,
    never_ns: np.int64,
) -> np.ndarray:
    """Open time of the first source candle, from first_bars[p] on, that trades through levels[p]."""
    n = len(bar_values)
    out = np.full(len(levels), never_ns, dtype=np.int64)
    for p in range(len(levels)):
        level = levels[p]
        i = first_bars[p]
        bar = -1
        while i < n:
            if i % block == 0:
                # skip a whole block when none of its candles goes through the level
                extreme = block_values[i // block]
                through = extreme > level if is_high else extreme < level
                if not through:
                    i += block
                    continue
            value = bar_values[i]
            through = value > level if is_high else value < level
            if through:
                bar = i
                break
            i += 1
        if bar < 0:
            continue
        for m in range(bar_start_src[bar], bar_end_src[bar]):
            value = src_values[m]
            through = value > level if is_high else value < level
            if through:
                out[p] = src_times[m]
                break
    return out


def _block_extremes(values: np.ndarray, is_high: bool) -> np.ndarray:
    blocks = (len(values) + _BLOCK - 1) // _BLOCK
    padded = np.full(blocks * _BLOCK, -np.inf if is_high else np.inf, dtype=np.float64)
    padded[: len(values)] = values
    shaped = padded.reshape(blocks, _BLOCK)
    return shaped.max(axis=1) if is_high else shaped.min(axis=1)


# ==================== BUILD ====================

_cache: "OrderedDict[tuple, Optional[KeyLevels]]" = OrderedDict()


def _cache_key(source_df: pd.DataFrame, timeframe: str, params: KeyLevelParams) -> tuple:
    times = pd.to_datetime(source_df["Time"])
    return (
        timeframe,
        params,
        len(source_df),
        int(times.iloc[0].value),
        int(times.iloc[-1].value),
        float(source_df["Close"].to_numpy(dtype=np.float64).sum()),
        float(source_df["Low"].to_numpy(dtype=np.float64).sum()),
    )


def build_key_levels(
    source_df: pd.DataFrame,
    timeframe: str,
    params: KeyLevelParams = KeyLevelParams(),
) -> Optional[KeyLevels]:
    """Key levels of `timeframe` built from finer candles (normally 1m).

    None when the data is too short or `timeframe` is not a multiple of the
    source candle size (e.g. 10m from 3m candles).
    """
    if source_df is None or source_df.empty:
        return None
    key = _cache_key(source_df, timeframe, params)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    result = _build_key_levels(source_df, timeframe, params)
    _cache[key] = result
    while len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)
    return result


def _build_key_levels(source_df: pd.DataFrame, timeframe: str, params: KeyLevelParams) -> Optional[KeyLevels]:
    tf_minutes = int(timeframe_to_minutes(timeframe, default=0) or 0)
    if tf_minutes <= 0:
        return None
    bar_ns = tf_minutes * MINUTE_NS

    src = source_df[["Time", "Open", "High", "Low", "Close"]].copy()
    src["Time"] = pd.to_datetime(src["Time"])
    src["Volume"] = source_df["Volume"].to_numpy() if "Volume" in source_df.columns else 0.0
    src = src.sort_values("Time").reset_index(drop=True)
    src_times = src["Time"].to_numpy(dtype="datetime64[ns]").astype(np.int64)
    if len(src_times) < 2:
        return None
    src_ns = int(np.median(np.diff(src_times)))
    if src_ns <= 0 or bar_ns < src_ns or bar_ns % src_ns != 0:
        return None

    bars = convert_timeframe(src, timeframe)
    if bars is None or bars.empty:
        return None
    bar_open_ns = bars["Time"].to_numpy(dtype="datetime64[ns]").astype(np.int64)
    # a bar counts only once its last source candle is in the data
    complete = bar_open_ns + bar_ns <= int(src_times[-1]) + src_ns
    bars = bars.loc[complete].reset_index(drop=True)
    bar_open_ns = bar_open_ns[complete]
    n = len(bars)
    if n < 3:
        return None

    highs = bars["High"].to_numpy(dtype=np.float64)
    lows = bars["Low"].to_numpy(dtype=np.float64)
    opens = bars["Open"].to_numpy(dtype=np.float64)
    closes = bars["Close"].to_numpy(dtype=np.float64)
    sources = {str(item).strip().lower() for item in params.sources}

    # (low, high, kind, confirm bar) per side
    found: dict[bool, List[Tuple[float, float, int, int]]] = {True: [], False: []}  # True = support

    if "swing" in sources:
        length = max(1, int(params.swing_length))
        for is_high, values in ((True, highs), (False, lows)):
            for pivot in np.flatnonzero(_pivot_flags_jit(values, length, is_high)):
                confirm = int(pivot) + length
                if confirm < n:
                    price = float(values[pivot])
                    found[not is_high].append((price, price, SWING, confirm))

    if "eqhl" in sources:
        for item in detect_eqh_eql(
            bars,
            bars_confirmation=max(1, int(params.eqhl_length)),
            threshold=float(params.eqhl_threshold),
        ):
            confirm = int(item.confirmed_index)
            if 0 <= confirm < n:
                level = float(item.level)
                found[item.type == "EQL"].append((level, level, EQHL, confirm))

    if "ob" in sources:
        ranges = highs - lows
        bodies = np.abs(closes - opens)
        for item in detect_order_blocks(
            bars,
            swing_length=max(1, int(params.ob_swing_length)),
            internal_length=max(1, int(params.ob_internal_length)),
            mitigation_mode="HIGHLOW",
        ):
            ob_idx = int(item.index)
            if params.ob_body_ratio > 0:
                if ranges[ob_idx] <= 0 or bodies[ob_idx] / ranges[ob_idx] <= params.ob_body_ratio:
                    continue
            confirm = int(item.break_index)
            if 0 <= confirm < n:
                found[item.bias == "BULLISH"].append((float(item.low), float(item.high), OB, confirm))

    if "fvg" in sources:
        for item in detect_fvg(bars, auto_threshold=bool(params.fvg_auto_threshold)):
            confirm = int(item.index)
            if 0 <= confirm < n:
                low = min(float(item.top), float(item.bottom))
                high = max(float(item.top), float(item.bottom))
                found[item.bias == "BULLISH"].append((low, high, FVG, confirm))

    bar_start_src = np.searchsorted(src_times, bar_open_ns, side="left").astype(np.int64)
    bar_end_src = np.searchsorted(src_times, bar_open_ns + bar_ns, side="left").astype(np.int64)
    src_highs = src["High"].to_numpy(dtype=np.float64)
    src_lows = src["Low"].to_numpy(dtype=np.float64)

    sides = {}
    for is_support, rows in found.items():
        is_high = not is_support
        if rows:
            low = np.array([row[0] for row in rows], dtype=np.float64)
            high = np.array([row[1] for row in rows], dtype=np.float64)
            kind = np.array([row[2] for row in rows], dtype=np.int8)
            confirm = np.array([row[3] for row in rows], dtype=np.int64)
        else:
            low = high = np.empty(0, dtype=np.float64)
            kind = np.empty(0, dtype=np.int8)
            confirm = np.empty(0, dtype=np.int64)
        bar_values = highs if is_high else lows
        # the search starts at the confirming candle itself: a level it already went through is never used
        end_ns = _end_times_jit(
            high if is_high else low,
            confirm,
            is_high,
            bar_values,
            _block_extremes(bar_values, is_high),
            _BLOCK,
            bar_start_src,
            bar_end_src,
            src_times,
            src_highs if is_high else src_lows,
            np.int64(NEVER_NS),
        )
        start_ns = bar_open_ns[confirm] + bar_ns if len(confirm) else np.empty(0, dtype=np.int64)
        order = np.argsort(start_ns, kind="stable")
        sides[is_support] = LevelSide(
            low=low[order],
            high=high[order],
            kind=kind[order],
            start_ns=start_ns[order].astype(np.int64),
            end_ns=end_ns[order],
        )

    return KeyLevels(timeframe=timeframe, bar_ns=bar_ns, support=sides[True], resistance=sides[False])


# ==================== PICK SL / TP ====================

def _label(levels: KeyLevels, is_support: bool, kind: int) -> str:
    names = _SUPPORT_NAMES if is_support else _RESISTANCE_NAMES
    return f"{levels.timeframe} {names.get(int(kind), '?')}"


def pick_stop(
    levels: KeyLevels,
    *,
    entry: float,
    trade_type: str,
    decision_ns: int,
    buffer_pct: float,
    min_sl_pct: float,
    max_sl_pct: float = 0.0,
    lookback_bars: int = 0,
) -> Optional[Tuple[float, str]]:
    """(SL, label) beyond the nearest support below (LONG) / resistance above (SHORT)
    the entry that is at least min_sl_pct away; None when there is none or the
    nearest one is wider than max_sl_pct (> 0)."""
    is_long = trade_type == "LONG"
    side = levels.support if is_long else levels.resistance
    idx = side.active(decision_ns, max(0, int(lookback_bars)) * levels.bar_ns)
    if len(idx) == 0 or entry <= 0:
        return None
    buffer = max(0.0, float(buffer_pct)) / 100.0
    if is_long:
        edge = side.low[idx]
        stops = edge * (1.0 - buffer)
        distance_pct = (entry - stops) / entry * 100.0
        ok = edge < entry
    else:
        edge = side.high[idx]
        stops = edge * (1.0 + buffer)
        distance_pct = (stops - entry) / entry * 100.0
        ok = edge > entry
    ok &= distance_pct >= float(min_sl_pct) - 1e-12
    candidates = np.flatnonzero(ok)
    if len(candidates) == 0:
        return None
    best = candidates[int(np.argmin(distance_pct[candidates]))]
    if max_sl_pct > 0 and distance_pct[best] > max_sl_pct:
        return None
    return float(stops[best]), _label(levels, is_long, side.kind[idx[best]])


def pick_target(
    levels: KeyLevels,
    *,
    entry: float,
    stop_loss: float,
    trade_type: str,
    decision_ns: int,
    buffer_pct: float,
    min_rr: float,
    lookback_bars: int = 0,
) -> Optional[Tuple[float, float, str]]:
    """(TP, RR, label) at the nearest resistance above (LONG) / support below (SHORT)
    the entry whose RR is at least min_rr (0 = the nearest one)."""
    is_long = trade_type == "LONG"
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None
    side = levels.resistance if is_long else levels.support
    idx = side.active(decision_ns, max(0, int(lookback_bars)) * levels.bar_ns)
    if len(idx) == 0:
        return None
    buffer = max(0.0, float(buffer_pct)) / 100.0
    if is_long:
        targets = side.low[idx] * (1.0 - buffer)   # TP just before the near edge
        reward = targets - entry
    else:
        targets = side.high[idx] * (1.0 + buffer)
        reward = entry - targets
    rr = reward / risk
    candidates = np.flatnonzero((reward > 0) & (rr >= max(0.0, float(min_rr)) - 1e-9))
    if len(candidates) == 0:
        return None
    best = candidates[int(np.argmin(reward[candidates]))]
    return float(targets[best]), float(rr[best]), _label(levels, not is_long, side.kind[idx[best]])
