"""
Statistics and Trade Simulation Module
======================================
Contains:
- TradeSimulator: Simulates trade exits based on signals and price data
- Trade: Completed trade with exit information
- Report generation functions
"""

import os
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

from strategy import STRATEGY_NAME, Signal, Config


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


# ==================== TRADE SIMULATOR ====================
class TradeSimulator:
    """
    Simulates trade exits based on signals and price data.
    Calculates: exit_time, exit_price, result, pnl
    """
    def __init__(self, config: Config):
        self.config = config
    
    def _check_exit(self, signal: Signal, row: pd.Series) -> Tuple[bool, Optional[str], Optional[float]]:
        """Check if trade should be closed based on SL/TP"""
        high = row['High']
        low = row['Low']
        
        if signal.trade_type == 'LONG':
            # Check SL first (worst case)
            if low <= signal.stop_loss:
                return True, 'LOSS', signal.stop_loss
            # Check TP
            if high >= signal.take_profit:
                return True, 'WIN', signal.take_profit
        else:  # SHORT
            # Check SL first (worst case)
            if high >= signal.stop_loss:
                return True, 'LOSS', signal.stop_loss
            # Check TP
            if low <= signal.take_profit:
                return True, 'WIN', signal.take_profit
        
        return False, None, None
    
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
        
        trades = []
        
        for signal in signals:
            # Find the index where this signal's entry_time occurs
            entry_idx = -1
            for idx in range(len(df)):
                row = df.iloc[idx]
                row_time = row['Time'] if isinstance(row['Time'], datetime) else pd.to_datetime(row['Time'])
                if row_time >= signal.entry_time:
                    entry_idx = idx
                    break
            
            if entry_idx < 0:
                continue
            
            # Create Trade from Signal
            trade = Trade(
                symbol=signal.symbol,
                trade_type=signal.trade_type,
                entry_time=signal.entry_time,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit
            )
            
            # Look for exit in subsequent candles
            found_exit = False
            for i in range(entry_idx + 1, len(df)):
                row = df.iloc[i]
                row_time = row['Time'] if isinstance(row['Time'], datetime) else pd.to_datetime(row['Time'])
                
                should_close, result, exit_price = self._check_exit(signal, row)
                
                if should_close and result is not None:
                    trade.exit_time = row_time
                    trade.exit_price = exit_price
                    trade.result = result
                    found_exit = True
                    break
            
            # If no exit found, mark as OPEN with last price
            if not found_exit:
                last_row = df.iloc[-1]
                trade.exit_time = last_row['Time'] if isinstance(last_row['Time'], datetime) else pd.to_datetime(last_row['Time'])
                trade.exit_price = float(last_row['Close'])
                trade.result = 'OPEN'
            
            trades.append(trade)
        
        return trades


# ==================== REPORT FUNCTIONS ====================
def generate_trade_report(trades: List[Trade], output_dir: str, symbol: str) -> pd.DataFrame:
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
            'Entry_Price': round(t.entry_price, 2),
            'Stop_Loss': round(t.stop_loss, 2),
            'Take_Profit': round(t.take_profit, 2),
            'Exit_Time': t.exit_time,
            'Exit_Price': round(t.exit_price, 2) if t.exit_price else None,
            'Result': result
        })
    
    df = pd.DataFrame(data)
    output_path = Path(output_dir) / f"{symbol}_trades.csv"
    df.to_csv(output_path, index=False)
    print(f"  - Trade report saved to: {output_path}")
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
            'win_rate': 0.0
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
    
    return {
        'symbol': symbol,
        'total_trades': total_trades,
        'wins': win_count,
        'losses': loss_count,
        'open_trades': open_count,
        'win_rate': round(win_rate, 2)
    }


def generate_text_report(all_summaries: List[Dict], config: Config, output_path: str):
    """
    Generate report with all results.
    Uses markdown table format that looks good both raw and rendered.
    Appends new results to existing file with timestamp.
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
    
    # Get RR ratio from config
    rr_ratio = config.risk_reward_ratio
    
    # Build report with aligned markdown table
    lines = [
        "",
        f"##  TRADE RESULTS - {STRATEGY_NAME} - {timestamp}",
        "| {:<14} | {:>6} | {:>6} | {:>6} | {:>6} | {:>10} | {:>8} |".format(
            "Symbol", "Total", "Wins", "Losses", "Open", "Win Rate", "ROE"
        ),
        "|{:-<16}|{:->8}|{:->8}|{:->8}|{:->8}|{:->12}|{:->10}|".format("", "", "", "", "", "", ""),
    ]
    
    # Add individual symbol results
    for summary in all_summaries:
        # ROE = (Win Rate / 100) * RR Ratio
        roe = (summary['win_rate'] / 100) * rr_ratio
        row = "| {:<14} | {:>6} | {:>6} | {:>6} | {:>6} | {:>10} | {:>8} |".format(
            summary['symbol'],
            summary['total_trades'],
            summary['wins'],
            summary['losses'],
            summary['open_trades'],
            f"**{summary['win_rate']}%**",
            f"{round(roe, 2)*100}%"
        )
        lines.append(row)
    
    # Add overall summary
    overall_roe = (overall_win_rate / 100) * rr_ratio
    total_row = "| {:<14} | {:>6} | {:>6} | {:>6} | {:>6} | {:>10} | {:>8} |".format(
        "TOTAL",
        total_trades,
        total_wins,
        total_losses,
        total_open,
        f"**{round(overall_win_rate, 2)}%**",
        f"{round(overall_roe, 2)*100}%"
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
    
    print("=" * 60)
