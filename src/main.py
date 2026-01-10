"""
Smart Money Concepts (SMC) Trading Strategy Backtester
=======================================================
Main entry point for running the backtesting system.

Modules:
- strategy.py: SMC strategy logic (swing points, OB, FVG, signals, backtester)
- statistics.py: Report generation and statistics
"""

import sys
from pathlib import Path

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

# Import strategy components
from strategy import Config, Backtester, Trade

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
# STRATEGY CONFIG (Trading strategy parameters - adjust as needed)
# ==============================================================================

# --- Risk Management ---
RISK_REWARD_RATIO = 2.0               # Take Profit = Stop Loss * RR (e.g., 2.0 = 1:2)
RISK_PER_TRADE = 10.0                 # Risk amount in USD per trade

# --- Market Structure ---
SWING_LENGTH = 15                     # Bars required for swing point detection
INTERNAL_LENGTH = 5                   # Bars for internal structure

# --- Order Block (OB) ---
OB_FILTER = "ATR"                     # "ATR" or "CMR" (Cumulative Mean Range)
OB_MITIGATION = "HighLow"             # "Close" or "HighLow"
MAX_ORDER_BLOCKS = 20                 # Maximum OBs to track
CHOCH_ONLY = True                     # Only trade OBs from CHoCH signals (better win rate)

# --- Fair Value Gap (FVG) ---
FVG_AUTO_THRESHOLD = True             # Auto calculate FVG threshold
FVG_THRESHOLD_MULTIPLIER = 2.0        # Threshold = avg_delta * multiplier

# --- Equal Highs/Lows ---
EQHL_LENGTH = 3                       # Confirmation bars
EQHL_THRESHOLD = 0.1                  # Threshold as % of ATR (0.1 = 10%)

# --- Premium/Discount Zone ---
PREMIUM_THRESHOLD = 0.7               # Above 70% = Premium zone
DISCOUNT_THRESHOLD = 0.3              # Below 30% = Discount zone

# --- ATR Settings ---
ATR_PERIOD = 14                       # ATR calculation period
ATR_MULTIPLIER_SL = 1.5               # Stop Loss = ATR * multiplier

# --- Entry Conditions ---
MIN_ENTRY_CONDITIONS = 3              # Minimum conditions required to enter trade

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
    """Create Config from user settings"""
    return Config(
        # Folders
        data_folder=DATA_FOLDER,
        output_folder=OUTPUT_FOLDER,
        # Risk/Reward
        risk_reward_ratio=RISK_REWARD_RATIO,
        risk_per_trade=RISK_PER_TRADE,
        # Market Structure
        swing_length=SWING_LENGTH,
        internal_length=INTERNAL_LENGTH,
        # Order Block
        ob_filter=OB_FILTER,
        ob_mitigation=OB_MITIGATION,
        max_order_blocks=MAX_ORDER_BLOCKS,
        choch_only=CHOCH_ONLY,
        # FVG
        fvg_auto_threshold=FVG_AUTO_THRESHOLD,
        fvg_threshold_multiplier=FVG_THRESHOLD_MULTIPLIER,
        # Equal Highs/Lows
        eqhl_length=EQHL_LENGTH,
        eqhl_threshold=EQHL_THRESHOLD,
        # Premium/Discount
        premium_threshold=PREMIUM_THRESHOLD,
        discount_threshold=DISCOUNT_THRESHOLD,
        # ATR
        atr_period=ATR_PERIOD,
        atr_multiplier_sl=ATR_MULTIPLIER_SL,
        # Entry
        min_entry_conditions=MIN_ENTRY_CONDITIONS,
    )


def main():
    """Main execution function"""
    print("=" * 60)
    print("Smart Money Concepts (SMC) Strategy Backtester")
    print("=" * 60)
    
    # Initialize configuration from user settings
    config = create_config()
    
    # Print current configuration
    print(f"\nConfiguration:")
    print(f"  - Risk/Reward Ratio: 1:{config.risk_reward_ratio}")
    print(f"  - Risk per Trade: ${config.risk_per_trade}")
    print(f"  - CHoCH Only: {config.choch_only}")
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
