"""
Trade Verification Script
=========================
Verifies trade results by re-simulating exits from Entry_Time to Exit_Time.
Compares calculated result with recorded result in trades CSV.
"""

import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional


def load_data(candles_path: str, trades_path: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Load candles and trades data from CSV files."""
    candles = pd.read_csv(candles_path)
    candles['Time'] = pd.to_datetime(candles['Time'])
    
    trades = pd.read_csv(trades_path)
    trades['Entry_Time'] = pd.to_datetime(trades['Entry_Time'])
    trades['Exit_Time'] = pd.to_datetime(trades['Exit_Time'])
    
    return candles, trades


def verify_single_trade(trade: pd.Series, candles: pd.DataFrame) -> Tuple[bool, str, str]:
    """
    Verify a single trade by checking SL/TP hit from Entry_Time to Exit_Time.
    
    Returns:
        Tuple of (is_valid, expected_result, actual_result)
    """
    trade_type = str(trade['Type'])
    entry_time = trade['Entry_Time']
    exit_time = trade['Exit_Time']
    stop_loss = float(trade['Stop_Loss'])
    take_profit = float(trade['Take_Profit'])
    recorded_result = str(trade['Result'])
    
    # Filter candles between entry and exit time
    mask = (candles['Time'] > entry_time) & (candles['Time'] <= exit_time)
    relevant_candles = candles[mask]
    
    if relevant_candles.empty:
        return False, "NO_DATA", recorded_result
    
    # Check each candle for SL/TP hit
    calculated_result = "Open"
    
    for _, row in relevant_candles.iterrows():
        high = row['High']
        low = row['Low']
        
        if trade_type == 'LONG':
            # Check SL first (worst case scenario)
            if low <= stop_loss:
                calculated_result = "Loss"
                break
            # Check TP
            if high >= take_profit:
                calculated_result = "Win"
                break
        else:  # SHORT
            # Check SL first (worst case scenario)
            if high >= stop_loss:
                calculated_result = "Loss"
                break
            # Check TP
            if low <= take_profit:
                calculated_result = "Win"
                break
    
    # Compare results
    is_valid = calculated_result.lower() == recorded_result.lower()
    
    return is_valid, calculated_result, recorded_result


def verify_trades(candles_path: str, trades_path: str) -> None:
    """
    Main function to verify all trades and print statistics.
    """
    print("=" * 60)
    print("TRADE VERIFICATION")
    print("=" * 60)
    
    # Load data
    print(f"\nLoading data...")
    print(f"  Candles: {candles_path}")
    print(f"  Trades:  {trades_path}")
    
    candles, trades = load_data(candles_path, trades_path)
    
    print(f"\n  Total candles: {len(candles)}")
    print(f"  Total trades:  {len(trades)}")
    
    # Verify each trade
    print("\n" + "-" * 60)
    print("VERIFICATION RESULTS")
    print("-" * 60)
    
    valid_count = 0
    invalid_count = 0
    invalid_trades = []
    
    trade_num = 0
    for idx, trade in trades.iterrows():
        trade_num += 1
        is_valid, calculated, recorded = verify_single_trade(trade, candles)
        
        if is_valid:
            valid_count += 1
            status = "✓ VALID"
        else:
            invalid_count += 1
            status = "✗ INVALID"
            invalid_trades.append({
                'index': trade_num,
                'type': trade['Type'],
                'entry_time': trade['Entry_Time'],
                'recorded': recorded,
                'calculated': calculated
            })
        
        print(f"Trade {trade_num}: {trade['Type']:<5} | "
              f"Entry: {trade['Entry_Time']} | "
              f"Recorded: {recorded:<4} | "
              f"Calculated: {calculated:<4} | "
              f"{status}")
    
    # Print summary
    total = valid_count + invalid_count
    valid_pct = (valid_count / total * 100) if total > 0 else 0
    invalid_pct = (invalid_count / total * 100) if total > 0 else 0
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Total trades:   {total}")
    print(f"  Valid trades:   {valid_count} ({valid_pct:.1f}%)")
    print(f"  Invalid trades: {invalid_count} ({invalid_pct:.1f}%)")
    
    # Print details of invalid trades
    if invalid_trades:
        print("\n" + "-" * 60)
        print("INVALID TRADE DETAILS")
        print("-" * 60)
        for t in invalid_trades:
            print(f"  Trade {t['index']}: {t['type']}")
            print(f"    Entry Time: {t['entry_time']}")
            print(f"    Recorded Result:   {t['recorded']}")
            print(f"    Calculated Result: {t['calculated']}")
            print()
    
    print("=" * 60)


if __name__ == "__main__":
    # Default paths
    CANDLES_PATH = "res/data/BTCUSDT_5m.csv"
    TRADES_PATH = "res/output/BTCUSDT_5m_trades.csv"
    
    verify_trades(str(CANDLES_PATH), str(TRADES_PATH))
