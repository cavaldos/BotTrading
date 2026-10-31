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
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from numba import njit
from decimal import Decimal

from src.strategy import BaseConfig, BaseSignal
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
    signal_timeframe: str = ""
    rr_ratio: Optional[float] = None
    flex_take_profit: Optional[float] = None
    flex_rr_ratio: Optional[float] = None
    base_risk_per_trade: Optional[float] = None
    risk_per_trade: Optional[float] = None
    risk_boost_multiplier: float = 1.0
    tp_capped: bool = False
    pnl_r: Optional[float] = None
    zone_high: Optional[float] = None
    zone_low: Optional[float] = None
    zone_time: Optional[datetime] = None
    zone_type: str = ""
    sl_source: str = ""
    tp_source: str = ""

    # Exit info (calculated by TradeSimulator)
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    result: str = 'OPEN'      # 'WIN', 'LOSS', 'OPEN'


def _resolve_risk_multiplier(
    *,
    base_risk_per_trade: Optional[float],
    risk_per_trade: Optional[float],
    risk_boost_multiplier: Optional[float],
) -> float:
    if risk_boost_multiplier is not None and risk_boost_multiplier > 0:
        return float(risk_boost_multiplier)

    if (
        base_risk_per_trade is not None
        and base_risk_per_trade > 0
        and risk_per_trade is not None
        and risk_per_trade >= 0
    ):
        return float(risk_per_trade) / float(base_risk_per_trade)

    return 1.0


def _calculate_trade_pnl_r(rr_ratio: Optional[float], result: str, risk_multiplier: float) -> Optional[float]:
    if result == 'WIN' and rr_ratio is not None:
        return rr_ratio * risk_multiplier
    if result == 'LOSS':
        return -1.0 * risk_multiplier
    return None


def _summary_gross_roe(summary: Dict) -> float:
    if 'gross_pnl_r' in summary:
        return float(summary['gross_pnl_r'])
    return float(summary['total_rr_wins']) - float(summary['losses'])


def _summary_roe(summary: Dict, fee_per_trade_r: float) -> float:
    if 'net_pnl_r' in summary:
        return float(summary['net_pnl_r'])

    closed_trades = summary['wins'] + summary['losses']
    return float(summary['total_rr_wins']) - float(summary['losses']) - (closed_trades * fee_per_trade_r)


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
    def __init__(self, config: BaseConfig):
        self.config = config
    
    def simulate(self, signals: List[BaseSignal], df: pd.DataFrame) -> List[Trade]:
        """
        Simulate trade exits for given signals.
        
        Args:
            signals: List of trade signals from Strategy
            df: Price data DataFrame with Time, High, Low, Close columns
            
        Returns:
            List of Trade objects with exit information (deduplicated)
        """
        if not signals:
            return []
        
        # Deduplicate signals: keep first signal per (entry_time, trade_type)
        seen_keys = set()
        unique_signals = []
        for signal in signals:
            key = (signal.entry_time, signal.trade_type, signal.signal_timeframe)
            if key not in seen_keys:
                seen_keys.add(key)
                unique_signals.append(signal)
        signals = unique_signals
        
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

            rr_ratio = signal.rr_ratio
            if rr_ratio is None:
                sl_distance = abs(signal.entry_price - signal.stop_loss)
                if sl_distance > 0:
                    rr_ratio = abs(signal.take_profit - signal.entry_price) / sl_distance
            
            # Convert exit time back to datetime
            exit_ts = pd.Timestamp(data_times[exit_idx], unit='ns')
            exit_time_dt = datetime.fromtimestamp(exit_ts.value / 1e9, tz=timezone.utc)
            
            trade = Trade(
                symbol=signal.symbol,
                trade_type=signal.trade_type,
                entry_time=signal.entry_time,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                signal_timeframe=signal.signal_timeframe,
                rr_ratio=rr_ratio,
                flex_take_profit=signal.flex_take_profit,
                flex_rr_ratio=signal.flex_rr_ratio,
                base_risk_per_trade=signal.base_risk_per_trade,
                risk_per_trade=signal.risk_per_trade,
                risk_boost_multiplier=signal.risk_boost_multiplier,
                tp_capped=signal.tp_capped,
                zone_high=getattr(signal, 'zone_high', None),
                zone_low=getattr(signal, 'zone_low', None),
                zone_time=getattr(signal, 'zone_time', None),
                zone_type=getattr(signal, 'zone_type', ''),
                sl_source=getattr(signal, 'sl_source', ''),
                tp_source=getattr(signal, 'tp_source', ''),
                exit_time=exit_time_dt,
                exit_price=exit_price,
                result=result_map[result_code],
                pnl_r=_calculate_trade_pnl_r(
                    rr_ratio,
                    result_map[result_code],
                    _resolve_risk_multiplier(
                        base_risk_per_trade=signal.base_risk_per_trade,
                        risk_per_trade=signal.risk_per_trade,
                        risk_boost_multiplier=signal.risk_boost_multiplier,
                    ),
                ),
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
            'Signal_Timeframe': t.signal_timeframe,
            'Type': t.trade_type,
            'Entry_Time': t.entry_time,
            'Entry_Price': round_price(t.entry_price),
            'Stop_Loss': round_price(t.stop_loss),
            'Take_Profit': round_price(t.take_profit),
            'RR': round(t.rr_ratio, 2) if t.rr_ratio is not None else None,
            'Flex_Take_Profit': round_price(t.flex_take_profit) if t.flex_take_profit is not None else None,
            'Flex_RR': round(t.flex_rr_ratio, 2) if t.flex_rr_ratio is not None else None,
            'Base_Risk_Per_Trade': round(t.base_risk_per_trade, 4) if t.base_risk_per_trade is not None else None,
            'Risk_Per_Trade': round(t.risk_per_trade, 4) if t.risk_per_trade is not None else None,
            'Risk_Boost_Multiplier': round(t.risk_boost_multiplier, 4) if t.risk_boost_multiplier is not None else None,
            'TP_Capped': t.tp_capped,
            'PnL_R': round(t.pnl_r, 4) if t.pnl_r is not None else None,
            'Exit_Time': t.exit_time,
            'Exit_Price': round_price(t.exit_price) if t.exit_price else None,
            'Result': result,
            'Zone_High': round_price(t.zone_high) if t.zone_high is not None else None,
            'Zone_Low': round_price(t.zone_low) if t.zone_low is not None else None,
            'Zone_Time': t.zone_time,
            'Zone_Type': t.zone_type,
            'SL_Source': t.sl_source,
            'TP_Source': t.tp_source,
        })
    
    df = pd.DataFrame(data)
    
    # Custom formatter to avoid floating-point artifacts in CSV
    def format_price(x):
        if pd.isna(x):
            return ''
        # Convert to string with enough precision, then normalize
        d = Decimal(str(round(x, 10)))  # Round to 10 decimals first to eliminate artifacts
        d = d.normalize()
        return str(d)
    
    output_path = Path(output_dir) / f"{symbol}_trades.csv"
    df.to_csv(output_path, index=False, float_format=format_price)
    return df


def generate_summary_report(trades: List[Trade], symbol: str, config: BaseConfig) -> Dict:
    """Generate simplified summary statistics for trades"""
    if not trades:
        return {
            'symbol': symbol,
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'open_trades': 0,
            'win_rate': 0.0,
            'avg_sl_percent': 0.0,
            'avg_rr': 0.0,
            'total_rr_wins': 0.0,
            'total_rr': 0.0,
            'rr_count': 0,
            'net_pnl_r': 0.0,
            'gross_pnl_r': 0.0,
            'avg_risk_per_trade': 0.0,
            'avg_risk_boost_multiplier': 1.0,
            'boosted_trades': 0,
            'longest_win_streak': 0,
            'longest_loss_streak': 0,
            'avg_win_streak': 0.0,
            'avg_loss_streak': 0.0
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
    
    # Calculate average SL percentage
    sl_percentages = []
    for t in trades:
        if t.entry_price > 0:
            sl_pct = abs(t.entry_price - t.stop_loss) / t.entry_price * 100
            sl_percentages.append(sl_pct)
    avg_sl_percent = sum(sl_percentages) / len(sl_percentages) if sl_percentages else 0

    rr_values = [t.rr_ratio for t in trades if t.rr_ratio is not None]
    avg_rr = sum(rr_values) / len(rr_values) if rr_values else 0
    total_rr = sum(rr_values) if rr_values else 0
    total_rr_wins = sum(t.rr_ratio for t in trades if t.result == 'WIN' and t.rr_ratio is not None)
    risk_per_trade_values = []
    risk_multipliers = []
    boosted_trades = 0
    net_pnl_r = 0.0
    gross_pnl_r = 0.0

    for t in trades:
        effective_risk = t.risk_per_trade
        if effective_risk is None:
            effective_risk = t.base_risk_per_trade
        if effective_risk is not None:
            risk_per_trade_values.append(float(effective_risk))

        risk_multiplier = _resolve_risk_multiplier(
            base_risk_per_trade=t.base_risk_per_trade,
            risk_per_trade=t.risk_per_trade,
            risk_boost_multiplier=t.risk_boost_multiplier,
        )
        risk_multipliers.append(risk_multiplier)
        if risk_multiplier > 1.0 + 1e-9:
            boosted_trades += 1

        if t.result == 'WIN' and t.rr_ratio is not None:
            net_pnl_r += float(t.rr_ratio) * risk_multiplier
        elif t.result == 'LOSS':
            net_pnl_r -= 1.0 * risk_multiplier

        if t.result != 'OPEN':
            net_pnl_r -= float(config.fee_per_trade_r) * risk_multiplier
            gross_pnl_r += _calculate_trade_pnl_r(t.rr_ratio, t.result, risk_multiplier) or 0.0
    
    # Calculate streaks
    longest_win_streak = 0
    longest_loss_streak = 0
    current_win_streak = 0
    current_loss_streak = 0
    win_streaks = []
    loss_streaks = []
    
    for trade in trades:
        if trade.result == 'WIN':
            current_win_streak += 1
            if current_loss_streak > 0:
                loss_streaks.append(current_loss_streak)
                current_loss_streak = 0
            longest_win_streak = max(longest_win_streak, current_win_streak)
        elif trade.result == 'LOSS':
            if current_win_streak > 0:
                win_streaks.append(current_win_streak)
                current_win_streak = 0
            current_loss_streak += 1
            longest_loss_streak = max(longest_loss_streak, current_loss_streak)
        else:
            if current_win_streak > 0:
                win_streaks.append(current_win_streak)
                current_win_streak = 0
            if current_loss_streak > 0:
                loss_streaks.append(current_loss_streak)
                current_loss_streak = 0
    if current_win_streak > 0:
        win_streaks.append(current_win_streak)
    if current_loss_streak > 0:
        loss_streaks.append(current_loss_streak)

    avg_win_streak = sum(win_streaks) / len(win_streaks) if win_streaks else 0
    avg_loss_streak = sum(loss_streaks) / len(loss_streaks) if loss_streaks else 0
    
    return {
        'symbol': symbol,
        'total_trades': total_trades,
        'wins': win_count,
        'losses': loss_count,
        'open_trades': open_count,
        'win_rate': round(win_rate, 2),
        'avg_sl_percent': round(avg_sl_percent, 2),
        'avg_rr': round(avg_rr, 2),
        'total_rr_wins': total_rr_wins,
        'total_rr': total_rr,
        'rr_count': len(rr_values),
        'net_pnl_r': net_pnl_r,
        'gross_pnl_r': gross_pnl_r,
        'avg_risk_per_trade': round(sum(risk_per_trade_values) / len(risk_per_trade_values), 4) if risk_per_trade_values else 0.0,
        'avg_risk_boost_multiplier': round(sum(risk_multipliers) / len(risk_multipliers), 4) if risk_multipliers else 1.0,
        'boosted_trades': boosted_trades,
        'longest_win_streak': longest_win_streak,
        'longest_loss_streak': longest_loss_streak,
        'avg_win_streak': round(avg_win_streak, 2),
        'avg_loss_streak': round(avg_loss_streak, 2)
    }


def generate_text_report(all_summaries: List[Dict], config: BaseConfig, output_path: str):
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
    total_rr_wins = sum(s['total_rr_wins'] for s in all_summaries)
    total_rr = sum(s['total_rr'] for s in all_summaries)
    total_rr_count = sum(s['rr_count'] for s in all_summaries)
    overall_avg_rr = total_rr / total_rr_count if total_rr_count > 0 else 0
    total_risk_multiplier_weight = sum(s['total_trades'] for s in all_summaries)
    overall_avg_risk_boost_multiplier = (
        sum(s['avg_risk_boost_multiplier'] * s['total_trades'] for s in all_summaries) / total_risk_multiplier_weight
        if total_risk_multiplier_weight > 0
        else 1.0
    )
    
    # Get SL multiplier from config
    sl_multiplier = config.sl_multiplier
    flex_tp_risk_boost_factor = getattr(config, "flex_tp_risk_boost_factor", 0.0)
    
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
        f"##  TRADE RESULTS - {timestamp} - {config.report_label()}",
        f"- Base Risk/Trade: ${config.risk_per_trade}",
        f"- Flex TP Risk Boost Factor: {flex_tp_risk_boost_factor}",
        f"- Fee/Trade: {config.fee_per_trade_r}R (ROE = after fees, ROE-NF = no fees, both in R)",
        "| {:<14} | {:>6} | {:>6} | {:>6} | {:>4} | {:>10} | {:>6} | {:>4} | {:>5} | {:>8} | {:>8} | {:>5} | {:>4} | {:>4} | {:>5} | {:>5} |".format(
            "Symbol", "Total", "Wins", "Losses", "Open", "Win Rate", "%SL", "RR", "SLM", "ROE-NF", "ROE", "RBM", "W-S", "L-S", "W-AVG", "L-AVG"
        ),
        "|{:-<16}|{:->8}|{:->8}|{:->8}|{:->6}|{:->12}|{:->8}|{:->6}|{:->7}|{:->10}|{:->10}|{:->7}|{:->6}|{:->6}|{:->7}|{:->7}|".format("", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""),
    ]
    
    # Add results grouped by timeframe
    for timeframe in sorted_timeframes:
        for summary in timeframe_groups[timeframe]:
            roe = _summary_roe(summary, config.fee_per_trade_r)
            gross_roe = _summary_gross_roe(summary)
            row = "| {:<14} | {:>6} | {:>6} | {:>6} | {:>4} | {:>10} | {:>6} | {:>4} | {:>5} | {:>8} | {:>8} | {:>5} | {:>4} | {:>4} | {:>5} | {:>5} |".format(
                summary['symbol'],
                summary['total_trades'],
                summary['wins'],
                summary['losses'],
                summary['open_trades'],
                f"*{summary['win_rate']}%*",
                f"{summary['avg_sl_percent']}%",
                f"{summary['avg_rr']}",
                f"{sl_multiplier}",
                f"{round(gross_roe, 2)}",
                f"{round(roe, 2)}",
                f"{summary['avg_risk_boost_multiplier']}",
                summary['longest_win_streak'],
                summary['longest_loss_streak'],
                summary['avg_win_streak'],
                summary['avg_loss_streak']
            )
            lines.append(row)
    
    # Add overall summary
    overall_roe = sum(_summary_roe(summary, config.fee_per_trade_r) for summary in all_summaries)
    overall_gross_roe = sum(_summary_gross_roe(summary) for summary in all_summaries)
    
    # Calculate overall streaks and average SL percent
    overall_longest_win_streak = 0
    overall_longest_loss_streak = 0
    overall_avg_win_streaks = []
    overall_avg_loss_streaks = []
    all_sl_percents = []
    for summary in all_summaries:
        overall_longest_win_streak = max(overall_longest_win_streak, summary['longest_win_streak'])
        overall_longest_loss_streak = max(overall_longest_loss_streak, summary['longest_loss_streak'])
        if summary['avg_sl_percent'] > 0:
            all_sl_percents.append(summary['avg_sl_percent'])
        if summary['avg_win_streak'] > 0:
            overall_avg_win_streaks.append(summary['avg_win_streak'])
        if summary['avg_loss_streak'] > 0:
            overall_avg_loss_streaks.append(summary['avg_loss_streak'])
    overall_avg_sl_percent = sum(all_sl_percents) / len(all_sl_percents) if all_sl_percents else 0
    overall_avg_win_streak = sum(overall_avg_win_streaks) / len(overall_avg_win_streaks) if overall_avg_win_streaks else 0
    overall_avg_loss_streak = sum(overall_avg_loss_streaks) / len(overall_avg_loss_streaks) if overall_avg_loss_streaks else 0
    
    total_row = "| {:<14} | {:>6} | {:>6} | {:>6} | {:>4} | {:>10} | {:>6} | {:>4} | {:>5} | {:>8} | {:>8} | {:>5} | {:>4} | {:>4} | {:>5} | {:>5} |".format(
        "**TOTAL**",
        total_trades,
        total_wins,
        total_losses,
        total_open,
        f"*{round(overall_win_rate, 2)}%*",
        f"{round(overall_avg_sl_percent, 2)}%",
        f"{round(overall_avg_rr, 2)}",
        f"{sl_multiplier}",
        f"{round(overall_gross_roe, 2)}",
        f"{round(overall_roe, 2)}",
        f"{round(overall_avg_risk_boost_multiplier, 4)}",
        overall_longest_win_streak,
        overall_longest_loss_streak,
        round(overall_avg_win_streak, 2),
        round(overall_avg_loss_streak, 2)
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
    print(f"  - ROE: {round(summary.get('net_pnl_r', 0.0), 2)}")
    print(
        f"  - Avg Risk/Trade: ${summary.get('avg_risk_per_trade', 0.0)} "
        f"(x{summary.get('avg_risk_boost_multiplier', 1.0)})"
    )
    print(f"  - Longest Win Streak: {summary['longest_win_streak']}, Longest Loss Streak: {summary['longest_loss_streak']}")
    print(f"  - Avg Win Streak: {summary['avg_win_streak']}, Avg Loss Streak: {summary['avg_loss_streak']}")


def print_final_summary(all_trades: List[Trade], config: Optional[BaseConfig] = None) -> None:
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
        fee_r = config.fee_per_trade_r if config else 0.5
        overall_roe = 0.0
        overall_gross_roe = 0.0
        risk_values = []
        boosted_trades = 0

        for trade in all_trades:
            effective_risk = trade.risk_per_trade
            if effective_risk is None:
                effective_risk = trade.base_risk_per_trade
            if effective_risk is not None:
                risk_values.append(float(effective_risk))

            risk_multiplier = _resolve_risk_multiplier(
                base_risk_per_trade=trade.base_risk_per_trade,
                risk_per_trade=trade.risk_per_trade,
                risk_boost_multiplier=trade.risk_boost_multiplier,
            )
            if risk_multiplier > 1.0 + 1e-9:
                boosted_trades += 1

            if trade.result == 'WIN' and trade.rr_ratio is not None:
                overall_roe += float(trade.rr_ratio) * risk_multiplier
            elif trade.result == 'LOSS':
                overall_roe -= 1.0 * risk_multiplier

            if trade.result != 'OPEN':
                overall_roe -= fee_r * risk_multiplier

        # overall_roe so far is gross + fee; rebuild the no-fee figure by adding fees back
        closed_risk = sum(
            _resolve_risk_multiplier(
                base_risk_per_trade=t.base_risk_per_trade,
                risk_per_trade=t.risk_per_trade,
                risk_boost_multiplier=t.risk_boost_multiplier,
            )
            for t in all_trades if t.result != 'OPEN'
        )
        overall_gross_roe = overall_roe + fee_r * closed_risk

        print(f"ROE no fee (total symbols): {round(overall_gross_roe, 2)}")
        print(f"ROE with fee {fee_r}R/trade (total symbols): {round(overall_roe, 2)}")
        if risk_values:
            print(f"Average risk per trade: ${round(sum(risk_values) / len(risk_values), 4)}")
        print(f"Boosted trades: {boosted_trades}")
        
        # Calculate streaks
        longest_win_streak = 0
        longest_loss_streak = 0
        current_win_streak = 0
        current_loss_streak = 0
        win_streaks = []
        loss_streaks = []
        
        for trade in all_trades:
            if trade.result == 'WIN':
                current_win_streak += 1
                if current_loss_streak > 0:
                    loss_streaks.append(current_loss_streak)
                    current_loss_streak = 0
                longest_win_streak = max(longest_win_streak, current_win_streak)
            elif trade.result == 'LOSS':
                if current_win_streak > 0:
                    win_streaks.append(current_win_streak)
                    current_win_streak = 0
                current_loss_streak += 1
                longest_loss_streak = max(longest_loss_streak, current_loss_streak)
            else:
                if current_win_streak > 0:
                    win_streaks.append(current_win_streak)
                    current_win_streak = 0
                if current_loss_streak > 0:
                    loss_streaks.append(current_loss_streak)
                    current_loss_streak = 0
        if current_win_streak > 0:
            win_streaks.append(current_win_streak)
        if current_loss_streak > 0:
            loss_streaks.append(current_loss_streak)
        avg_win_streak = sum(win_streaks) / len(win_streaks) if win_streaks else 0
        avg_loss_streak = sum(loss_streaks) / len(loss_streaks) if loss_streaks else 0
        
        print(f"Longest win streak: {longest_win_streak}")
        print(f"Longest loss streak: {longest_loss_streak}")
        print(f"Average win streak: {round(avg_win_streak, 2)}")
        print(f"Average loss streak: {round(avg_loss_streak, 2)}")
    
    print("=" * 60)
