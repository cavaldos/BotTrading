"""
Benchmark Reversal Patterns
===========================
Script chạy backtest lần lượt từng cặp pattern nến đảo chiều
và tổng hợp kết quả để so sánh hiệu quả.

Cách sử dụng:
    python src/benchmark_patterns.py

Output:
    - Console: Bảng so sánh kết quả các pattern
"""

from pathlib import Path
from typing import List, Dict, Any
import pandas as pd
from tqdm import tqdm

# Import strategy components
from src.strategy import Config, Strategy, STRATEGY_NAME
from src.statistics import TradeSimulator, generate_summary_report
from src.config import load_config
from src.utils.reversal_patterns import PATTERN_PAIR_NAMES


# ==============================================================================
# CONFIGURATION
# ==============================================================================

# Danh sách pattern IDs cần test (0 = all, 1-10 = từng cặp)
# Thay đổi mảng này nếu chỉ muốn test một số pattern nhất định
PATTERN_IDS_TO_TEST = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

# Folders
DEFAULT_DATA_FOLDER = "res/data"
DEFAULT_OUTPUT_FOLDER = "res/output"
DEFAULT_REPORT_FOLDER = "res/report"


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def load_data(file_path: str) -> pd.DataFrame:
    """Load CSV data"""
    df = pd.read_csv(file_path)
    df['Time'] = pd.to_datetime(df['Time'])
    df.columns = df.columns.str.strip()
    return df


def get_symbol_from_filename(filename: str) -> str:
    """Extract symbol name from filename"""
    return filename.replace('.csv', '')


def create_config_with_pattern(pattern_ids: List[int]) -> Config:
    """Create Config with specific pattern IDs"""
    data_config = load_config()
    
    data_folder = data_config.get('folder_path', DEFAULT_DATA_FOLDER)
    output_folder = data_config.get('output_folder', DEFAULT_OUTPUT_FOLDER)
    
    config = Config(
        data_folder=data_folder,
        output_folder=output_folder,
    )
    config.reversal_pattern_ids = pattern_ids
    
    return config


def calculate_trade_pnl(trade) -> float:
    """Tính PnL cho một trade dựa trên entry/exit price"""
    if trade.exit_price is None or trade.result == 'OPEN':
        return 0.0
    
    if trade.trade_type == 'LONG':
        return trade.exit_price - trade.entry_price
    else:  # SHORT
        return trade.entry_price - trade.exit_price


def run_backtest_for_pattern(pattern_id: int, csv_files: List[Path]) -> Dict[str, Any]:
    """
    Chạy backtest cho một pattern ID cụ thể.
    
    Returns:
        Dict chứa kết quả tổng hợp
    """
    # Tạo config với pattern ID
    config = create_config_with_pattern([pattern_id])
    
    # Initialize strategy and simulator
    strategy = Strategy(config)
    simulator = TradeSimulator(config)
    
    all_trades = []
    total_signals = 0
    
    # Process từng file
    for csv_file in csv_files:
        symbol = get_symbol_from_filename(csv_file.name)
        df = load_data(str(csv_file))
        
        # Generate signals
        signals = strategy.generate_signals(df, symbol)
        total_signals += len(signals)
        
        # Simulate trades
        trades = simulator.simulate(signals, df)
        all_trades.extend(trades)
    
    # Tính toán metrics
    total_trades = len(all_trades)
    
    if total_trades == 0:
        return {
            'pattern_id': pattern_id,
            'pattern_name': PATTERN_PAIR_NAMES[pattern_id],
            'total_signals': total_signals,
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'win_rate': 0.0,
            'total_pnl': 0.0,
            'avg_pnl': 0.0,
            'profit_factor': 0.0,
            'max_win': 0.0,
            'max_loss': 0.0,
        }
    
    # Tính PnL cho từng trade
    pnls = [calculate_trade_pnl(t) for t in all_trades]
    
    wins = sum(1 for t in all_trades if t.result == 'WIN')
    losses = sum(1 for t in all_trades if t.result == 'LOSS')
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
    
    total_pnl = sum(pnls)
    avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
    
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    max_win = max(pnls) if pnls else 0
    max_loss = min(pnls) if pnls else 0
    
    return {
        'pattern_id': pattern_id,
        'pattern_name': PATTERN_PAIR_NAMES[pattern_id],
        'total_signals': total_signals,
        'total_trades': total_trades,
        'wins': wins,
        'losses': losses,
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'avg_pnl': avg_pnl,
        'profit_factor': profit_factor,
        'max_win': max_win,
        'max_loss': max_loss,
    }


def print_results_table(results: List[Dict[str, Any]]):
    """In bảng kết quả ra console"""
    
    # Header
    print("\n" + "=" * 100)
    print("BENCHMARK RESULTS - REVERSAL PATTERNS")
    print("=" * 100)
    
    # Column headers
    print(f"{'ID':<4} {'Pattern Name':<22} {'Signals':<8} {'Trades':<8} {'Wins':<6} {'Losses':<8} {'Win%':<8} {'PnL':<12} {'PF':<8}")
    print("-" * 100)
    
    # Sort by win_rate descending
    sorted_results = sorted(results, key=lambda x: x['win_rate'], reverse=True)
    
    for r in sorted_results:
        pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] != float('inf') else "∞"
        print(f"{r['pattern_id']:<4} {r['pattern_name']:<22} {r['total_signals']:<8} {r['total_trades']:<8} "
              f"{r['wins']:<6} {r['losses']:<8} {r['win_rate']:<7.2f}% ${r['total_pnl']:<11.2f} {pf_str:<8}")
    
    print("-" * 100)
    
    # Best performers
    if sorted_results and sorted_results[0]['total_trades'] > 0:
        best = sorted_results[0]
        print(f"\n🏆 Best Win Rate: {best['pattern_name']} ({best['win_rate']:.2f}%)")
    
    # Best PnL
    best_pnl = max(results, key=lambda x: x['total_pnl'])
    if best_pnl['total_pnl'] > 0:
        print(f"💰 Best PnL: {best_pnl['pattern_name']} (${best_pnl['total_pnl']:.2f})")
    
    # Best Profit Factor
    valid_pf = [r for r in results if r['profit_factor'] > 0 and r['profit_factor'] != float('inf')]
    if valid_pf:
        best_pf = max(valid_pf, key=lambda x: x['profit_factor'])
        print(f"📊 Best Profit Factor: {best_pf['pattern_name']} ({best_pf['profit_factor']:.2f})")





# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

def main():
    """Main execution function"""
    print("=" * 60)
    print(f"Pattern Benchmark - {STRATEGY_NAME}")
    print("=" * 60)
    
    # Load config để lấy data folder
    data_config = load_config()
    data_folder = data_config.get('folder_path', DEFAULT_DATA_FOLDER)
    report_folder = data_config.get('report_folder', DEFAULT_REPORT_FOLDER)
    
    # Tìm CSV files
    data_dir = Path(data_folder)
    csv_files = list(data_dir.glob("*.csv"))
    
    if not csv_files:
        print(f"\n❌ No CSV files found in {data_folder}")
        return
    
    print(f"\n📂 Data folder: {data_folder}")
    print(f"📊 Found {len(csv_files)} data file(s)")
    print(f"🔢 Testing {len(PATTERN_IDS_TO_TEST)} pattern(s): {PATTERN_IDS_TO_TEST}")
    print("-" * 60)
    
    # Chạy benchmark cho từng pattern
    results = []
    
    for pattern_id in tqdm(PATTERN_IDS_TO_TEST, desc="Benchmarking patterns", unit="pattern"):
        pattern_name = PATTERN_PAIR_NAMES[pattern_id]
        tqdm.write(f"  Testing: [{pattern_id}] {pattern_name}")
        
        result = run_backtest_for_pattern(pattern_id, csv_files)
        results.append(result)
        
        tqdm.write(f"    → {result['total_trades']} trades, {result['win_rate']:.1f}% win rate")
    
    # In bảng kết quả
    print_results_table(results)
    
    print("\n✅ Benchmark completed!")


if __name__ == "__main__":
    main()
