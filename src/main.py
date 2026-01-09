"""
EMA Crossover Trading Strategy Backtester
==========================================
Strategy:
- BUY: EMA20 crosses above EMA50, Close > EMA50
- SELL: EMA20 crosses below EMA50, Close < EMA50
- SL/TP based on ATR
- Risk per trade: configurable (default 10 USD)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Tuple, List, Dict, Optional
from dataclasses import dataclass, field
import os

# ==================== CONFIGURATION ====================
@dataclass
class Config:
    """Trading strategy configuration"""
    # EMA settings
    ema_fast: int = 20
    ema_slow: int = 50
    
    # ATR settings
    atr_period: int = 14
    atr_multiplier_sl: float = 1.5  # SL = ATR * multiplier
    
    # Risk/Reward settings
    risk_reward_ratio: float = 2.0  # TP = SL * RR
    risk_per_trade: float = 10.0    # USD per trade
    
    # Data settings
    data_folder: str = "src/data"
    output_folder: str = "src/output"


# ==================== DATA CLASSES ====================
@dataclass
class Trade:
    """Represents a single trade"""
    symbol: str
    trade_id: int
    entry_time: datetime
    entry_price: float
    trade_type: str  # 'LONG' or 'SHORT'
    stop_loss: float
    take_profit: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    result: Optional[str] = None  # 'WIN', 'LOSS', 'OPEN'
    pnl: float = 0.0
    risk_reward: float = 0.0
    status: str = 'OPEN'  # 'OPEN', 'CLOSED'


# ==================== INDICATORS ====================
def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average"""
    return series.ewm(span=period, adjust=False).mean()


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range"""
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.ewm(span=period, adjust=False).mean()
    
    return atr


def add_indicators(df: pd.DataFrame, config: Config) -> pd.DataFrame:
    """Add all technical indicators to dataframe"""
    df = df.copy()
    
    # Calculate EMAs
    df['EMA_fast'] = calculate_ema(df['Close'], config.ema_fast)
    df['EMA_slow'] = calculate_ema(df['Close'], config.ema_slow)
    
    # Calculate ATR
    df['ATR'] = calculate_atr(df, config.atr_period)
    
    # EMA crossover signals
    df['EMA_fast_prev'] = df['EMA_fast'].shift(1)
    df['EMA_slow_prev'] = df['EMA_slow'].shift(1)
    
    # Crossover detection
    df['cross_up'] = (df['EMA_fast_prev'] <= df['EMA_slow_prev']) & (df['EMA_fast'] > df['EMA_slow'])
    df['cross_down'] = (df['EMA_fast_prev'] >= df['EMA_slow_prev']) & (df['EMA_fast'] < df['EMA_slow'])
    
    return df


# ==================== SIGNAL DETECTION ====================
def detect_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detect BUY/SELL signals based on strategy rules:
    - BUY: EMA20 crosses above EMA50, Close > EMA50
    - SELL: EMA20 crosses below EMA50, Close < EMA50
    """
    df = df.copy()
    
    # BUY signal: EMA crossover up + Close > EMA_slow
    df['signal_buy'] = df['cross_up'] & (df['Close'] > df['EMA_slow'])
    
    # SELL signal: EMA crossover down + Close < EMA_slow
    df['signal_sell'] = df['cross_down'] & (df['Close'] < df['EMA_slow'])
    
    return df


# ==================== BACKTESTER ====================
class Backtester:
    def __init__(self, config: Config):
        self.config = config
        self.trades: List[Trade] = []
        self.trade_counter = 0
        
    def calculate_sl_tp(self, entry_price: float, atr: float, trade_type: str) -> Tuple[float, float]:
        """Calculate Stop Loss and Take Profit based on ATR"""
        sl_distance = atr * self.config.atr_multiplier_sl
        tp_distance = sl_distance * self.config.risk_reward_ratio
        
        if trade_type == 'LONG':
            stop_loss = entry_price - sl_distance
            take_profit = entry_price + tp_distance
        else:  # SHORT
            stop_loss = entry_price + sl_distance
            take_profit = entry_price - tp_distance
            
        return stop_loss, take_profit
    
    def check_exit(self, trade: Trade, row: pd.Series) -> Tuple[bool, Optional[str], Optional[float]]:
        """Check if trade should be closed based on SL/TP"""
        high = row['High']
        low = row['Low']
        
        if trade.trade_type == 'LONG':
            # Check SL first (worst case)
            if low <= trade.stop_loss:
                return True, 'LOSS', trade.stop_loss
            # Check TP
            if high >= trade.take_profit:
                return True, 'WIN', trade.take_profit
        else:  # SHORT
            # Check SL first (worst case)
            if high >= trade.stop_loss:
                return True, 'LOSS', trade.stop_loss
            # Check TP
            if low <= trade.take_profit:
                return True, 'WIN', trade.take_profit
                
        return False, None, None
    
    def run(self, df: pd.DataFrame, symbol: str) -> List[Trade]:
        """Run backtest on given data"""
        # Add indicators and detect signals
        df = add_indicators(df, self.config)
        df = detect_signals(df)
        
        # Skip first N rows to allow indicators to stabilize
        start_idx = max(self.config.ema_slow, self.config.atr_period) + 5
        
        open_trade: Optional[Trade] = None
        trades = []
        
        for i in range(start_idx, len(df)):
            row = df.iloc[i]
            time = row['Time'] if isinstance(row['Time'], datetime) else pd.to_datetime(row['Time'])
            
            # Check if we have an open trade
            if open_trade is not None:
                should_close, result, exit_price = self.check_exit(open_trade, row)
                
                if should_close:
                    open_trade.exit_time = time
                    open_trade.exit_price = exit_price
                    open_trade.result = result
                    open_trade.status = 'CLOSED'
                    
                    # Calculate PnL
                    if open_trade.trade_type == 'LONG':
                        pnl_pct = (exit_price - open_trade.entry_price) / open_trade.entry_price
                    else:
                        pnl_pct = (open_trade.entry_price - exit_price) / open_trade.entry_price
                    
                    # PnL based on risk amount
                    if result == 'WIN':
                        open_trade.pnl = self.config.risk_per_trade * self.config.risk_reward_ratio
                        open_trade.risk_reward = self.config.risk_reward_ratio
                    else:
                        open_trade.pnl = -self.config.risk_per_trade
                        open_trade.risk_reward = -1.0
                    
                    trades.append(open_trade)
                    open_trade = None
            
            # Look for new signals only if no open trade
            if open_trade is None:
                if row['signal_buy']:
                    self.trade_counter += 1
                    entry_price = row['Close']
                    atr = row['ATR']
                    sl, tp = self.calculate_sl_tp(entry_price, atr, 'LONG')
                    
                    open_trade = Trade(
                        symbol=symbol,
                        trade_id=self.trade_counter,
                        entry_time=time,
                        entry_price=entry_price,
                        trade_type='LONG',
                        stop_loss=sl,
                        take_profit=tp
                    )
                    
                elif row['signal_sell']:
                    self.trade_counter += 1
                    entry_price = row['Close']
                    atr = row['ATR']
                    sl, tp = self.calculate_sl_tp(entry_price, atr, 'SHORT')
                    
                    open_trade = Trade(
                        symbol=symbol,
                        trade_id=self.trade_counter,
                        entry_time=time,
                        entry_price=entry_price,
                        trade_type='SHORT',
                        stop_loss=sl,
                        take_profit=tp
                    )
        
        # Close any remaining open trade at last price
        if open_trade is not None:
            last_row = df.iloc[-1]
            open_trade.exit_time = last_row['Time'] if isinstance(last_row['Time'], datetime) else pd.to_datetime(last_row['Time'])
            open_trade.exit_price = last_row['Close']
            open_trade.status = 'CLOSED'
            
            if open_trade.trade_type == 'LONG':
                pnl = open_trade.exit_price - open_trade.entry_price
            else:
                pnl = open_trade.entry_price - open_trade.exit_price
                
            if pnl > 0:
                open_trade.result = 'WIN'
                open_trade.pnl = self.config.risk_per_trade * (pnl / abs(open_trade.entry_price - open_trade.stop_loss))
            else:
                open_trade.result = 'LOSS'
                open_trade.pnl = -self.config.risk_per_trade * abs(pnl / abs(open_trade.entry_price - open_trade.stop_loss))
            
            open_trade.risk_reward = open_trade.pnl / self.config.risk_per_trade
            trades.append(open_trade)
        
        self.trades.extend(trades)
        return trades


# ==================== REPORT GENERATION ====================
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
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'profit_factor': 0.0,
            'max_drawdown': 0.0
        }
    
    wins = [t for t in trades if t.result == 'WIN']
    losses = [t for t in trades if t.result == 'LOSS']
    
    total_trades = len(trades)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
    
    total_win = sum(t.pnl for t in wins)
    total_loss = abs(sum(t.pnl for t in losses))
    net_profit = sum(t.pnl for t in trades)
    
    avg_win = total_win / win_count if win_count > 0 else 0
    avg_loss = total_loss / loss_count if loss_count > 0 else 0
    profit_factor = total_win / total_loss if total_loss > 0 else float('inf')
    
    # Calculate max drawdown
    cumulative_pnl = []
    running_total = 0
    for t in trades:
        running_total += t.pnl
        cumulative_pnl.append(running_total)
    
    peak = cumulative_pnl[0]
    max_dd = 0
    for pnl in cumulative_pnl:
        if pnl > peak:
            peak = pnl
        dd = peak - pnl
        if dd > max_dd:
            max_dd = dd
    
    return {
        'symbol': symbol,
        'total_trades': total_trades,
        'wins': win_count,
        'losses': loss_count,
        'win_rate': round(win_rate, 2),
        'net_profit': round(net_profit, 2),
        'profit_factor': round(profit_factor, 2),
        'max_drawdown': round(max_dd, 2),
        'risk_reward': config.risk_reward_ratio
    }


def generate_markdown_report(all_summaries: List[Dict], config: Config, output_path: str):
    """Generate markdown report with all results"""
    
    lines = []
    
    for summary in all_summaries:
        lines.extend([
            f"### {summary['symbol']}",
            "",
            f"- Total Trades : {summary['total_trades']}",
            f"- Wins : {summary['wins']}",
            f"- Losses : {summary['losses']}",
            f"- Win Rate : {summary['win_rate']}%",
            f"- Net Profit : ${summary['net_profit']}",
            f"- Profit Factor : {summary['profit_factor']}",
            f"- Max Drawdown : ${summary['max_drawdown']}",
            f"- Risk/Reward : {summary['risk_reward']}",
            ""
        ])
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    print(f"Summary report saved to: {output_path}")


# ==================== MAIN EXECUTION ====================
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


def main():
    """Main execution function"""
    # Initialize configuration
    config = Config()
    
    # Create output folder if not exists
    output_dir = Path(config.output_folder)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Find all CSV files in data folder
    data_dir = Path(config.data_folder)
    csv_files = list(data_dir.glob("*.csv"))
    
    if not csv_files:
        print(f"No CSV files found in {config.data_folder}")
        return
    
    print(f"Found {len(csv_files)} data file(s)")
    print("=" * 50)
    
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
        print(f"  - Win Rate: {summary['win_rate']}%")
        print(f"  - Net Profit: ${summary['net_profit']}")
        
        # Generate trade CSV report for this symbol
        generate_trade_report(trades, str(output_dir), symbol)
    
    print("\n" + "=" * 50)
    print("Generating summary report...")
    
    # Generate markdown summary
    summary_md_path = "./src/res/backtest_results.md"
    generate_markdown_report(all_summaries, config, str(summary_md_path))
    
    print("\n" + "=" * 50)
    print("Backtest completed!")
    print(f"Total trades: {len(all_trades)}")
    print(f"Total profit: ${round(sum(t.pnl for t in all_trades), 2)}")


if __name__ == "__main__":
    main()
