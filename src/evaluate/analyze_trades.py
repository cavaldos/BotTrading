"""
Trade Analysis Script
=====================
Phân tích chi tiết các lệnh giao dịch từ folder output/
Xuất báo cáo HTML với biểu đồ Plotly

Các phân tích:
- Thống kê cơ bản (Win rate, Total trades, etc.)
- Phân tích theo khung thời gian (Giờ, Ngày trong tuần, Tháng)
- So sánh giữa các symbol và timeframe
- Performance metrics (ROE, Profit Factor, Streaks)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import glob


class TradeAnalyzer:
    """Phân tích các lệnh giao dịch từ CSV files"""
    
    # Cấu hình tài khoản mô phỏng
    INITIAL_CAPITAL = 1000.0  # Vốn ban đầu (USD)
    RISK_PERCENT = 0.01  # Risk 1% mỗi lệnh
    TRADING_FEE_RATE = 0.00075  # 0.075% phí giao dịch (taker fee)
    
    def __init__(self, output_folder: str = "output"):
        self.output_folder = Path(output_folder)
        self.trades_df = None
        self.all_trades = []
        
        # Account simulation data
        self.simulation_result = None
        
    def load_trades(self):
        """Load tất cả file CSV từ folder output"""
        csv_files = glob.glob(str(self.output_folder / "*.csv"))
        
        if not csv_files:
            print(f"Không tìm thấy file CSV nào trong folder {self.output_folder}")
            return False
        
        print(f"\n{'='*60}")
        print(f"Đang load {len(csv_files)} file CSV...")
        print(f"{'='*60}")
        
        for file_path in csv_files:
            df = pd.read_csv(file_path)
            df['Entry_Time'] = pd.to_datetime(df['Entry_Time'])
            df['Exit_Time'] = pd.to_datetime(df['Exit_Time'])
            self.all_trades.append(df)
            print(f"  ✓ Loaded: {Path(file_path).name} ({len(df)} trades)")
        
        # Gộp tất cả trades
        self.trades_df = pd.concat(self.all_trades, ignore_index=True)
        
        # Thêm các cột phân tích
        self.trades_df['Hour'] = self.trades_df['Entry_Time'].dt.hour
        self.trades_df['DayOfWeek'] = self.trades_df['Entry_Time'].dt.day_name()
        self.trades_df['DayOfWeekNum'] = self.trades_df['Entry_Time'].dt.dayofweek
        self.trades_df['Month'] = self.trades_df['Entry_Time'].dt.month
        self.trades_df['MonthName'] = self.trades_df['Entry_Time'].dt.strftime('%Y-%m')
        self.trades_df['Date'] = self.trades_df['Entry_Time'].dt.date
        
        # Normalize timezone: strip tz info to avoid tz-naive vs tz-aware mismatch
        for col in ['Entry_Time', 'Exit_Time']:
            if self.trades_df[col].dt.tz is not None:
                self.trades_df[col] = self.trades_df[col].dt.tz_localize(None)

        # Tính toán thời gian giao dịch (phút)
        self.trades_df['Duration_Minutes'] = (
            self.trades_df['Exit_Time'] - self.trades_df['Entry_Time']
        ).dt.total_seconds() / 60
        
        # Tính SL% (Stop Loss Percent)
        self.trades_df['SL_Percent'] = self.trades_df.apply(self._calculate_sl_percent, axis=1)
        
        # Tính RR Ratio (Risk:Reward) từ dữ liệu TP/SL
        self.trades_df['RR_Ratio'] = self.trades_df.apply(self._calculate_rr_ratio, axis=1)
        
        # Tính profit/loss (sử dụng RR thực tế từ dữ liệu)
        self.trades_df['PnL'] = self.trades_df.apply(self._calculate_pnl, axis=1)
        
        print(f"\n✓ Tổng cộng: {len(self.trades_df)} trades")
        print(f"{'='*60}\n")
        return True
    
    def _calculate_pnl(self, row):
        """
        Tính PnL dựa trên RR thực tế từ dữ liệu
        Risk = $10 (1% của $1000)
        Reward = Risk × RR_Ratio
        Đã bao gồm phí giao dịch 0.075% (taker fee) cho cả entry và exit
        Tổng phí = 0.075% x 2 = 0.15% trên mỗi lệnh
        """
        # Giả sử position size = $1000 (leverage 10x với margin $100)
        position_size = 1000.0
        risk_amount = 10.0  # Risk $10 per trade (1% of $1000)
        trading_fee_rate = 0.00075  # 0.075% per trade
        
        # Phí giao dịch = 0.075% x 2 (entry + exit) = 0.15%
        total_fee = position_size * trading_fee_rate * 2
        
        # Lấy RR ratio từ dữ liệu (đã tính ở _calculate_rr_ratio)
        rr_ratio = row.get('RR_Ratio', 2.0) if 'RR_Ratio' in row.index else 2.0
        if rr_ratio <= 0:
            rr_ratio = 2.0  # Fallback to default RR=2 if invalid
        
        if row['Result'] == 'Win':
            gross_pnl = risk_amount * rr_ratio  # Win = +Risk × RR
            return gross_pnl - total_fee  # Net PnL sau phí
        elif row['Result'] == 'Loss':
            gross_pnl = -risk_amount  # Loss = -Risk
            return gross_pnl - total_fee  # Net PnL sau phí
        else:
            return 0.0
    
    def _calculate_sl_percent(self, row):
        """
        Tính SL% (khoảng cách từ Entry đến Stop Loss theo %)
        LONG: SL% = (Entry_Price - Stop_Loss) / Entry_Price * 100
        SHORT: SL% = (Stop_Loss - Entry_Price) / Entry_Price * 100
        """
        try:
            entry_price = float(row['Entry_Price'])
            stop_loss = float(row['Stop_Loss'])
            
            if entry_price == 0:
                return 0.0
            
            if row['Type'] == 'LONG':
                sl_percent = (entry_price - stop_loss) / entry_price * 100
            else:  # SHORT
                sl_percent = (stop_loss - entry_price) / entry_price * 100
            
            return abs(sl_percent)  # Luôn trả về giá trị dương
        except (ValueError, KeyError, TypeError):
            return 0.0
    
    def _calculate_rr_ratio(self, row):
        """
        Tính tỉ lệ Risk:Reward (RR) từ Entry, TP, SL
        LONG: RR = (Take_Profit - Entry_Price) / (Entry_Price - Stop_Loss)
        SHORT: RR = (Entry_Price - Take_Profit) / (Stop_Loss - Entry_Price)
        
        Returns:
            float: Tỉ lệ RR (ví dụ: 2.0 nghĩa là 1:2)
        """
        try:
            entry_price = float(row['Entry_Price'])
            stop_loss = float(row['Stop_Loss'])
            take_profit = float(row['Take_Profit'])
            
            if entry_price == 0:
                return 0.0
            
            if row['Type'] == 'LONG':
                risk = entry_price - stop_loss
                reward = take_profit - entry_price
            else:  # SHORT
                risk = stop_loss - entry_price
                reward = entry_price - take_profit
            
            if risk <= 0:
                return 0.0
            
            return abs(reward / risk)
        except (ValueError, KeyError, TypeError):
            return 0.0
    
    def simulate_account(self):
        """
        Giả lập tài khoản giao dịch với:
        - Vốn ban đầu: $1000
        - Risk mỗi lệnh: 1% của balance hiện tại
        - Khi balance <= 0: Nạp thêm $1000 và tiếp tục
        
        Returns:
            dict: Kết quả mô phỏng bao gồm equity curve, ROE, ROI, số lần deposit
        """
        df = self.trades_df[self.trades_df['Result'] != 'Open'].copy()
        df = df.sort_values('Entry_Time').reset_index(drop=True)
        
        # Khởi tạo tài khoản
        initial_capital = self.INITIAL_CAPITAL
        balance = initial_capital
        total_deposited = initial_capital  # Tổng vốn đã nạp
        deposit_count = 0  # Số lần nạp thêm (không tính lần đầu)
        
        # Tracking data
        equity_history = []
        deposit_events = []  # Lưu thời điểm và số dư khi deposit
        
        for idx, row in df.iterrows():
            # Risk 1% của balance hiện tại
            risk_amount = balance * self.RISK_PERCENT
            
            # Position size dựa trên SL%
            sl_percent = row['SL_Percent'] if row['SL_Percent'] > 0 else 1.0
            position_size = (risk_amount / sl_percent) * 100
            
            # Phí giao dịch - giới hạn max 50% của risk_amount để tránh phí quá lớn
            raw_fee = position_size * self.TRADING_FEE_RATE * 2
            total_fee = min(raw_fee, risk_amount * 0.5)
            
            # Tính PnL dựa trên kết quả
            rr_ratio = row['RR_Ratio'] if row['RR_Ratio'] > 0 else 2.0
            
            if row['Result'] == 'Win':
                pnl = (risk_amount * rr_ratio) - total_fee
            else:  # Loss
                pnl = -risk_amount - total_fee
            
            # Cập nhật balance
            balance += pnl
            
            # Lưu lịch sử
            equity_history.append({
                'Entry_Time': row['Entry_Time'],
                'Symbol': row['Symbol'],
                'Type': row['Type'],
                'Result': row['Result'],
                'RR_Ratio': rr_ratio,
                'Risk_Amount': risk_amount,
                'PnL': pnl,
                'Balance': balance,
                'Deposit_Count': deposit_count
            })
            
            # Kiểm tra cháy tài khoản
            if balance <= 0:
                deposit_count += 1
                deposit_amount = self.INITIAL_CAPITAL
                total_deposited += deposit_amount
                
                deposit_events.append({
                    'Time': row['Entry_Time'],
                    'Trade_Index': idx,
                    'Balance_Before': balance,
                    'Deposit_Amount': deposit_amount,
                    'Deposit_Number': deposit_count
                })
                
                balance = deposit_amount  # Reset balance
                
                # Cập nhật record cuối cùng với balance mới sau deposit
                equity_history[-1]['Balance_After_Deposit'] = balance
        
        # Tạo DataFrame từ equity history
        equity_df = pd.DataFrame(equity_history)
        
        # Tính các metrics
        final_balance = balance
        total_pnl = final_balance - total_deposited
        
        # ROE = (Final Balance - Initial Capital) / Initial Capital × 100%
        # Chỉ tính trên vốn ban đầu $1000
        roe = ((final_balance - initial_capital) / initial_capital) * 100
        
        # ROI = Total PnL / Total Deposited × 100%
        # Tính trên tổng vốn đã bỏ ra
        roi = (total_pnl / total_deposited) * 100 if total_deposited > 0 else 0
        
        # RR Statistics
        closed_df = df[df['Result'] != 'Open']
        avg_rr = closed_df['RR_Ratio'].mean() if len(closed_df) > 0 else 0
        min_rr = closed_df['RR_Ratio'].min() if len(closed_df) > 0 else 0
        max_rr = closed_df['RR_Ratio'].max() if len(closed_df) > 0 else 0
        median_rr = closed_df['RR_Ratio'].median() if len(closed_df) > 0 else 0
        
        # Drawdown từ equity curve simulation
        if len(equity_df) > 0:
            equity_df['Peak'] = equity_df['Balance'].cummax()
            equity_df['Drawdown'] = equity_df['Balance'] - equity_df['Peak']
            equity_df['Drawdown_Pct'] = (equity_df['Drawdown'] / equity_df['Peak']) * 100
            max_drawdown = equity_df['Drawdown'].min()
            max_drawdown_pct = equity_df['Drawdown_Pct'].min()
        else:
            max_drawdown = 0
            max_drawdown_pct = 0
        
        self.simulation_result = {
            'initial_capital': initial_capital,
            'final_balance': final_balance,
            'total_deposited': total_deposited,
            'deposit_count': deposit_count,
            'total_pnl': total_pnl,
            'roe': roe,
            'roi': roi,
            'avg_rr': avg_rr,
            'min_rr': min_rr,
            'max_rr': max_rr,
            'median_rr': median_rr,
            'max_drawdown': max_drawdown,
            'max_drawdown_pct': max_drawdown_pct,
            'equity_df': equity_df,
            'deposit_events': deposit_events
        }
        
        return self.simulation_result
    
    def get_account_simulation_stats(self):
        """
        Lấy thống kê từ simulation (chạy simulate_account nếu chưa có)
        """
        if self.simulation_result is None:
            self.simulate_account()
        
        return self.simulation_result
    
    def get_basic_stats(self):
        """Thống kê cơ bản"""
        df = self.trades_df
        
        total_trades = len(df)
        wins = len(df[df['Result'] == 'Win'])
        losses = len(df[df['Result'] == 'Loss'])
        open_trades = len(df[df['Result'] == 'Open'])
        
        closed_trades = wins + losses
        win_rate = (wins / closed_trades * 100) if closed_trades > 0 else 0
        
        total_pnl = df['PnL'].sum()
        
        # Chuỗi thắng/thua dài nhất
        max_win_streak = self._calculate_max_streak(df, 'Win')
        max_loss_streak = self._calculate_max_streak(df, 'Loss')
        
        # Chuỗi thắng/thua trung bình
        avg_win_streak = self._calculate_average_streak(df, 'Win')
        avg_loss_streak = self._calculate_average_streak(df, 'Loss')
        
        # Thời gian giao dịch
        avg_duration = df['Duration_Minutes'].mean()
        
        # LONG vs SHORT
        long_trades = len(df[df['Type'] == 'LONG'])
        short_trades = len(df[df['Type'] == 'SHORT'])
        
        long_wins = len(df[(df['Type'] == 'LONG') & (df['Result'] == 'Win')])
        short_wins = len(df[(df['Type'] == 'SHORT') & (df['Result'] == 'Win')])
        
        long_closed = len(df[(df['Type'] == 'LONG') & (df['Result'] != 'Open')])
        short_closed = len(df[(df['Type'] == 'SHORT') & (df['Result'] != 'Open')])
        
        long_wr = (long_wins / long_closed * 100) if long_closed > 0 else 0
        short_wr = (short_wins / short_closed * 100) if short_closed > 0 else 0
        
        # Thời gian giữ lệnh trung bình theo LONG/SHORT
        long_df = df[(df['Type'] == 'LONG') & (df['Result'] != 'Open')]
        short_df = df[(df['Type'] == 'SHORT') & (df['Result'] != 'Open')]
        avg_long_duration = long_df['Duration_Minutes'].mean() if len(long_df) > 0 else 0
        avg_short_duration = short_df['Duration_Minutes'].mean() if len(short_df) > 0 else 0
        
        # Thống kê số lệnh theo ngày
        trades_per_day = df.groupby('Date').size()
        max_trades_per_day = int(trades_per_day.max()) if len(trades_per_day) > 0 else 0
        avg_trades_per_day = float(trades_per_day.mean()) if len(trades_per_day) > 0 else 0
        total_trading_days = len(trades_per_day)
        
        # RR Statistics
        closed_df = df[df['Result'] != 'Open']
        avg_rr = closed_df['RR_Ratio'].mean() if len(closed_df) > 0 else 0
        min_rr = closed_df['RR_Ratio'].min() if len(closed_df) > 0 else 0
        max_rr = closed_df['RR_Ratio'].max() if len(closed_df) > 0 else 0
        median_rr = float(np.median(closed_df['RR_Ratio'])) if len(closed_df) > 0 else 0
        
        return {
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'open_trades': open_trades,
            'closed_trades': closed_trades,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'max_win_streak': max_win_streak,
            'max_loss_streak': max_loss_streak,
            'avg_win_streak': avg_win_streak,
            'avg_loss_streak': avg_loss_streak,
            'avg_duration_minutes': avg_duration,
            'long_trades': long_trades,
            'short_trades': short_trades,
            'long_win_rate': long_wr,
            'short_win_rate': short_wr,
            'avg_long_duration': avg_long_duration,
            'avg_short_duration': avg_short_duration,
            'max_trades_per_day': max_trades_per_day,
            'avg_trades_per_day': avg_trades_per_day,
            'total_trading_days': total_trading_days,
            'avg_rr': avg_rr,
            'min_rr': min_rr,
            'max_rr': max_rr,
            'median_rr': median_rr
        }
    
    def _calculate_max_streak(self, df, result_type):
        """Tính chuỗi thắng/thua dài nhất"""
        max_streak = 0
        current_streak = 0
        
        for _, row in df.iterrows():
            if row['Result'] == result_type:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        
        return max_streak
    
    def _calculate_average_streak(self, df, result_type):
        """Tính chuỗi thắng/thua trung bình"""
        streaks = []
        current_streak = 0
        
        for _, row in df.iterrows():
            if row['Result'] == result_type:
                current_streak += 1
            else:
                if current_streak > 0:
                    streaks.append(current_streak)
                current_streak = 0
        
        # Thêm chuỗi cuối cùng nếu có
        if current_streak > 0:
            streaks.append(current_streak)
        
        return np.mean(streaks) if streaks else 0
    
    def get_stats_by_symbol(self):
        """Thống kê theo symbol"""
        df = self.trades_df
        symbols = df['Symbol'].unique()
        
        stats = []
        for symbol in symbols:
            symbol_df = df[df['Symbol'] == symbol]
            
            total = len(symbol_df)
            wins = len(symbol_df[symbol_df['Result'] == 'Win'])
            losses = len(symbol_df[symbol_df['Result'] == 'Loss'])
            closed = wins + losses
            wr = (wins / closed * 100) if closed > 0 else 0
            pnl = symbol_df['PnL'].sum()
            
            stats.append({
                'Symbol': symbol,
                'Total': total,
                'Wins': wins,
                'Losses': losses,
                'Win_Rate': wr,
                'PnL': pnl
            })
        
        return pd.DataFrame(stats)
    
    def get_stats_by_hour(self):
        """Thống kê theo giờ trong ngày"""
        df = self.trades_df[self.trades_df['Result'] != 'Open']
        
        hour_stats = []
        for hour in range(24):
            hour_df = df[df['Hour'] == hour]
            if len(hour_df) == 0:
                continue
            
            total = len(hour_df)
            wins = len(hour_df[hour_df['Result'] == 'Win'])
            wr = (wins / total * 100) if total > 0 else 0
            
            hour_stats.append({
                'Hour': hour,
                'Total_Trades': total,
                'Wins': wins,
                'Win_Rate': wr
            })
        
        return pd.DataFrame(hour_stats)
    
    def get_stats_by_day_of_week(self):
        """Thống kê theo ngày trong tuần"""
        df = self.trades_df[self.trades_df['Result'] != 'Open']
        
        day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        
        day_stats = []
        for day in day_order:
            day_df = df[df['DayOfWeek'] == day]
            if len(day_df) == 0:
                continue
            
            total = len(day_df)
            wins = len(day_df[day_df['Result'] == 'Win'])
            wr = (wins / total * 100) if total > 0 else 0
            
            day_stats.append({
                'DayOfWeek': day,
                'Total_Trades': total,
                'Wins': wins,
                'Win_Rate': wr
            })
        
        return pd.DataFrame(day_stats)
    
    def get_stats_by_month(self):
        """Thống kê theo tháng"""
        df = self.trades_df[self.trades_df['Result'] != 'Open']
        
        month_stats = []
        for month_name in sorted(df['MonthName'].unique()):
            month_df = df[df['MonthName'] == month_name]
            
            total = len(month_df)
            wins = len(month_df[month_df['Result'] == 'Win'])
            wr = (wins / total * 100) if total > 0 else 0
            pnl = month_df['PnL'].sum()
            
            month_stats.append({
                'Month': month_name,
                'Total_Trades': total,
                'Wins': wins,
                'Win_Rate': wr,
                'PnL': pnl
            })
        
        return pd.DataFrame(month_stats)
    
    def get_cumulative_pnl(self):
        """Tính PnL tích lũy theo thời gian"""
        df = self.trades_df[self.trades_df['Result'] != 'Open'].copy()
        df = df.sort_values('Entry_Time')
        df['Cumulative_PnL'] = df['PnL'].cumsum()
        return df[['Entry_Time', 'Symbol', 'Type', 'Result', 'PnL', 'Cumulative_PnL']]
    
    def get_trades_per_day_stats(self):
        """Phân tích chi tiết số lệnh theo ngày"""
        df = self.trades_df
        
        # Đếm số lệnh mỗi ngày
        trades_per_day = df.groupby('Date').agg({
            'Symbol': 'count',
            'Result': lambda x: (x == 'Win').sum()
        }).rename(columns={'Symbol': 'Total_Trades', 'Result': 'Wins'})
        
        trades_per_day['Date'] = trades_per_day.index
        trades_per_day = trades_per_day.reset_index(drop=True)
        
        # Tính win rate
        trades_per_day['Win_Rate'] = (
            trades_per_day['Wins'] / trades_per_day['Total_Trades'] * 100
        ).fillna(0)
        
        # Sort by date
        trades_per_day = trades_per_day.sort_values('Date')
        
        return trades_per_day
    
    def get_drawdown_analysis(self):
        """Phân tích Drawdown (giống MT5)"""
        df = self.trades_df[self.trades_df['Result'] != 'Open'].copy()
        df = df.sort_values('Entry_Time')
        df['Cumulative_PnL'] = df['PnL'].cumsum()
        
        # Tính running max (peak)
        df['Peak'] = df['Cumulative_PnL'].cummax()
        
        # Tính drawdown
        df['Drawdown'] = df['Cumulative_PnL'] - df['Peak']
        df['Drawdown_Pct'] = (df['Drawdown'] / df['Peak'].replace(0, np.nan)) * 100
        
        # Max Drawdown
        max_drawdown = df['Drawdown'].min()
        max_drawdown_pct = df['Drawdown_Pct'].min() if not df['Drawdown_Pct'].isna().all() else 0
        
        # Tìm thời điểm max drawdown
        if max_drawdown < 0:
            max_dd_idx = df['Drawdown'].idxmin()
            max_dd_date = df.loc[max_dd_idx, 'Entry_Time']
        else:
            max_dd_date = None
        
        # Tính số lần drawdown (mỗi lần equity giảm xuống dưới peak)
        drawdown_periods = []
        in_drawdown = False
        dd_start = None
        dd_start_value = 0
        
        for idx, row in df.iterrows():
            if row['Drawdown'] < 0 and not in_drawdown:
                in_drawdown = True
                dd_start = row['Entry_Time']
                dd_start_value = row['Peak']
            elif row['Drawdown'] >= 0 and in_drawdown:
                in_drawdown = False
                drawdown_periods.append({
                    'Start': dd_start,
                    'End': row['Entry_Time'],
                    'Peak_Value': dd_start_value,
                    'Recovery_Time': row['Entry_Time'] - dd_start if dd_start else None
                })
        
        # Nếu vẫn đang trong drawdown
        if in_drawdown:
            drawdown_periods.append({
                'Start': dd_start,
                'End': None,
                'Peak_Value': dd_start_value,
                'Recovery_Time': None
            })
        
        # Tính average recovery time
        recovery_times = [p['Recovery_Time'].days for p in drawdown_periods 
                        if p['Recovery_Time'] is not None]
        avg_recovery_days = np.mean(recovery_times) if recovery_times else 0
        
        # Tính Average Drawdown (chỉ tính các giá trị drawdown < 0)
        negative_drawdowns = df[df['Drawdown'] < 0]['Drawdown']
        avg_drawdown = negative_drawdowns.mean() if len(negative_drawdowns) > 0 else 0
        avg_drawdown_pct = df[df['Drawdown_Pct'] < 0]['Drawdown_Pct'].mean() if len(negative_drawdowns) > 0 else 0
        
        return {
            'max_drawdown': max_drawdown,
            'max_drawdown_pct': max_drawdown_pct,
            'max_drawdown_date': max_dd_date,
            'avg_drawdown': avg_drawdown,
            'avg_drawdown_pct': avg_drawdown_pct,
            'num_drawdown_periods': len(drawdown_periods),
            'avg_recovery_days': avg_recovery_days,
            'drawdown_df': df[['Entry_Time', 'Cumulative_PnL', 'Peak', 'Drawdown', 'Drawdown_Pct']],
            'drawdown_periods': drawdown_periods
        }
    
    def get_risk_metrics(self):
        """Tính các chỉ số rủi ro (giống MT5)"""
        df = self.trades_df[self.trades_df['Result'] != 'Open']
        
        wins = df[df['Result'] == 'Win']
        losses = df[df['Result'] == 'Loss']
        
        total_profit = wins['PnL'].sum() if len(wins) > 0 else 0
        total_loss = abs(losses['PnL'].sum()) if len(losses) > 0 else 0
        
        # Profit Factor = Gross Profit / Gross Loss
        profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
        
        # Expectancy = (Win% × Avg Win) - (Loss% × Avg Loss)
        win_rate = len(wins) / len(df) if len(df) > 0 else 0
        loss_rate = len(losses) / len(df) if len(df) > 0 else 0
        avg_win = wins['PnL'].mean() if len(wins) > 0 else 0
        avg_loss = abs(losses['PnL'].mean()) if len(losses) > 0 else 0
        expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
        
        # Recovery Factor = Net Profit / Max Drawdown
        net_profit = df['PnL'].sum()
        dd_analysis = self.get_drawdown_analysis()
        max_dd = abs(dd_analysis['max_drawdown']) if dd_analysis['max_drawdown'] < 0 else 1
        recovery_factor = net_profit / max_dd if max_dd > 0 else float('inf')
        
        # Payoff Ratio = Avg Win / Avg Loss
        payoff_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')
        
        # Sharpe Ratio (simplified - daily returns)
        df_sorted = df.sort_values('Entry_Time').copy()
        df_sorted['Date'] = df_sorted['Entry_Time'].dt.date
        daily_pnl = df_sorted.groupby('Date')['PnL'].sum()
        
        if len(daily_pnl) > 1:
            sharpe_ratio = (daily_pnl.mean() / daily_pnl.std()) * np.sqrt(252) if daily_pnl.std() > 0 else 0
        else:
            sharpe_ratio = 0
        
        # Sortino Ratio (chỉ tính downside deviation)
        negative_returns = daily_pnl[daily_pnl < 0]
        if len(negative_returns) > 1:
            downside_std = negative_returns.std()
            sortino_ratio = (daily_pnl.mean() / downside_std) * np.sqrt(252) if downside_std > 0 else 0
        else:
            sortino_ratio = 0
        
        return {
            'profit_factor': profit_factor,
            'expectancy': expectancy,
            'recovery_factor': recovery_factor,
            'payoff_ratio': payoff_ratio,
            'sharpe_ratio': sharpe_ratio,
            'sortino_ratio': sortino_ratio,
            'total_profit': total_profit,
            'total_loss': total_loss,
            'net_profit': net_profit,
            'avg_win': avg_win,
            'avg_loss': avg_loss
        }
    
    def get_trade_quality_stats(self):
        """Phân tích chất lượng trade (giống MT5)"""
        df = self.trades_df[self.trades_df['Result'] != 'Open']
        
        wins = df[df['Result'] == 'Win']
        losses = df[df['Result'] == 'Loss']
        
        # Thống kê cơ bản
        avg_win = wins['PnL'].mean() if len(wins) > 0 else 0
        avg_loss = abs(losses['PnL'].mean()) if len(losses) > 0 else 0
        max_win = wins['PnL'].max() if len(wins) > 0 else 0
        max_loss = abs(losses['PnL'].min()) if len(losses) > 0 else 0
        
        # Actual RR ratio
        actual_rr = avg_win / avg_loss if avg_loss > 0 else 0
        
        # Win/Loss size distribution
        win_sizes = wins['PnL'].tolist() if len(wins) > 0 else []
        loss_sizes = [abs(x) for x in losses['PnL'].tolist()] if len(losses) > 0 else []
        
        # Consecutive wins/losses
        consecutive_wins = []
        consecutive_losses = []
        current_win_streak = 0
        current_loss_streak = 0
        
        for _, row in df.iterrows():
            if row['Result'] == 'Win':
                current_win_streak += 1
                if current_loss_streak > 0:
                    consecutive_losses.append(current_loss_streak)
                    current_loss_streak = 0
            else:
                current_loss_streak += 1
                if current_win_streak > 0:
                    consecutive_wins.append(current_win_streak)
                    current_win_streak = 0
        
        # Append last streak
        if current_win_streak > 0:
            consecutive_wins.append(current_win_streak)
        if current_loss_streak > 0:
            consecutive_losses.append(current_loss_streak)
        
        return {
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'max_win': max_win,
            'max_loss': max_loss,
            'actual_rr': actual_rr,
            'win_sizes': win_sizes,
            'loss_sizes': loss_sizes,
            'consecutive_wins': consecutive_wins,
            'consecutive_losses': consecutive_losses,
            'median_win': np.median(win_sizes) if win_sizes else 0,
            'median_loss': np.median(loss_sizes) if loss_sizes else 0
        }
    
    def get_stats_by_session(self):
        """Thống kê theo phiên giao dịch (Asian, London, New York)"""
        df = self.trades_df[self.trades_df['Result'] != 'Open'].copy()
        
        # Định nghĩa các phiên (giờ UTC+7)
        def get_session(hour):
            if 1 <= hour < 9:
                return 'Asian'
            elif 14 <= hour < 17:
                return 'London'
            elif 19 <= hour <= 23:
                return 'New York'
            else:
                return 'Off-Session'
        
        df['Session'] = df['Hour'].apply(get_session)
        
        session_stats = []
        session_order = ['Asian', 'London', 'New York', 'Off-Session']
        
        for session in session_order:
            session_df = df[df['Session'] == session]
            if len(session_df) == 0:
                continue
            
            total = len(session_df)
            wins = len(session_df[session_df['Result'] == 'Win'])
            losses = len(session_df[session_df['Result'] == 'Loss'])
            win_rate = (wins / total * 100) if total > 0 else 0
            pnl = session_df['PnL'].sum()
            avg_pnl = session_df['PnL'].mean()
            
            session_stats.append({
                'Session': session,
                'Total_Trades': total,
                'Wins': wins,
                'Losses': losses,
                'Win_Rate': win_rate,
                'PnL': pnl,
                'Avg_PnL': avg_pnl
            })
        
        return pd.DataFrame(session_stats)
    
    def get_consecutive_analysis(self):
        """Phân tích hiệu suất sau chuỗi thắng/thua liên tiếp"""
        df = self.trades_df[self.trades_df['Result'] != 'Open'].copy()
        df = df.sort_values('Entry_Time').reset_index(drop=True)
        
        # Tính win streak trước mỗi trade
        results = []
        win_streak = 0
        loss_streak = 0
        
        for i, row in df.iterrows():
            # Lưu streak trước trade này
            results.append({
                'Trade_Index': i,
                'Result': row['Result'],
                'Win_Streak_Before': win_streak,
                'Loss_Streak_Before': loss_streak
            })
            
            # Cập nhật streak
            if row['Result'] == 'Win':
                win_streak += 1
                loss_streak = 0
            else:
                loss_streak += 1
                win_streak = 0
        
        analysis_df = pd.DataFrame(results)
        
        # Phân tích win rate sau các mức streak khác nhau
        after_win_streak = {}
        after_loss_streak = {}
        
        for streak in range(1, 6):  # Phân tích streak từ 1-5
            # Sau chuỗi thắng
            after_wins = analysis_df[analysis_df['Win_Streak_Before'] >= streak]
            if len(after_wins) > 0:
                wr = (after_wins['Result'] == 'Win').sum() / len(after_wins) * 100
                after_win_streak[streak] = {
                    'total': len(after_wins),
                    'win_rate': wr
                }
            
            # Sau chuỗi thua
            after_losses = analysis_df[analysis_df['Loss_Streak_Before'] >= streak]
            if len(after_losses) > 0:
                wr = (after_losses['Result'] == 'Win').sum() / len(after_losses) * 100
                after_loss_streak[streak] = {
                    'total': len(after_losses),
                    'win_rate': wr
                }
        
        return {
            'analysis_df': analysis_df,
            'after_win_streak': after_win_streak,
            'after_loss_streak': after_loss_streak
        }
    
    def get_hold_time_analysis(self):
        """Phân tích hiệu suất theo thời gian giữ lệnh"""
        df = self.trades_df[self.trades_df['Result'] != 'Open'].copy()
        
        # Phân loại thời gian giữ lệnh
        def categorize_duration(minutes):
            if minutes < 30:
                return 'Very Short (<30m)'
            elif minutes < 60:
                return 'Short (30m-1h)'
            elif minutes < 240:
                return 'Medium (1h-4h)'
            elif minutes < 1440:
                return 'Long (4h-1d)'
            else:
                return 'Very Long (>1d)'
        
        df['Duration_Category'] = df['Duration_Minutes'].apply(categorize_duration)
        
        categories_order = ['Very Short (<30m)', 'Short (30m-1h)', 'Medium (1h-4h)', 
                           'Long (4h-1d)', 'Very Long (>1d)']
        
        hold_stats = []
        for category in categories_order:
            cat_df = df[df['Duration_Category'] == category]
            if len(cat_df) == 0:
                continue
            
            total = len(cat_df)
            wins = len(cat_df[cat_df['Result'] == 'Win'])
            win_rate = (wins / total * 100) if total > 0 else 0
            pnl = cat_df['PnL'].sum()
            avg_duration = cat_df['Duration_Minutes'].mean()
            
            hold_stats.append({
                'Duration_Category': category,
                'Total_Trades': total,
                'Wins': wins,
                'Win_Rate': win_rate,
                'PnL': pnl,
                'Avg_Duration_Min': avg_duration
            })
        
        return pd.DataFrame(hold_stats)
    
    def get_sl_percent_analysis(self, num_ranges: int = 5):
        """
        Phân tích Win Rate theo khoảng SL% (Stop Loss Percent)
        
        Tính toán:
        - SL% trung bình, lớn nhất, nhỏ nhất
        - Chia thành 5 khoảng đều (equal range) từ Min đến Max
        - Chia thành 5 khoảng theo percentile (equal count)
        - Tính Win Rate cho từng khoảng
        
        Args:
            num_ranges: Số khoảng để chia (default: 5)
        """
        df = self.trades_df[self.trades_df['Result'] != 'Open'].copy()
        
        # Lọc bỏ các giá trị SL_Percent = 0 hoặc bất thường
        df = df[df['SL_Percent'] > 0]
        
        if len(df) == 0:
            return None
        
        sl_values = df['SL_Percent']
        
        # Thống kê cơ bản SL%
        avg_sl = sl_values.mean()
        min_sl = sl_values.min()
        max_sl = sl_values.max()
        median_sl = sl_values.median()
        std_sl = sl_values.std()
        
        # === CHIA THEO EQUAL RANGE (khoảng đều) ===
        range_size = (max_sl - min_sl) / num_ranges
        equal_range_stats = []
        
        for i in range(num_ranges):
            range_min = min_sl + (i * range_size)
            range_max = min_sl + ((i + 1) * range_size)
            
            # Khoảng cuối cùng bao gồm cả max
            if i == num_ranges - 1:
                range_df = df[(df['SL_Percent'] >= range_min) & (df['SL_Percent'] <= range_max)]
            else:
                range_df = df[(df['SL_Percent'] >= range_min) & (df['SL_Percent'] < range_max)]
            
            total = len(range_df)
            wins = len(range_df[range_df['Result'] == 'Win'])
            win_rate = (wins / total * 100) if total > 0 else 0
            
            equal_range_stats.append({
                'Range': f"{range_min:.2f}% - {range_max:.2f}%",
                'Range_Min': range_min,
                'Range_Max': range_max,
                'Total_Trades': total,
                'Wins': wins,
                'Losses': total - wins,
                'Win_Rate': win_rate
            })
        
        # === CHIA THEO PERCENTILE (số lượng đều) ===
        percentile_stats = []
        percentiles = np.linspace(0, 100, num_ranges + 1)
        
        for i in range(num_ranges):
            p_min = np.percentile(sl_values, percentiles[i])
            p_max = np.percentile(sl_values, percentiles[i + 1])
            
            # Khoảng cuối cùng bao gồm cả max
            if i == num_ranges - 1:
                range_df = df[(df['SL_Percent'] >= p_min) & (df['SL_Percent'] <= p_max)]
            else:
                range_df = df[(df['SL_Percent'] >= p_min) & (df['SL_Percent'] < p_max)]
            
            total = len(range_df)
            wins = len(range_df[range_df['Result'] == 'Win'])
            win_rate = (wins / total * 100) if total > 0 else 0
            
            percentile_stats.append({
                'Range': f"{p_min:.2f}% - {p_max:.2f}%",
                'Percentile': f"P{int(percentiles[i])}-P{int(percentiles[i+1])}",
                'Range_Min': p_min,
                'Range_Max': p_max,
                'Total_Trades': total,
                'Wins': wins,
                'Losses': total - wins,
                'Win_Rate': win_rate
            })
        
        return {
            'avg_sl_percent': avg_sl,
            'min_sl_percent': min_sl,
            'max_sl_percent': max_sl,
            'median_sl_percent': median_sl,
            'std_sl_percent': std_sl,
            'total_trades': len(df),
            'equal_range_stats': pd.DataFrame(equal_range_stats),
            'percentile_stats': pd.DataFrame(percentile_stats)
        }
    
    def get_random_sample_analysis(self, sample_size: int = 100, num_iterations: int = 20):
        """
        Phân tích Random Sampling - lấy ngẫu nhiên N lệnh liên tục, thực hiện M lần
        So sánh kết quả với thống kê tổng thể
        
        Args:
            sample_size: Số lệnh mỗi lần sample (default: 100)
            num_iterations: Số lần thực hiện random (default: 20)
        """
        df = self.trades_df[self.trades_df['Result'] != 'Open'].copy()
        df = df.sort_values('Entry_Time').reset_index(drop=True)
        
        total_trades = len(df)
        
        # Nếu không đủ lệnh để sample
        if total_trades < sample_size:
            return None
        
        # Thống kê tổng thể để so sánh
        overall_stats = self._calculate_sample_stats(df)
        
        # Thực hiện random sampling
        iterations = []
        max_start_idx = total_trades - sample_size
        
        for i in range(num_iterations):
            # Random điểm bắt đầu
            start_idx = np.random.randint(0, max_start_idx + 1)
            sample_df = df.iloc[start_idx:start_idx + sample_size].copy()
            
            # Tính stats cho sample này
            sample_stats = self._calculate_sample_stats(sample_df)
            sample_stats['start_idx'] = start_idx
            sample_stats['iteration'] = i + 1
            iterations.append(sample_stats)
        
        # Tính summary statistics
        summary = {
            'win_rate': {
                'avg': np.mean([it['win_rate'] for it in iterations]),
                'min': np.min([it['win_rate'] for it in iterations]),
                'max': np.max([it['win_rate'] for it in iterations]),
                'std': np.std([it['win_rate'] for it in iterations]),
                'overall': overall_stats['win_rate']
            },
            'max_drawdown': {
                'avg': np.mean([it['max_drawdown'] for it in iterations]),
                'min': np.min([it['max_drawdown'] for it in iterations]),
                'max': np.max([it['max_drawdown'] for it in iterations]),
                'std': np.std([it['max_drawdown'] for it in iterations]),
                'overall': overall_stats['max_drawdown']
            },
            'profit_factor': {
                'avg': np.mean([it['profit_factor'] for it in iterations if it['profit_factor'] != float('inf')]),
                'min': np.min([it['profit_factor'] for it in iterations if it['profit_factor'] != float('inf')]) if any(it['profit_factor'] != float('inf') for it in iterations) else 0,
                'max': np.max([it['profit_factor'] for it in iterations if it['profit_factor'] != float('inf')]) if any(it['profit_factor'] != float('inf') for it in iterations) else 0,
                'std': np.std([it['profit_factor'] for it in iterations if it['profit_factor'] != float('inf')]) if any(it['profit_factor'] != float('inf') for it in iterations) else 0,
                'overall': overall_stats['profit_factor']
            },
            'max_win_streak': {
                'avg': np.mean([it['max_win_streak'] for it in iterations]),
                'min': np.min([it['max_win_streak'] for it in iterations]),
                'max': np.max([it['max_win_streak'] for it in iterations]),
                'std': np.std([it['max_win_streak'] for it in iterations]),
                'overall': overall_stats['max_win_streak']
            },
            'max_loss_streak': {
                'avg': np.mean([it['max_loss_streak'] for it in iterations]),
                'min': np.min([it['max_loss_streak'] for it in iterations]),
                'max': np.max([it['max_loss_streak'] for it in iterations]),
                'std': np.std([it['max_loss_streak'] for it in iterations]),
                'overall': overall_stats['max_loss_streak']
            }
        }
        
        return {
            'sample_size': sample_size,
            'num_iterations': num_iterations,
            'total_trades': total_trades,
            'iterations': iterations,
            'summary': summary,
            'overall_stats': overall_stats
        }
    
    def _calculate_sample_stats(self, df):
        """Tính các thống kê cho một sample DataFrame"""
        total = len(df)
        wins = len(df[df['Result'] == 'Win'])
        losses = len(df[df['Result'] == 'Loss'])
        
        win_rate = (wins / total * 100) if total > 0 else 0
        
        # Tính PnL và Drawdown
        df = df.copy()
        df['Cumulative_PnL'] = df['PnL'].cumsum()
        df['Peak'] = df['Cumulative_PnL'].cummax()
        df['Drawdown'] = df['Cumulative_PnL'] - df['Peak']
        max_drawdown = df['Drawdown'].min()
        
        # Profit Factor
        total_profit = df[df['PnL'] > 0]['PnL'].sum()
        total_loss = abs(df[df['PnL'] < 0]['PnL'].sum())
        profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
        
        # Max Win/Loss Streak
        max_win_streak = 0
        max_loss_streak = 0
        current_win = 0
        current_loss = 0
        
        for _, row in df.iterrows():
            if row['Result'] == 'Win':
                current_win += 1
                current_loss = 0
                max_win_streak = max(max_win_streak, current_win)
            elif row['Result'] == 'Loss':
                current_loss += 1
                current_win = 0
                max_loss_streak = max(max_loss_streak, current_loss)
        
        return {
            'total_trades': total,
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
            'max_drawdown': max_drawdown,
            'profit_factor': profit_factor,
            'max_win_streak': max_win_streak,
            'max_loss_streak': max_loss_streak,
            'total_pnl': df['PnL'].sum()
        }
    
    def generate_html_report(self, output_file='trade_analysis_report.html'):
        """Tạo báo cáo HTML với biểu đồ Plotly (MT5 Style)"""
        print("\n" + "="*60)
        print("Đang tạo báo cáo HTML...")
        print("="*60)
        
        # Lấy dữ liệu cơ bản
        basic_stats = self.get_basic_stats()
        symbol_stats = self.get_stats_by_symbol()
        hour_stats = self.get_stats_by_hour()
        day_stats = self.get_stats_by_day_of_week()
        month_stats = self.get_stats_by_month()
        cumulative_df = self.get_cumulative_pnl()
        trades_per_day_df = self.get_trades_per_day_stats()
        
        # Lấy dữ liệu phân tích mới
        dd_analysis = self.get_drawdown_analysis()
        risk_metrics = self.get_risk_metrics()
        trade_quality = self.get_trade_quality_stats()
        session_stats = self.get_stats_by_session()
        consecutive_data = self.get_consecutive_analysis()
        hold_time_stats = self.get_hold_time_analysis()
        sl_analysis = self.get_sl_percent_analysis()
        
        # Account Simulation
        sim_data = self.get_account_simulation_stats()
        
        # Tạo HTML
        html_content = self._create_html_template()
        
        # Thêm thống kê cơ bản (MT5 Style) - bao gồm cả simulation stats
        html_content += self._create_basic_stats_section(basic_stats, risk_metrics, dd_analysis, sl_analysis, sim_data)
        
        # Tạo các biểu đồ
        fig_symbol = self._create_symbol_chart(symbol_stats)
        fig_hour = self._create_hour_chart(hour_stats)
        fig_day = self._create_day_chart(day_stats)
        fig_month = self._create_month_chart(month_stats)
        fig_cumulative = self._create_cumulative_pnl_chart(cumulative_df)
        fig_distribution = self._create_win_loss_distribution()
        fig_trades_per_day = self._create_trades_per_day_chart(trades_per_day_df)
        
        # Biểu đồ mới
        fig_drawdown = self._create_drawdown_chart(dd_analysis)
        fig_risk_metrics = self._create_risk_metrics_chart(risk_metrics, trade_quality)
        fig_session = self._create_session_chart(session_stats)
        fig_consecutive = self._create_consecutive_chart(consecutive_data)
        fig_hold_time = self._create_hold_time_chart(hold_time_stats)
        
        # Biểu đồ Account Simulation
        fig_simulation = self._create_simulation_equity_chart(sim_data)
        fig_rr_distribution = self._create_rr_distribution_chart(sim_data)
        
        # === ACCOUNT SIMULATION SECTION ===
        html_content += '<div class="chart-container">'
        html_content += '<h2>💰 Account Simulation (Vốn $1,000 - Risk 1%)</h2>'
        
        # Summary cards cho simulation
        roe_color = '#27ae60' if sim_data['roe'] >= 0 else '#e74c3c'
        roi_color = '#27ae60' if sim_data['roi'] >= 0 else '#e74c3c'
        deposit_color = '#e74c3c' if sim_data['deposit_count'] > 0 else '#27ae60'
        
        html_content += f'''
        <div style="display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin-bottom: 20px;">
            <div style="background: #2c3e50; color: white; padding: 15px; border-radius: 6px; text-align: center;">
                <div style="font-size: 0.75em; color: #aaa; text-transform: uppercase;">Initial Capital</div>
                <div style="font-size: 1.3em; font-weight: 600;">${sim_data['initial_capital']:.0f}</div>
            </div>
            <div style="background: #2c3e50; color: white; padding: 15px; border-radius: 6px; text-align: center;">
                <div style="font-size: 0.75em; color: #aaa; text-transform: uppercase;">Final Balance</div>
                <div style="font-size: 1.3em; font-weight: 600; color: {roe_color};">${sim_data['final_balance']:.0f}</div>
            </div>
            <div style="background: #2c3e50; color: white; padding: 15px; border-radius: 6px; text-align: center;">
                <div style="font-size: 0.75em; color: #aaa; text-transform: uppercase;">Total Deposited</div>
                <div style="font-size: 1.3em; font-weight: 600;">${sim_data['total_deposited']:.0f}</div>
            </div>
            <div style="background: #2c3e50; color: white; padding: 15px; border-radius: 6px; text-align: center;">
                <div style="font-size: 0.75em; color: #aaa; text-transform: uppercase;">ROE</div>
                <div style="font-size: 1.3em; font-weight: 600; color: {roe_color};">{sim_data['roe']:.1f}%</div>
            </div>
            <div style="background: #2c3e50; color: white; padding: 15px; border-radius: 6px; text-align: center;">
                <div style="font-size: 0.75em; color: #aaa; text-transform: uppercase;">ROI</div>
                <div style="font-size: 1.3em; font-weight: 600; color: {roi_color};">{sim_data['roi']:.1f}%</div>
            </div>
            <div style="background: #2c3e50; color: white; padding: 15px; border-radius: 6px; text-align: center;">
                <div style="font-size: 0.75em; color: #aaa; text-transform: uppercase;">Margin Calls</div>
                <div style="font-size: 1.3em; font-weight: 600; color: {deposit_color};">{sim_data['deposit_count']}</div>
            </div>
        </div>
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 20px;">
            <div style="background: #3498db; color: white; padding: 12px; border-radius: 6px; text-align: center;">
                <div style="font-size: 0.7em; text-transform: uppercase;">Avg RR</div>
                <div style="font-size: 1.1em; font-weight: 600;">1:{sim_data['avg_rr']:.2f}</div>
            </div>
            <div style="background: #27ae60; color: white; padding: 12px; border-radius: 6px; text-align: center;">
                <div style="font-size: 0.7em; text-transform: uppercase;">Min RR</div>
                <div style="font-size: 1.1em; font-weight: 600;">1:{sim_data['min_rr']:.2f}</div>
            </div>
            <div style="background: #e74c3c; color: white; padding: 12px; border-radius: 6px; text-align: center;">
                <div style="font-size: 0.7em; text-transform: uppercase;">Max RR</div>
                <div style="font-size: 1.1em; font-weight: 600;">1:{sim_data['max_rr']:.2f}</div>
            </div>
            <div style="background: #9b59b6; color: white; padding: 12px; border-radius: 6px; text-align: center;">
                <div style="font-size: 0.7em; text-transform: uppercase;">Median RR</div>
                <div style="font-size: 1.1em; font-weight: 600;">1:{sim_data['median_rr']:.2f}</div>
            </div>
        </div>
        '''
        
        if fig_simulation is not None:
            html_content += fig_simulation.to_html(full_html=False, include_plotlyjs='cdn')
        html_content += '</div>'
        
        # RR Distribution Chart
        if fig_rr_distribution is not None:
            html_content += '<div class="chart-container">'
            html_content += '<h2>📊 RR Ratio Distribution</h2>'
            html_content += fig_rr_distribution.to_html(full_html=False, include_plotlyjs=False)
            html_content += '</div>'
        
        # === RISK & PERFORMANCE SECTION ===
        html_content += '<div class="chart-container">'
        html_content += '<h2>📈 Equity Curve & Drawdown Analysis</h2>'
        html_content += fig_drawdown.to_html(full_html=False, include_plotlyjs=False)
        html_content += '</div>'
        
        html_content += '<div class="chart-container">'
        html_content += '<h2>⚖️ Risk Metrics & Trade Quality (MT5 Style)</h2>'
        html_content += fig_risk_metrics.to_html(full_html=False, include_plotlyjs=False)
        html_content += '</div>'
        
        # === SL% ANALYSIS SECTION ===
        if sl_analysis is not None:
            fig_sl_percent = self._create_sl_percent_chart(sl_analysis)
            if fig_sl_percent is not None:
                html_content += '<div class="chart-container">'
                html_content += '<h2>🎯 Phân Tích Win Rate Theo SL% (Stop Loss Percent)</h2>'
                html_content += f'<p style="color: #666; margin-bottom: 15px;">Avg SL%: {sl_analysis["avg_sl_percent"]:.2f}% | Min: {sl_analysis["min_sl_percent"]:.2f}% | Max: {sl_analysis["max_sl_percent"]:.2f}% | Median: {sl_analysis["median_sl_percent"]:.2f}%</p>'
                
                # Thêm bảng chi tiết SL% stats
                equal_range_df = sl_analysis['equal_range_stats']
                percentile_df = sl_analysis['percentile_stats']
                
                html_content += '''
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px;">
                    <div>
                        <h4 style="margin-bottom: 10px; color: #2c3e50;">Chia Đều Khoảng (Equal Range)</h4>
                        <table style="width: 100%; border-collapse: collapse; font-size: 0.85em;">
                            <thead>
                                <tr style="background: #2c3e50; color: white;">
                                    <th style="padding: 8px; text-align: left;">Khoảng SL%</th>
                                    <th style="padding: 8px; text-align: center;">Số Lệnh</th>
                                    <th style="padding: 8px; text-align: center;">Wins</th>
                                    <th style="padding: 8px; text-align: center;">Win Rate</th>
                                </tr>
                            </thead>
                            <tbody>
                '''
                
                for _, row in equal_range_df.iterrows():
                    wr_color = '#27ae60' if row['Win_Rate'] >= 50 else '#e74c3c'
                    html_content += f'''
                                <tr>
                                    <td style="padding: 8px; border-bottom: 1px solid #eee;">{row['Range']}</td>
                                    <td style="padding: 8px; text-align: center; border-bottom: 1px solid #eee;">{row['Total_Trades']}</td>
                                    <td style="padding: 8px; text-align: center; border-bottom: 1px solid #eee;">{row['Wins']}</td>
                                    <td style="padding: 8px; text-align: center; border-bottom: 1px solid #eee; color: {wr_color}; font-weight: 600;">{row['Win_Rate']:.1f}%</td>
                                </tr>
                    '''
                
                html_content += '''
                            </tbody>
                        </table>
                    </div>
                    <div>
                        <h4 style="margin-bottom: 10px; color: #2c3e50;">Chia Theo Percentile (Equal Count)</h4>
                        <table style="width: 100%; border-collapse: collapse; font-size: 0.85em;">
                            <thead>
                                <tr style="background: #2c3e50; color: white;">
                                    <th style="padding: 8px; text-align: left;">Percentile</th>
                                    <th style="padding: 8px; text-align: left;">Khoảng SL%</th>
                                    <th style="padding: 8px; text-align: center;">Số Lệnh</th>
                                    <th style="padding: 8px; text-align: center;">Win Rate</th>
                                </tr>
                            </thead>
                            <tbody>
                '''
                
                for _, row in percentile_df.iterrows():
                    wr_color = '#27ae60' if row['Win_Rate'] >= 50 else '#e74c3c'
                    html_content += f'''
                                <tr>
                                    <td style="padding: 8px; border-bottom: 1px solid #eee;">{row['Percentile']}</td>
                                    <td style="padding: 8px; border-bottom: 1px solid #eee;">{row['Range']}</td>
                                    <td style="padding: 8px; text-align: center; border-bottom: 1px solid #eee;">{row['Total_Trades']}</td>
                                    <td style="padding: 8px; text-align: center; border-bottom: 1px solid #eee; color: {wr_color}; font-weight: 600;">{row['Win_Rate']:.1f}%</td>
                                </tr>
                    '''
                
                html_content += '''
                            </tbody>
                        </table>
                    </div>
                </div>
                '''
                
                html_content += fig_sl_percent.to_html(full_html=False, include_plotlyjs=False)
                html_content += '</div>'
        
        # === SESSION ANALYSIS SECTION ===
        html_content += '<div class="chart-container">'
        html_content += '<h2>🌍 Phân Tích Theo Phiên Giao Dịch</h2>'
        html_content += '<p style="color: #666; margin-bottom: 15px;">Asian (1h-9h) | London (14h-17h) | New York (19h-23h)</p>'
        html_content += fig_session.to_html(full_html=False, include_plotlyjs=False)
        html_content += '</div>'
        
        # === BEHAVIORAL ANALYSIS ===
        html_content += '<div class="chart-container">'
        html_content += '<h2>🔄 Phân Tích Chuỗi Thắng/Thua (Consecutive Analysis)</h2>'
        html_content += '<p style="color: #666; margin-bottom: 15px;">Win rate của trade tiếp theo sau chuỗi thắng/thua liên tiếp</p>'
        html_content += fig_consecutive.to_html(full_html=False, include_plotlyjs=False)
        html_content += '</div>'
        
        # === RANDOM SAMPLING ANALYSIS ===
        random_sample_data = self.get_random_sample_analysis(sample_size=100, num_iterations=20)
        if random_sample_data is not None:
            fig_random_sample = self._create_random_sample_chart(random_sample_data)
            fig_random_distribution = self._create_random_sample_distribution_chart(random_sample_data)
            
            html_content += '<div class="chart-container">'
            html_content += '<h2>🎲 Random Sampling Analysis (100 lệnh x 20 lần)</h2>'
            html_content += '<p style="color: #666; margin-bottom: 15px;">Lấy ngẫu nhiên 100 lệnh liên tục, thực hiện 20 lần để kiểm tra tính ổn định của chiến lược</p>'
            
            # Thêm summary table
            summary = random_sample_data['summary']
            html_content += '''
            <div style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 20px;">
                <div style="background: #f8f9fa; padding: 12px; border-radius: 6px; text-align: center;">
                    <div style="font-size: 0.75em; color: #666; text-transform: uppercase;">Win Rate</div>
                    <div style="font-size: 1.1em; font-weight: 600; color: #2c3e50;">''' + f"{summary['win_rate']['avg']:.1f}%" + '''</div>
                    <div style="font-size: 0.7em; color: #888;">Range: ''' + f"{summary['win_rate']['min']:.1f}% - {summary['win_rate']['max']:.1f}%" + '''</div>
                </div>
                <div style="background: #f8f9fa; padding: 12px; border-radius: 6px; text-align: center;">
                    <div style="font-size: 0.75em; color: #666; text-transform: uppercase;">Max Drawdown</div>
                    <div style="font-size: 1.1em; font-weight: 600; color: #e74c3c;">$''' + f"{abs(summary['max_drawdown']['avg']):.0f}" + '''</div>
                    <div style="font-size: 0.7em; color: #888;">Range: $''' + f"{abs(summary['max_drawdown']['min']):.0f} - ${abs(summary['max_drawdown']['max']):.0f}" + '''</div>
                </div>
                <div style="background: #f8f9fa; padding: 12px; border-radius: 6px; text-align: center;">
                    <div style="font-size: 0.75em; color: #666; text-transform: uppercase;">Profit Factor</div>
                    <div style="font-size: 1.1em; font-weight: 600; color: #27ae60;">''' + f"{summary['profit_factor']['avg']:.2f}" + '''</div>
                    <div style="font-size: 0.7em; color: #888;">Range: ''' + f"{summary['profit_factor']['min']:.2f} - {summary['profit_factor']['max']:.2f}" + '''</div>
                </div>
                <div style="background: #f8f9fa; padding: 12px; border-radius: 6px; text-align: center;">
                    <div style="font-size: 0.75em; color: #666; text-transform: uppercase;">Max Win Streak</div>
                    <div style="font-size: 1.1em; font-weight: 600; color: #27ae60;">''' + f"{summary['max_win_streak']['avg']:.1f}" + '''</div>
                    <div style="font-size: 0.7em; color: #888;">Range: ''' + f"{summary['max_win_streak']['min']:.0f} - {summary['max_win_streak']['max']:.0f}" + '''</div>
                </div>
                <div style="background: #f8f9fa; padding: 12px; border-radius: 6px; text-align: center;">
                    <div style="font-size: 0.75em; color: #666; text-transform: uppercase;">Max Loss Streak</div>
                    <div style="font-size: 1.1em; font-weight: 600; color: #e74c3c;">''' + f"{summary['max_loss_streak']['avg']:.1f}" + '''</div>
                    <div style="font-size: 0.7em; color: #888;">Range: ''' + f"{summary['max_loss_streak']['min']:.0f} - {summary['max_loss_streak']['max']:.0f}" + '''</div>
                </div>
            </div>
            '''
            
            if fig_random_sample is not None:
                html_content += fig_random_sample.to_html(full_html=False, include_plotlyjs=False)
            html_content += '</div>'
            
            html_content += '<div class="chart-container">'
            html_content += '<h2>📊 Phân Phối Kết Quả Random Sampling</h2>'
            html_content += '<p style="color: #666; margin-bottom: 15px;">Box plot thể hiện phân phối các metrics qua 20 lần random. Đường cam = giá trị tổng thể.</p>'
            if fig_random_distribution is not None:
                html_content += fig_random_distribution.to_html(full_html=False, include_plotlyjs=False)
            html_content += '</div>'
        
        html_content += '<div class="chart-container">'
        html_content += '<h2>⏱️ Phân Tích Theo Thời Gian Giữ Lệnh</h2>'
        html_content += fig_hold_time.to_html(full_html=False, include_plotlyjs=False)
        html_content += '</div>'
        
        # === TRADITIONAL ANALYSIS ===
        html_content += '<div class="chart-container">'
        html_content += '<h2>📊 Phân Tích Theo Symbol</h2>'
        html_content += fig_symbol.to_html(full_html=False, include_plotlyjs=False)
        html_content += '</div>'
        
        html_content += '<div class="chart-container">'
        html_content += '<h2>⏰ Phân Tích Theo Giờ Trong Ngày</h2>'
        html_content += fig_hour.to_html(full_html=False, include_plotlyjs=False)
        html_content += '</div>'
        
        html_content += '<div class="chart-container">'
        html_content += '<h2>📅 Phân Tích Theo Ngày Trong Tuần</h2>'
        html_content += fig_day.to_html(full_html=False, include_plotlyjs=False)
        html_content += '</div>'
        
        html_content += '<div class="chart-container">'
        html_content += '<h2>📆 PnL Theo Tháng</h2>'
        html_content += fig_month.to_html(full_html=False, include_plotlyjs=False)
        html_content += '</div>'
        
        html_content += '<div class="chart-container">'
        html_content += '<h2>💰 PnL Tích Lũy Theo Thời Gian</h2>'
        html_content += fig_cumulative.to_html(full_html=False, include_plotlyjs=False)
        html_content += '</div>'
        
        html_content += '<div class="chart-container">'
        html_content += '<h2>📊 Phân Phối Win/Loss</h2>'
        html_content += fig_distribution.to_html(full_html=False, include_plotlyjs=False)
        html_content += '</div>'
        
        html_content += '<div class="chart-container">'
        html_content += '<h2>📈 Số Lệnh Theo Từng Ngày</h2>'
        html_content += fig_trades_per_day.to_html(full_html=False, include_plotlyjs=False)
        html_content += '</div>'
        
        # Thêm bảng chi tiết
        html_content += self._create_detailed_tables(
            symbol_stats, hour_stats, day_stats, month_stats, 
            session_stats, hold_time_stats
        )
        
        # Kết thúc HTML
        html_content += '</div></body></html>'
        
        # Lưu file
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"\n✓ Báo cáo đã được lưu: {output_path.absolute()}")
        print(f"{'='*60}\n")
        
        return output_path
    
    def _create_html_template(self):
        """Tạo template HTML"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        html = f"""
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trade Analysis Report</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f5f5f5;
            padding: 15px;
            color: #333;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .header {{
            background: #2c3e50;
            color: white;
            padding: 20px;
            text-align: center;
        }}
        .header h1 {{
            font-size: 1.6em;
            margin-bottom: 5px;
        }}
        .header p {{
            font-size: 0.9em;
            opacity: 0.8;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 8px;
            padding: 15px;
            background: #fafafa;
        }}
        .stat-card {{
            background: white;
            padding: 10px 12px;
            border-radius: 4px;
            border: 1px solid #e0e0e0;
        }}
        .stat-label {{
            font-size: 0.7em;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }}
        .stat-value {{
            font-size: 1.1em;
            font-weight: 600;
            color: #2c3e50;
        }}
        .stat-value.positive {{
            color: #27ae60;
        }}
        .stat-value.negative {{
            color: #e74c3c;
        }}
        .summary-box {{
            background: #2c3e50;
            color: white;
            padding: 15px;
            border-radius: 4px;
            grid-column: span 4;
        }}
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
        }}
        .summary-item .label {{
            font-size: 0.7em;
            opacity: 0.8;
            text-transform: uppercase;
        }}
        .summary-item .value {{
            font-size: 1.2em;
            font-weight: 600;
        }}
        .chart-container {{
            padding: 20px;
            border-bottom: 1px solid #eee;
        }}
        .chart-container h2 {{
            margin-bottom: 15px;
            color: #2c3e50;
            font-size: 1.2em;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
            font-size: 0.85em;
        }}
        th {{
            background: #2c3e50;
            color: white;
            padding: 10px;
            text-align: left;
            font-weight: 500;
        }}
        td {{
            padding: 8px 10px;
            border-bottom: 1px solid #eee;
        }}
        tr:hover {{
            background: #f9f9f9;
        }}
        .footer {{
            text-align: center;
            padding: 15px;
            background: #fafafa;
            color: #666;
            font-size: 0.8em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>TRADE ANALYSIS REPORT</h1>
            <p>Generated: {timestamp}</p>
        </div>
"""
        return html
    
    def _create_basic_stats_section(self, stats, risk_metrics, dd_analysis, sl_analysis=None, sim_data=None):
        """Tạo section thống kê cơ bản"""
        # Format các giá trị
        pf = risk_metrics['profit_factor']
        pf_display = f"{pf:.2f}" if pf != float('inf') else "-"
        rf = risk_metrics['recovery_factor']
        rf_display = f"{rf:.2f}" if rf != float('inf') else "-"
        pr = risk_metrics['payoff_ratio']
        pr_display = f"{pr:.2f}" if pr != float('inf') else "-"
        
        # SL% stats
        if sl_analysis is not None:
            avg_sl = f"{sl_analysis['avg_sl_percent']:.2f}%"
            min_sl = f"{sl_analysis['min_sl_percent']:.2f}%"
            max_sl = f"{sl_analysis['max_sl_percent']:.2f}%"
            median_sl = f"{sl_analysis['median_sl_percent']:.2f}%"
        else:
            avg_sl = min_sl = max_sl = median_sl = "-"
        
        html = f"""
        <div style="padding: 15px; background: #fafafa;">
            <!-- Row 1: Summary -->
            <div style="display: grid; grid-template-columns: repeat(8, 1fr); gap: 8px; margin-bottom: 8px;">
                <div class="stat-card" style="background: #2c3e50; color: white;">
                    <div class="stat-label" style="color: #aaa;">Net Profit</div>
                    <div class="stat-value" style="color: {'#2ecc71' if stats['total_pnl'] >= 0 else '#e74c3c'};">${stats['total_pnl']:.0f}</div>
                </div>
                <div class="stat-card" style="background: #2c3e50; color: white;">
                    <div class="stat-label" style="color: #aaa;">Win Rate</div>
                    <div class="stat-value" style="color: white;">{stats['win_rate']:.2f}%</div>
                </div>
                <div class="stat-card" style="background: #2c3e50; color: white;">
                    <div class="stat-label" style="color: #aaa;">Profit Factor</div>
                    <div class="stat-value" style="color: white;">{pf_display}</div>
                </div>
                <div class="stat-card" style="background: #2c3e50; color: white;">
                    <div class="stat-label" style="color: #aaa;">Max Drawdown</div>
                    <div class="stat-value" style="color: #e74c3c;">${dd_analysis['max_drawdown']:.0f}</div>
                </div>
                <div class="stat-card" style="background: #2c3e50; color: white;">
                    <div class="stat-label" style="color: #aaa;">Avg Drawdown</div>
                    <div class="stat-value" style="color: #e74c3c;">${dd_analysis['avg_drawdown']:.0f}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Total</div>
                    <div class="stat-value">{stats['total_trades']}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Wins</div>
                    <div class="stat-value positive">{stats['wins']}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Losses</div>
                    <div class="stat-value negative">{stats['losses']}</div>
                </div>
            </div>
            
            <!-- Row 2: Risk Metrics -->
            <div style="display: grid; grid-template-columns: repeat(8, 1fr); gap: 8px; margin-bottom: 8px;">
                <div class="stat-card">
                    <div class="stat-label">Gross Profit</div>
                    <div class="stat-value positive">${risk_metrics['total_profit']:.0f}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Gross Loss</div>
                    <div class="stat-value negative">${risk_metrics['total_loss']:.0f}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Expectancy</div>
                    <div class="stat-value {'positive' if risk_metrics['expectancy'] >= 0 else 'negative'}">${risk_metrics['expectancy']:.2f}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Payoff Ratio</div>
                    <div class="stat-value">{pr_display}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Sharpe</div>
                    <div class="stat-value">{risk_metrics['sharpe_ratio']:.2f}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Sortino</div>
                    <div class="stat-value">{risk_metrics['sortino_ratio']:.2f}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Trading Days</div>
                    <div class="stat-value">{stats['total_trading_days']}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">DD Periods</div>
                    <div class="stat-value">{dd_analysis['num_drawdown_periods']}</div>
                </div>
            </div>
            
            <!-- Row 3: Trade Details -->
            <div style="display: grid; grid-template-columns: repeat(8, 1fr); gap: 8px;">
                <div class="stat-card">
                    <div class="stat-label">Avg Win</div>
                    <div class="stat-value positive">${risk_metrics['avg_win']:.2f}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Avg Loss</div>
                    <div class="stat-value negative">${risk_metrics['avg_loss']:.2f}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Win Streak</div>
                    <div class="stat-value">{stats['max_win_streak']}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Loss Streak</div>
                    <div class="stat-value">{stats['max_loss_streak']}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Avg Duration</div>
                    <div class="stat-value">{stats['avg_duration_minutes']:.0f}m</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">LONG WR | Dur</div>
                    <div class="stat-value">{stats['long_win_rate']:.1f}% | {stats['avg_long_duration']:.0f}m</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">SHORT WR | Dur</div>
                    <div class="stat-value">{stats['short_win_rate']:.1f}% | {stats['avg_short_duration']:.0f}m</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Trades/Day</div>
                    <div class="stat-value">{stats['avg_trades_per_day']:.1f}</div>
                </div>
            </div>
            
            <!-- Row 4: SL% Analysis -->
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 8px;">
                <div class="stat-card" style="background: #3498db; color: white;">
                    <div class="stat-label" style="color: #ddd;">Avg SL%</div>
                    <div class="stat-value" style="color: white;">{avg_sl}</div>
                </div>
                <div class="stat-card" style="background: #27ae60; color: white;">
                    <div class="stat-label" style="color: #ddd;">Min SL%</div>
                    <div class="stat-value" style="color: white;">{min_sl}</div>
                </div>
                <div class="stat-card" style="background: #e74c3c; color: white;">
                    <div class="stat-label" style="color: #ddd;">Max SL%</div>
                    <div class="stat-value" style="color: white;">{max_sl}</div>
                </div>
                <div class="stat-card" style="background: #9b59b6; color: white;">
                    <div class="stat-label" style="color: #ddd;">Median SL%</div>
                    <div class="stat-value" style="color: white;">{median_sl}</div>
                </div>
            </div>
        </div>
"""
        return html
    
    def _create_symbol_chart(self, df):
        """Biểu đồ so sánh các symbol"""
        fig = make_subplots(
            rows=1, cols=2,
            subplot_titles=('Win Rate by Symbol', 'Total Trades by Symbol'),
            specs=[[{'type': 'bar'}, {'type': 'bar'}]]
        )
        
        # Win Rate
        fig.add_trace(
            go.Bar(
                x=df['Symbol'],
                y=df['Win_Rate'],
                name='Win Rate',
                marker_color='rgb(55, 83, 109)',
                text=[f"{val:.1f}%" for val in df['Win_Rate']],
                textposition='outside'
            ),
            row=1, col=1
        )
        
        # Total Trades
        colors = ['rgb(26, 118, 255)' if 'BTC' in sym else 'rgb(255, 127, 14)' 
                  for sym in df['Symbol']]
        fig.add_trace(
            go.Bar(
                x=df['Symbol'],
                y=df['Total'],
                name='Total Trades',
                marker_color=colors,
                text=df['Total'],
                textposition='outside'
            ),
            row=1, col=2
        )
        
        fig.update_xaxes(title_text="Symbol", row=1, col=1)
        fig.update_xaxes(title_text="Symbol", row=1, col=2)
        fig.update_yaxes(title_text="Win Rate (%)", row=1, col=1)
        fig.update_yaxes(title_text="Number of Trades", row=1, col=2)
        
        fig.update_layout(
            height=500,
            showlegend=False,
            plot_bgcolor='rgba(240,240,240,0.5)',
            paper_bgcolor='white'
        )
        
        return fig
    
    def _create_hour_chart(self, df):
        """Biểu đồ theo giờ"""
        fig = make_subplots(
            rows=2, cols=1,
            subplot_titles=('Win Rate by Hour', 'Trade Volume by Hour'),
            specs=[[{'type': 'scatter'}], [{'type': 'bar'}]],
            vertical_spacing=0.15
        )
        
        # Win Rate line
        fig.add_trace(
            go.Scatter(
                x=df['Hour'],
                y=df['Win_Rate'],
                mode='lines+markers',
                name='Win Rate',
                line=dict(color='rgb(55, 83, 109)', width=3),
                marker=dict(size=8),
                fill='tozeroy',
                fillcolor='rgba(55, 83, 109, 0.2)'
            ),
            row=1, col=1
        )
        
        # Trade volume bars
        fig.add_trace(
            go.Bar(
                x=df['Hour'],
                y=df['Total_Trades'],
                name='Total Trades',
                marker_color='rgb(26, 118, 255)',
                text=df['Total_Trades'],
                textposition='outside'
            ),
            row=2, col=1
        )
        
        fig.update_xaxes(title_text="Giờ trong ngày (24h)", row=2, col=1)
        fig.update_yaxes(title_text="Win Rate (%)", row=1, col=1)
        fig.update_yaxes(title_text="Số lệnh", row=2, col=1)
        
        fig.update_layout(
            height=700,
            showlegend=False,
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_day_chart(self, df):
        """Biểu đồ theo ngày trong tuần"""
        fig = go.Figure()
        
        fig.add_trace(go.Bar(
            x=df['DayOfWeek'],
            y=df['Total_Trades'],
            name='Total Trades',
            marker_color='rgb(55, 83, 109)',
            text=df['Total_Trades'],
            textposition='outside',
            yaxis='y1'
        ))
        
        fig.add_trace(go.Scatter(
            x=df['DayOfWeek'],
            y=df['Win_Rate'],
            name='Win Rate',
            mode='lines+markers',
            line=dict(color='rgb(255, 127, 14)', width=3),
            marker=dict(size=10),
            yaxis='y2'
        ))
        
        fig.update_layout(
            yaxis=dict(title='Total Trades', side='left'),
            yaxis2=dict(title='Win Rate (%)', overlaying='y', side='right'),
            xaxis_title='Ngày trong tuần',
            height=500,
            hovermode='x unified',
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_month_chart(self, df):
        """Biểu đồ PnL theo tháng"""
        fig = go.Figure()
        
        # PnL bars với màu theo giá trị
        colors = ['rgb(46, 204, 113)' if val >= 0 else 'rgb(231, 76, 60)' for val in df['PnL']]
        
        fig.add_trace(go.Bar(
            x=df['Month'],
            y=df['PnL'],
            name='PnL',
            marker_color=colors,
            text=[f"${val:.0f}" for val in df['PnL']],
            textposition='outside',
            hovertemplate='<b>%{x}</b><br>PnL: $%{y:.2f}<extra></extra>'
        ))
        
        # Thêm đường zero
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        
        fig.update_layout(
            xaxis_title='Tháng',
            yaxis_title='PnL ($)',
            height=400,
            showlegend=False,
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_cumulative_pnl_chart(self, df):
        """Biểu đồ PnL tích lũy"""
        fig = go.Figure()
        
        # Cumulative PnL line
        fig.add_trace(go.Scatter(
            x=df['Entry_Time'],
            y=df['Cumulative_PnL'],
            mode='lines',
            name='Cumulative PnL',
            line=dict(color='rgb(26, 118, 255)', width=3),
            fill='tozeroy',
            fillcolor='rgba(26, 118, 255, 0.2)',
            hovertemplate='<b>%{x}</b><br>PnL: $%{y:.2f}<extra></extra>'
        ))
        
        # Add zero line
        fig.add_hline(y=0, line_dash="dash", line_color="red", opacity=0.5)
        
        fig.update_layout(
            xaxis_title='Thời gian',
            yaxis_title='Cumulative PnL ($)',
            height=500,
            hovermode='x unified',
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_win_loss_distribution(self):
        """Biểu đồ phân phối Win/Loss"""
        df = self.trades_df[self.trades_df['Result'] != 'Open']
        
        result_counts = df['Result'].value_counts()
        
        fig = go.Figure()
        
        fig.add_trace(go.Pie(
            labels=result_counts.index,
            values=result_counts.values,
            hole=0.4,
            marker_colors=['rgb(46, 204, 113)', 'rgb(231, 76, 60)'],
            textinfo='label+percent+value',
            textfont_size=14
        ))
        
        fig.update_layout(
            height=500,
            annotations=[dict(text='Results', x=0.5, y=0.5, font_size=20, showarrow=False)]
        )
        
        return fig
    
    def _create_trades_per_day_chart(self, df):
        """Biểu đồ số lệnh theo từng ngày"""
        fig = make_subplots(
            rows=2, cols=1,
            subplot_titles=('Số Lệnh Theo Từng Ngày', 'Win Rate Theo Từng Ngày'),
            specs=[[{'type': 'bar'}], [{'type': 'scatter'}]],
            vertical_spacing=0.15
        )
        
        # Trades per day bars
        fig.add_trace(
            go.Bar(
                x=df['Date'],
                y=df['Total_Trades'],
                name='Total Trades',
                marker_color='rgb(26, 118, 255)',
                text=df['Total_Trades'],
                textposition='outside',
                hovertemplate='<b>%{x}</b><br>Trades: %{y}<extra></extra>'
            ),
            row=1, col=1
        )
        
        # Win rate line
        fig.add_trace(
            go.Scatter(
                x=df['Date'],
                y=df['Win_Rate'],
                mode='lines+markers',
                name='Win Rate',
                line=dict(color='rgb(46, 204, 113)', width=2),
                marker=dict(size=6),
                hovertemplate='<b>%{x}</b><br>Win Rate: %{y:.1f}%<extra></extra>'
            ),
            row=2, col=1
        )
        
        fig.update_xaxes(title_text="Ngày", row=2, col=1)
        fig.update_yaxes(title_text="Số lệnh", row=1, col=1)
        fig.update_yaxes(title_text="Win Rate (%)", row=2, col=1)
        
        fig.update_layout(
            height=700,
            showlegend=False,
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_drawdown_chart(self, dd_analysis):
        """Biểu đồ Drawdown (giống MT5)"""
        df = dd_analysis['drawdown_df']
        avg_drawdown = dd_analysis['avg_drawdown']
        max_drawdown = dd_analysis['max_drawdown']
        
        fig = make_subplots(
            rows=2, cols=1,
            subplot_titles=('Equity Curve & Peak', 'Drawdown'),
            specs=[[{'type': 'scatter'}], [{'type': 'scatter'}]],
            vertical_spacing=0.12,
            row_heights=[0.6, 0.4]
        )
        
        # Equity curve
        fig.add_trace(
            go.Scatter(
                x=df['Entry_Time'],
                y=df['Cumulative_PnL'],
                mode='lines',
                name='Equity',
                line=dict(color='rgb(26, 118, 255)', width=2),
                fill='tozeroy',
                fillcolor='rgba(26, 118, 255, 0.1)',
                hovertemplate='<b>%{x}</b><br>Equity: $%{y:.2f}<extra></extra>'
            ),
            row=1, col=1
        )
        
        # Peak line
        fig.add_trace(
            go.Scatter(
                x=df['Entry_Time'],
                y=df['Peak'],
                mode='lines',
                name='Peak',
                line=dict(color='rgb(46, 204, 113)', width=1, dash='dot'),
                hovertemplate='<b>%{x}</b><br>Peak: $%{y:.2f}<extra></extra>'
            ),
            row=1, col=1
        )
        
        # Drawdown area
        fig.add_trace(
            go.Scatter(
                x=df['Entry_Time'],
                y=df['Drawdown'],
                mode='lines',
                name='Drawdown',
                line=dict(color='rgb(231, 76, 60)', width=2),
                fill='tozeroy',
                fillcolor='rgba(231, 76, 60, 0.3)',
                hovertemplate='<b>%{x}</b><br>Drawdown: $%{y:.2f}<extra></extra>'
            ),
            row=2, col=1
        )
        
        # Average Drawdown line (đường ngang)
        fig.add_hline(
            y=avg_drawdown, 
            line_dash="dash", 
            line_color="orange", 
            line_width=2,
            annotation_text=f"Avg DD: ${avg_drawdown:.0f}",
            annotation_position="right",
            row=2, col=1
        )
        
        # Max Drawdown line (đường ngang)
        fig.add_hline(
            y=max_drawdown, 
            line_dash="dot", 
            line_color="darkred", 
            line_width=2,
            annotation_text=f"Max DD: ${max_drawdown:.0f}",
            annotation_position="right",
            row=2, col=1
        )
        
        fig.update_yaxes(title_text="Equity ($)", row=1, col=1)
        fig.update_yaxes(title_text="Drawdown ($)", row=2, col=1)
        fig.update_xaxes(title_text="Thời gian", row=2, col=1)
        
        fig.update_layout(
            height=600,
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_simulation_equity_chart(self, sim_data):
        """
        Biểu đồ Equity Curve từ Account Simulation
        Hiển thị balance theo thời gian với các điểm deposit
        """
        equity_df = sim_data['equity_df']
        deposit_events = sim_data['deposit_events']
        
        if len(equity_df) == 0:
            return None
        
        fig = make_subplots(
            rows=2, cols=1,
            subplot_titles=(
                f"Account Simulation (Initial: ${sim_data['initial_capital']:.0f}, Risk: 1%)",
                'Drawdown from Peak'
            ),
            specs=[[{'type': 'scatter'}], [{'type': 'scatter'}]],
            vertical_spacing=0.12,
            row_heights=[0.65, 0.35]
        )
        
        # Equity curve
        fig.add_trace(
            go.Scatter(
                x=equity_df['Entry_Time'],
                y=equity_df['Balance'],
                mode='lines',
                name='Balance',
                line=dict(color='rgb(26, 118, 255)', width=2),
                fill='tozeroy',
                fillcolor='rgba(26, 118, 255, 0.1)',
                hovertemplate='<b>%{x}</b><br>Balance: $%{y:.2f}<extra></extra>'
            ),
            row=1, col=1
        )
        
        # Peak line
        fig.add_trace(
            go.Scatter(
                x=equity_df['Entry_Time'],
                y=equity_df['Peak'],
                mode='lines',
                name='Peak',
                line=dict(color='rgb(46, 204, 113)', width=1, dash='dot'),
                hovertemplate='<b>%{x}</b><br>Peak: $%{y:.2f}<extra></extra>'
            ),
            row=1, col=1
        )
        
        # Deposit markers
        if deposit_events:
            deposit_times = [e['Time'] for e in deposit_events]
            deposit_balances = [sim_data['initial_capital'] for _ in deposit_events]  # Balance after deposit
            deposit_numbers = [e['Deposit_Number'] for e in deposit_events]
            
            fig.add_trace(
                go.Scatter(
                    x=deposit_times,
                    y=deposit_balances,
                    mode='markers+text',
                    name='Deposit',
                    marker=dict(color='rgb(255, 193, 7)', size=15, symbol='triangle-up'),
                    text=[f"Deposit #{n}" for n in deposit_numbers],
                    textposition='top center',
                    hovertemplate='<b>Deposit #%{text}</b><br>Time: %{x}<br>New Balance: $%{y:.0f}<extra></extra>'
                ),
                row=1, col=1
            )
        
        # Drawdown area
        fig.add_trace(
            go.Scatter(
                x=equity_df['Entry_Time'],
                y=equity_df['Drawdown'],
                mode='lines',
                name='Drawdown',
                line=dict(color='rgb(231, 76, 60)', width=2),
                fill='tozeroy',
                fillcolor='rgba(231, 76, 60, 0.3)',
                hovertemplate='<b>%{x}</b><br>Drawdown: $%{y:.2f}<extra></extra>'
            ),
            row=2, col=1
        )
        
        # Initial capital reference line
        fig.add_hline(
            y=sim_data['initial_capital'],
            line_dash="dash",
            line_color="gray",
            annotation_text=f"Initial: ${sim_data['initial_capital']:.0f}",
            annotation_position="right",
            row=1, col=1
        )
        
        # Max Drawdown line
        fig.add_hline(
            y=sim_data['max_drawdown'],
            line_dash="dot",
            line_color="darkred",
            line_width=2,
            annotation_text=f"Max DD: ${sim_data['max_drawdown']:.0f}",
            annotation_position="right",
            row=2, col=1
        )
        
        fig.update_yaxes(title_text="Balance ($)", row=1, col=1)
        fig.update_yaxes(title_text="Drawdown ($)", row=2, col=1)
        fig.update_xaxes(title_text="Time", row=2, col=1)
        
        fig.update_layout(
            height=650,
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_rr_distribution_chart(self, sim_data):
        """
        Biểu đồ phân phối RR Ratio
        """
        df = self.trades_df[self.trades_df['Result'] != 'Open']
        
        if len(df) == 0:
            return None
        
        fig = make_subplots(
            rows=1, cols=2,
            subplot_titles=(
                'RR Ratio Distribution',
                'RR Ratio: Wins vs Losses'
            ),
            specs=[[{'type': 'histogram'}, {'type': 'box'}]]
        )
        
        # Histogram of RR Ratio
        fig.add_trace(
            go.Histogram(
                x=df['RR_Ratio'],
                nbinsx=30,
                name='RR Distribution',
                marker_color='rgb(26, 118, 255)',
                opacity=0.7
            ),
            row=1, col=1
        )
        
        # Average RR line
        avg_rr = df['RR_Ratio'].mean()
        fig.add_vline(
            x=avg_rr,
            line_dash="dash",
            line_color="red",
            annotation_text=f"Avg: {avg_rr:.2f}",
            row=1, col=1
        )
        
        # Box plot comparing Wins vs Losses RR
        wins_df = df[df['Result'] == 'Win']
        losses_df = df[df['Result'] == 'Loss']
        
        fig.add_trace(
            go.Box(
                y=wins_df['RR_Ratio'],
                name='Wins',
                marker_color='rgb(46, 204, 113)',
                boxmean=True
            ),
            row=1, col=2
        )
        
        fig.add_trace(
            go.Box(
                y=losses_df['RR_Ratio'],
                name='Losses',
                marker_color='rgb(231, 76, 60)',
                boxmean=True
            ),
            row=1, col=2
        )
        
        fig.update_xaxes(title_text="RR Ratio", row=1, col=1)
        fig.update_yaxes(title_text="Count", row=1, col=1)
        fig.update_yaxes(title_text="RR Ratio", row=1, col=2)
        
        fig.update_layout(
            height=400,
            showlegend=False,
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_session_chart(self, session_df):
        """Biểu đồ theo phiên giao dịch"""
        fig = make_subplots(
            rows=1, cols=2,
            subplot_titles=('Win Rate theo Session', 'PnL theo Session'),
            specs=[[{'type': 'bar'}, {'type': 'bar'}]]
        )
        
        # Màu cho các session
        session_colors = {
            'Asian': 'rgb(255, 193, 7)',
            'London': 'rgb(0, 123, 255)',
            'New York': 'rgb(40, 167, 69)',
            'Off-Session': 'rgb(108, 117, 125)'
        }
        colors = [session_colors.get(s, 'gray') for s in session_df['Session']]
        
        # Win Rate
        fig.add_trace(
            go.Bar(
                x=session_df['Session'],
                y=session_df['Win_Rate'],
                name='Win Rate',
                marker_color=colors,
                text=[f"{v:.1f}%" for v in session_df['Win_Rate']],
                textposition='outside'
            ),
            row=1, col=1
        )
        
        # PnL
        pnl_colors = ['rgb(46, 204, 113)' if v >= 0 else 'rgb(231, 76, 60)' for v in session_df['PnL']]
        fig.add_trace(
            go.Bar(
                x=session_df['Session'],
                y=session_df['PnL'],
                name='PnL',
                marker_color=pnl_colors,
                text=[f"${v:.0f}" for v in session_df['PnL']],
                textposition='outside'
            ),
            row=1, col=2
        )
        
        fig.update_yaxes(title_text="Win Rate (%)", row=1, col=1)
        fig.update_yaxes(title_text="PnL ($)", row=1, col=2)
        
        fig.update_layout(
            height=400,
            showlegend=False,
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_consecutive_chart(self, consecutive_data):
        """Biểu đồ phân tích sau chuỗi thắng/thua"""
        after_win = consecutive_data['after_win_streak']
        after_loss = consecutive_data['after_loss_streak']
        
        fig = make_subplots(
            rows=1, cols=2,
            subplot_titles=('Win Rate SAU Chuỗi Thắng', 'Win Rate SAU Chuỗi Thua'),
            specs=[[{'type': 'bar'}, {'type': 'bar'}]]
        )
        
        # After win streak
        if after_win:
            streaks = list(after_win.keys())
            win_rates = [after_win[s]['win_rate'] for s in streaks]
            totals = [after_win[s]['total'] for s in streaks]
            
            fig.add_trace(
                go.Bar(
                    x=[f"{s}+ wins" for s in streaks],
                    y=win_rates,
                    name='After Win Streak',
                    marker_color='rgb(46, 204, 113)',
                    text=[f"{wr:.1f}%<br>(n={t})" for wr, t in zip(win_rates, totals)],
                    textposition='outside'
                ),
                row=1, col=1
            )
        
        # After loss streak
        if after_loss:
            streaks = list(after_loss.keys())
            win_rates = [after_loss[s]['win_rate'] for s in streaks]
            totals = [after_loss[s]['total'] for s in streaks]
            
            fig.add_trace(
                go.Bar(
                    x=[f"{s}+ losses" for s in streaks],
                    y=win_rates,
                    name='After Loss Streak',
                    marker_color='rgb(231, 76, 60)',
                    text=[f"{wr:.1f}%<br>(n={t})" for wr, t in zip(win_rates, totals)],
                    textposition='outside'
                ),
                row=1, col=2
            )
        
        # Thêm đường 50% reference
        fig.add_hline(y=50, line_dash="dash", line_color="gray", opacity=0.5, row=1, col=1)
        fig.add_hline(y=50, line_dash="dash", line_color="gray", opacity=0.5, row=1, col=2)
        
        fig.update_yaxes(title_text="Win Rate (%)", range=[0, 100], row=1, col=1)
        fig.update_yaxes(title_text="Win Rate (%)", range=[0, 100], row=1, col=2)
        
        fig.update_layout(
            height=400,
            showlegend=False,
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_hold_time_chart(self, hold_df):
        """Biểu đồ phân tích theo thời gian giữ lệnh"""
        fig = make_subplots(
            rows=1, cols=2,
            subplot_titles=('Win Rate theo Thời Gian Giữ Lệnh', 'PnL theo Thời Gian Giữ Lệnh'),
            specs=[[{'type': 'bar'}, {'type': 'bar'}]]
        )
        
        # Win Rate
        fig.add_trace(
            go.Bar(
                x=hold_df['Duration_Category'],
                y=hold_df['Win_Rate'],
                name='Win Rate',
                marker_color='rgb(55, 83, 109)',
                text=[f"{v:.1f}%<br>(n={t})" for v, t in zip(hold_df['Win_Rate'], hold_df['Total_Trades'])],
                textposition='outside'
            ),
            row=1, col=1
        )
        
        # PnL
        pnl_colors = ['rgb(46, 204, 113)' if v >= 0 else 'rgb(231, 76, 60)' for v in hold_df['PnL']]
        fig.add_trace(
            go.Bar(
                x=hold_df['Duration_Category'],
                y=hold_df['PnL'],
                name='PnL',
                marker_color=pnl_colors,
                text=[f"${v:.0f}" for v in hold_df['PnL']],
                textposition='outside'
            ),
            row=1, col=2
        )
        
        fig.update_yaxes(title_text="Win Rate (%)", row=1, col=1)
        fig.update_yaxes(title_text="PnL ($)", row=1, col=2)
        fig.update_xaxes(tickangle=45)
        
        fig.update_layout(
            height=450,
            showlegend=False,
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_sl_percent_chart(self, sl_analysis):
        """
        Biểu đồ phân tích Win Rate theo khoảng SL%
        Bao gồm 2 biểu đồ: Equal Range và Percentile
        """
        if sl_analysis is None:
            return None
        
        equal_range_df = sl_analysis['equal_range_stats']
        percentile_df = sl_analysis['percentile_stats']
        
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                'Win Rate theo SL% (Chia Đều Khoảng)',
                'Số Lệnh theo SL% (Chia Đều Khoảng)',
                'Win Rate theo SL% (Chia Theo Percentile)',
                'Số Lệnh theo SL% (Chia Theo Percentile)'
            ),
            specs=[[{'type': 'bar'}, {'type': 'bar'}],
                   [{'type': 'bar'}, {'type': 'bar'}]],
            vertical_spacing=0.15,
            horizontal_spacing=0.1
        )
        
        # === Row 1: Equal Range ===
        # Win Rate - Equal Range
        colors_wr = ['rgb(46, 204, 113)' if wr >= 50 else 'rgb(231, 76, 60)' 
                     for wr in equal_range_df['Win_Rate']]
        fig.add_trace(
            go.Bar(
                x=equal_range_df['Range'],
                y=equal_range_df['Win_Rate'],
                name='Win Rate',
                marker_color=colors_wr,
                text=[f"{v:.1f}%" for v in equal_range_df['Win_Rate']],
                textposition='outside'
            ),
            row=1, col=1
        )
        
        # Trades Count - Equal Range (Stacked: Wins vs Losses)
        fig.add_trace(
            go.Bar(
                x=equal_range_df['Range'],
                y=equal_range_df['Wins'],
                name='Wins',
                marker_color='rgb(46, 204, 113)',
                text=[f"{v}" for v in equal_range_df['Wins']],
                textposition='inside'
            ),
            row=1, col=2
        )
        fig.add_trace(
            go.Bar(
                x=equal_range_df['Range'],
                y=equal_range_df['Losses'],
                name='Losses',
                marker_color='rgb(231, 76, 60)',
                text=[f"{v}" for v in equal_range_df['Losses']],
                textposition='inside'
            ),
            row=1, col=2
        )
        
        # === Row 2: Percentile ===
        # Win Rate - Percentile
        colors_wr_p = ['rgb(46, 204, 113)' if wr >= 50 else 'rgb(231, 76, 60)' 
                       for wr in percentile_df['Win_Rate']]
        fig.add_trace(
            go.Bar(
                x=percentile_df['Percentile'],
                y=percentile_df['Win_Rate'],
                name='Win Rate',
                marker_color=colors_wr_p,
                text=[f"{v:.1f}%<br>({r})" for v, r in zip(percentile_df['Win_Rate'], percentile_df['Range'])],
                textposition='outside',
                showlegend=False
            ),
            row=2, col=1
        )
        
        # Trades Count - Percentile (Stacked)
        fig.add_trace(
            go.Bar(
                x=percentile_df['Percentile'],
                y=percentile_df['Wins'],
                name='Wins',
                marker_color='rgb(46, 204, 113)',
                text=[f"{v}" for v in percentile_df['Wins']],
                textposition='inside',
                showlegend=False
            ),
            row=2, col=2
        )
        fig.add_trace(
            go.Bar(
                x=percentile_df['Percentile'],
                y=percentile_df['Losses'],
                name='Losses',
                marker_color='rgb(231, 76, 60)',
                text=[f"{v}" for v in percentile_df['Losses']],
                textposition='inside',
                showlegend=False
            ),
            row=2, col=2
        )
        
        # Update layout
        fig.update_layout(
            height=700,
            barmode='stack',
            showlegend=True,
            legend=dict(
                orientation='h',
                yanchor='bottom',
                y=1.02,
                xanchor='center',
                x=0.5
            ),
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        # Add 50% reference line for Win Rate charts
        fig.add_hline(y=50, line_dash="dash", line_color="gray", 
                      annotation_text="50%", row=1, col=1)
        fig.add_hline(y=50, line_dash="dash", line_color="gray", 
                      annotation_text="50%", row=2, col=1)
        
        fig.update_yaxes(title_text="Win Rate (%)", row=1, col=1)
        fig.update_yaxes(title_text="Số Lệnh", row=1, col=2)
        fig.update_yaxes(title_text="Win Rate (%)", row=2, col=1)
        fig.update_yaxes(title_text="Số Lệnh", row=2, col=2)
        
        fig.update_xaxes(tickangle=30)
        
        return fig
    
    def _create_random_sample_chart(self, random_data):
        """Biểu đồ phân tích Random Sampling"""
        if random_data is None:
            return None
        
        summary = random_data['summary']
        iterations = random_data['iterations']
        
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                'Win Rate: Random Samples vs Overall',
                'Max Drawdown: Random Samples vs Overall',
                'Profit Factor: Random Samples vs Overall',
                'Win/Loss Streak: Random Samples vs Overall'
            ),
            vertical_spacing=0.15,
            horizontal_spacing=0.1
        )
        
        # === Win Rate Chart ===
        wr_data = summary['win_rate']
        fig.add_trace(
            go.Bar(
                x=['Min', 'Avg', 'Max', 'Overall'],
                y=[wr_data['min'], wr_data['avg'], wr_data['max'], wr_data['overall']],
                marker_color=['rgb(255, 193, 7)', 'rgb(26, 118, 255)', 'rgb(46, 204, 113)', 'rgb(108, 117, 125)'],
                text=[f"{v:.1f}%" for v in [wr_data['min'], wr_data['avg'], wr_data['max'], wr_data['overall']]],
                textposition='outside',
                name='Win Rate'
            ),
            row=1, col=1
        )
        
        # === Max Drawdown Chart ===
        dd_data = summary['max_drawdown']
        fig.add_trace(
            go.Bar(
                x=['Min (Best)', 'Avg', 'Max (Worst)', 'Overall'],
                y=[abs(dd_data['min']), abs(dd_data['avg']), abs(dd_data['max']), abs(dd_data['overall'])],
                marker_color=['rgb(46, 204, 113)', 'rgb(255, 193, 7)', 'rgb(231, 76, 60)', 'rgb(108, 117, 125)'],
                text=[f"${abs(v):.0f}" for v in [dd_data['min'], dd_data['avg'], dd_data['max'], dd_data['overall']]],
                textposition='outside',
                name='Max Drawdown'
            ),
            row=1, col=2
        )
        
        # === Profit Factor Chart ===
        pf_data = summary['profit_factor']
        pf_overall = pf_data['overall'] if pf_data['overall'] != float('inf') else 10
        fig.add_trace(
            go.Bar(
                x=['Min', 'Avg', 'Max', 'Overall'],
                y=[pf_data['min'], pf_data['avg'], min(pf_data['max'], 10), min(pf_overall, 10)],
                marker_color=['rgb(231, 76, 60)', 'rgb(26, 118, 255)', 'rgb(46, 204, 113)', 'rgb(108, 117, 125)'],
                text=[f"{v:.2f}" for v in [pf_data['min'], pf_data['avg'], min(pf_data['max'], 10), min(pf_overall, 10)]],
                textposition='outside',
                name='Profit Factor'
            ),
            row=2, col=1
        )
        
        # === Win/Loss Streak Chart ===
        ws_data = summary['max_win_streak']
        ls_data = summary['max_loss_streak']
        
        fig.add_trace(
            go.Bar(
                x=['Win Streak (Avg)', 'Win Streak (Max)', 'Loss Streak (Avg)', 'Loss Streak (Max)'],
                y=[ws_data['avg'], ws_data['max'], ls_data['avg'], ls_data['max']],
                marker_color=['rgb(144, 238, 144)', 'rgb(46, 204, 113)', 'rgb(255, 182, 193)', 'rgb(231, 76, 60)'],
                text=[f"{v:.1f}" for v in [ws_data['avg'], ws_data['max'], ls_data['avg'], ls_data['max']]],
                textposition='outside',
                name='Streaks'
            ),
            row=2, col=2
        )
        
        # Add reference lines for overall values
        fig.add_hline(y=ws_data['overall'], line_dash="dash", line_color="green", 
                     annotation_text=f"Overall: {ws_data['overall']}", row=2, col=2)
        
        fig.update_yaxes(title_text="Win Rate (%)", row=1, col=1)
        fig.update_yaxes(title_text="Drawdown ($)", row=1, col=2)
        fig.update_yaxes(title_text="Profit Factor", row=2, col=1)
        fig.update_yaxes(title_text="Streak Length", row=2, col=2)
        
        fig.update_layout(
            height=600,
            showlegend=False,
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_random_sample_distribution_chart(self, random_data):
        """Biểu đồ phân phối của các lần random sampling"""
        if random_data is None:
            return None
        
        iterations = random_data['iterations']
        
        # Extract data for box plots
        win_rates = [it['win_rate'] for it in iterations]
        drawdowns = [abs(it['max_drawdown']) for it in iterations]
        profit_factors = [it['profit_factor'] for it in iterations if it['profit_factor'] != float('inf')]
        
        fig = make_subplots(
            rows=1, cols=3,
            subplot_titles=(
                f'Win Rate Distribution (n={len(iterations)})',
                f'Max Drawdown Distribution (n={len(iterations)})',
                f'Profit Factor Distribution (n={len(profit_factors)})'
            )
        )
        
        # Win Rate Box Plot
        fig.add_trace(
            go.Box(
                y=win_rates,
                name='Win Rate',
                marker_color='rgb(26, 118, 255)',
                boxmean=True
            ),
            row=1, col=1
        )
        
        # Max Drawdown Box Plot
        fig.add_trace(
            go.Box(
                y=drawdowns,
                name='Max Drawdown',
                marker_color='rgb(231, 76, 60)',
                boxmean=True
            ),
            row=1, col=2
        )
        
        # Profit Factor Box Plot
        fig.add_trace(
            go.Box(
                y=profit_factors,
                name='Profit Factor',
                marker_color='rgb(46, 204, 113)',
                boxmean=True
            ),
            row=1, col=3
        )
        
        # Add overall reference lines
        overall = random_data['overall_stats']
        fig.add_hline(y=overall['win_rate'], line_dash="dash", line_color="orange",
                     annotation_text=f"Overall: {overall['win_rate']:.1f}%", row=1, col=1)
        fig.add_hline(y=abs(overall['max_drawdown']), line_dash="dash", line_color="orange",
                     annotation_text=f"Overall: ${abs(overall['max_drawdown']):.0f}", row=1, col=2)
        if overall['profit_factor'] != float('inf'):
            fig.add_hline(y=overall['profit_factor'], line_dash="dash", line_color="orange",
                         annotation_text=f"Overall: {overall['profit_factor']:.2f}", row=1, col=3)
        
        fig.update_yaxes(title_text="Win Rate (%)", row=1, col=1)
        fig.update_yaxes(title_text="Drawdown ($)", row=1, col=2)
        fig.update_yaxes(title_text="Profit Factor", row=1, col=3)
        
        fig.update_layout(
            height=400,
            showlegend=False,
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_risk_metrics_chart(self, risk_metrics, trade_quality):
        """Biểu đồ các chỉ số rủi ro (style MT5)"""
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                'Risk Metrics', 
                'Profit vs Loss Distribution',
                'Trade Quality Indicators',
                'Win/Loss Size Comparison'
            ),
            specs=[
                [{'type': 'indicator'}, {'type': 'pie'}],
                [{'type': 'bar'}, {'type': 'bar'}]
            ],
            vertical_spacing=0.15,
            horizontal_spacing=0.1
        )
        
        # Risk Metrics Gauge
        pf = risk_metrics['profit_factor']
        pf_display = min(pf, 10) if pf != float('inf') else 10
        
        fig.add_trace(
            go.Indicator(
                mode="gauge+number",
                value=pf_display,
                title={'text': "Profit Factor"},
                gauge={
                    'axis': {'range': [0, 5]},
                    'bar': {'color': "darkblue"},
                    'steps': [
                        {'range': [0, 1], 'color': "red"},
                        {'range': [1, 2], 'color': "yellow"},
                        {'range': [2, 5], 'color': "green"}
                    ],
                    'threshold': {
                        'line': {'color': "black", 'width': 4},
                        'thickness': 0.75,
                        'value': pf_display
                    }
                }
            ),
            row=1, col=1
        )
        
        # Profit vs Loss Pie
        fig.add_trace(
            go.Pie(
                labels=['Gross Profit', 'Gross Loss'],
                values=[risk_metrics['total_profit'], risk_metrics['total_loss']],
                hole=0.4,
                marker_colors=['rgb(46, 204, 113)', 'rgb(231, 76, 60)'],
                textinfo='label+percent',
                textfont_size=12
            ),
            row=1, col=2
        )
        
        # Trade Quality Bar
        quality_metrics = ['Expectancy', 'Avg Win', 'Avg Loss', 'Payoff Ratio']
        quality_values = [
            risk_metrics['expectancy'],
            risk_metrics['avg_win'],
            risk_metrics['avg_loss'],
            min(risk_metrics['payoff_ratio'], 10) if risk_metrics['payoff_ratio'] != float('inf') else 10
        ]
        colors = ['rgb(26, 118, 255)', 'rgb(46, 204, 113)', 'rgb(231, 76, 60)', 'rgb(255, 193, 7)']
        
        fig.add_trace(
            go.Bar(
                x=quality_metrics,
                y=quality_values,
                marker_color=colors,
                text=[f"${v:.2f}" if i < 3 else f"{v:.2f}" for i, v in enumerate(quality_values)],
                textposition='outside'
            ),
            row=2, col=1
        )
        
        # Win/Loss Size Comparison
        fig.add_trace(
            go.Bar(
                x=['Max Win', 'Median Win', 'Median Loss', 'Max Loss'],
                y=[
                    trade_quality['max_win'],
                    trade_quality['median_win'],
                    trade_quality['median_loss'],
                    trade_quality['max_loss']
                ],
                marker_color=['rgb(46, 204, 113)', 'rgb(144, 238, 144)', 
                             'rgb(255, 182, 193)', 'rgb(231, 76, 60)'],
                text=[
                    f"${trade_quality['max_win']:.0f}",
                    f"${trade_quality['median_win']:.0f}",
                    f"${trade_quality['median_loss']:.0f}",
                    f"${trade_quality['max_loss']:.0f}"
                ],
                textposition='outside'
            ),
            row=2, col=2
        )
        
        fig.update_layout(
            height=700,
            showlegend=False,
            plot_bgcolor='rgba(240,240,240,0.5)'
        )
        
        return fig
    
    def _create_detailed_tables(self, symbol_stats, hour_stats, day_stats, month_stats, 
                                   session_stats=None, hold_time_stats=None):
        """Tạo bảng chi tiết"""
        html = '<div style="padding: 30px;">'
        html += '<h2>📋 Chi Tiết Thống Kê</h2>'
        
        # Symbol table
        html += '<h3 style="margin-top: 30px; color: #374151;">Theo Symbol</h3>'
        html += symbol_stats.to_html(index=False, classes='table', border=0)
        
        # Session table (MỚI)
        if session_stats is not None and len(session_stats) > 0:
            html += '<h3 style="margin-top: 30px; color: #374151;">Theo Phiên Giao Dịch</h3>'
            html += session_stats.to_html(index=False, classes='table', border=0)
        
        # Hold time table (MỚI)
        if hold_time_stats is not None and len(hold_time_stats) > 0:
            html += '<h3 style="margin-top: 30px; color: #374151;">Theo Thời Gian Giữ Lệnh</h3>'
            html += hold_time_stats.to_html(index=False, classes='table', border=0)
        
        # Hour table
        html += '<h3 style="margin-top: 30px; color: #374151;">Theo Giờ</h3>'
        html += hour_stats.to_html(index=False, classes='table', border=0)
        
        # Day table
        html += '<h3 style="margin-top: 30px; color: #374151;">Theo Ngày Trong Tuần</h3>'
        html += day_stats.to_html(index=False, classes='table', border=0)
        
        # Month table
        html += '<h3 style="margin-top: 30px; color: #374151;">Theo Tháng</h3>'
        html += month_stats.to_html(index=False, classes='table', border=0)
        
        html += '</div>'
        
        html += '<div class="footer">'
        html += '<p>📊 Bot_Trade - Smart Money Concepts Analysis</p>'
        html += '<p>© 2025 - All rights reserved</p>'
        html += '</div>'
        
        return html


def main():
    """Main function"""
    print("\n" + "="*60)
    print("   📊 TRADE ANALYSIS TOOL")
    print("   Smart Money Concepts Trading Strategy")
    print("="*60)
    
    # Khởi tạo analyzer
    analyzer = TradeAnalyzer(output_folder="res/output")

    # Load dữ liệu
    if not analyzer.load_trades():
        print("❌ Không thể load dữ liệu!")
        return
    
    # In thống kê cơ bản ra console
    print("\n📈 THỐNG KÊ TỔNG QUAN:")
    print("-" * 60)
    stats = analyzer.get_basic_stats()
    print(f"  Tổng số lệnh:           {stats['total_trades']}")
    print(f"  Số lệnh thắng:          {stats['wins']}")
    print(f"  Số lệnh thua:           {stats['losses']}")
    print(f"  Win Rate:               {stats['win_rate']:.2f}%")
    print(f"  Total PnL:              ${stats['total_pnl']:.2f}")
    print(f"  Chuỗi thắng dài nhất:   {stats['max_win_streak']}")
    print(f"  Chuỗi thua dài nhất:    {stats['max_loss_streak']}")
    print(f"  Chuỗi thắng TB:         {stats['avg_win_streak']:.2f}")
    print(f"  Chuỗi thua TB:          {stats['avg_loss_streak']:.2f}")
    print(f"  Thời gian TB:           {stats['avg_duration_minutes']:.0f} phút")
    print(f"  LONG Trades:            {stats['long_trades']} (WR: {stats['long_win_rate']:.1f}%)")
    print(f"  SHORT Trades:           {stats['short_trades']} (WR: {stats['short_win_rate']:.1f}%)")
    
    print(f"\n📊 RR RATIO:")
    print("-" * 60)
    print(f"  Avg RR:                 1:{stats['avg_rr']:.2f}")
    print(f"  Min RR:                 1:{stats['min_rr']:.2f}")
    print(f"  Max RR:                 1:{stats['max_rr']:.2f}")
    print(f"  Median RR:              1:{stats['median_rr']:.2f}")
    
    print(f"\n📊 THỐNG KÊ THEO NGÀY:")
    print("-" * 60)
    print(f"  Tổng số ngày giao dịch: {stats['total_trading_days']}")
    print(f"  Số lệnh/ngày TB:        {stats['avg_trades_per_day']:.2f}")
    print(f"  Max lệnh trong 1 ngày:  {stats['max_trades_per_day']}")
    
    # Account Simulation
    print(f"\n💰 ACCOUNT SIMULATION (Vốn $1,000 - Risk 1%):")
    print("-" * 60)
    sim_data = analyzer.get_account_simulation_stats()
    print(f"  Initial Capital:        ${sim_data['initial_capital']:.0f}")
    print(f"  Final Balance:          ${sim_data['final_balance']:.2f}")
    print(f"  Total Deposited:        ${sim_data['total_deposited']:.0f}")
    print(f"  Margin Calls (Resets):  {sim_data['deposit_count']}")
    print(f"  ROE:                    {sim_data['roe']:.2f}%")
    print(f"  ROI:                    {sim_data['roi']:.2f}%")
    print(f"  Max Drawdown:           ${sim_data['max_drawdown']:.2f} ({sim_data['max_drawdown_pct']:.1f}%)")
    
    # Tạo báo cáo HTML
    output_file = analyzer.generate_html_report('res/report/trade_analysis_report.html')
    
    print(f"\n✅ Hoàn tất! Mở file để xem báo cáo:")
    print(f"   {output_file}")
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
