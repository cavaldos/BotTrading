"""
Export Chart Data to JSON for React Lightweight-Charts
=======================================================
Exports OHLCV data, trades, and Order Blocks to JSON format
for use with TradingView's lightweight-charts library.

Scans all files in res/data/ and res/output/ directories,
exports each symbol to a separate JSON file.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
import json
import sys
import os
from bisect import bisect_left
from glob import glob
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from src.config import load_backtest_config

# Add project root to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# ==================== CONFIGURATION ====================
config = load_backtest_config(include_strategy=False)
DATA_DIR = config.get('folder_path', 'res/data')
TRADES_DIR = config.get('output_folder', 'res/output')
OUTPUT_DIR = 'chart/public/data/orders'

# Colors configuration (matching TradingView style)
COLORS = {
    'bullish_candle': '#26a69a',
    'bearish_candle': '#ef5350',
}


def scan_data_files() -> list[dict]:
    """
    Scan res/data/ directory for data files and match with trades files.
    Returns list of dict with data_file, trades_file, symbol, timeframe.
    """
    data_files = glob(os.path.join(DATA_DIR, '*.csv'))
    results = []
    
    for data_file in data_files:
        filename = os.path.basename(data_file)
        # Parse filename: SYMBOL_TIMEFRAME.csv
        name_without_ext = filename.replace('.csv', '')
        parts = name_without_ext.rsplit('_', 1)
        
        if len(parts) != 2:
            print(f"  Skipping {filename} - invalid format (expected SYMBOL_TIMEFRAME.csv)")
            continue
        
        symbol, timeframe = parts
        
        # Look for matching trades file
        trades_file = os.path.join(TRADES_DIR, f'{name_without_ext}_trades.csv')
        
        if not os.path.exists(trades_file):
            print(f"  Warning: No trades file found for {filename}")
            trades_file = None
        
        results.append({
            'data_file': data_file,
            'trades_file': trades_file,
            'symbol': symbol,
            'timeframe': timeframe,
            'id': name_without_ext,  # e.g., BTCUSDT_15m
        })
    
    return sorted(results, key=lambda x: (x['symbol'], x['timeframe']))


def load_data(data_file: str, trades_file: str | None) -> tuple:
    """Load price data and trades data."""
    # Load OHLCV data
    df = pd.read_csv(data_file)
    df['Time'] = pd.to_datetime(df['Time'])
    df.columns = df.columns.str.strip()
    
    # Load trades if available
    trades_df = None
    if trades_file and os.path.exists(trades_file):
        trades_df = pd.read_csv(trades_file)
        trades_df.columns = trades_df.columns.str.strip()
        trades_df['Entry_Time'] = pd.to_datetime(trades_df['Entry_Time'])
        trades_df['Exit_Time'] = pd.to_datetime(trades_df['Exit_Time'])
    
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


def snap_to_next_candle(timestamp: int, candle_times: list[int]) -> int:
    """Snap timestamp forward to the nearest available candle time."""
    if not candle_times:
        return timestamp

    idx = bisect_left(candle_times, timestamp)
    if idx < len(candle_times):
        return candle_times[idx]

    return candle_times[-1]


def export_candles(df: pd.DataFrame) -> list:
    """Export candlestick data in lightweight-charts format."""
    candles = []
    for _, row in df.iterrows():
        candles.append({
            'time': datetime_to_timestamp(row['Time']),
            'open': float(row['Open']),
            'high': float(row['High']),
            'low': float(row['Low']),
            'close': float(row['Close']),
        })
    return candles


def export_volume(df: pd.DataFrame) -> list:
    """Export volume data in lightweight-charts histogram format."""
    volume = []
    for _, row in df.iterrows():
        is_bullish = row['Close'] >= row['Open']
        volume.append({
            'time': datetime_to_timestamp(row['Time']),
            'value': float(row['Volume']),
            'color': COLORS['bullish_candle'] if is_bullish else COLORS['bearish_candle'],
        })
    return volume


def export_trades(trades_df: pd.DataFrame | None, candle_times: list[int]) -> list:
    """Export trades data with entry/exit info."""
    if trades_df is None:
        return []
    
    trades = []
    records = trades_df.to_dict('records')
    for idx, row in enumerate(records):
        is_win = row['Result'] != 'Loss'
        entry_time_raw = datetime_to_timestamp(row['Entry_Time'])
        exit_time_raw = datetime_to_timestamp(row['Exit_Time'])
        entry_time = snap_to_next_candle(entry_time_raw, candle_times)
        exit_time = snap_to_next_candle(exit_time_raw, candle_times)

        trades.append({
            'id': int(idx),
            'type': row['Type'],
            'entryTime': entry_time,
            'exitTime': exit_time,
            'entryTimeRaw': entry_time_raw,
            'exitTimeRaw': exit_time_raw,
            'entryPrice': float(row['Entry_Price']),
            'exitPrice': float(row['Exit_Price']),
            'stopLoss': float(row['Stop_Loss']),
            'takeProfit': float(row['Take_Profit']),
            'result': 'Win' if is_win else 'Loss',
            'pnlPercent': float(row['PnL_%']) if 'PnL_%' in row else 0,
        })
    return trades


def export_markers(trades_df: pd.DataFrame | None, candle_times: list[int]) -> list:
    """
    Export trade markers for lightweight-charts.
    Creates entry and exit markers.
    """
    if trades_df is None:
        return []
    
    markers = []
    records = trades_df.to_dict('records')

    for idx, row in enumerate(records):
        is_long = row['Type'] == 'LONG'
        is_win = row['Result'] != 'Loss'
        entry_time = snap_to_next_candle(datetime_to_timestamp(row['Entry_Time']), candle_times)
        exit_time = snap_to_next_candle(datetime_to_timestamp(row['Exit_Time']), candle_times)
        
        # Entry marker
        markers.append({
            'time': entry_time,
            'position': 'belowBar' if is_long else 'aboveBar',
            'color': '#26a69a' if is_long else '#ef5350',
            'shape': 'arrowUp' if is_long else 'arrowDown',
            'text': f"{'L' if is_long else 'S'}{idx + 1}",
            'size': 2,
        })
        
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


def calculate_summary(trades_df: pd.DataFrame | None) -> dict:
    """Calculate trade summary statistics."""
    if trades_df is None:
        return {
            'total': 0,
            'wins': 0,
            'losses': 0,
            'winRate': 0,
        }
    
    total = len(trades_df)
    wins = len(trades_df[trades_df['Result'] != 'Loss'])
    losses = total - wins
    win_rate = (wins / total * 100) if total > 0 else 0
    
    return {
        'total': total,
        'wins': wins,
        'losses': losses,
        'winRate': round(win_rate, 1),
    }


def export_symbol(symbol_info: dict) -> dict:
    """Export a single symbol to JSON file."""
    print(f"\nProcessing {symbol_info['id']}...")
    
    # Load data
    df, trades_df = load_data(symbol_info['data_file'], symbol_info['trades_file'])
    print(f"  Loaded {len(df)} candles", end='')
    if trades_df is not None:
        print(f" and {len(trades_df)} trades")
    else:
        print(" (no trades)")
    
    candles = export_candles(df)
    candle_times = [c['time'] for c in candles]
    volume = export_volume(df)
    trades = export_trades(trades_df, candle_times)
    markers = export_markers(trades_df, candle_times)

    # Build export object
    chart_data = {
        'candles': candles,
        'volume': volume,
        'trades': trades,
        'markers': markers,
        'summary': calculate_summary(trades_df),
        'metadata': {
            'symbol': symbol_info['symbol'],
            'timeframe': symbol_info['timeframe'],
            'id': symbol_info['id'],
            'dataPoints': len(df),
            'exportedAt': datetime.now().isoformat(),
        }
    }
    
    # Save to JSON
    output_file = os.path.join(OUTPUT_DIR, f"{symbol_info['id']}.json")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(chart_data, f)
    
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
    
    # Scan for data files
    print(f"\nScanning {DATA_DIR}/ for data files...")
    symbol_infos = scan_data_files()
    
    if not symbol_infos:
        print("No data files found!")
        return
    
    print(f"Found {len(symbol_infos)} symbol(s):")
    for info in symbol_infos:
        has_trades = "with trades" if info['trades_file'] else "no trades"
        print(f"  - {info['id']} ({has_trades})")
    
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
