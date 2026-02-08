"""
FVG (Fair Value Gap) Indicator
==============================
Detects Fair Value Gaps from 3 consecutive candles, matching LuxAlgo smc.pine.

Pine logic (drawFairValueGaps in docs/strategy/smc.pine):
  - Bullish FVG: currentLow > last2High AND lastClose > last2High AND barDeltaPercent > threshold (and newTimeframe)
  - Bearish FVG: currentHigh < last2Low AND lastClose < last2Low AND -barDeltaPercent > threshold (and newTimeframe)
  - barDeltaPercent = (lastClose - lastOpen) / (lastOpen * 100)  [middle candle body %]
  - Auto threshold: threshold = ta.cum(abs(barDeltaPercent)) / bar_index * 2; when false, threshold = 0
  - Same-timeframe: no request.security; use current series. newTimeframe treated as true every bar.

Usage:
    from src.indicators.FVG import detect_fvg, FairValueGap, to_json

    fvgs = detect_fvg(df, auto_threshold=True)
    json_data = to_json(fvgs, times=df['Time'].values)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Tuple
from dataclasses import dataclass
from numba import njit


# ==================== DATA CLASS ====================

@dataclass
class FairValueGap:
    """Represents a Fair Value Gap."""
    top: float
    bottom: float
    bias: str           # 'BULLISH' or 'BEARISH'
    time: datetime
    index: int
    is_filled: bool = False


# ==================== NUMBA JIT FUNCTIONS ====================

@njit(cache=True)
def _detect_fvg_jit(
    highs: np.ndarray,
    lows: np.ndarray,
    opens: np.ndarray,
    closes: np.ndarray,
    use_auto_threshold: bool,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Detect FVGs matching Pine drawFairValueGaps (same-timeframe).

    At bar i (i >= 2):
      lastClose = closes[i-1], lastOpen = opens[i-1]
      currentHigh = highs[i], currentLow = lows[i]
      last2High = highs[i-2], last2Low = lows[i-2]
      barDeltaPercent = (lastClose - lastOpen) / (lastOpen * 100)  [avoid div by zero]
      If use_auto_threshold: cum += abs(barDeltaPercent), threshold = cum / (i+1) * 2
      Bullish: currentLow > last2High and lastClose > last2High and barDeltaPercent > threshold
      Bearish: currentHigh < last2Low and lastClose < last2Low and -barDeltaPercent > threshold

    Returns:
        fvg_indices, fvg_tops, fvg_bottoms, fvg_biases (1=BULLISH, 0=BEARISH)
    """
    n = len(highs)
    max_fvg = n
    fvg_indices = np.empty(max_fvg, dtype=np.int64)
    fvg_tops = np.empty(max_fvg, dtype=np.float64)
    fvg_bottoms = np.empty(max_fvg, dtype=np.float64)
    fvg_biases = np.empty(max_fvg, dtype=np.int8)
    count = 0

    cum_abs_delta = 0.0

    for i in range(2, n):
        last_open = opens[i - 1]
        last_close = closes[i - 1]
        last2_high = highs[i - 2]
        last2_low = lows[i - 2]
        curr_high = highs[i]
        curr_low = lows[i]

        # Middle bar body % (Pine: barDeltaPercent). Avoid div by zero.
        if last_open != 0:
            bar_delta_pct = (last_close - last_open) / (last_open * 100.0)
        else:
            bar_delta_pct = 0.0

        if use_auto_threshold:
            cum_abs_delta += abs(bar_delta_pct)
            # Pine: threshold = ta.cum(...) / bar_index * 2. At bar i, bar_index = i.
            threshold = (cum_abs_delta / i) * 2.0
        else:
            threshold = 0.0

        # Bullish FVG: gap up, middle close above gap top, optional filter
        if curr_low > last2_high and last_close > last2_high and bar_delta_pct > threshold:
            fvg_indices[count] = i
            fvg_tops[count] = curr_low
            fvg_bottoms[count] = last2_high
            fvg_biases[count] = 1
            count += 1

        # Bearish FVG: gap down, middle close below gap bottom
        if curr_high < last2_low and last_close < last2_low and -bar_delta_pct > threshold:
            fvg_indices[count] = i
            fvg_tops[count] = last2_low
            fvg_bottoms[count] = curr_high
            fvg_biases[count] = 0
            count += 1

    return (
        fvg_indices[:count].copy(),
        fvg_tops[:count].copy(),
        fvg_bottoms[:count].copy(),
        fvg_biases[:count].copy(),
    )


@njit(cache=True)
def _check_fvg_filled_jit(
    fvg_top: float,
    fvg_bottom: float,
    fvg_bias: int,
    fvg_index: int,
    highs: np.ndarray,
    lows: np.ndarray,
    n: int,
) -> bool:
    """True if FVG has been filled by subsequent price (Pine: deleteFairValueGaps logic)."""
    for i in range(fvg_index + 1, n):
        if fvg_bias == 1:  # BULLISH: filled when low <= bottom
            if lows[i] <= fvg_bottom:
                return True
        else:  # BEARISH: filled when high >= top
            if highs[i] >= fvg_top:
                return True
    return False


# ==================== MAIN DETECTION FUNCTION ====================

def detect_fvg(
    df: pd.DataFrame,
    auto_threshold: bool = True,
) -> List[FairValueGap]:
    """
    Detect Fair Value Gaps (3-candle imbalance). Matches smc.pine drawFairValueGaps.

    Args:
        df: DataFrame with columns: Time, Open, High, Low, Close
        auto_threshold: If True, filter by barDeltaPercent > threshold (Pine: Fair Value Gaps Auto Threshold)

    Returns:
        List of FairValueGap objects
    """
    df = df.sort_index()

    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    opens = df['Open'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    times = df['Time'].values
    n = len(highs)

    fvg_indices, fvg_tops, fvg_bottoms, fvg_biases = _detect_fvg_jit(
        highs, lows, opens, closes, auto_threshold
    )

    results = []
    for i in range(len(fvg_indices)):
        idx = int(fvg_indices[i])
        bias_val = int(fvg_biases[i])
        top_val = float(fvg_tops[i])
        bottom_val = float(fvg_bottoms[i])

        is_filled = _check_fvg_filled_jit(
            top_val, bottom_val, bias_val, idx, highs, lows, n
        )

        t = times[idx]
        if isinstance(t, (int, np.integer)):
            dt = datetime.fromtimestamp(int(t))
        else:
            dt = pd.Timestamp(t).to_pydatetime()

        results.append(
            FairValueGap(
                top=top_val,
                bottom=bottom_val,
                bias='BULLISH' if bias_val == 1 else 'BEARISH',
                time=dt,
                index=idx,
                is_filled=is_filled,
            )
        )

    return results


# ==================== JSON EXPORT ====================

def to_json(
    fvg_list: List[FairValueGap],
    df: pd.DataFrame = None,
    times: np.ndarray = None,
) -> List[dict]:
    """
    Format for lightweight-charts. If `times` is provided, use same timestamp
    conversion as candle export for alignment.
    """
    if times is None and df is not None:
        times = df['Time'].values

    result = []
    for fvg in fvg_list:
        if times is not None and len(times) > 0 and fvg.index < len(times):
            time_ts = int(pd.Timestamp(times[fvg.index]).timestamp())
        else:
            time_ts = int(fvg.time.timestamp())

        # Pine: fairValueGapsBullColor = #00ff68 (70), fairValueGapsBearColor = #ff0008 (70)
        color = (
            'rgba(0, 255, 104, 0.3)' if fvg.bias == 'BULLISH'
            else 'rgba(255, 0, 8, 0.3)'
        )

        result.append({
            'time': time_ts,
            'top': fvg.top,
            'bottom': fvg.bottom,
            'bias': fvg.bias,
            'isFilled': fvg.is_filled,
            'color': color,
        })
    return result


# ==================== EXAMPLE ====================

if __name__ == "__main__":
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=200, freq='H')
    base = 100
    prices = base + np.cumsum(np.random.randn(200) * 0.5)

    df = pd.DataFrame({
        'Time': dates,
        'Open': prices,
        'High': prices + np.abs(np.random.randn(200) * 0.3),
        'Low': prices - np.abs(np.random.randn(200) * 0.3),
        'Close': prices + np.random.randn(200) * 0.1,
    })

    fvgs = detect_fvg(df, auto_threshold=True)
    print(f"Detected {len(fvgs)} FVGs")
    for f in fvgs[:5]:
        print(f"  {f.bias} @ bar {f.index} top={f.top:.2f} bottom={f.bottom:.2f} filled={f.is_filled}")

    json_data = to_json(fvgs, times=df['Time'].values)
    print(f"\nJSON export: {len(json_data)} events")
