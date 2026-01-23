"""
Statistics and Trade Simulation Module
======================================
Contains:
- TradeSimulator: Simulates trade exits based on signals and price data
- Trade: Completed trade with exit information
- Report generation functions

PERFORMANCE: Uses Numba JIT for fast trade simulation while preserving exact logic.
"""

import os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from numba import njit

from src.strategy import STRATEGY_NAME, Signal, Config
from src.utils.rounding import round_price


# ==================== DATA CLASSES ====================
@dataclass
class Trade:
    """
    Completed trade with entry and exit information.
    Extends Signal with exit data.
    """
    # Entry info (from Signal)
    symbol: str
    trade_type: str           # 'LONG' or 'SHORT'
    entry_time: datetime
    entry_price: float
    stop_loss: float
    take_profit: float
    
    # Exit info (calculated by TradeSimulator)
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    result: str = 'OPEN'      # 'WIN', 'LOSS', 'OPEN'


# ==================== NUMBA JIT OPTIMIZED FUNCTIONS ====================

@njit(cache=True)
def _find_entry_idx_jit(data_times: np.ndarray, signal_time: np.int64) -> int:
    """Binary search for entry index - JIT optimized."""
    left, right = 0, len(data_times) - 1
    result = -1
    
    while left <= right:
        mid = (left + right) // 2
        if data_times[mid] >= signal_time:
            result = mid
            right = mid - 1
        else:
            left = mid + 1
    
    return result


@njit(cache=True)
def _simulate_single_trade_jit(
    entry_idx: int,
    is_long: bool,
    stop_loss: float,
    take_profit: float,
    data_highs: np.ndarray,
    data_lows: np.ndarray,
    data_closes: np.ndarray,
    data_times: np.ndarray
) -> Tuple[int, float, int]:
    """
    Simulate a single trade exit - JIT optimized.
    
    Returns:
        (exit_idx, exit_price, result)
        result: 0=LOSS, 1=WIN, 2=OPEN
    """
    n = len(data_highs)
    
    for i in range(entry_idx + 1, n):
        high = data_highs[i]
        low = data_lows[i]
        
        if is_long:
            # Check SL first (worst case)
            if low <= stop_loss:
                return i, stop_loss, 0  # LOSS
            # Check TP
            if high >= take_profit:
                return i, take_profit, 1  # WIN
        else:  # SHORT
            # Check SL first (worst case)
            if high >= stop_loss:
                return i, stop_loss, 0  # LOSS
            # Check TP
            if low <= take_profit:
                return i, take_profit, 1  # WIN
    
    # No exit found - mark as OPEN
    return n - 1, data_closes[n - 1], 2  # OPEN


# ==================== TRADE SIMULATOR ====================
class TradeSimulator:
    """
    Simulates trade exits based on signals and price data.
    Calculates: exit_time, exit_price, result, pnl
    
    PERFORMANCE: Uses Numba JIT for binary search and exit simulation.
    """
    def __init__(self, config: Config):
        self.config = config
    
    def simulate(self, signals: List[Signal], df: pd.DataFrame) -> List[Trade]:
        """
        Simulate trade exits for given signals.
        
        Args:
            signals: List of trade signals from Strategy
            df: Price data DataFrame with Time, High, Low, Close columns
            
        Returns:
            List of Trade objects with exit information
        """
        if not signals:
            return []
        
        # Prepare data arrays for JIT functions
        data_times = pd.to_datetime(df['Time']).values.astype(np.int64)
        data_highs = df['High'].values.astype(np.float64)
        data_lows = df['Low'].values.astype(np.float64)
        data_closes = df['Close'].values.astype(np.float64)
        
        trades = []
        result_map = {0: 'LOSS', 1: 'WIN', 2: 'OPEN'}
        
        for signal in signals:
            # Convert signal time to int64
            signal_time = np.int64(pd.Timestamp(signal.entry_time).value)
            
            # Find entry index using JIT binary search
            entry_idx = _find_entry_idx_jit(data_times, signal_time)
            
            if entry_idx < 0:
                continue
            
            # Simulate exit using JIT function
            is_long = signal.trade_type == 'LONG'
            exit_idx, exit_price, result_code = _simulate_single_trade_jit(
                entry_idx, is_long, signal.stop_loss, signal.take_profit,
                data_highs, data_lows, data_closes, data_times
            )
            
            # Convert exit time back to datetime
            exit_ts = pd.Timestamp(data_times[exit_idx], unit='ns')
            exit_time_dt = datetime.utcfromtimestamp(exit_ts.value / 1e9)
            
            trade = Trade(
                symbol=signal.symbol,
                trade_type=signal.trade_type,
                entry_time=signal.entry_time,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                exit_time=exit_time_dt,
                exit_price=exit_price,
                result=result_map[result_code]
            )
            trades.append(trade)
        
        return trades


# ==================== REPORT FUNCTIONS ====================
def generate_trade_orders(trades: List[Trade], output_dir: str, symbol: str) -> pd.DataFrame:
    """Generate CSV report of trades for a single symbol"""
    if not trades:
        return pd.DataFrame()
    
    data = []
    for t in trades:
        # Determine result
        if t.result == 'OPEN':
            result = 'Open'
        elif t.result == 'WIN':
            result = 'Win'
        elif t.result == 'LOSS':
            result = 'Loss'
        else:
            result = t.result
        
        data.append({
            'Symbol': t.symbol,
            'Type': t.trade_type,
            'Entry_Time': t.entry_time,
            'Entry_Price': round_price(t.entry_price),
            'Stop_Loss': round_price(t.stop_loss),
            'Take_Profit': round_price(t.take_profit),
            'Exit_Time': t.exit_time,
            'Exit_Price': round_price(t.exit_price) if t.exit_price else None,
            'Result': result
        })
    
    df = pd.DataFrame(data)
    output_path = Path(output_dir) / f"{symbol}_trades.csv"
    df.to_csv(output_path, index=False)
    return df


def generate_summary_report(trades: List[Trade], symbol: str, config: Config) -> Dict:
    """Generate simplified summary statistics for trades"""
    if not trades:
        return {
            'symbol': symbol,
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'open_trades': 0,
            'win_rate': 0.0,
            'longest_win_streak': 0,
            'longest_loss_streak': 0
        }
    
    wins = [t for t in trades if t.result == 'WIN']
    losses = [t for t in trades if t.result == 'LOSS']
    open_trades = [t for t in trades if t.result == 'OPEN']
    
    total_trades = len(trades)
    win_count = len(wins)
    loss_count = len(losses)
    open_count = len(open_trades)
    closed_trades = total_trades - open_count
    
    win_rate = (win_count / closed_trades * 100) if closed_trades > 0 else 0
    
    # Calculate streaks
    longest_win_streak = 0
    longest_loss_streak = 0
    current_win_streak = 0
    current_loss_streak = 0
    
    for trade in trades:
        if trade.result == 'WIN':
            current_win_streak += 1
            current_loss_streak = 0
            longest_win_streak = max(longest_win_streak, current_win_streak)
        elif trade.result == 'LOSS':
            current_loss_streak += 1
            current_win_streak = 0
            longest_loss_streak = max(longest_loss_streak, current_loss_streak)
    
    return {
        'symbol': symbol,
        'total_trades': total_trades,
        'wins': win_count,
        'losses': loss_count,
        'open_trades': open_count,
        'win_rate': round(win_rate, 2),
        'longest_win_streak': longest_win_streak,
        'longest_loss_streak': longest_loss_streak
    }


def generate_text_report(all_summaries: List[Dict], config: Config, output_path: str):
    """
    Generate report with all results.
    Uses markdown table format that looks good both raw and rendered.
    Groups results by timeframe and appends new results to existing file with timestamp.
    """
    # Get current timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Calculate overall stats
    total_trades = sum(s['total_trades'] for s in all_summaries)
    total_wins = sum(s['wins'] for s in all_summaries)
    total_losses = sum(s['losses'] for s in all_summaries)
    total_open = sum(s['open_trades'] for s in all_summaries)
    total_closed = total_wins + total_losses
    overall_win_rate = (total_wins / total_closed * 100) if total_closed > 0 else 0
    
    # Get RR ratio and SL multiplier from config
    rr_ratio = config.risk_reward_ratio
    sl_multiplier = config.sl_multiplier
    
    # Group summaries by timeframe
    timeframe_groups = {}
    for summary in all_summaries:
        # Extract timeframe from symbol (e.g., "LTCUSDT_1m" -> "1m")
        symbol = summary['symbol']
        timeframe = symbol.split('_')[-1] if '_' in symbol else 'unknown'
        if timeframe not in timeframe_groups:
            timeframe_groups[timeframe] = []
        timeframe_groups[timeframe].append(summary)
    
    # Sort timeframes by order: 1m, 5m, 15m, 30m, 1h, 4h, 1d
    timeframe_order = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']
    sorted_timeframes = sorted(timeframe_groups.keys(), 
                              key=lambda x: timeframe_order.index(x) if x in timeframe_order else 999)
    
    # Build report with aligned markdown table
    lines = [
        "",
        f"##  TRADE RESULTS - {timestamp} - {config.reversal_pattern_ids}",
        "| {:<14} | {:>6} | {:>6} | {:>6} | {:>4} | {:>10} | {:>4} | {:>5} | {:>6} | {:>4} | {:>4} |".format(
            "Symbol", "**Total**", "Wins", "Losses", "Open", "Win Rate", "RR", "SLM", "ROE", "W-S", "L-S"
        ),
        "|{:-<16}|{:->8}|{:->8}|{:->8}|{:->6}|{:->12}|{:->6}|{:->7}|{:->8}|{:->6}|{:->6}|".format("", "", "", "", "", "", "", "", "", "", ""),
    ]
    
    # Add results grouped by timeframe
    for timeframe in sorted_timeframes:
        for summary in timeframe_groups[timeframe]:
            # ROE = (Wins * RR Ratio) - Losses
            roe = (summary['wins'] * rr_ratio) - summary['losses']
            row = "| {:<14} | {:>6} | {:>6} | {:>6} | {:>4} | {:>10} | {:>4} | {:>5} | {:>6} | {:>4} | {:>4} |".format(
                summary['symbol'],
                summary['total_trades'],
                summary['wins'],
                summary['losses'],
                summary['open_trades'],
                f"*{summary['win_rate']}%*",
                f"{rr_ratio}",
                f"{sl_multiplier}",
                f"{round(roe, 2)}",
                summary['longest_win_streak'],
                summary['longest_loss_streak']
            )
            lines.append(row)
    
    # Add overall summary
    # ROE = (Total Wins * RR Ratio) - Total Losses
    overall_roe = (total_wins * rr_ratio) - total_losses
    
    # Calculate overall streaks
    overall_longest_win_streak = 0
    overall_longest_loss_streak = 0
    for summary in all_summaries:
        overall_longest_win_streak = max(overall_longest_win_streak, summary['longest_win_streak'])
        overall_longest_loss_streak = max(overall_longest_loss_streak, summary['longest_loss_streak'])
    
    total_row = "| {:<14} | {:>6} | {:>6} | {:>6} | {:>4} | {:>10} | {:>4} | {:>5} | {:>6} | {:>4} | {:>4} |".format(
        "TOTAL",
        total_trades,
        total_wins,
        total_losses,
        total_open,
        f"*{round(overall_win_rate, 2)}%*",
        f"{rr_ratio}",
        f"{sl_multiplier}",
        f"{round(overall_roe, 2)}",
        overall_longest_win_streak,
        overall_longest_loss_streak
    )
    lines.append(total_row)
    lines.append("")
    
    # Ensure output directory exists
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # Read existing content if file exists
    existing_content = ""
    if os.path.exists(output_path):
        with open(output_path, 'r', encoding='utf-8') as f:
            existing_content = f.read()
    
    # Write header if file is new/empty
    if not existing_content.strip():
        existing_content = "# TRADE HISTORY\n"
    
    # Append new results to existing content
    new_content = existing_content + '\n'.join(lines)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"Summary report appended to: {output_path}")


def print_quick_stats(summary: Dict) -> None:
    """Print quick statistics to console"""
    print(f"  - Total Trades: {summary['total_trades']}")
    print(f"  - Wins: {summary['wins']}, Losses: {summary['losses']}, Open: {summary['open_trades']}")
    print(f"  - Win Rate: {summary['win_rate']}%")
    print(f"  - Longest Win Streak: {summary['longest_win_streak']}, Longest Loss Streak: {summary['longest_loss_streak']}")


def print_final_summary(all_trades: List[Trade]) -> None:
    """Print final summary after backtest completion"""
    print("\n" + "=" * 60)
    print("Analysis completed!")
    print(f"Total trades: {len(all_trades)}")
    
    if all_trades:
        wins = len([t for t in all_trades if t.result == 'WIN'])
        losses = len([t for t in all_trades if t.result == 'LOSS'])
        open_trades = len([t for t in all_trades if t.result == 'OPEN'])
        closed_trades = wins + losses
        overall_wr = (wins / closed_trades * 100) if closed_trades > 0 else 0
        print(f"Wins: {wins}, Losses: {losses}, Open: {open_trades}")
        print(f"Overall win rate: {round(overall_wr, 2)}%")
        
        # Calculate streaks
        longest_win_streak = 0
        longest_loss_streak = 0
        current_win_streak = 0
        current_loss_streak = 0
        
        for trade in all_trades:
            if trade.result == 'WIN':
                current_win_streak += 1
                current_loss_streak = 0
                longest_win_streak = max(longest_win_streak, current_win_streak)
            elif trade.result == 'LOSS':
                current_loss_streak += 1
                current_win_streak = 0
                longest_loss_streak = max(longest_loss_streak, current_loss_streak)
        
        print(f"Longest win streak: {longest_win_streak}")
        print(f"Longest loss streak: {longest_loss_streak}")
    
    print("=" * 60)
