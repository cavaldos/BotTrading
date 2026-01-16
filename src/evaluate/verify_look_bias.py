from pathlib import Path

import pandas as pd
from glob import glob

from src.strategy import Config, Strategy

# Import folder settings from main.py
try:
    from src.main import DATA_FOLDER, OUTPUT_FOLDER
except ImportError:
    # Fallback to defaults if main.py not found
    DATA_FOLDER = "res/data"
    OUTPUT_FOLDER = "res/output"


def load_latest_trade_file(output_folder: str):
    """Load the most recent trade CSV file"""
    trade_files = glob(f"{output_folder}/*_trades.csv")
    
    if not trade_files:
        print(f"No trade files found in {output_folder}/")
        return None, None
    
    # Get most recent file by modification time
    latest_file = max(trade_files, key=lambda f: Path(f).stat().st_mtime)
    
    df_trades = pd.read_csv(latest_file)
    df_trades['Entry_Time'] = pd.to_datetime(df_trades['Entry_Time'])
    df_trades['Exit_Time'] = pd.to_datetime(df_trades['Exit_Time'])
    
    return df_trades, latest_file


def load_market_data(data_folder: str, symbol: str):
    """
    Load market data CSV that matches the symbol.
    
    Args:
        data_folder: Path to data folder
        symbol: Symbol name (e.g., "BTCUSDT_15m")
    
    Returns:
        DataFrame or None if not found
    """
    # Try to find exact match first
    data_file = Path(data_folder) / f"{symbol}.csv"
    
    if data_file.exists():
        df = pd.read_csv(data_file)
        df['Time'] = pd.to_datetime(df['Time'])
        df.columns = df.columns.str.strip()
        return df
    
    # If not found, list available files
    csv_files = list(Path(data_folder).glob("*.csv"))
    
    if not csv_files:
        print(f"No data files found in {data_folder}/")
        return None
    
    print(f"Warning: Data file for symbol '{symbol}' not found: {data_file}")
    print(f"Available data files:")
    for f in csv_files:
        print(f"  - {f.name}")
    
    return None


def verify_trade(trade_row, df_market, strategy, symbol):
    """
    Verify a single trade signal.
    
    Returns:
        (is_valid, message)
        - is_valid: True if signal is correct, False otherwise
        - message: Description of the result
    """
    entry_time = trade_row['Entry_Time']
    trade_type = trade_row['Type']
    entry_price = trade_row['Entry_Price']
    
    # Find index for entry_time in market data
    entry_idx = None
    for i, time in enumerate(df_market['Time']):
        if time == entry_time:
            entry_idx = i
            break
    
    if entry_idx is None:
        return False, f"Entry time {entry_time} not found in market data"
    
    # Slice data up to and including entry_time
    df_slice = df_market.iloc[:entry_idx + 1].copy()
    
    # Generate signals on sliced data
    signals = strategy.generate_signals(df_slice, symbol)
    
    # Check if there's a signal at the last candle (entry_time)
    last_candle_time = df_slice['Time'].iloc[-1]
    
    if last_candle_time != entry_time:
        return False, f"Data slice error: last candle {last_candle_time} != entry_time {entry_time}"
    
    # Look for signal at entry_time
    signal_found = None
    for sig in signals:
        if sig.entry_time == entry_time:
            signal_found = sig
            break
    
    if signal_found is None:
        return False, f"Signal NOT found at entry_time {entry_time} (look-ahead bias detected!)"
    
    # Verify signal matches trade
    if signal_found.trade_type != trade_type:
        return False, f"Signal type mismatch: expected {trade_type}, got {signal_found.trade_type}"
    
    # Allow small price difference due to rounding
    price_diff = abs(signal_found.entry_price - entry_price)
    if price_diff > 0.01:
        return False, f"Entry price mismatch: expected {entry_price:.2f}, got {signal_found.entry_price:.2f}"
    
    return True, "OK"


def main():
    print("=" * 80)
    print("TRADE SIGNAL VERIFICATION - NO LOOK-AHEAD BIAS TEST")
    print("=" * 80)
    print()
    
    print(f"Configuration:")
    print(f"  Data folder: {DATA_FOLDER}")
    print(f"  Output folder: {OUTPUT_FOLDER}")
    print()
    
    # Load trade file
    print("Loading trade file...")
    df_trades, trade_file = load_latest_trade_file(OUTPUT_FOLDER)
    
    if df_trades is None:
        return
    
    print(f"Loaded: {trade_file}")
    print(f"Total trades: {len(df_trades)}")
    
    # Extract symbol from trade file name
    symbol = Path(trade_file).stem.replace("_trades", "")
    print(f"Symbol: {symbol}")
    print()
    
    # Load market data for this symbol
    print("Loading market data...")
    df_market = load_market_data(DATA_FOLDER, symbol)
    
    if df_market is None:
        return
    
    print(f"Market data: {len(df_market)} candles")
    print(f"Date range: {df_market['Time'].min()} to {df_market['Time'].max()}")
    print()
    
    # Initialize strategy
    config = Config()
    strategy = Strategy(config)
    
    # Verify each trade
    print("Verifying trades...")
    print("-" * 80)
    
    passed = 0
    failed = 0
    failed_trades = []
    
    total_trades = len(df_trades)
    for idx, trade in df_trades.iterrows():
        # Show progress
        print(f"  Verifying trade {int(idx) + 1}/{total_trades}...", end='\r')
        
        is_valid, message = verify_trade(trade, df_market, strategy, symbol)
        
        if is_valid:
            passed += 1
        else:
            failed += 1
            failed_trades.append({
                'index': int(idx) + 1,
                'entry_time': trade['Entry_Time'],
                'type': trade['Type'],
                'entry_price': trade['Entry_Price'],
                'reason': message
            })
    
    print()  # Clear progress line
    
    # Print results
    print()
    print("=" * 80)
    print("RESULTS")
    print("=" * 80)
    
    total = passed + failed
    pass_rate = (passed / total * 100) if total > 0 else 0
    
    print(f"Total trades tested: {total}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Pass rate: {pass_rate:.1f}%")
    print()
    
    if failed > 0:
        print("=" * 80)
        print("FAILED TRADES")
        print("=" * 80)
        for ft in failed_trades:
            print(f"\nTrade #{ft['index']}")
            print(f"  Entry Time: {ft['entry_time']}")
            print(f"  Type: {ft['type']}")
            print(f"  Entry Price: {ft['entry_price']:.2f}")
            print(f"  Reason: {ft['reason']}")
    else:
        print("✓✓✓ ALL TRADES PASSED - NO LOOK-AHEAD BIAS DETECTED!")
    
    print()
    print("=" * 80)


if __name__ == "__main__":
    main()
