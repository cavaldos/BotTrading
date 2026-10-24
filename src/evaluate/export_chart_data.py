"""
Export Chart Data to JSON for React Lightweight-Charts
=======================================================
Exports OHLCV data, trades, and Order Blocks to JSON format
for use with TradingView's lightweight-charts library.

Scans every *_trades.csv in the backtest output folder (res/output/) and
exports each one to a separate JSON file. The backtest names trades files
SYMBOL_<signal_tf>_trades.csv but only reads SYMBOL_<entry_tf>.csv candles,
so candles are rebuilt the same way: the entry timeframe file is resampled
to the signal timeframe.
"""

from __future__ import annotations

import sys
import os

# Add project root to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pandas as pd
import numpy as np
import json
from bisect import bisect_right
from glob import glob
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from src.config import load_backtest_config, load_strategy_config
from src.utils.convertTF import convert_timeframe

# ==================== CONFIGURATION ====================
config = load_backtest_config(include_strategy=False)
strategy_config = load_strategy_config()
DATA_DIR = config.get('folder_path', 'res/data')
TRADES_DIR = config.get('output_folder', 'res/output')
ENTRY_TIMEFRAME = strategy_config.get('entry_timeframe', '1m')
OUTPUT_DIR = 'chart/public/data/orders'
CONVERTED_DATA_DIR = 'res/data_converted'

TRADES_SUFFIX = '_trades.csv'

# Colors configuration (matching TradingView style)
COLORS = {
    'bullish_candle': '#26a69a',
    'bearish_candle': '#ef5350',
}


def find_candle_source(symbol: str, timeframe: str) -> tuple[str | None, bool]:
    """
    Locate candles for SYMBOL_TIMEFRAME.
    Returns (data_file, needs_resample).

    Prefers the entry timeframe file the backtest itself read, so the chart
    covers exactly the same candles as the trades. Falls back to a file that
    already has the target timeframe.
    """
    entry_file = os.path.join(DATA_DIR, f'{symbol}_{ENTRY_TIMEFRAME}.csv')
    if os.path.isfile(entry_file):
        return entry_file, timeframe != ENTRY_TIMEFRAME

    exact_files = [
        os.path.join(directory, f'{symbol}_{timeframe}.csv')
        for directory in (DATA_DIR, CONVERTED_DATA_DIR)
    ]
    exact_files = [path for path in exact_files if os.path.isfile(path)]
    if exact_files:
        return max(exact_files, key=os.path.getmtime), False

    return None, False


def scan_data_files() -> list[dict]:
    """
    Scan trades files and match each one with its candle data.
    Returns list of dict with data_file, trades_file, symbol, timeframe.
    """
    results = []
    for trades_file in glob(os.path.join(TRADES_DIR, f'*{TRADES_SUFFIX}')):
        filename = os.path.basename(trades_file)
        # Parse filename: SYMBOL_TIMEFRAME_trades.csv
        name_without_suffix = filename[:-len(TRADES_SUFFIX)]
        parts = name_without_suffix.rsplit('_', 1)

        if len(parts) != 2:
            print(f"  Skipping {filename} - invalid format (expected SYMBOL_TIMEFRAME{TRADES_SUFFIX})")
            continue

        symbol, timeframe = parts
        data_file, needs_resample = find_candle_source(symbol, timeframe)

        if data_file is None:
            print(f"  Warning: No candle data for {filename} "
                  f"(looked for {symbol}_{ENTRY_TIMEFRAME}.csv or {symbol}_{timeframe}.csv)")
            continue

        results.append({
            'data_file': data_file,
            'trades_file': trades_file,
            'needs_resample': needs_resample,
            'symbol': symbol,
            'timeframe': timeframe,
            'id': name_without_suffix,  # e.g., BTCUSDT_15m
        })

    return sorted(results, key=lambda x: (x['symbol'], x['timeframe']))


def load_data(symbol_info: dict) -> tuple:
    """Load price data (resampled to the trades timeframe) and trades data."""
    # Load OHLCV data
    df = pd.read_csv(symbol_info['data_file'])
    df.columns = df.columns.str.strip()
    df['Time'] = pd.to_datetime(df['Time'])

    if symbol_info['needs_resample']:
        df = convert_timeframe(df, symbol_info['timeframe'])

    trades_df = pd.read_csv(symbol_info['trades_file'])
    trades_df.columns = trades_df.columns.str.strip()
    trades_df['Entry_Time'] = pd.to_datetime(trades_df['Entry_Time'], format='mixed', errors='coerce')
    trades_df['Exit_Time'] = pd.to_datetime(trades_df['Exit_Time'], format='mixed', errors='coerce')

    return df, trades_df


def datetime_to_timestamp(dt) -> int:
    """Convert datetime to Unix timestamp (seconds)."""
    if dt is None:
        return 0

    if isinstance(dt, pd.Timestamp):
        ts_value = dt.value
        if ts_value == pd.NaT.value:
            return 0
        return int(ts_value // 1_000_000_000)
    elif isinstance(dt, datetime):
        return int(dt.timestamp())
    elif isinstance(dt, np.datetime64):
        if np.isnat(dt):
            return 0
        ts_value = pd.Timestamp(dt).value
        if ts_value == pd.NaT.value:
            return 0
        return int(ts_value // 1_000_000_000)
    return int(dt)


def series_to_timestamps(series: pd.Series) -> list[int]:
    """Convert a datetime Series to Unix timestamps (seconds); naive times are treated as UTC."""
    if series.dt.tz is not None:
        series = series.dt.tz_convert('UTC').dt.tz_localize(None)
    return ((series - pd.Timestamp(0)) // pd.Timedelta(seconds=1)).astype('int64').tolist()


def snap_to_containing_candle(timestamp: int, candle_times: list[int]) -> int:
    """Snap timestamp to the candle bucket that contains it."""
    if not candle_times:
        return timestamp

    idx = bisect_right(candle_times, timestamp) - 1
    if idx >= 0:
        return candle_times[idx]

    return candle_times[0]


def trade_result(row: dict) -> str:
    """Normalize the backtest result to Win / Loss / Open."""
    if row['Result'] == 'Open' or pd.isna(row['Exit_Time']):
        return 'Open'
    return 'Loss' if row['Result'] == 'Loss' else 'Win'


def export_candles(df: pd.DataFrame) -> list:
    """Export candlestick data in lightweight-charts format."""
    return [
        {'time': t, 'open': o, 'high': h, 'low': l, 'close': c}
        for t, o, h, l, c in zip(
            series_to_timestamps(df['Time']),
            df['Open'].astype(float).tolist(),
            df['High'].astype(float).tolist(),
            df['Low'].astype(float).tolist(),
            df['Close'].astype(float).tolist(),
        )
    ]


def export_volume(df: pd.DataFrame) -> list:
    """Export volume data in lightweight-charts histogram format."""
    is_bullish = (df['Close'] >= df['Open']).tolist()
    return [
        {
            'time': t,
            'value': v,
            'color': COLORS['bullish_candle'] if bullish else COLORS['bearish_candle'],
        }
        for t, v, bullish in zip(
            series_to_timestamps(df['Time']),
            df['Volume'].astype(float).tolist(),
            is_bullish,
        )
    ]


def export_trades(trades_df: pd.DataFrame, candle_times: list[int]) -> list:
    """Export trades data with entry/exit info."""
    trades = []
    records = trades_df.to_dict('records')
    for idx, row in enumerate(records):
        result = trade_result(row)
        entry_time_raw = datetime_to_timestamp(row['Entry_Time'])
        entry_time = snap_to_containing_candle(entry_time_raw, candle_times)

        if result == 'Open':
            # Still running at the end of the data: draw it up to the last candle
            exit_time_raw = None
            exit_time = candle_times[-1] if candle_times else entry_time
            exit_price = None
        else:
            exit_time_raw = datetime_to_timestamp(row['Exit_Time'])
            exit_time = snap_to_containing_candle(exit_time_raw, candle_times)
            exit_price = float(row['Exit_Price'])

        trades.append({
            'id': int(idx),
            'type': row['Type'],
            'entryTime': entry_time,
            'exitTime': exit_time,
            'entryTimeRaw': entry_time_raw,
            'exitTimeRaw': exit_time_raw,
            'entryPrice': float(row['Entry_Price']),
            'exitPrice': exit_price,
            'stopLoss': float(row['Stop_Loss']),
            'takeProfit': float(row['Take_Profit']),
            'result': result,
            'pnlPercent': float(row['PnL_%']) if 'PnL_%' in row else 0,
        })
    return trades


def export_markers(trades_df: pd.DataFrame, candle_times: list[int]) -> list:
    """
    Export trade markers for lightweight-charts.
    Creates entry and exit markers (open trades only get an entry marker).
    """
    markers = []
    records = trades_df.to_dict('records')

    for idx, row in enumerate(records):
        is_long = row['Type'] == 'LONG'
        result = trade_result(row)
        entry_time = snap_to_containing_candle(datetime_to_timestamp(row['Entry_Time']), candle_times)

        # Entry marker
        markers.append({
            'time': entry_time,
            'position': 'belowBar' if is_long else 'aboveBar',
            'color': '#26a69a' if is_long else '#ef5350',
            'shape': 'arrowUp' if is_long else 'arrowDown',
            'text': f"{'L' if is_long else 'S'}{idx + 1}",
            'size': 2,
        })

        if result == 'Open':
            continue

        is_win = result == 'Win'
        exit_time = snap_to_containing_candle(datetime_to_timestamp(row['Exit_Time']), candle_times)

        # Exit marker
        markers.append({
            'time': exit_time,
            'position': 'aboveBar' if is_win else 'belowBar',
            'color': '#26a69a' if is_win else '#ef5350',
            'shape': 'circle',
            'text': 'W' if is_win else 'X',
            'size': 1,
        })

    return markers


def calculate_summary(trades: list[dict]) -> dict:
    """Calculate trade summary statistics (win rate over closed trades)."""
    total = len(trades)
    wins = sum(1 for t in trades if t['result'] == 'Win')
    losses = sum(1 for t in trades if t['result'] == 'Loss')
    open_trades = total - wins - losses
    closed = wins + losses
    win_rate = (wins / closed * 100) if closed > 0 else 0

    return {
        'total': total,
        'wins': wins,
        'losses': losses,
        'open': open_trades,
        'winRate': round(win_rate, 1),
    }


def count_trades_outside_candles(trades: list[dict], candle_times: list[int]) -> int:
    """Count trades whose entry falls outside the candle range (data/trades mismatch)."""
    if not candle_times:
        return len(trades)
    first, last = candle_times[0], candle_times[-1]
    return sum(1 for t in trades if not first <= t['entryTimeRaw'] <= last)


def export_symbol(symbol_info: dict) -> dict:
    """Export a single symbol to JSON file."""
    print(f"\nProcessing {symbol_info['id']}...")

    # Load data
    df, trades_df = load_data(symbol_info)
    source = os.path.basename(symbol_info['data_file'])
    if symbol_info['needs_resample']:
        source += f" -> {symbol_info['timeframe']}"
    print(f"  Loaded {len(df)} candles ({source}) and {len(trades_df)} trades")

    candles = export_candles(df)
    candle_times = [c['time'] for c in candles]
    volume = export_volume(df)
    trades = export_trades(trades_df, candle_times)
    markers = export_markers(trades_df, candle_times)

    outside = count_trades_outside_candles(trades, candle_times)
    if outside:
        print(f"  Warning: {outside}/{len(trades)} trades are outside the candle range - "
              f"{symbol_info['data_file']} may not be the data the backtest ran on")

    # Build export object
    chart_data = {
        'candles': candles,
        'volume': volume,
        'trades': trades,
        'markers': markers,
        'summary': calculate_summary(trades),
        'metadata': {
            'symbol': symbol_info['symbol'],
            'timeframe': symbol_info['timeframe'],
            'id': symbol_info['id'],
            'dataPoints': len(df),
            'exportedAt': datetime.now().isoformat(),
        }
    }

    # Save to JSON (allow_nan=False: NaN is not valid JSON and breaks the chart)
    output_file = os.path.join(OUTPUT_DIR, f"{symbol_info['id']}.json")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(chart_data, f, allow_nan=False)

    file_size = os.path.getsize(output_file) / 1024  # KB
    print(f"  Exported to {output_file} ({file_size:.1f} KB)")

    return {
        'id': symbol_info['id'],
        'symbol': symbol_info['symbol'],
        'timeframe': symbol_info['timeframe'],
        'dataPoints': len(df),
        'trades': len(chart_data['trades']),
        'winRate': chart_data['summary']['winRate'],
    }


def export_symbols_list(symbols: list[dict]):
    """Export list of available symbols to symbols.json."""
    output_file = os.path.join(OUTPUT_DIR, 'symbols.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'symbols': symbols,
            'exportedAt': datetime.now().isoformat(),
        }, f, indent=2)
    print(f"\nSymbols list saved to {output_file}")


def main():
    """Main function to export all chart data to JSON."""
    print("=" * 60)
    print("Export Chart Data to JSON")
    print("=" * 60)

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Scan for trades files
    print(f"\nScanning {TRADES_DIR}/ for trades files (candles from {DATA_DIR}/, entry timeframe {ENTRY_TIMEFRAME})...")
    symbol_infos = scan_data_files()

    if not symbol_infos:
        print(f"No exportable *{TRADES_SUFFIX} files found in {TRADES_DIR}/ - run the backtest first.")
        return

    print(f"Found {len(symbol_infos)} symbol(s):")
    for info in symbol_infos:
        print(f"  - {info['id']} (candles: {info['data_file']})")

    # Export each symbol
    exported_symbols = []
    for info in symbol_infos:
        try:
            result = export_symbol(info)
            exported_symbols.append(result)
        except Exception as e:
            print(f"  Error exporting {info['id']}: {e}")

    # Export symbols list
    export_symbols_list(exported_symbols)

    # Summary
    print("\n" + "=" * 60)
    print("Export Summary")
    print("=" * 60)
    print(f"Total symbols exported: {len(exported_symbols)}")
    for sym in exported_symbols:
        print(f"  {sym['id']}: {sym['dataPoints']} candles, {sym['trades']} trades, {sym['winRate']}% win rate")
    print(f"\nOutput directory: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
