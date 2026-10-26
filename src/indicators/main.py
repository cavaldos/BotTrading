"""
Indicators Main - Run All SMC Indicators and Export to JSON
=============================================================
Main entry point that:
1. Loads OHLCV data from res/data
2. Runs ALL SMC indicators on each data file
3. Exports combined JSON for frontend (TradingView lightweight-charts format)

Output JSON structure:
{
    candles, volume, bos, choch, orderBlocks, fvg, ifvg, eqhEql,
    imbalance, breakerBlocks, mitigationBlocks, retracement, swing, gannSwing, metadata
}

Usage:
    python -m src.indicators.main                    # export the data files as they are
    python -m src.indicators.main --tfs 15m,1h,4h    # also resample them and export these timeframes
"""

from __future__ import annotations

import os
import sys
import json
import argparse
import pandas as pd
import numpy as np
from glob import glob
from datetime import datetime
from typing import List, Dict

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.config import load_backtest_config

from src.core.timeframe import convert_timeframe, parse_timeframe_minutes
from src.indicators.export import (
    datetime_to_timestamp,
    export_candles,
    export_volume,
    run_indicators,
)


# ==================== CONFIGURATION ====================

config = load_backtest_config(include_strategy=True)
DATA_DIR = config.get('folder_path', 'res/data')
OUTPUT_DIR = 'chart/public/data/indicators'

strategy_config = config.get('strategy', {}) if isinstance(config.get('strategy', {}), dict) else {}
try:
    gann_length_raw = strategy_config.get('gann_swing_length', strategy_config.get('internal_length', 3))
    GANN_SWING_LENGTH = max(1, int(gann_length_raw))
except (TypeError, ValueError):
    GANN_SWING_LENGTH = 3

# ==================== DATA LOADING ====================

def scan_data_files() -> List[dict]:
    """
    Scan data directory for CSV files.
    Returns list of dict with data_file, symbol, timeframe.
    """
    data_files = glob(os.path.join(DATA_DIR, '*.csv'))
    results = []

    for data_file in data_files:
        filename = os.path.basename(data_file)
        name_without_ext = filename.replace('.csv', '')
        parts = name_without_ext.rsplit('_', 1)

        if len(parts) != 2:
            print(f"  Skipping {filename} - invalid format (expected SYMBOL_TIMEFRAME.csv)")
            continue

        symbol, timeframe = parts
        results.append({
            'data_file': data_file,
            'symbol': symbol,
            'timeframe': timeframe,
            'id': name_without_ext,
        })

    return sorted(results, key=lambda x: (x['symbol'], x['timeframe']))


def load_data(data_file: str) -> pd.DataFrame:
    """Load OHLCV data from CSV."""
    df = pd.read_csv(data_file)
    df['Time'] = pd.to_datetime(df['Time'])
    df.columns = df.columns.str.strip()
    return df


# ==================== MAIN PROCESSING ====================

def run_all_indicators(df: pd.DataFrame) -> dict:
    """
    Run ALL SMC indicators on a DataFrame.
    Each indicator uses its own default config defined in its own file
    (Gann swing length comes from the strategy config).
    """
    return run_indicators(df, gann_swing_length=GANN_SWING_LENGTH, verbose=True)


def export_symbol(symbol_info: dict) -> dict:
    """Process and export a single symbol to JSON."""
    print(f"\nProcessing {symbol_info['id']}...")

    # Load data (resampled to a larger timeframe when requested)
    df = load_data(symbol_info['data_file'])
    if symbol_info.get('resample'):
        df = convert_timeframe(df, symbol_info['timeframe'])
        print(f"  Resampled {os.path.basename(symbol_info['data_file'])} -> {symbol_info['timeframe']}")
    print(f"  Loaded {len(df)} candles")

    # Run all indicators
    print("  Running indicators:")
    indicator_results = run_all_indicators(df)

    # Build export object
    chart_data = {
        'candles': export_candles(df),
        'volume': export_volume(df),
        **indicator_results,
        'metadata': {
            'symbol': symbol_info['symbol'],
            'timeframe': symbol_info['timeframe'],
            'id': symbol_info['id'],
            'dataPoints': len(df),
            'gannSwingLength': GANN_SWING_LENGTH,
            'exportedAt': datetime.now().isoformat(),
        }
    }

    # Save to JSON
    output_file = os.path.join(OUTPUT_DIR, f"{symbol_info['id']}.json")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(chart_data, f)

    file_size = os.path.getsize(output_file) / 1024
    print(f"  Exported to {output_file} ({file_size:.1f} KB)")

    return {
        'id': symbol_info['id'],
        'symbol': symbol_info['symbol'],
        'timeframe': symbol_info['timeframe'],
        'dataPoints': len(df),
        'indicators': {
            'bos': len(indicator_results['bos']),
            'choch': len(indicator_results['choch']),
            'orderBlocks': len(indicator_results['orderBlocks']),
            'fvg': len(indicator_results['fvg']),
            'ifvg': len(indicator_results['ifvg']),
            'eqhEql': len(indicator_results['eqhEql']),
            'imbalance': len(indicator_results['imbalance']),
            'breakerBlocks': len(indicator_results['breakerBlocks']),
            'mitigationBlocks': len(indicator_results['mitigationBlocks']),
            'retracement': len(indicator_results['retracement']),
            'swing': len(indicator_results.get('swing', [])),
            'gannSwing': len(indicator_results.get('gannSwing', [])),
        },
    }


def plan_exports(native_infos: List[dict], tfs: List[str] | None) -> List[dict]:
    """Native files when no timeframes are given, otherwise each requested timeframe per symbol."""
    if not tfs:
        return native_infos
    plans = []
    for info in native_infos:
        native_minutes = parse_timeframe_minutes(info['timeframe'])
        for tf in tfs:
            target = parse_timeframe_minutes(tf)
            if not native_minutes or not target or target < native_minutes or target % native_minutes:
                continue
            if target == native_minutes:
                plans.append(info)
                continue
            plans.append({**info, 'timeframe': tf, 'id': f"{info['symbol']}_{tf}", 'resample': True,
                          'source_minutes': native_minutes})
    # One export per id: a native file wins, otherwise resample from the largest source timeframe (fewer rows)
    def rank(plan: dict) -> int:
        return 10**9 if not plan.get('resample') else plan['source_minutes']

    best: Dict[str, dict] = {}
    for plan in plans:
        if plan['id'] not in best or rank(plan) > rank(best[plan['id']]):
            best[plan['id']] = plan
    return sorted(best.values(), key=lambda x: (x['symbol'], parse_timeframe_minutes(x['timeframe']) or 0))


def export_symbols_list(symbols: List[dict]):
    """Write symbols.json, keeping earlier entries whose JSON file still exists."""
    output_file = os.path.join(OUTPUT_DIR, 'symbols.json')
    merged = {}
    try:
        with open(output_file, 'r', encoding='utf-8') as f:
            for entry in json.load(f).get('symbols', []):
                if os.path.exists(os.path.join(OUTPUT_DIR, f"{entry.get('id')}.json")):
                    merged[entry['id']] = entry
    except (OSError, ValueError, AttributeError):
        pass
    for entry in symbols:
        merged[entry['id']] = entry
    symbols = sorted(merged.values(), key=lambda x: (x['symbol'], parse_timeframe_minutes(x['timeframe']) or 0))
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'symbols': symbols,
            'exportedAt': datetime.now().isoformat(),
        }, f, indent=2)
    print(f"\nSymbols list saved to {output_file}")


# ==================== ENTRY POINT ====================

def main():
    """Main function to run all indicators and export to JSON."""
    parser = argparse.ArgumentParser(description="Export SMC indicators to chart JSON")
    parser.add_argument("--tfs", default="", help="Comma-separated timeframes to export, e.g. 1m,5m,15m,1h,4h")
    args = parser.parse_args()
    tfs = [tf.strip() for tf in args.tfs.split(',') if tf.strip()]

    print("=" * 60)
    print("SMC Indicators - Export All to JSON")
    print("=" * 60)

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Scan for data files
    print(f"\nScanning {DATA_DIR}/ for data files...")
    symbol_infos = plan_exports(scan_data_files(), tfs)

    if not symbol_infos:
        print("No data files found!")
        return

    print(f"Found {len(symbol_infos)} symbol(s):")
    for info in symbol_infos:
        print(f"  - {info['id']}")

    # Process each symbol
    exported_symbols = []
    for info in symbol_infos:
        try:
            result = export_symbol(info)
            exported_symbols.append(result)
        except Exception as e:
            print(f"  Error processing {info['id']}: {e}")
            import traceback
            traceback.print_exc()

    # Export symbols list
    export_symbols_list(exported_symbols)

    # Summary
    print("\n" + "=" * 60)
    print("Export Summary")
    print("=" * 60)
    print(f"Total symbols exported: {len(exported_symbols)}")
    for sym in exported_symbols:
        ind = sym['indicators']
        total_indicators = sum(ind.values())
        print(f"  {sym['id']}: {sym['dataPoints']} candles, {total_indicators} indicator events")
        print(f"    BOS={ind['bos']}, CHoCH={ind['choch']}, OB={ind['orderBlocks']}, "
              f"FVG={ind['fvg']}, IFVG={ind['ifvg']}, EQH/EQL={ind['eqhEql']}")
        print(f"    Imbalance={ind['imbalance']}, Breaker={ind['breakerBlocks']}, "
              f"Mitigation={ind['mitigationBlocks']}, Retracement={ind['retracement']}")
        print(f"    Swing={ind.get('swing', 0)}, GannSwing={ind.get('gannSwing', 0)}")
    print(f"\nOutput directory: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
