"""
Trading Strategy Backtester
===========================
Main entry point for running the backtesting system.

Architecture:
- strategy.py: Strategy generates Signals (entry info only)
- statistics.py: TradeSimulator calculates exits, then generates reports

To change strategy:
1. Replace strategy.py with new strategy file
2. Ensure new strategy exports: Config, Strategy, Signal
3. Strategy must have generate_signals(df, symbol) -> List[Signal] method
"""

import sys
from pathlib import Path

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from tqdm import tqdm

# Import strategy components (interface contract)
from strategy import Config, Strategy, Signal, STRATEGY_NAME

# Import statistics/reporting functions
from statistics import (
    Trade,
    TradeSimulator,
    generate_trade_report,
    generate_summary_report,
    generate_text_report,
    print_quick_stats,
    print_final_summary
)


# ==============================================================================
# SYSTEM CONFIG (Fixed settings - paths and folders)
# ==============================================================================
DATA_FOLDER = "src/data"                  # Input folder containing CSV data files
OUTPUT_FOLDER = "src/output"          # Output folder for trade reports
REPORT_FOLDER = "src/report"           # Folder for markdown summary report


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def load_data(file_path: str) -> pd.DataFrame:
    """Load CSV data"""
    df = pd.read_csv(file_path)
    df['Time'] = pd.to_datetime(df['Time'])
    # Clean column names (remove spaces)
    df.columns = df.columns.str.strip()
    return df


def get_symbol_from_filename(filename: str) -> str:
    """Extract symbol name from filename"""
    # e.g., BTCUSDT_15m.csv -> BTCUSDT_15m
    return filename.replace('.csv', '')


def create_config() -> Config:
    """
    Create Config with system paths.
    Strategy-specific settings use defaults from strategy.py.
    """
    return Config(
        data_folder=DATA_FOLDER,
        output_folder=OUTPUT_FOLDER,
    )


# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

def main():
    """Main execution function"""
    print("=" * 60)
    print(f"Strategy Backtester - {STRATEGY_NAME}")
    print("=" * 60)
    
    # Initialize configuration (system paths + strategy defaults)
    config = create_config()
    
    # Print current configuration
    strategy_info = config.get_strategy_info()
    print(f"\nStrategy: {strategy_info['name']}")
    print(f"Configuration:")
    print(f"  - Risk/Reward Ratio: 1:{config.risk_reward_ratio}")
    print(f"  - Risk per Trade: ${config.risk_per_trade}")
    print(f"  - Data Folder: {config.data_folder}")
    print(f"  - Output Folder: {config.output_folder}")
    
    # Create output folder if not exists
    output_dir = Path(config.output_folder)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Find all CSV files in data folder
    data_dir = Path(config.data_folder)
    csv_files = list(data_dir.glob("*.csv"))
    
    if not csv_files:
        print(f"\nNo CSV files found in {config.data_folder}")
        return
    
    print(f"\nFound {len(csv_files)} data file(s)")
    print("-" * 60)
    
    # Initialize strategy and simulator
    strategy = Strategy(config)
    simulator = TradeSimulator(config)
    
    all_summaries = []
    all_trades = []
    
    # Progress bar for processing files
    for csv_file in tqdm(csv_files, desc="Processing files", unit="file"):
        symbol = get_symbol_from_filename(csv_file.name)
        tqdm.write(f"\nProcessing: {symbol}")
        
        # Load data
        df = load_data(str(csv_file))
        tqdm.write(f"  - Loaded {len(df)} candles")
        tqdm.write(f"  - Date range: {df['Time'].min()} to {df['Time'].max()}")
        
        # Step 1: Generate signals (entry info only)
        signals = strategy.generate_signals(df, symbol)
        tqdm.write(f"  - Generated {len(signals)} signals")
        
        # Step 2: Simulate trades (calculate exits)
        trades = simulator.simulate(signals, df)
        all_trades.extend(trades)
        tqdm.write(f"  - Simulated {len(trades)} trades")
        
        # Generate summary
        summary = generate_summary_report(trades, symbol, config)
        all_summaries.append(summary)
        
        # Print quick stats
        print_quick_stats(summary)
        
        # Generate trade CSV report for this symbol
        generate_trade_report(trades, str(output_dir), symbol)
    
    print("\n" + "-" * 60)
    print("Generating summary report...")
    
    # Create report folder if needed
    report_dir = Path(REPORT_FOLDER)
    report_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate text summary
    summary_path = str(report_dir / "backtest_results.md")
    generate_text_report(all_summaries, config, summary_path)
    
    # Print final summary
    print_final_summary(all_trades)


if __name__ == "__main__":
    main()
