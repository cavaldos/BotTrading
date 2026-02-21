"""
Indicators Main - Run All SMC Indicators and Export to JSON
=============================================================
Main entry point that:
1. Loads OHLCV data from res/data
2. Runs ALL SMC indicators on each data file
3. Exports combined JSON for frontend (TradingView lightweight-charts format)

Output JSON structure:
{
    candles, volume, bos, choch, orderBlocks, fvg, eqhEql,
    imbalance, breakerBlocks, mitigationBlocks, retracement, metadata
}

Usage:
    python -m src.indicators.main
"""

from __future__ import annotations

import os
import sys
import json
import pandas as pd
import numpy as np
from glob import glob
from datetime import datetime
from typing import List, Dict

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.config import load_backtest_config

# Import all indicators
from src.indicators.BOS import detect_bos, to_json as bos_to_json
from src.indicators.CHOCH import detect_choch, to_json as choch_to_json
from src.indicators.order_block import detect_order_blocks, to_json as ob_to_json
from src.indicators.FVG import detect_fvg, to_json as fvg_to_json
from src.indicators.eqh_eql import detect_eqh_eql, to_json as eqhl_to_json
from src.indicators.imbalance import detect_imbalance, to_json as imbalance_to_json
from src.indicators.breaker_block import detect_breaker_blocks, to_json as breaker_to_json
from src.indicators.mitigation_block import detect_mitigation_blocks, to_json as mitigation_to_json
from src.indicators.retracement import detect_retracements, to_json as retracement_to_json

# Swing indicator
from src.indicators.swing import detect_swings, to_json as swing_to_json


# ==================== CONFIGURATION ====================

config = load_backtest_config(include_strategy=False)
DATA_DIR = config.get('folder_path', 'res/data')
OUTPUT_DIR = 'chart/public/data/indicators'

COLORS = {
    'bullish_candle': '#26a69a',
    'bearish_candle': '#ef5350',
}


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


# ==================== EXPORT HELPERS ====================

def datetime_to_timestamp(dt) -> int:
    """Convert datetime to Unix timestamp (seconds)."""
    if isinstance(dt, pd.Timestamp):
        return int(dt.timestamp())
    elif isinstance(dt, datetime):
        return int(dt.timestamp())
    elif isinstance(dt, np.datetime64):
        return int(pd.Timestamp(dt).timestamp())
    return int(dt)


def export_candles(df: pd.DataFrame) -> List[dict]:
    """Export candlestick data in lightweight-charts format."""
    candles = []
    times = df['Time'].values
    opens = df['Open'].values
    highs = df['High'].values
    lows = df['Low'].values
    closes = df['Close'].values

    for i in range(len(df)):
        candles.append({
            'time': int(pd.Timestamp(times[i]).timestamp()),
            'open': float(opens[i]),
            'high': float(highs[i]),
            'low': float(lows[i]),
            'close': float(closes[i]),
        })
    return candles


def export_volume(df: pd.DataFrame) -> List[dict]:
    """Export volume data in lightweight-charts histogram format."""
    volume = []
    times = df['Time'].values
    opens = df['Open'].values
    closes = df['Close'].values
    volumes = df['Volume'].values if 'Volume' in df.columns else np.zeros(len(df))

    for i in range(len(df)):
        is_bullish = closes[i] >= opens[i]
        volume.append({
            'time': int(pd.Timestamp(times[i]).timestamp()),
            'value': float(volumes[i]),
            'color': COLORS['bullish_candle'] if is_bullish else COLORS['bearish_candle'],
        })
    return volume


# ==================== MAIN PROCESSING ====================

def run_all_indicators(df: pd.DataFrame) -> dict:
    """
    Run ALL SMC indicators on a DataFrame.
    Each indicator uses its own default config defined in its own file.

    Returns dict with all indicator results in JSON-serializable format.
    """
    results = {}

    # 1. BOS (pass times for same timestamp conversion as candles → alignment)
    print("    BOS...", end='', flush=True)
    bos_list = detect_bos(df)
    results['bos'] = bos_to_json(bos_list, times=df['Time'].values)
    print(f" {len(bos_list)} found")

    # 2. CHoCH (pass times for alignment with candles)
    print("    CHoCH...", end='', flush=True)
    choch_list = detect_choch(df)
    results['choch'] = choch_to_json(choch_list, times=df['Time'].values)
    print(f" {len(choch_list)} found")

    # 3. Order Blocks
    print("    Order Blocks...", end='', flush=True)
    ob_list = detect_order_blocks(df)
    results['orderBlocks'] = ob_to_json(ob_list, df)
    print(f" {len(ob_list)} found")

    # 4. FVG
    print("    FVG...", end='', flush=True)
    fvg_list = detect_fvg(df)
    results['fvg'] = fvg_to_json(fvg_list, times=df['Time'].values)
    print(f" {len(fvg_list)} found")

    # 5. EQH/EQL
    print("    EQH/EQL...", end='', flush=True)
    eqhl_list = detect_eqh_eql(df)
    results['eqhEql'] = eqhl_to_json(eqhl_list, times=df['Time'].values)
    print(f" {len(eqhl_list)} found")

    # 6. Imbalance
    print("    Imbalance...", end='', flush=True)
    imb_list = detect_imbalance(df)
    results['imbalance'] = imbalance_to_json(imb_list)
    print(f" {len(imb_list)} found")

    # 7. Breaker Blocks
    print("    Breaker Blocks...", end='', flush=True)
    breaker_list = detect_breaker_blocks(df)
    results['breakerBlocks'] = breaker_to_json(breaker_list, df)
    print(f" {len(breaker_list)} found")

    # 8. Mitigation Blocks
    print("    Mitigation Blocks...", end='', flush=True)
    mit_list = detect_mitigation_blocks(df)
    results['mitigationBlocks'] = mitigation_to_json(mit_list, df)
    print(f" {len(mit_list)} found")

    # 9. Retracement
    print("    Retracement...", end='', flush=True)
    ret_list = detect_retracements(df)
    results['retracement'] = retracement_to_json(ret_list)
    print(f" {len(ret_list)} found")

    # 10. Swings (HH/HL/LH/LL)
    print("    Swings...", end='', flush=True)
    swings = detect_swings(df)
    results['swing'] = swing_to_json(swings, times=df['Time'].values)
    print(f" {len(swings)} found")

    return results


def export_symbol(symbol_info: dict) -> dict:
    """Process and export a single symbol to JSON."""
    print(f"\nProcessing {symbol_info['id']}...")

    # Load data
    df = load_data(symbol_info['data_file'])
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
            'eqhEql': len(indicator_results['eqhEql']),
            'imbalance': len(indicator_results['imbalance']),
            'breakerBlocks': len(indicator_results['breakerBlocks']),
            'mitigationBlocks': len(indicator_results['mitigationBlocks']),
            'retracement': len(indicator_results['retracement']),
            'swing': len(indicator_results.get('swing', [])),
        },
    }


def export_symbols_list(symbols: List[dict]):
    """Export list of available symbols to symbols.json."""
    output_file = os.path.join(OUTPUT_DIR, 'symbols.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'symbols': symbols,
            'exportedAt': datetime.now().isoformat(),
        }, f, indent=2)
    print(f"\nSymbols list saved to {output_file}")


# ==================== ENTRY POINT ====================

def main():
    """Main function to run all indicators and export to JSON."""
    print("=" * 60)
    print("SMC Indicators - Export All to JSON")
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
              f"FVG={ind['fvg']}, EQH/EQL={ind['eqhEql']}")
        print(f"    Imbalance={ind['imbalance']}, Breaker={ind['breakerBlocks']}, "
              f"Mitigation={ind['mitigationBlocks']}, Retracement={ind['retracement']}")
    print(f"\nOutput directory: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
