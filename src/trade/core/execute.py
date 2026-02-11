"""
Live Trade Signal Generator
===========================
Scan data files and generate trade signals, save to CSV output.
No markdown reports or console statistics.

Usage:
    python execute.py
"""

from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

# Import strategy components (interface contract)
from src.strategy import Config, Strategy, STRATEGY_NAME

# Import trade simulation and report generation
from src.statistics import TradeSimulator, generate_trade_orders


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


def load_data(file_path: str) -> pd.DataFrame:
    """Load CSV data"""
    df = pd.read_csv(file_path)
    df['Time'] = pd.to_datetime(df['Time'])
    df.columns = df.columns.str.strip()
    return df


def get_symbol_from_filename(filename: str) -> str:
    """Extract symbol name from filename"""
    return filename.replace('.csv', '')


def create_config(
    DATA_FOLDER, 
    OUTPUT_FOLDER, 
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
    risk_per_trade: float = 10.0
) -> Config:
    """Create Config with system paths and all strategy settings."""
    return Config(
        data_folder=DATA_FOLDER,
        output_folder=OUTPUT_FOLDER,
        risk_reward_ratio=risk_reward_ratio,
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
    )


def execute(
    DATA_FOLDER="res/data", 
    OUTPUT_FOLDER="res/output", 
    timeframe: str = "",
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
    risk_per_trade: float = 10.0
):
    """Main execution function
    
    Args:
        DATA_FOLDER: Path to data folder containing CSV files
        OUTPUT_FOLDER: Path to output folder for trade reports
        timeframe: Optional timeframe filter (e.g., "1m", "5m", "1h")
        All other args: Strategy configuration parameters
    """
    config = create_config(
        DATA_FOLDER, OUTPUT_FOLDER,
        risk_reward_ratio=risk_reward_ratio,
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
        risk_per_trade=risk_per_trade
    )
    
    # Create output folder if not exists
    output_dir = Path(config.output_folder)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Find CSV files in data folder (filter by timeframe if specified)
    data_dir = Path(config.data_folder)
    if timeframe:
        csv_files = list(data_dir.glob(f"*_{timeframe}.csv"))
    else:
        csv_files = list(data_dir.glob("*.csv"))
    
    if not csv_files:
        return
    
    # Initialize strategy and simulator
    strategy = Strategy(config)
    simulator = TradeSimulator(config)
    
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
