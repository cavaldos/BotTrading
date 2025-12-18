import pandas as pd
import numpy as np
from datetime import datetime

# ============ CONFIG ============
DATA_PATH = "/Users/bourbon/Code/Workspace/Bot_Trade/src/data/BTCUSDT_15m.csv"
TRADES_OUTPUT = "/Users/bourbon/Code/Workspace/Bot_Trade/src/res/trades.csv"
RESULT_OUTPUT = "/Users/bourbon/Code/Workspace/Bot_Trade/src/res/result.md"
SWING_LOOKBACK = 5  # Số nến để xác định swing high/low


def find_swing_points(df, lookback=5):
    """Tìm các điểm swing high và swing low"""
    swing_highs = []
    swing_lows = []
    
    for i in range(lookback, len(df) - lookback):
        # Swing High: High của nến hiện tại cao hơn tất cả các nến xung quanh
        is_swing_high = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and df['High'].iloc[j] >= df['High'].iloc[i]:
                is_swing_high = False
                break
        if is_swing_high:
            swing_highs.append({
                'index': i,
                'time': df['Time'].iloc[i],
                'price': df['High'].iloc[i]
            })
        
        # Swing Low: Low của nến hiện tại thấp hơn tất cả các nến xung quanh
        is_swing_low = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and df['Low'].iloc[j] <= df['Low'].iloc[i]:
                is_swing_low = False
                break
        if is_swing_low:
            swing_lows.append({
                'index': i,
                'time': df['Time'].iloc[i],
                'price': df['Low'].iloc[i]
            })
    
    return swing_highs, swing_lows


def get_previous_swing_low(swing_lows, current_index):
    """Lấy swing low gần nhất trước vị trí hiện tại"""
    for sw in reversed(swing_lows):
        if sw['index'] < current_index:
            return sw
    return None


def backtest_breakout_strategy(df, swing_highs, swing_lows):
    """
    Backtest chiến lược breakout:
    - Khi giá phá đỉnh (swing high) -> Vào lệnh BUY
    - SL đặt ở swing low trước đó
    - TP = Entry + 2 * (Entry - SL) (tỉ lệ 2R)
    """
    trades = []
    used_swing_highs = set()  # Để tránh vào lệnh nhiều lần cho cùng 1 swing high
    
    for i in range(len(df)):
        current_high = df['High'].iloc[i]
        current_time = df['Time'].iloc[i]
        
        # Kiểm tra xem có phá đỉnh nào không
        for sh in swing_highs:
            if sh['index'] >= i:  # Chỉ xét các swing high đã hình thành trước đó
                continue
            if sh['index'] in used_swing_highs:
                continue
            
            # Giá phá đỉnh
            if current_high > sh['price']:
                # Tìm swing low trước đó làm SL
                prev_swing_low = get_previous_swing_low(swing_lows, sh['index'])
                if prev_swing_low is None:
                    continue
                
                entry_price = sh['price']  # Entry tại điểm phá đỉnh
                sl_price = prev_swing_low['price']
                risk = entry_price - sl_price
                
                if risk <= 0:
                    continue
                
                tp_price = entry_price + 2 * risk  # Tỉ lệ 2R
                
                # Đánh dấu swing high đã được sử dụng
                used_swing_highs.add(sh['index'])
                
                # Kiểm tra kết quả lệnh trong các nến tiếp theo
                trade_result = check_trade_result(df, i, entry_price, sl_price, tp_price)
                
                trades.append({
                    'entry_time': current_time,
                    'entry_price': entry_price,
                    'sl_price': sl_price,
                    'tp_price': tp_price,
                    'risk': risk,
                    'reward': 2 * risk,
                    'result': trade_result['result'],
                    'exit_time': trade_result['exit_time'],
                    'exit_price': trade_result['exit_price'],
                    'pnl_percent': trade_result['pnl_percent']
                })
    
    return trades


def check_trade_result(df, entry_index, entry_price, sl_price, tp_price):
    """Kiểm tra kết quả của lệnh dựa trên dữ liệu sau đó"""
    for j in range(entry_index + 1, len(df)):
        candle_low = df['Low'].iloc[j]
        candle_high = df['High'].iloc[j]
        
        # Kiểm tra SL trước (giả định worst case)
        if candle_low <= sl_price:
            return {
                'result': 'LOSS',
                'exit_time': df['Time'].iloc[j],
                'exit_price': sl_price,
                'pnl_percent': -((entry_price - sl_price) / entry_price) * 100
            }
        
        # Kiểm tra TP
        if candle_high >= tp_price:
            return {
                'result': 'WIN',
                'exit_time': df['Time'].iloc[j],
                'exit_price': tp_price,
                'pnl_percent': ((tp_price - entry_price) / entry_price) * 100
            }
    
    # Nếu chưa kết thúc, đánh dấu là OPEN
    return {
        'result': 'OPEN',
        'exit_time': None,
        'exit_price': None,
        'pnl_percent': 0
    }


def save_trades_to_csv(trades, output_path):
    """Lưu danh sách lệnh vào file CSV (làm tròn kết quả số)"""
    df_trades = pd.DataFrame(trades)

    # Làm tròn các cột số cho file CSV: prices/risk/reward -> 2 chữ số thập phân, pnl_percent -> 4 chữ số
    for col in ['entry_price', 'sl_price', 'tp_price', 'risk', 'reward', 'exit_price']:
        if col in df_trades.columns:
            df_trades[col] = pd.to_numeric(df_trades[col], errors='coerce').round(2)
    if 'pnl_percent' in df_trades.columns:
        df_trades['pnl_percent'] = pd.to_numeric(df_trades['pnl_percent'], errors='coerce').round(4)

    df_trades.to_csv(output_path, index=False)
    print(f"Đã lưu {len(trades)} lệnh vào {output_path}")


def generate_result_report(trades, output_path):
    """Tạo báo cáo kết quả và lưu vào file Markdown"""
    total_trades = len(trades)
    wins = sum(1 for t in trades if t['result'] == 'WIN')
    losses = sum(1 for t in trades if t['result'] == 'LOSS')
    opens = sum(1 for t in trades if t['result'] == 'OPEN')
    
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    
    total_pnl = sum(t['pnl_percent'] for t in trades if t['result'] != 'OPEN')
    avg_win = np.mean([t['pnl_percent'] for t in trades if t['result'] == 'WIN']) if wins > 0 else 0
    avg_loss = np.mean([t['pnl_percent'] for t in trades if t['result'] == 'LOSS']) if losses > 0 else 0
    
    report = f"""# Báo Cáo Kết Quả Backtest

## Chiến Lược: Price Action Breakout (2R)

### Thống Kê Tổng Quan

| Chỉ số | Giá trị |
|--------|---------|
| Tổng số lệnh | {total_trades} |
| Số lệnh thắng | {wins} |
| Số lệnh thua | {losses} |
| Số lệnh chưa đóng | {opens} |
| Tỉ lệ thắng | {win_rate:.2f}% |

### Thống Kê Lợi Nhuận

| Chỉ số | Giá trị |
|--------|---------|
| Tổng PnL (%) | {total_pnl:.4f}% |
| Trung bình lệnh thắng (%) | {avg_win:.4f}% |
| Trung bình lệnh thua (%) | {avg_loss:.4f}% |

### Mô Tả Chiến Lược

- **Entry**: Khi giá phá vỡ swing high (đỉnh cục bộ)
- **Stop Loss**: Đặt tại swing low (đáy cục bộ) trước đó
- **Take Profit**: Entry + 2 * Risk (tỉ lệ Risk:Reward = 1:2)
- **Swing Lookback**: {SWING_LOOKBACK} nến

---
*Báo cáo được tạo tự động vào: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"Đã tạo báo cáo tại {output_path}")


def run_backtest():
    """Chạy backtest"""
    print("=" * 50)
    print("BACKTEST PRICE ACTION BREAKOUT STRATEGY")
    print("=" * 50)
    
    # Đọc dữ liệu
    df = pd.read_csv(DATA_PATH)
    df['Time'] = pd.to_datetime(df['Time'])
    print(f"Đã tải {len(df)} nến từ {df['Time'].iloc[0]} đến {df['Time'].iloc[-1]}")
    
    # Tìm swing points
    swing_highs, swing_lows = find_swing_points(df, SWING_LOOKBACK)
    print(f"Tìm thấy {len(swing_highs)} swing highs và {len(swing_lows)} swing lows")
    
    # Chạy backtest
    trades = backtest_breakout_strategy(df, swing_highs, swing_lows)
    print(f"Tổng số lệnh: {len(trades)}")
    
    # Lưu kết quả
    save_trades_to_csv(trades, TRADES_OUTPUT)
    generate_result_report(trades, RESULT_OUTPUT)
    
    # In tóm tắt
    wins = sum(1 for t in trades if t['result'] == 'WIN')
    losses = sum(1 for t in trades if t['result'] == 'LOSS')
    print(f"\n📊 KẾT QUẢ: {wins} WIN / {losses} LOSS")
    if wins + losses > 0:
        print(f"📈 TỈ LỆ THẮNG: {wins/(wins+losses)*100:.2f}%")


if __name__ == "__main__":
    run_backtest()
