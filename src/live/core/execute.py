"""
Live Trade Signal Generator
===========================
Scan data files and generate trade signals, save to CSV output.
No markdown reports or console statistics.

Supports multi-timeframe mode to prevent repainting:
- signal_timeframe: Used for indicator calculation (e.g., ["3m", "5m"])
- entry_timeframe: Used for precise entry detection (e.g., 1m)

Usage:
    python execute.py
"""

from pathlib import Path
from datetime import datetime, timezone
from dataclasses import fields
from typing import Any
import pandas as pd

# Import strategy components (interface contract)
from src.strategy import Config, Strategy

# Import trade simulation and report generation
from src.backtest.statistics import TradeSimulator, generate_trade_orders

# Import timeframe conversion utility
from src.utils.convertTF import convert_timeframe


# Timeframe string to minutes mapping
def _timeframe_to_minutes(tf: str) -> int:
    """Convert timeframe string to minutes. E.g. '5m'->5, '1h'->60, '4h'->240, '1d'->1440"""
    tf = tf.lower().strip()
    if tf.endswith("m"):
        return int(tf[:-1])
    elif tf.endswith("h"):
        return int(tf[:-1]) * 60
    elif tf.endswith("d"):
        return int(tf[:-1]) * 1440
    return 60  # fallback


def _drop_incomplete_candle(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """
    Drop the last candle if it hasn't closed yet (safety net against repainting).
    
    Compares the last candle's timestamp + timeframe duration against current UTC time.
    If candle_end_time > now, the candle is still forming → drop it.
    """
    if df.empty:
        return df
    
    tf_minutes = _timeframe_to_minutes(timeframe)
    last_candle_time = pd.to_datetime(df['Time'].iloc[-1])
    
    # Make timezone-aware (UTC) for comparison
    if last_candle_time.tzinfo is None:
        last_candle_time = last_candle_time.tz_localize('UTC')
    
    candle_end_time = last_candle_time + pd.Timedelta(minutes=tf_minutes)
    now_utc = datetime.now(timezone.utc)
    
    if candle_end_time > now_utc:
        # Last candle hasn't closed yet → drop it
        return df.iloc[:-1].copy()
    
    return df


def _normalize_signal_timeframes(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]

    if not isinstance(value, list):
        return []

    normalized: list[str] = []
    for item in value:
        tf = str(item).strip()
        if tf:
            normalized.append(tf)
    return normalized


def load_data(file_path: str) -> pd.DataFrame:
    """Load CSV data"""
    df = pd.read_csv(file_path)
    df['Time'] = pd.to_datetime(df['Time'])
    df.columns = df.columns.str.strip()
    return df


def get_symbol_from_filename(filename: str) -> str:
    """Extract symbol name from filename"""
    return filename.replace('.csv', '')


def extract_base_symbol(filename: str) -> str:
    """Extract base symbol without timeframe from filename"""
    # e.g., BTCUSDT_1m.csv -> BTCUSDT
    name = filename.replace('.csv', '')
    parts = name.rsplit('_', 1)
    return parts[0] if len(parts) > 1 else name


def create_config(
    DATA_FOLDER, 
    OUTPUT_FOLDER, 
    risk_reward_ratio: float = 2.0,
    signal_timeframe: str = "5m",
    entry_timeframe: str = "1m",
    swing_length: int = 10,
    internal_length: int = 3,
    eqhl_length: int = 3,
    eqhl_threshold: float = 0.3,
    ob_mitigation: str = "high_low",
    ob_body_ratio: float = 0.35,
    require_liquidity: bool = True,
    require_reversal_pattern: bool = True,
    enable_simple_reversal: bool = True,
    simple_reversal_wick_ratio: float = 0.25,
    touch_tolerance_pct: float = 0.0,
    touch_confirmation_bars: int = 0,
    fvg_atr_filter: bool = True,
    sl_buffer_pct: float = 0.1,
    sl_multiplier: float = 1.05,
    min_sl_pct: float = 0.05,
    risk_per_trade: float = 10.0,
    reversal_pattern_ids: list[int] | None = None,
) -> Config:
    """Create Config with system paths and all strategy settings."""
    config_values = {
        "data_folder": DATA_FOLDER,
        "output_folder": OUTPUT_FOLDER,
        "risk_reward_ratio": risk_reward_ratio,
        "signal_timeframe": signal_timeframe,
        "entry_timeframe": entry_timeframe,
        "swing_length": swing_length,
        "internal_length": internal_length,
        "eqhl_length": eqhl_length,
        "eqhl_threshold": eqhl_threshold,
        "ob_mitigation": ob_mitigation,
        "ob_body_ratio": ob_body_ratio,
        "require_liquidity": require_liquidity,
        "require_reversal_pattern": require_reversal_pattern,
        "enable_simple_reversal": enable_simple_reversal,
        "simple_reversal_wick_ratio": simple_reversal_wick_ratio,
        "touch_tolerance_pct": touch_tolerance_pct,
        "touch_confirmation_bars": touch_confirmation_bars,
        "fvg_atr_filter": fvg_atr_filter,
        "sl_buffer_pct": sl_buffer_pct,
        "sl_multiplier": sl_multiplier,
        "min_sl_pct": min_sl_pct,
        "risk_per_trade": risk_per_trade,
        "reversal_pattern_ids": reversal_pattern_ids if reversal_pattern_ids is not None else [0],
    }

    config_fields = {item.name for item in fields(Config)}
    filtered_values = {key: value for key, value in config_values.items() if key in config_fields}
    return Config(**filtered_values)


def execute(
    DATA_FOLDER="res/data", 
    OUTPUT_FOLDER="res/output", 
    timeframe: str = "",
    signal_timeframe: str | list[str] | None = None,
    entry_timeframe: str = "",
    risk_reward_ratio: float = 2.0,
    swing_length: int = 10,
    internal_length: int = 3,
    eqhl_length: int = 3,
    eqhl_threshold: float = 0.3,
    ob_mitigation: str = "high_low",
    ob_body_ratio: float = 0.35,
    require_liquidity: bool = True,
    require_reversal_pattern: bool = True,
    enable_simple_reversal: bool = True,
    simple_reversal_wick_ratio: float = 0.25,
    touch_tolerance_pct: float = 0.0,
    touch_confirmation_bars: int = 0,
    fvg_atr_filter: bool = True,
    sl_buffer_pct: float = 0.1,
    sl_multiplier: float = 1.05,
    min_sl_pct: float = 0.05,
    risk_per_trade: float = 10.0,
    reversal_pattern_ids: list[int] | None = None,
):
    """Main execution function
    
    Supports two modes:
    1. Single-timeframe mode (legacy): Uses `timeframe` param, processes files matching that TF
    2. Strategy-timeframe mode: Uses `signal_timeframe` (string/list) and `entry_timeframe`
       - Loads entry_timeframe data (e.g., 1m)
       - Converts to each signal_timeframe (e.g., 3m, 5m) for indicator calculation
       - Uses entry_timeframe data for precise entry detection
    
    Args:
        DATA_FOLDER: Path to data folder containing CSV files
        OUTPUT_FOLDER: Path to output folder for trade reports
        timeframe: (Legacy) Single timeframe filter (e.g., "1m", "5m", "1h")
        signal_timeframe: Timeframe(s) for signal/indicator calculation (e.g., "5m" or ["3m", "5m"])
        entry_timeframe: Timeframe for precise entry detection (e.g., "1m")
        All other args: Strategy configuration parameters
    """
    strategy_signal_tfs = _normalize_signal_timeframes(signal_timeframe)
    use_strategy_timeframes = bool(strategy_signal_tfs and entry_timeframe)

    base_signal_tf = strategy_signal_tfs[0] if strategy_signal_tfs else (timeframe or "5m")
    base_entry_tf = entry_timeframe if use_strategy_timeframes else base_signal_tf
    base_config = create_config(
        DATA_FOLDER, OUTPUT_FOLDER,
        risk_reward_ratio=risk_reward_ratio,
        signal_timeframe=base_signal_tf,
        entry_timeframe=base_entry_tf,
        swing_length=swing_length,
        internal_length=internal_length,
        eqhl_length=eqhl_length,
        eqhl_threshold=eqhl_threshold,
        ob_mitigation=ob_mitigation,
        ob_body_ratio=ob_body_ratio,
        require_liquidity=require_liquidity,
        require_reversal_pattern=require_reversal_pattern,
        enable_simple_reversal=enable_simple_reversal,
        simple_reversal_wick_ratio=simple_reversal_wick_ratio,
        touch_tolerance_pct=touch_tolerance_pct,
        touch_confirmation_bars=touch_confirmation_bars,
        fvg_atr_filter=fvg_atr_filter,
        sl_buffer_pct=sl_buffer_pct,
        sl_multiplier=sl_multiplier,
        min_sl_pct=min_sl_pct,
        risk_per_trade=risk_per_trade,
        reversal_pattern_ids=reversal_pattern_ids,
    )

    # Create output folder if not exists
    output_dir = Path(base_config.output_folder)
    output_dir.mkdir(parents=True, exist_ok=True)

    data_dir = Path(base_config.data_folder)

    if use_strategy_timeframes:
        # === STRATEGY TIMEFRAME MODE ===
        # Load entry timeframe files once, then evaluate each configured signal timeframe.
        entry_files = list(data_dir.glob(f"*_{entry_timeframe}.csv"))

        if not entry_files:
            return

        strategies_by_signal: dict[str, Strategy] = {}
        simulators_by_signal: dict[str, TradeSimulator] = {}
        for signal_tf in strategy_signal_tfs:
            tf_config = create_config(
                DATA_FOLDER,
                OUTPUT_FOLDER,
                risk_reward_ratio=risk_reward_ratio,
                signal_timeframe=signal_tf,
                entry_timeframe=entry_timeframe,
                swing_length=swing_length,
                internal_length=internal_length,
                eqhl_length=eqhl_length,
                eqhl_threshold=eqhl_threshold,
                ob_mitigation=ob_mitigation,
                ob_body_ratio=ob_body_ratio,
                require_liquidity=require_liquidity,
                require_reversal_pattern=require_reversal_pattern,
                enable_simple_reversal=enable_simple_reversal,
                simple_reversal_wick_ratio=simple_reversal_wick_ratio,
                touch_tolerance_pct=touch_tolerance_pct,
                touch_confirmation_bars=touch_confirmation_bars,
                fvg_atr_filter=fvg_atr_filter,
                sl_buffer_pct=sl_buffer_pct,
                sl_multiplier=sl_multiplier,
                min_sl_pct=min_sl_pct,
                risk_per_trade=risk_per_trade,
                reversal_pattern_ids=reversal_pattern_ids,
            )
            strategies_by_signal[signal_tf] = Strategy(tf_config)
            simulators_by_signal[signal_tf] = TradeSimulator(tf_config)

        for entry_file in entry_files:
            base_symbol = extract_base_symbol(entry_file.name)

            df_entry = load_data(str(entry_file))
            df_entry = _drop_incomplete_candle(df_entry, entry_timeframe)
            if df_entry.empty:
                continue

            for signal_tf in strategy_signal_tfs:
                strategy = strategies_by_signal[signal_tf]
                simulator = simulators_by_signal[signal_tf]

                if signal_tf != entry_timeframe:
                    df_signal = convert_timeframe(df_entry, signal_tf)
                    if df_signal.empty:
                        continue
                    signals = strategy.generate_signals(df_signal, base_symbol, df_entry=df_entry)
                else:
                    df_signal = df_entry
                    signals = strategy.generate_signals(df_signal, base_symbol)

                trades = simulator.simulate(signals, df_entry)
                report_symbol = f"{base_symbol}_{signal_tf}"
                generate_trade_orders(trades, str(output_dir), report_symbol)
    else:
        # === SINGLE-TIMEFRAME MODE (Legacy) ===
        config = base_config
        strategy = Strategy(config)
        simulator = TradeSimulator(config)

        # Find CSV files in data folder (filter by timeframe if specified)
        if timeframe:
            csv_files = list(data_dir.glob(f"*_{timeframe}.csv"))
        else:
            csv_files = list(data_dir.glob("*.csv"))
        
        if not csv_files:
            return
        
        # Process each file
        for csv_file in csv_files:
            symbol = get_symbol_from_filename(csv_file.name)
            
            # Load data
            df = load_data(str(csv_file))
            
            # Safety net: drop incomplete (still-forming) candle to prevent repainting
            if timeframe:
                df = _drop_incomplete_candle(df, timeframe)
                if df.empty:
                    continue
            
            # Generate signals
            signals = strategy.generate_signals(df, symbol)
            
            # Simulate trades
            trades = simulator.simulate(signals, df)
            
            # Save trades to CSV
            generate_trade_orders(trades, str(output_dir), symbol)


if __name__ == "__main__":
    execute()
