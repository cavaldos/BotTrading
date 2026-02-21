"""
Trading Strategy Backtester
===========================
Main entry point for running the backtesting system.

Architecture:
- strategy/: Strategy generates signals (entry info only)
- backtest/statistics.py: TradeSimulator calculates exits, then generates reports

To change strategy:
1. Edit strategy logic in src/strategy/smc_strategy.py
2. Keep interface exports: Config, Strategy, Signal
"""

from pathlib import Path
from dataclasses import fields

import pandas as pd
from tqdm import tqdm

# Import strategy components (interface contract)
from src.strategy import Config, Strategy, Signal, STRATEGY_NAME

# Import statistics/reporting functions
from src.backtest.statistics import (
    Trade,
    TradeSimulator,
    generate_trade_orders,
    generate_summary_report,
    generate_text_report,
    print_quick_stats,
    print_final_summary
)

# Import config loader
from src.config import load_backtest_config, load_strategy_config

# Import utility functions
from src.utils.remove_all_files import remove_all_files


# ==============================================================================
# SYSTEM CONFIG (Default values - can be overridden by backtest.yaml/strategy.yaml)
# ==============================================================================
DEFAULT_DATA_FOLDER = "res/data"
DEFAULT_OUTPUT_FOLDER = "res/output"
DEFAULT_REPORT_FOLDER = "res/report"
DEFAULT_BACKTEST_RESULTS_FILE = "backtest_results"
DEFAULT_RISK_REWARD_RATIO = 2.0
# Structure Detection
DEFAULT_SWING_LENGTH = 10
DEFAULT_INTERNAL_LENGTH = 3
# EQH/EQL Detection
DEFAULT_EQHL_LENGTH = 3
DEFAULT_EQHL_THRESHOLD = 0.3
# Order Block
DEFAULT_OB_MITIGATION = "high_low"
DEFAULT_OB_BODY_RATIO = 0.35
# Entry filters
DEFAULT_REQUIRE_LIQUIDITY = True
DEFAULT_REQUIRE_REVERSAL_PATTERN = True
DEFAULT_ENABLE_SIMPLE_REVERSAL = True
DEFAULT_SIMPLE_REVERSAL_WICK_RATIO = 0.25
DEFAULT_TOUCH_TOLERANCE_PCT = 0.0
DEFAULT_TOUCH_CONFIRMATION_BARS = 0
# FVG
DEFAULT_FVG_ATR_FILTER = True
# Risk Management
DEFAULT_SL_BUFFER_PCT = 0.1
DEFAULT_SL_MULTIPLIER = 1.05
DEFAULT_MIN_SL_PCT = 0.05
DEFAULT_RISK_PER_TRADE = 10.0
DEFAULT_REVERSAL_PATTERN_IDS = [0]


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
     Create strategy Config by composing backtest and strategy config files.
     """
     data_config = load_backtest_config(include_strategy=False)
     strategy_cfg = load_strategy_config()
      
     data_folder = data_config.get('folder_path', DEFAULT_DATA_FOLDER)
     output_folder = data_config.get('output_folder', DEFAULT_OUTPUT_FOLDER)
      
     strategy_defaults = {
         "risk_reward_ratio": DEFAULT_RISK_REWARD_RATIO,
         "swing_length": DEFAULT_SWING_LENGTH,
         "internal_length": DEFAULT_INTERNAL_LENGTH,
         "eqhl_length": DEFAULT_EQHL_LENGTH,
         "eqhl_threshold": DEFAULT_EQHL_THRESHOLD,
         "ob_mitigation": DEFAULT_OB_MITIGATION,
         "ob_body_ratio": DEFAULT_OB_BODY_RATIO,
         "require_liquidity": DEFAULT_REQUIRE_LIQUIDITY,
         "require_reversal_pattern": DEFAULT_REQUIRE_REVERSAL_PATTERN,
         "enable_simple_reversal": DEFAULT_ENABLE_SIMPLE_REVERSAL,
         "simple_reversal_wick_ratio": DEFAULT_SIMPLE_REVERSAL_WICK_RATIO,
         "touch_tolerance_pct": DEFAULT_TOUCH_TOLERANCE_PCT,
         "touch_confirmation_bars": DEFAULT_TOUCH_CONFIRMATION_BARS,
         "fvg_atr_filter": DEFAULT_FVG_ATR_FILTER,
         "sl_buffer_pct": DEFAULT_SL_BUFFER_PCT,
         "sl_multiplier": DEFAULT_SL_MULTIPLIER,
         "min_sl_pct": DEFAULT_MIN_SL_PCT,
         "risk_per_trade": DEFAULT_RISK_PER_TRADE,
         "reversal_pattern_ids": DEFAULT_REVERSAL_PATTERN_IDS,
     }

     config_fields = {item.name for item in fields(Config)}
     strategy_kwargs = {
         key: strategy_cfg.get(key, default_value)
         for key, default_value in strategy_defaults.items()
         if key in config_fields
     }

     base_kwargs = {}
     if "data_folder" in config_fields:
         base_kwargs["data_folder"] = data_folder
     if "output_folder" in config_fields:
         base_kwargs["output_folder"] = output_folder

     return Config(**base_kwargs, **strategy_kwargs)


def get_report_folder() -> str:
    """Get report folder from config"""
    data_config = load_backtest_config(include_strategy=False)
    return data_config.get('report_folder', DEFAULT_REPORT_FOLDER)


def get_backtest_results_file() -> str:
    """Get backtest results filename from config"""
    data_config = load_backtest_config(include_strategy=False)
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
    strategy_name = strategy_info.get("name", STRATEGY_NAME)
    print(f"\nStrategy: {strategy_name}")
    print(f"Configuration:")
    print(f"  - Risk/Reward Ratio: 1:{config.risk_reward_ratio}")
    print(f"  - Risk per Trade: ${config.risk_per_trade}")
    print(f"  - Data Folder: {config.data_folder}")
    print(f"  - Output Folder: {config.output_folder}")
    
    # Create output folder if not exists
    output_dir = Path(config.output_folder)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Clear output folder before running
    print(f"\nClearing output folder: {config.output_folder}")
    remove_all_files(config.output_folder)
    
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
