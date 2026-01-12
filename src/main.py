"""
Trading Strategy Backtester
===========================
Main entry point for running the backtesting system.

Modules:
- strategy.py: Trading strategy logic (swap this file to change strategy)
- statistics.py: Report generation and statistics

To change strategy:
1. Replace strategy.py with new strategy file
2. Ensure new strategy exports: Config, Backtester, Trade
3. Backtester must have run(df, symbol) -> List[Trade] method
"""

import sys
from pathlib import Path

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

# Import strategy components (interface contract)
from strategy import Config, Backtester, Trade, STRATEGY_NAME

# Import statistics/reporting functions
from statistics import (
    generate_trade_report,
    generate_summary_report,
    generate_markdown_report,
    print_quick_stats,
    print_final_summary
)


# ==============================================================================
# SYSTEM CONFIG (Fixed settings - paths and folders)
# ==============================================================================
DATA_FOLDER = "data"                  # Input folder containing CSV data files
OUTPUT_FOLDER = "src/output"          # Output folder for trade reports
REPORT_FOLDER = "./src/res"           # Folder for markdown summary report


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
    
    # Initialize backtester
    backtester = Backtester(config)
    all_summaries = []
    all_trades = []
    
    for csv_file in csv_files:
        symbol = get_symbol_from_filename(csv_file.name)
        print(f"\nProcessing: {symbol}")
        
        # Load data
        df = load_data(str(csv_file))
        print(f"  - Loaded {len(df)} candles")
        print(f"  - Date range: {df['Time'].min()} to {df['Time'].max()}")
        
        # Run backtest
        trades = backtester.run(df, symbol)
        all_trades.extend(trades)
        print(f"  - Generated {len(trades)} trades")
        
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
    
    # Generate markdown summary
    summary_md_path = str(report_dir / "backtest_results.md")
    generate_markdown_report(all_summaries, config, summary_md_path)
    
    # Print final summary
    total_profit = sum(t.pnl for t in all_trades)
    print_final_summary(all_trades, total_profit)


if __name__ == "__main__":
    main()
