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
    
    def __init__(self, output_folder: str = "output"):
        self.output_folder = Path(output_folder)
        self.trades_df = None
        self.all_trades = []
        
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
        
        # Tính toán thời gian giao dịch (phút)
        self.trades_df['Duration_Minutes'] = (
            self.trades_df['Exit_Time'] - self.trades_df['Entry_Time']
        ).dt.total_seconds() / 60
        
        # Tính profit/loss (đơn giản hóa, giả sử RR = 2)
        self.trades_df['PnL'] = self.trades_df.apply(self._calculate_pnl, axis=1)
        
        print(f"\n✓ Tổng cộng: {len(self.trades_df)} trades")
        print(f"{'='*60}\n")
        return True
    
    def _calculate_pnl(self, row):
        """Tính PnL đơn giản (giả sử mỗi lệnh risk $10, RR=2)"""
        if row['Result'] == 'Win':
            return 20.0  # Win = +20 (RR 1:2)
        elif row['Result'] == 'Loss':
            return -10.0  # Loss = -10
        else:
            return 0.0
    
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
        
        # Thống kê số lệnh theo ngày
        trades_per_day = df.groupby('Date').size()
        max_trades_per_day = int(trades_per_day.max()) if len(trades_per_day) > 0 else 0
        avg_trades_per_day = float(trades_per_day.mean()) if len(trades_per_day) > 0 else 0
        total_trading_days = len(trades_per_day)
        
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
            'max_trades_per_day': max_trades_per_day,
            'avg_trades_per_day': avg_trades_per_day,
            'total_trading_days': total_trading_days
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
    
    def generate_html_report(self, output_file='trade_analysis_report.html'):
        """Tạo báo cáo HTML với biểu đồ Plotly"""
        print("\n" + "="*60)
        print("Đang tạo báo cáo HTML...")
        print("="*60)
        
        # Lấy dữ liệu
        basic_stats = self.get_basic_stats()
        symbol_stats = self.get_stats_by_symbol()
        hour_stats = self.get_stats_by_hour()
        day_stats = self.get_stats_by_day_of_week()
        month_stats = self.get_stats_by_month()
        cumulative_df = self.get_cumulative_pnl()
        trades_per_day_df = self.get_trades_per_day_stats()
        
        # Tạo HTML
        html_content = self._create_html_template()
        
        # Thêm thống kê cơ bản
        html_content += self._create_basic_stats_section(basic_stats)
        
        # Tạo các biểu đồ
        fig_symbol = self._create_symbol_chart(symbol_stats)
        fig_hour = self._create_hour_chart(hour_stats)
        fig_day = self._create_day_chart(day_stats)
        fig_month = self._create_month_chart(month_stats)
        fig_cumulative = self._create_cumulative_pnl_chart(cumulative_df)
        fig_distribution = self._create_win_loss_distribution()
        fig_trades_per_day = self._create_trades_per_day_chart(trades_per_day_df)
        
        # Chèn biểu đồ vào HTML
        html_content += '<div class="chart-container">'
        html_content += '<h2>📊 Phân Tích Theo Symbol</h2>'
        html_content += fig_symbol.to_html(full_html=False, include_plotlyjs='cdn')
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
        html_content += '<h2>📆 Phân Tích Theo Tháng</h2>'
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
        html_content += self._create_detailed_tables(symbol_stats, hour_stats, day_stats, month_stats, trades_per_day_df)
        
        # Kết thúc HTML
        html_content += '</div></body></html>'
        
        # Lưu file
        output_path = Path(output_file)
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
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            color: #333;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 15px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}
        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }}
        .header p {{
            font-size: 1.1em;
            opacity: 0.9;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            padding: 30px;
            background: #f8f9fa;
        }}
        .stat-card {{
            background: white;
            padding: 25px;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }}
        .stat-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 8px 15px rgba(0,0,0,0.2);
        }}
        .stat-label {{
            font-size: 0.9em;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 10px;
        }}
        .stat-value {{
            font-size: 2em;
            font-weight: bold;
            color: #667eea;
        }}
        .stat-value.positive {{
            color: #10b981;
        }}
        .stat-value.negative {{
            color: #ef4444;
        }}
        .chart-container {{
            padding: 30px;
            border-bottom: 1px solid #e5e7eb;
        }}
        .chart-container:last-child {{
            border-bottom: none;
        }}
        .chart-container h2 {{
            margin-bottom: 20px;
            color: #374151;
            font-size: 1.8em;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            background: white;
        }}
        th {{
            background: #667eea;
            color: white;
            padding: 15px;
            text-align: left;
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.85em;
            letter-spacing: 0.5px;
        }}
        td {{
            padding: 12px 15px;
            border-bottom: 1px solid #e5e7eb;
        }}
        tr:hover {{
            background: #f9fafb;
        }}
        .footer {{
            text-align: center;
            padding: 30px;
            background: #f8f9fa;
            color: #666;
            font-size: 0.9em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📈 TRADE ANALYSIS REPORT</h1>
            <p>Smart Money Concepts Trading Strategy</p>
            <p style="font-size: 0.9em; margin-top: 10px;">Generated: {timestamp}</p>
        </div>
"""
        return html
    
    def _create_basic_stats_section(self, stats):
        """Tạo section thống kê cơ bản"""
        pnl_class = 'positive' if stats['total_pnl'] >= 0 else 'negative'
        
        html = f"""
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Tổng Số Lệnh</div>
                <div class="stat-value">{stats['total_trades']}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Win Rate</div>
                <div class="stat-value positive">{stats['win_rate']:.2f}%</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Số Lệnh Thắng</div>
                <div class="stat-value positive">{stats['wins']}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Số Lệnh Thua</div>
                <div class="stat-value negative">{stats['losses']}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total PnL</div>
                <div class="stat-value {pnl_class}">${stats['total_pnl']:.2f}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Chuỗi Thắng Dài Nhất</div>
                <div class="stat-value">{stats['max_win_streak']}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Chuỗi Thua Dài Nhất</div>
                <div class="stat-value">{stats['max_loss_streak']}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Chuỗi Thắng TB</div>
                <div class="stat-value">{stats['avg_win_streak']:.2f}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Chuỗi Thua TB</div>
                <div class="stat-value">{stats['avg_loss_streak']:.2f}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Thời Gian TB (phút)</div>
                <div class="stat-value">{stats['avg_duration_minutes']:.0f}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">LONG Trades</div>
                <div class="stat-value">{stats['long_trades']} ({stats['long_win_rate']:.1f}%)</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">SHORT Trades</div>
                <div class="stat-value">{stats['short_trades']} ({stats['short_win_rate']:.1f}%)</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Số Ngày Giao Dịch</div>
                <div class="stat-value">{stats['total_trading_days']}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Lệnh/Ngày TB</div>
                <div class="stat-value">{stats['avg_trades_per_day']:.2f}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Max Lệnh/Ngày</div>
                <div class="stat-value">{stats['max_trades_per_day']}</div>
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
        """Biểu đồ theo tháng"""
        fig = make_subplots(
            rows=2, cols=1,
            subplot_titles=('PnL by Month', 'Win Rate by Month'),
            specs=[[{'type': 'bar'}], [{'type': 'bar'}]],
            vertical_spacing=0.15
        )
        
        # PnL bars
        colors = ['green' if val >= 0 else 'red' for val in df['PnL']]
        fig.add_trace(
            go.Bar(
                x=df['Month'],
                y=df['PnL'],
                name='PnL',
                marker_color=colors,
                text=[f"${val:.0f}" for val in df['PnL']],
                textposition='outside'
            ),
            row=1, col=1
        )
        
        # Win Rate bars
        fig.add_trace(
            go.Bar(
                x=df['Month'],
                y=df['Win_Rate'],
                name='Win Rate',
                marker_color='rgb(26, 118, 255)',
                text=[f"{val:.1f}%" for val in df['Win_Rate']],
                textposition='outside'
            ),
            row=2, col=1
        )
        
        fig.update_xaxes(title_text="Tháng", row=2, col=1)
        fig.update_yaxes(title_text="PnL ($)", row=1, col=1)
        fig.update_yaxes(title_text="Win Rate (%)", row=2, col=1)
        
        fig.update_layout(
            height=700,
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
    
    def _create_detailed_tables(self, symbol_stats, hour_stats, day_stats, month_stats, trades_per_day_df=None):
        """Tạo bảng chi tiết"""
        html = '<div style="padding: 30px;">'
        html += '<h2>📋 Chi Tiết Thống Kê</h2>'
        
        # Symbol table
        html += '<h3 style="margin-top: 30px; color: #374151;">Theo Symbol</h3>'
        html += symbol_stats.to_html(index=False, classes='table', border=0)
        
        # Hour table
        html += '<h3 style="margin-top: 30px; color: #374151;">Theo Giờ</h3>'
        html += hour_stats.to_html(index=False, classes='table', border=0)
        
        # Day table
        html += '<h3 style="margin-top: 30px; color: #374151;">Theo Ngày Trong Tuần</h3>'
        html += day_stats.to_html(index=False, classes='table', border=0)
        
        # Month table
        html += '<h3 style="margin-top: 30px; color: #374151;">Theo Tháng</h3>'
        html += month_stats.to_html(index=False, classes='table', border=0)
        
        # Trades per day table (nếu có)
        if trades_per_day_df is not None and len(trades_per_day_df) > 0:
            html += '<h3 style="margin-top: 30px; color: #374151;">Số Lệnh Theo Từng Ngày (Top 20 ngày có nhiều lệnh nhất)</h3>'
            top_days = trades_per_day_df.nlargest(20, 'Total_Trades')
            html += top_days.to_html(index=False, classes='table', border=0)
        
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
    analyzer = TradeAnalyzer(output_folder="src/output")
    
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
    print(f"\n📊 THỐNG KÊ THEO NGÀY:")
    print("-" * 60)
    print(f"  Tổng số ngày giao dịch: {stats['total_trading_days']}")
    print(f"  Số lệnh/ngày TB:        {stats['avg_trades_per_day']:.2f}")
    print(f"  Max lệnh trong 1 ngày:  {stats['max_trades_per_day']}")
    
    # Tạo báo cáo HTML
    output_file = analyzer.generate_html_report('src/report/trade_analysis_report.html')
    
    print(f"\n✅ Hoàn tất! Mở file để xem báo cáo:")
    print(f"   {output_file}")
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
