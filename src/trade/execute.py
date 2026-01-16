"""
Live Trade Signal Generator
===========================
Scan data files and generate trade signals, save to CSV output.
No markdown reports or console statistics.

Usage:
    python execute.py
"""

from pathlib import Path
import pandas as pd

# Import strategy components (interface contract)
from src.strategy import Config, Strategy, STRATEGY_NAME

# Import trade simulation and report generation
from src.statistics import TradeSimulator, generate_trade_report


def load_data(file_path: str) -> pd.DataFrame:
    """Load CSV data"""
    df = pd.read_csv(file_path)
    df['Time'] = pd.to_datetime(df['Time'])
    df.columns = df.columns.str.strip()
    return df


def get_symbol_from_filename(filename: str) -> str:
    """Extract symbol name from filename"""
    return filename.replace('.csv', '')


def create_config(DATA_FOLDER, OUTPUT_FOLDER) -> Config:
    """Create Config with system paths."""
    return Config(
        data_folder=DATA_FOLDER,
        output_folder=OUTPUT_FOLDER,
    )

def execute(DATA_FOLDER="res/data", OUTPUT_FOLDER="res/output"):
    """Main execution function"""
    config = create_config(DATA_FOLDER, OUTPUT_FOLDER)
    
    # Create output folder if not exists
    output_dir = Path(config.output_folder)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Find all CSV files in data folder
    data_dir = Path(config.data_folder)
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
        
        # Generate signals
        signals = strategy.generate_signals(df, symbol)
        
        # Simulate trades
        trades = simulator.simulate(signals, df)
        
        # Save trades to CSV
        generate_trade_report(trades, str(output_dir), symbol)


if __name__ == "__main__":
    execute()
