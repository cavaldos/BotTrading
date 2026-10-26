"""
Trading Strategy Backtester
===========================
Main entry point for running the backtesting system.

Architecture:
- strategy/: the strategy plug-in generates signals (entry info only)
- backtest/statistics.py: TradeSimulator calculates exits, then generates reports

The strategy is chosen in strategy.yaml (`strategy: smc` -> src/strategy/smc/).
To change strategy logic, edit only its package; see src/strategy/__init__.py.
"""

import argparse
from pathlib import Path
from typing import cast

import pandas as pd
from tqdm import tqdm

# Strategy plug-in chosen in strategy.yaml
from src.strategy import BaseConfig, BaseStrategy, build_strategy_config, create_strategy, strategy_label

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
from src.config.load_config import load_yaml_config

# Import utility functions
from src.utils.remove_all_files import remove_all_files
from src.core.symbols import symbol_from_filename as extract_base_symbol
from src.core.timeframe import convert_timeframe, normalize_timeframes


# ==============================================================================
# SYSTEM CONFIG (Default values - can be overridden by backtest.yaml/strategy.yaml)
# ==============================================================================
DEFAULT_DATA_FOLDER = "res/data"
DEFAULT_OUTPUT_FOLDER = "res/output"
DEFAULT_REPORT_FOLDER = "res/report"
DEFAULT_BACKTEST_RESULTS_FILE = "backtest_results"
# Multi-timeframe settings (anti-repainting)
DEFAULT_SIGNAL_TIMEFRAMES = ["5m"]
DEFAULT_ENTRY_TIMEFRAME = "1m"


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


def normalize_signal_timeframes(value) -> list[str]:
    return normalize_timeframes(value, default=DEFAULT_SIGNAL_TIMEFRAMES)


# ============================================================================== 
# MAIN EXECUTION
# ==============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the strategy backtester.")
    parser.add_argument("--backtest-config", default=None, help="Path to backtest YAML config")
    parser.add_argument("--strategy-config", default=None, help="Path to strategy YAML config")
    return parser.parse_args()


def main():
    """Main execution function"""
    args = parse_args()
    print("=" * 60)
    print("Strategy Backtester")
    print("=" * 60)
    data_config = load_backtest_config(include_strategy=False)
    if args.backtest_config:
        data_config.update(load_backtest_config(config_path=args.backtest_config, include_strategy=False))

    strategy_cfg = load_strategy_config()
    if args.strategy_config:
        strategy_cfg.update(load_yaml_config(config_path=args.strategy_config, default_name="strategy.yaml"))
        strategy_cfg["signal_timeframe"] = normalize_signal_timeframes(
            strategy_cfg.get("signal_timeframe", DEFAULT_SIGNAL_TIMEFRAMES)
        )
        strategy_cfg["entry_timeframe"] = str(
            strategy_cfg.get("entry_timeframe", DEFAULT_ENTRY_TIMEFRAME)
        ).strip() or DEFAULT_ENTRY_TIMEFRAME
    data_folder = str(data_config.get('folder_path', DEFAULT_DATA_FOLDER)).strip() or DEFAULT_DATA_FOLDER
    output_folder = str(data_config.get('output_folder', DEFAULT_OUTPUT_FOLDER)).strip() or DEFAULT_OUTPUT_FOLDER

    signal_timeframes = normalize_signal_timeframes(strategy_cfg.get('signal_timeframe', DEFAULT_SIGNAL_TIMEFRAMES))
    entry_tf = str(strategy_cfg.get('entry_timeframe', DEFAULT_ENTRY_TIMEFRAME)).strip() or DEFAULT_ENTRY_TIMEFRAME

    configs_by_signal: dict[str, BaseConfig] = {}
    strategies_by_signal: dict[str, BaseStrategy] = {}
    simulators_by_signal: dict[str, TradeSimulator] = {}
    for signal_tf in signal_timeframes:
        tf_config = build_strategy_config(
            data_folder=data_folder,
            output_folder=output_folder,
            strategy_config=strategy_cfg,
            overrides={
                "signal_timeframe": signal_tf,
                "entry_timeframe": entry_tf,
            },
        )
        configs_by_signal[signal_tf] = tf_config
        strategies_by_signal[signal_tf] = create_strategy(tf_config)
        simulators_by_signal[signal_tf] = TradeSimulator(tf_config)

    primary_signal_tf = signal_timeframes[0]
    primary_config = configs_by_signal[primary_signal_tf]
    use_multi_tf = len(signal_timeframes) > 1 or any(tf != entry_tf for tf in signal_timeframes)

    print(f"\nStrategy: {strategy_label(primary_config)} ({primary_config.strategy})")
    print("Configuration:")
    print(f"  - Risk/Reward Ratio: 1:{primary_config.risk_reward_ratio}")
    print(f"  - Risk per Trade: ${primary_config.risk_per_trade}")
    if hasattr(primary_config, "flex_tp_risk_boost_factor"):
        print(f"  - Flex TP Risk Boost Factor: {primary_config.flex_tp_risk_boost_factor}")
    print(f"  - Data Folder: {primary_config.data_folder}")
    print(f"  - Output Folder: {primary_config.output_folder}")

    if use_multi_tf:
        print("\n  [MULTI-TIMEFRAME MODE - Anti-Repainting]")
        print(f"  - Signal Timeframes: {', '.join(signal_timeframes)} (for indicators)")
        print(f"  - Entry Timeframe: {entry_tf} (for precise entry)")
    else:
        print("\n  [SINGLE-TIMEFRAME MODE]")
        print(f"  - Timeframe: {primary_signal_tf}")

    # Create output folder if not exists
    output_dir = Path(primary_config.output_folder)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Clear output folder before running
    print(f"\nClearing output folder: {primary_config.output_folder}")
    remove_all_files(primary_config.output_folder)

    # Find CSV files in data folder
    data_dir = Path(primary_config.data_folder)
    csv_files = list(data_dir.glob(f"*_{entry_tf}.csv"))

    if not csv_files:
        print(f"\nNo {entry_tf} CSV files found in {primary_config.data_folder}")
        print(f"Backtest requires entry timeframe files: *_{entry_tf}.csv")
        return

    print(f"\nFound {len(csv_files)} data file(s)")
    print("-" * 60)

    all_summaries = []
    all_trades = []

    # Progress bar for processing files
    for csv_file in tqdm(csv_files, desc="Processing files", unit="file"):
        base_symbol = extract_base_symbol(csv_file.name)
        tqdm.write(f"\nProcessing: {base_symbol}")

        # Load entry timeframe data once, reuse for all signal timeframes
        df_entry = load_data(str(csv_file))
        tqdm.write(f"  - Loaded {len(df_entry)} candles ({entry_tf})")
        tqdm.write(f"  - Date range: {df_entry['Time'].min()} to {df_entry['Time'].max()}")

        for signal_tf in signal_timeframes:
            symbol = f"{base_symbol}_{signal_tf}"
            strategy = strategies_by_signal[signal_tf]
            simulator = simulators_by_signal[signal_tf]

            if signal_tf != entry_tf:
                tqdm.write(f"  - Converting {entry_tf} -> {signal_tf} for indicators...")
                converted_signal_df = convert_timeframe(df_entry, signal_tf)
                if not isinstance(converted_signal_df, pd.DataFrame) or converted_signal_df.empty:
                    tqdm.write(f"  - Skipped {signal_tf}: conversion returned no candles")
                    continue
                signal_df = cast(pd.DataFrame, converted_signal_df)
                tqdm.write(f"  - Converted to {len(signal_df)} candles ({signal_tf})")
                signals = strategy.generate_signals(signal_df, symbol, df_entry=df_entry)
            else:
                signals = strategy.generate_signals(df_entry, symbol)

            tqdm.write(f"  - [{signal_tf}] Generated {len(signals)} signals")

            trades = simulator.simulate(signals, df_entry)
            all_trades.extend(trades)
            tqdm.write(f"  - [{signal_tf}] Simulated {len(trades)} trades")

            summary = generate_summary_report(trades, symbol, configs_by_signal[signal_tf])
            all_summaries.append(summary)
            print_quick_stats(summary)

            generate_trade_orders(trades, str(output_dir), symbol)

    print("\n" + "-" * 60)
    print("Generating summary report...")

    # Create report folder if needed
    report_dir = Path(data_config.get('report_folder', DEFAULT_REPORT_FOLDER))
    report_dir.mkdir(parents=True, exist_ok=True)

    # Generate text summary
    backtest_file = str(data_config.get('backtest_results_file', DEFAULT_BACKTEST_RESULTS_FILE)).strip() or DEFAULT_BACKTEST_RESULTS_FILE
    summary_path = str(report_dir / f"{backtest_file}.md")
    generate_text_report(all_summaries, primary_config, summary_path)

    # Print final summary
    print_final_summary(all_trades, primary_config)


if __name__ == "__main__":
    main()
