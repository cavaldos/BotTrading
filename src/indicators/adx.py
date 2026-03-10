"""
ADX (Average Directional Index) Indicator
=========================================
Computes ADX (+DI / -DI) using Wilder smoothing with Numba JIT.

Usage:
    from src.indicators.adx import compute_adx, compute_adx_jit

    adx, plus_di, minus_di = compute_adx(df, period=14)
"""

import numpy as np
import pandas as pd
from numba import njit


@njit(cache=True)
def compute_adx_jit(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Compute ADX, +DI, -DI with Wilder's smoothing.

    Args:
        highs: High prices (float64).
        lows: Low prices (float64).
        closes: Close prices (float64).
        period: ADX length (default commonly 14).

    Returns:
        (adx, plus_di, minus_di), each same length as input.
        Warm-up values are NaN until enough bars are available.
    """
    n = len(closes)
    adx = np.empty(n, dtype=np.float64)
    plus_di = np.empty(n, dtype=np.float64)
    minus_di = np.empty(n, dtype=np.float64)

    for i in range(n):
        adx[i] = np.nan
        plus_di[i] = np.nan
        minus_di[i] = np.nan

    if n == 0:
        return adx, plus_di, minus_di

    p = 1 if period < 1 else period
    if n <= p:
        return adx, plus_di, minus_di

    tr = np.zeros(n, dtype=np.float64)
    plus_dm = np.zeros(n, dtype=np.float64)
    minus_dm = np.zeros(n, dtype=np.float64)
    dx = np.empty(n, dtype=np.float64)
    for i in range(n):
        dx[i] = np.nan

    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        if up_move > down_move and up_move > 0.0:
            plus_dm[i] = up_move
        if down_move > up_move and down_move > 0.0:
            minus_dm[i] = down_move

        tr0 = highs[i] - lows[i]
        tr1 = abs(highs[i] - closes[i - 1])
        tr2 = abs(lows[i] - closes[i - 1])
        tr[i] = max(tr0, tr1, tr2)

    atr = 0.0
    plus_sm = 0.0
    minus_sm = 0.0

    for i in range(1, p + 1):
        atr += tr[i]
        plus_sm += plus_dm[i]
        minus_sm += minus_dm[i]

    if atr > 0.0:
        plus_di[p] = 100.0 * (plus_sm / atr)
        minus_di[p] = 100.0 * (minus_sm / atr)
        di_sum = plus_di[p] + minus_di[p]
        if di_sum > 0.0:
            dx[p] = 100.0 * abs(plus_di[p] - minus_di[p]) / di_sum

    for i in range(p + 1, n):
        atr = atr - (atr / p) + tr[i]
        plus_sm = plus_sm - (plus_sm / p) + plus_dm[i]
        minus_sm = minus_sm - (minus_sm / p) + minus_dm[i]

        if atr <= 0.0:
            continue

        plus_di[i] = 100.0 * (plus_sm / atr)
        minus_di[i] = 100.0 * (minus_sm / atr)

        di_sum = plus_di[i] + minus_di[i]
        if di_sum <= 0.0:
            continue
        dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / di_sum

    first_adx_idx = 2 * p - 1
    if first_adx_idx >= n:
        return adx, plus_di, minus_di

    dx_sum = 0.0
    valid_count = 0
    for i in range(p, first_adx_idx + 1):
        if np.isfinite(dx[i]):
            dx_sum += dx[i]
            valid_count += 1

    if valid_count == 0:
        return adx, plus_di, minus_di

    adx[first_adx_idx] = dx_sum / valid_count

    for i in range(first_adx_idx + 1, n):
        if not np.isfinite(dx[i]):
            continue
        prev = adx[i - 1]
        if not np.isfinite(prev):
            prev = adx[first_adx_idx]
        adx[i] = ((prev * (p - 1)) + dx[i]) / p

    return adx, plus_di, minus_di


def compute_adx(df_or_arrays, period: int = 14) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Convenience wrapper for compute_adx_jit.

    Args:
        df_or_arrays: DataFrame with High/Low/Close columns,
                      or tuple/list (highs, lows, closes).
        period: ADX period (default 14).

    Returns:
        (adx, plus_di, minus_di)
    """
    if isinstance(df_or_arrays, pd.DataFrame):
        highs = df_or_arrays['High'].values.astype(np.float64)
        lows = df_or_arrays['Low'].values.astype(np.float64)
        closes = df_or_arrays['Close'].values.astype(np.float64)
    else:
        highs = np.array(df_or_arrays[0], dtype=np.float64)
        lows = np.array(df_or_arrays[1], dtype=np.float64)
        closes = np.array(df_or_arrays[2], dtype=np.float64)

    return compute_adx_jit(highs, lows, closes, period)
