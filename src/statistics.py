"""
Statistics and Report Generation Module
=======================================
Contains functions for generating trade reports and statistics:
- Trade CSV reports
- Summary statistics
- Markdown reports
"""

import os
import pandas as pd
from pathlib import Path
from typing import List, Dict

try:
    from strategy import Trade, Config
except ImportError:
    from .strategy import Trade, Config


def generate_trade_report(trades: List[Trade], output_dir: str, symbol: str) -> pd.DataFrame:
    """Generate CSV report of trades for a single symbol"""
    if not trades:
        return pd.DataFrame()
    
    data = []
    for t in trades:
        data.append({
            'Trade_ID': t.trade_id,
            'Symbol': t.symbol,
            'Type': t.trade_type,
            'Entry_Time': t.entry_time,
            'Entry_Price': round(t.entry_price, 2),
            'Stop_Loss': round(t.stop_loss, 2),
            'Take_Profit': round(t.take_profit, 2),
            'Entry_Reason': t.entry_reason,
            'Exit_Time': t.exit_time,
            'Exit_Price': round(t.exit_price, 2) if t.exit_price else None,
            'Result': t.result,
            'PnL': round(t.pnl, 2),
            'Risk_Reward': round(t.risk_reward, 2),
            'Status': t.status
        })
    
    df = pd.DataFrame(data)
    output_path = Path(output_dir) / f"{symbol}_trades.csv"
    df.to_csv(output_path, index=False)
    print(f"  - Trade report saved to: {output_path}")
    return df


def generate_summary_report(trades: List[Trade], symbol: str, config: Config) -> Dict:
    """Generate summary statistics for trades"""
    if not trades:
        return {
            'symbol': symbol,
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'win_rate': 0.0,
            'net_profit': 0.0,
            'profit_factor': 0.0,
            'max_drawdown': 0.0,
            'risk_reward': config.risk_reward_ratio,
            'max_win_streak': 0,
            'max_loss_streak': 0,
            'long_trades': 0,
            'short_trades': 0
        }
    
    wins = [t for t in trades if t.result == 'WIN']
    losses = [t for t in trades if t.result == 'LOSS']
    long_trades = [t for t in trades if t.trade_type == 'LONG']
    short_trades = [t for t in trades if t.trade_type == 'SHORT']
    
    total_trades = len(trades)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
    
    total_win = sum(t.pnl for t in wins)
    total_loss = abs(sum(t.pnl for t in losses))
    net_profit = sum(t.pnl for t in trades)
    
    profit_factor = total_win / total_loss if total_loss > 0 else float('inf')
    
    # Calculate max drawdown
    cumulative_pnl = []
    running_total = 0
    for t in trades:
        running_total += t.pnl
        cumulative_pnl.append(running_total)
    
    peak = cumulative_pnl[0] if cumulative_pnl else 0
    max_dd = 0
    for pnl in cumulative_pnl:
        if pnl > peak:
            peak = pnl
        dd = peak - pnl
        if dd > max_dd:
            max_dd = dd
    
    # Calculate max win/loss streaks
    max_win_streak = 0
    max_loss_streak = 0
    current_win_streak = 0
    current_loss_streak = 0
    
    for t in trades:
        if t.result == 'WIN':
            current_win_streak += 1
            current_loss_streak = 0
            if current_win_streak > max_win_streak:
                max_win_streak = current_win_streak
        else:
            current_loss_streak += 1
            current_win_streak = 0
            if current_loss_streak > max_loss_streak:
                max_loss_streak = current_loss_streak
    
    return {
        'symbol': symbol,
        'total_trades': total_trades,
        'wins': win_count,
        'losses': loss_count,
        'win_rate': round(win_rate, 2),
        'net_profit': round(net_profit, 2),
        'profit_factor': round(profit_factor, 2),
        'max_drawdown': round(max_dd, 2),
        'risk_reward': config.risk_reward_ratio,
        'max_win_streak': max_win_streak,
        'max_loss_streak': max_loss_streak,
        'long_trades': len(long_trades),
        'short_trades': len(short_trades)
    }


def generate_markdown_report(all_summaries: List[Dict], config: Config, output_path: str):
    """
    Generate markdown report with all results.
    Appends new results to existing file with timestamp.
    """
    from datetime import datetime
    
    # Get current timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Build new report section
    lines = [
        "",
        "=" * 80,
        "",
        f"# Backtest Results - {timestamp}",
        "",
        "## Strategy Configuration",
        f"- Swing Length: {config.swing_length}",
        f"- Internal Length: {config.internal_length}",
        f"- OB Filter: {config.ob_filter}",
        f"- OB Mitigation: {config.ob_mitigation}",
        f"- CHoCH Only: {config.choch_only}",
        f"- FVG Auto Threshold: {config.fvg_auto_threshold}",
        f"- Premium Zone: >{config.premium_threshold*100}%",
        f"- Discount Zone: <{config.discount_threshold*100}%",
        f"- Min Entry Conditions: {config.min_entry_conditions}",
        f"- ATR Period: {config.atr_period}",
        f"- ATR Multiplier (SL): {config.atr_multiplier_sl}",
        f"- Risk/Reward: {config.risk_reward_ratio}",
        f"- Risk per Trade: ${config.risk_per_trade}",
        "",
        "---",
        ""
    ]
    
    # Calculate overall stats
    total_trades = sum(s['total_trades'] for s in all_summaries)
    total_wins = sum(s['wins'] for s in all_summaries)
    total_profit = sum(s['net_profit'] for s in all_summaries)
    overall_win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
    
    lines.extend([
        "## Overall Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total Trades | {total_trades} |",
        f"| Total Wins | {total_wins} |",
        f"| Overall Win Rate | {round(overall_win_rate, 2)}% |",
        f"| Total Net Profit | ${round(total_profit, 2)} |",
        "",
        "---",
        ""
    ])
    
    # Add individual symbol results
    for summary in all_summaries:
        lines.extend([
            f"### {summary['symbol']}",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Trades | {summary['total_trades']} |",
            f"| Long Trades | {summary['long_trades']} |",
            f"| Short Trades | {summary['short_trades']} |",
            f"| Wins | {summary['wins']} |",
            f"| Losses | {summary['losses']} |",
            f"| Win Rate | {summary['win_rate']}% |",
            f"| Net Profit | ${summary['net_profit']} |",
            f"| Profit Factor | {summary['profit_factor']} |",
            f"| Max Drawdown | ${summary['max_drawdown']} |",
            f"| Risk/Reward | {summary['risk_reward']} |",
            f"| Max Win Streak | {summary['max_win_streak']} |",
            f"| Max Loss Streak | {summary['max_loss_streak']} |",
            ""
        ])
    
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
        header = [
            "# SMC Strategy Backtest History",
            "",
            "This file contains accumulated backtest results.",
            ""
        ]
        existing_content = '\n'.join(header)
    
    # Append new results to existing content
    new_content = existing_content + '\n'.join(lines)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"Summary report appended to: {output_path}")


def print_quick_stats(summary: Dict) -> None:
    """Print quick statistics to console"""
    print(f"  - Win Rate: {summary['win_rate']}%")
    print(f"  - Net Profit: ${summary['net_profit']}")
    print(f"  - Profit Factor: {summary['profit_factor']}")
    print(f"  - Long/Short: {summary['long_trades']}/{summary['short_trades']}")


def print_final_summary(all_trades: List[Trade], total_profit: float) -> None:
    """Print final summary after backtest completion"""
    print("\n" + "=" * 60)
    print("Backtest completed!")
    print(f"Total trades: {len(all_trades)}")
    print(f"Total profit: ${round(total_profit, 2)}")
    
    if all_trades:
        overall_wins = len([t for t in all_trades if t.result == 'WIN'])
        overall_wr = (overall_wins / len(all_trades)) * 100
        print(f"Overall win rate: {round(overall_wr, 2)}%")
    
    print("=" * 60)
