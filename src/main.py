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

from pathlib import Path

import pandas as pd
from tqdm import tqdm

# Import strategy components (interface contract)
from src.strategy import Config, Strategy, Signal, STRATEGY_NAME

# Import statistics/reporting functions
from src.statistics import (
    Trade,
    TradeSimulator,
    generate_trade_orders,
    generate_summary_report,
    generate_text_report,
    print_quick_stats,
    print_final_summary
)

# Import config loader
from src.config import load_config


# ==============================================================================
# SYSTEM CONFIG (Default values - can be overridden by data_config.yaml)
# ==============================================================================
DEFAULT_DATA_FOLDER = "res/data"
DEFAULT_OUTPUT_FOLDER = "res/output"
DEFAULT_REPORT_FOLDER = "res/report"
DEFAULT_BACKTEST_RESULTS_FILE = "backtest_results"
DEFAULT_RISK_REWARD_RATIO = 2.0


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
     Create Config with system paths and risk settings from data_config.yaml.
     Strategy-specific settings use defaults from strategy.py.
     """
     # Load paths from data_config.yaml
     data_config = load_config()
     
     data_folder = data_config.get('folder_path', DEFAULT_DATA_FOLDER)
     output_folder = data_config.get('output_folder', DEFAULT_OUTPUT_FOLDER)
     
     # Support both top-level and nested strategy section
     if 'strategy' in data_config and isinstance(data_config['strategy'], dict):
         risk_reward_ratio = data_config['strategy'].get('risk_reward_ratio', DEFAULT_RISK_REWARD_RATIO)
     else:
         risk_reward_ratio = data_config.get('risk_reward_ratio', DEFAULT_RISK_REWARD_RATIO)
     
     return Config(
         data_folder=data_folder,
         output_folder=output_folder,
         risk_reward_ratio=risk_reward_ratio,
     )


def get_report_folder() -> str:
    """Get report folder from config"""
    data_config = load_config()
    return data_config.get('report_folder', DEFAULT_REPORT_FOLDER)


def get_backtest_results_file() -> str:
    """Get backtest results filename from config"""
    data_config = load_config()
    return data_config.get('backtest_results_file', DEFAULT_BACKTEST_RESULTS_FILE)


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
        generate_trade_orders(trades, str(output_dir), symbol)
    
    print("\n" + "-" * 60)
    print("Generating summary report...")
    
    # Create report folder if needed
    report_dir = Path(get_report_folder())
    report_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate text summary
    backtest_file = get_backtest_results_file()
    summary_path = str(report_dir / f"{backtest_file}.md")
    generate_text_report(all_summaries, config, summary_path)
    
    # Print final summary
    print_final_summary(all_trades)


if __name__ == "__main__":
    main()
