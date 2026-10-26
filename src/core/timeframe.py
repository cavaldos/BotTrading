"""
Timeframe helpers shared by every module: parse "5m" / "1h" strings and
resample OHLCV candles to a higher timeframe.

    timeframe_to_minutes("15m")             -> 15
    timeframe_to_minutes("bad", default=1)  -> 1   (no default: ValueError)
    normalize_timeframes(["1m", " 5m", ""]) -> ["1m", "5m"]
    convert_timeframe(df_1m, "5m")          -> 5m candles (label = candle open time)

CLI: python -m src.core.timeframe <input_folder> <output_folder> <timeframe>
"""

import pandas as pd
import os
import re
import argparse
import shutil
from io import StringIO
from typing import Any, Optional


TIMEFRAME_TO_MINUTES = {
    '1m': 1, '1min': 1,
    '2m': 2, '2min': 2,
    '3m': 3, '3min': 3,
    '5m': 5, '5min': 5,
    '15m': 15, '15min': 15,
    '30m': 30, '30min': 30,
    '1h': 60, '1H': 60,
    '2h': 120, '2H': 120,
    '4h': 240, '4H': 240,
    '6h': 360, '6H': 360,
    '8h': 480, '8H': 480,
    '12h': 720, '12H': 720,
    '1d': 1440, '1D': 1440,
    '1w': 10080, '1W': 10080,
}

_UNIT_MINUTES = {'m': 1, 'min': 1, 'h': 60, 'd': 1440, 'w': 10080}
_TIMEFRAME_RE = re.compile(r'^(\d+)\s*(min|m|h|d|w)$', re.IGNORECASE)
_RAISE = object()


def timeframe_to_minutes(timeframe: Any, default: Any = _RAISE) -> Any:
    """'5m' -> 5, '1h' -> 60, '1d' -> 1440, '1w' -> 10080.

    An invalid timeframe returns `default`, or raises ValueError when no default is given.
    """
    text = str(timeframe or '').strip()
    match = _TIMEFRAME_RE.match(text)
    if match:
        return int(match.group(1)) * _UNIT_MINUTES[match.group(2).lower()]
    if default is _RAISE:
        raise ValueError(f"Invalid timeframe: {timeframe!r} (expected e.g. '1m', '5m', '1h', '1d')")
    return default


def parse_timeframe_minutes(tf_str) -> Optional[int]:
    """Minutes of a timeframe, or None when it is not a timeframe."""
    return timeframe_to_minutes(tf_str, default=None)


def normalize_timeframes(value: Any, default: Optional[list[str]] = None) -> list[str]:
    """A timeframe or a list of them -> clean list ('5m' -> ['5m']). Empty -> `default` (or [])."""
    if isinstance(value, str):
        value = [value]
    items = [str(item).strip() for item in value] if isinstance(value, (list, tuple)) else []
    items = [item for item in items if item]
    return items or list(default or [])


def detect_timeframe_from_filename(filename):
    match = re.search(r'_(\d+[mhdwMHDW]|1min|3min|5min|15min|30min)\.csv$', filename, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def convert_timeframe(df, timeframe):
    """
    Convert OHLCV data sang timeframe mới
    
    Parameters:
    -----------
    df : pandas DataFrame
        DataFrame chứa dữ liệu OHLCV với cột Time, Open, High, Low, Close, Volume
    timeframe : str
        Timeframe mới: '5min', '15min', '30min', '1H', '4H', '1D', etc.
    
    Returns:
    --------
    pandas DataFrame với timeframe mới
    """
    
    # Đảm bảo cột Time là datetime và set làm index
    df = df.copy()
    df['Time'] = pd.to_datetime(df['Time'])
    df.sort_values('Time', inplace=True)
    df.set_index('Time', inplace=True)
    
    # Quy tắc resample cho OHLCV
    ohlcv_rules = {
        'Open': 'first',    # Giá mở cửa đầu tiên
        'High': 'max',      # Giá cao nhất
        'Low': 'min',       # Giá thấp nhất
        'Close': 'last',    # Giá đóng cửa cuối cùng
        'Volume': 'sum'     # Tổng khối lượng
    }
    
    # Normalize timeframe to pandas resample rule
    # Note: lowercase 'm' can be interpreted as month-related alias in newer pandas.
    # For minutes, always use 'min' to avoid FutureWarning.
    tf_match = re.match(r'^\s*(\d+)\s*(m|min|h|H|d|D|w|W)\s*$', str(timeframe))
    if tf_match:
        tf_value = int(tf_match.group(1))
        tf_unit = tf_match.group(2).lower()
        if tf_unit in ('m', 'min'):
            resample_rule = f"{tf_value}min"
        elif tf_unit == 'h':
            resample_rule = f"{tf_value}h"
        elif tf_unit == 'd':
            resample_rule = f"{tf_value}d"
        else:
            resample_rule = f"{tf_value}W"
    else:
        resample_rule = timeframe

    # Resample theo timeframe mới.
    # Dữ liệu nguồn trong project đang dùng timestamp là thời gian mở nến,
    # nên resample phải giữ nhãn bucket ở cạnh trái để 5m `15:30` đại diện
    # cho cụm 15:30 -> 15:34.
    df_resampled = df.resample(resample_rule, label='left', closed='left').agg(ohlcv_rules)
    
    # Loại bỏ các hàng có giá trị NaN (nếu có)
    df_resampled.dropna(inplace=True)
    
    # Reset index để Time trở thành cột
    df_resampled.reset_index(inplace=True)
    
    return df_resampled


def batch_convert(input_folder, output_folder, target_tf):
    """
    Đọc tất cả file CSV trong input_folder, convert sang target_tf,
    và lưu vào output_folder. Nếu timeframe gốc >= target_tf thì giữ nguyên (copy).
    """
    os.makedirs(output_folder, exist_ok=True)

    target_minutes = parse_timeframe_minutes(target_tf)
    if target_minutes is None:
        print(f"[ERROR] Không nhận diện được timeframe: {target_tf}")
        return

    resample_tf = target_tf
    for key, val in TIMEFRAME_TO_MINUTES.items():
        if val == target_minutes and ('min' in key or 'H' in key or 'D' in key or 'W' in key):
            resample_tf = key
            break

    csv_files = sorted([f for f in os.listdir(input_folder) if f.endswith('.csv')])
    if not csv_files:
        print(f"[WARNING] Không tìm thấy file CSV nào trong {input_folder}")
        return

    print(f"Tìm thấy {len(csv_files)} file CSV trong {input_folder}")
    print(f"Target timeframe: {target_tf} ({target_minutes} phút)")
    print("=" * 60)

    for filename in csv_files:
        input_path = os.path.join(input_folder, filename)
        output_path = os.path.join(output_folder, filename)

        source_tf = detect_timeframe_from_filename(filename)
        source_minutes = parse_timeframe_minutes(source_tf) if source_tf else None

        if source_minutes is not None and source_minutes >= target_minutes:
            print(f"[SKIP] {filename} (source={source_tf} >= target={target_tf}) -> copy nguyên")
            shutil.copy2(input_path, output_path)
            continue

        try:
            df = pd.read_csv(input_path, skipinitialspace=True)
            if not all(col in df.columns for col in ['Time', 'Open', 'High', 'Low', 'Close', 'Volume']):
                print(f"[SKIP] {filename} - thiếu cột OHLCV")
                continue

            df_converted = convert_timeframe(df, resample_tf)
            df_converted.to_csv(output_path, index=False)
            print(f"[OK]   {filename} ({len(df)} rows -> {len(df_converted)} rows)")
        except Exception as e:
            print(f"[ERROR] {filename}: {e}")

    print("=" * 60)
    print(f"Hoàn tất! Output: {output_folder}")


if __name__ == "__main__":
    input_folder = "res/test"
    output_folder = "res/data_converted"
    timeframe = "5min"

    batch_convert(input_folder, output_folder, timeframe)
