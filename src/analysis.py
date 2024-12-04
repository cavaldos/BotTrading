import os
import pandas as pd
import numpy as np
import time
import threading
from util.notify import entry_point

# Đọc dữ liệu từ file CSV
def load_data(filename):
    df = pd.read_csv(filename, parse_dates=['Time'])
    df['Time'] = pd.to_datetime(df['Time'])
    return df

# Tìm mức hỗ trợ và kháng cự (đỉnh và đáy gần nhất trong cửa sổ thời gian)
def find_support_resistance(df, window=1):
    df['Resistance'] = df['High'].rolling(window=window).max()
    df['Support'] = df['Low'].rolling(window=window).min()
    return df

# Kiểm tra nến đảo chiều (Doji, Engulfing, Hammer)
def check_reversal_candles(df):
    df['Doji'] = np.where(abs(df['Close'] - df['Open']) <= 0.1 * (df['High'] - df['Low']), 1, 0)
    df['Engulfing'] = np.where(((df['Close'] > df['Open']) & (df['Close'].shift(1) < df['Open'].shift(1))) |
                                ((df['Close'] < df['Open']) & (df['Close'].shift(1) > df['Open'].shift(1))), 1, 0)
    df['Hammer'] = np.where((df['Close'] > df['Open']) & (df['High'] - df['Low'] > 3 * (df['Close'] - df['Open'])), 1, 0)
    return df

def check_breakout_and_retest(df, order_blocks, min_reversal_signal=1):
    df['Long_Signal'] = 0
    df['Short_Signal'] = 0
    
    for index, row in df.iterrows():
        # Kiểm tra nếu hỗ trợ hoặc kháng cự trùng với vùng order block
        if (row['Support'] in order_blocks or row['Resistance'] in order_blocks):
            # Kiểm tra nếu có nến đảo chiều tại điểm hỗ trợ/kháng cự
            if row['Doji'] >= min_reversal_signal or row['Engulfing'] >= min_reversal_signal or row['Hammer'] >= min_reversal_signal:
                if row['Close'] > df['Resistance'].shift(1).iloc[index]:  # Lệnh Long
                    df.at[index, 'Long_Signal'] = 1
                elif row['Close'] < df['Support'].shift(1).iloc[index]:  # Lệnh Short
                    df.at[index, 'Short_Signal'] = -1
    return df

def execute_trades(df, RR_ratio=2):
    trades = []
    
    trades = []
    for index, row in df.iterrows():
        if row['Long_Signal'] == 1:
            stoploss = row['Support']  # Stoploss dưới vùng hỗ trợ
            take_profit = row['Close'] + RR_ratio * (row['Close'] - stoploss)  # Tỷ lệ R/R là 2
            trades.append({
                'Time': row['Time'],
                'Type': 'Long',
                'Entry': row['Close'],
                'Stoploss': stoploss,
                'Take_Profit': take_profit,
                'Status': 'Ongoing'  # Mặc định trạng thái là "Ongoing"
            })
        elif row['Short_Signal'] == -1:
            stoploss = row['Resistance']  # Stoploss trên vùng kháng cự
            take_profit = row['Close'] - RR_ratio * (stoploss - row['Close'])  # Tỷ lệ R/R là 2
            trades.append({
                'Time': row['Time'],
                'Type': 'Short',
                'Entry': row['Close'],
                'Stoploss': stoploss,
                'Take_Profit': take_profit,
                'Status': 'Ongoing'  # Mặc định trạng thái là "Ongoing"
            })
    return trades


# Hàm phân tích dữ liệu và kiểm tra sự thay đổi
def analyze_data(file_path, last_trades):
    try:
        # Đọc dữ liệu
        df = pd.read_csv(file_path, parse_dates=['Time'])
        df['Time'] = pd.to_datetime(df['Time'])
        
        # Tìm hỗ trợ và kháng cự
        df = find_support_resistance(df)

        # Nhận diện nến đảo chiều
        df = check_reversal_candles(df)

        # Giả định có dữ liệu về "order blocks"
        order_blocks = df['Support'].values.tolist() + df['Resistance'].values.tolist()

        # Kiểm tra tín hiệu Long và Short
        df = check_breakout_and_retest(df, order_blocks)
        
        # Thực hiện giao dịch
        trades = execute_trades(df, RR_ratio=2)

        # Lọc các giao dịch mới chưa được in
        new_trades = [trade for trade in trades if trade not in last_trades]
        # Nếu có lệnh mới, in ra màn hình
        if new_trades:
            print("\n--- Các lệnh giao dịch mới ---")
            for trade in new_trades:
                print(f"Thời gian: {trade['Time']}")
                print(f"Loại: {trade['Type']}")
                print(f"Giá vào: {trade['Entry']}")
                print(f"Stoploss: {trade['Stoploss']}")
                print(f"Take Profit: {trade['Take_Profit']}")
                print("---")
                entry_point(trade['Type'],
                            trade['Entry'],
                            trade['Stoploss'],
                            trade['Take_Profit'],
                            str(trade['Time']))
                            
        # Cập nhật các lệnh đã in ra
        return trades
    
    except Exception as e:
        print(f"Lỗi khi phân tích dữ liệu: {e}")
        return last_trades  # Trả lại các lệnh trước đó nếu có lỗi

# Chạy phân tích dữ liệu liên tục và chỉ in khi có sự thay đổi
def continuous_analysis(file_path):
    last_trades = []  # Lưu các lệnh đã in ra
    while True:
        # print("\n--- Bắt đầu phân tích dữ liệu ---")
        last_trades = analyze_data(file_path, last_trades)
        print("\n waiting for 1 minute")
        time.sleep(60)




def analysis_func():
    # Đường dẫn file dữ liệu
    file_path = "src/data/NEARUSDT_1m.csv"  # Thay đổi đường dẫn phù hợp
    
    # Tạo và bắt đầu luồng phân tích liên tục
    analysis_thread = threading.Thread(target=continuous_analysis, args=(file_path,))
    analysis_thread.daemon = True  # Cho phép luồng kết thúc khi chương trình chính kết thúc
    analysis_thread.start()
    
    # Giữ cho chương trình chạy
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDừng chương trình...")

if __name__ == '__main__':
    analysis_func()