"""
Export Live Trade Data to JSON for React Lightweight-Charts
============================================================
Exports OHLCV data and trades from res/logs/data and res/logs/output
to JSON format for live trading visualization.

This script is designed to be called periodically during live trading
to update the chart data in real-time.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
import json
import os
import yaml
import time
from glob import glob
from datetime import datetime
from pathlib import Path


# ==================== CONFIGURATION ====================
def load_trade_config() -> dict:
    """Load configuration from trade config.yaml"""
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    # Fallback defaults
    return {
        "data": {"folder": "res/logs/data"},
        "output": {"folder": "res/logs/output"}
    }

# Load config
_CONFIG = load_trade_config()

# Folders for live trading data (from config or defaults)
LIVE_DATA_DIR = _CONFIG.get("data", {}).get("folder", "res/logs/data")
LIVE_TRADES_DIR = _CONFIG.get("output", {}).get("folder", "res/logs/output")
OUTPUT_DIR = 'chart/public/data/live'

# Colors configuration (matching TradingView style)
COLORS = {
    'bullish_candle': '#26a69a',
    'bearish_candle': '#ef5350',
}


def ensure_output_dir():
    """Ensure output directory exists."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def scan_data_files() -> list[dict]:
    """
    Scan res/logs/data/ directory for data files and match with trades files.
    Returns list of dict with data_file, trades_file, symbol, timeframe.
    """
    if not os.path.exists(LIVE_DATA_DIR):
        return []
    
    data_files = glob(os.path.join(LIVE_DATA_DIR, '*.csv'))
    results = []
    
    for data_file in data_files:
        filename = os.path.basename(data_file)
        # Parse filename: SYMBOL_TIMEFRAME.csv
        name_without_ext = filename.replace('.csv', '')
        parts = name_without_ext.rsplit('_', 1)
        
        if len(parts) != 2:
            continue
        
        symbol, timeframe = parts
        
        # Look for matching trades file in output folder
        trades_file = os.path.join(LIVE_TRADES_DIR, f'{name_without_ext}_trades.csv')
        
        if not os.path.exists(trades_file):
            trades_file = None
        
        results.append({
            'data_file': data_file,
            'trades_file': trades_file,
            'symbol': symbol,
            'timeframe': timeframe,
            'id': name_without_ext,
        })
    
    return sorted(results, key=lambda x: (x['symbol'], x['timeframe']))


def datetime_to_timestamp(dt) -> int:
    """Convert datetime to Unix timestamp (seconds)."""
    if isinstance(dt, pd.Timestamp):
        return int(dt.timestamp())
    elif isinstance(dt, datetime):
        return int(dt.timestamp())
    elif isinstance(dt, np.datetime64):
        return int(pd.Timestamp(dt).timestamp())
    return int(dt)


def load_candle_data(data_file: str) -> pd.DataFrame:
    """Load OHLCV data from CSV file."""
    df = pd.read_csv(data_file)
    df['Time'] = pd.to_datetime(df['Time'])
    df.columns = df.columns.str.strip()
    return df


def load_trades_data(trades_file: str | None) -> pd.DataFrame | None:
    """Load trades data from CSV file."""
    if not trades_file or not os.path.exists(trades_file):
        return None
    
    try:
        trades_df = pd.read_csv(trades_file)
        trades_df.columns = trades_df.columns.str.strip()
        trades_df['Entry_Time'] = pd.to_datetime(trades_df['Entry_Time'])
        
        # Exit_Time might be empty for open trades
        if 'Exit_Time' in trades_df.columns:
            trades_df['Exit_Time'] = pd.to_datetime(trades_df['Exit_Time'], errors='coerce')
        
        return trades_df
    except Exception as e:
        print(f"Error loading trades: {e}")
        return None


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


def export_trades(trades_df: pd.DataFrame | None) -> list:
    """
    Export trades data with entry/exit info.
    Handles both closed trades and open trades (without exit info).
    """
    if trades_df is None or trades_df.empty:
        return []
    
    trades = []
    for idx, row in trades_df.iterrows():
        # Check if trade is closed (has Exit_Time and Exit_Price)
        is_closed = (
            'Exit_Time' in row and 
            pd.notna(row.get('Exit_Time')) and
            'Exit_Price' in row and
            pd.notna(row.get('Exit_Price'))
        )
        
        trade_data = {
            'id': int(idx),
            'type': row['Type'],
            'entryTime': datetime_to_timestamp(row['Entry_Time']),
            'entryPrice': float(row['Entry_Price']),
            'stopLoss': float(row['Stop_Loss']),
            'takeProfit': float(row['Take_Profit']),
            'status': 'closed' if is_closed else 'open',
        }
        
        if is_closed:
            is_win = row.get('Result', '') != 'Loss'
            trade_data.update({
                'exitTime': datetime_to_timestamp(row['Exit_Time']),
                'exitPrice': float(row['Exit_Price']),
                'result': 'Win' if is_win else 'Loss',
                'pnlPercent': float(row['PnL_%']) if 'PnL_%' in row and pd.notna(row.get('PnL_%')) else 0,
            })
        else:
            # For open trades, result is pending
            trade_data['result'] = 'Pending'
        
        trades.append(trade_data)
    
    return trades


def export_markers(trades_df: pd.DataFrame | None) -> list:
    """
    Export trade markers for lightweight-charts.
    Creates entry markers for all trades, exit markers only for closed trades.
    """
    if trades_df is None or trades_df.empty:
        return []
    
    markers = []
    
    for idx, row in trades_df.iterrows():
        is_long = row['Type'] == 'LONG'
        
        # Entry marker (always present)
        markers.append({
            'time': datetime_to_timestamp(row['Entry_Time']),
            'position': 'belowBar' if is_long else 'aboveBar',
            'color': '#26a69a' if is_long else '#ef5350',
            'shape': 'arrowUp' if is_long else 'arrowDown',
            'text': f"{'L' if is_long else 'S'}{idx + 1}",
            'size': 2,
        })
        
        # Exit marker (only for closed trades)
        is_closed = (
            'Exit_Time' in row and 
            pd.notna(row.get('Exit_Time')) and
            'Exit_Price' in row and
            pd.notna(row.get('Exit_Price'))
        )
        
        if is_closed:
            is_win = row.get('Result', '') != 'Loss'
            markers.append({
                'time': datetime_to_timestamp(row['Exit_Time']),
                'position': 'aboveBar' if is_win else 'belowBar',
                'color': '#26a69a' if is_win else '#ef5350',
                'shape': 'circle',
                'text': 'W' if is_win else 'X',
                'size': 1,
            })
    
    return markers


def calculate_summary(trades_df: pd.DataFrame | None) -> dict:
    """Calculate trade summary statistics."""
    if trades_df is None or trades_df.empty:
        return {
            'total': 0,
            'wins': 0,
            'losses': 0,
            'open': 0,
            'winRate': 0,
        }
    
    total = len(trades_df)
    
    # Count closed trades
    closed_trades = trades_df[
        trades_df['Exit_Time'].notna() if 'Exit_Time' in trades_df.columns else pd.Series([False] * len(trades_df))
    ]
    
    open_count = total - len(closed_trades)
    wins = len(closed_trades[closed_trades['Result'] != 'Loss']) if 'Result' in closed_trades.columns else 0
    losses = len(closed_trades) - wins
    win_rate = (wins / len(closed_trades) * 100) if len(closed_trades) > 0 else 0
    
    return {
        'total': total,
        'wins': wins,
        'losses': losses,
        'open': open_count,
        'winRate': round(win_rate, 1),
    }


def export_symbol(symbol_info: dict, verbose: bool = False) -> dict | None:
    """Export a single symbol to JSON file."""
    try:
        # Load data
        df = load_candle_data(symbol_info['data_file'])
        trades_df = load_trades_data(symbol_info['trades_file'])
        
        if verbose:
            print(f"  {symbol_info['id']}: {len(df)} candles", end='')
            if trades_df is not None:
                print(f", {len(trades_df)} trades")
            else:
                print(" (no trades)")
        
        # Build export object (no order blocks for live trading)
        chart_data = {
            'candles': export_candles(df),
            'volume': export_volume(df),
            'trades': export_trades(trades_df),
            'markers': export_markers(trades_df),
            'summary': calculate_summary(trades_df),
            'metadata': {
                'symbol': symbol_info['symbol'],
                'timeframe': symbol_info['timeframe'],
                'id': symbol_info['id'],
                'dataPoints': len(df),
                'exportedAt': datetime.now().isoformat(),
                'isLive': True,
            }
        }
        
        # Save to JSON
        output_file = os.path.join(OUTPUT_DIR, f"{symbol_info['id']}.json")
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(chart_data, f)
        
        return {
            'id': symbol_info['id'],
            'symbol': symbol_info['symbol'],
            'timeframe': symbol_info['timeframe'],
            'dataPoints': len(df),
            'trades': len(chart_data['trades']),
            'winRate': chart_data['summary']['winRate'],
        }
    except Exception as e:
        if verbose:
            print(f"  Error exporting {symbol_info['id']}: {e}")
        return None


def export_symbols_list(symbols: list[dict]):
    """Export list of available symbols to symbols.json."""
    output_file = os.path.join(OUTPUT_DIR, 'symbols.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'symbols': symbols,
            'exportedAt': datetime.now().isoformat(),
            'isLive': True,
        }, f, indent=2)


def export_all(verbose: bool = False) -> list[dict]:
    """
    Export all available symbol data to JSON files.
    
    Args:
        verbose: If True, print progress messages
    
    Returns:
        List of exported symbol info dicts
    """
    ensure_output_dir()
    
    # Scan for data files
    symbol_infos = scan_data_files()
    
    if not symbol_infos:
        if verbose:
            print("No data files found in", LIVE_DATA_DIR)
        return []
    
    if verbose:
        print(f"Found {len(symbol_infos)} symbol(s)")
    
    # Export each symbol
    exported_symbols = []
    for info in symbol_infos:
        result = export_symbol(info, verbose=verbose)
        if result:
            exported_symbols.append(result)
    
    # Export symbols list
    export_symbols_list(exported_symbols)
    
    return exported_symbols


def main():
    """Main function - export all data with verbose output."""
    print("=" * 60)
    print("Export Live Trade Data to JSON")
    print("=" * 60)
    print(f"\nData source: {LIVE_DATA_DIR}")
    print(f"Trades source: {LIVE_TRADES_DIR}")
    print(f"Output: {OUTPUT_DIR}")
    print(f"\nRunning continuously every 30 seconds...")
    print("Press Ctrl+C to stop.\n")
    
    try:
        while True:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{timestamp}] Exporting data...")
            
            exported = export_all(verbose=True)
            
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Exported {len(exported)} symbol(s)")
            print("Waiting 30 seconds until next export...\n")
            
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n\nExport stopped by user.")
        print("=" * 60)


if __name__ == "__main__":
    main()
