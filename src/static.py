
import os
import pandas as pd
import numpy as np
import shutil
import time
import schedule
from util.config_file import remove_all_files

def load_data(filename):
    df = pd.read_csv(filename, parse_dates=['Time'])
    df['Time'] = pd.to_datetime(df['Time'])
    return df

def find_support_resistance(df, window=24):
    df['Resistance'] = df['High'].rolling(window=window).max()
    df['Support'] = df['Low'].rolling(window=window).min()
    return df

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
        if (row['Support'] in order_blocks and row['Resistance'] in order_blocks):
            if  row['Doji'] >= min_reversal_signal or \
                row['Hammer'] >= min_reversal_signal:
                if row['Close'] > df['Resistance'].shift(1).iloc[index]:  # Lệnh Long
                    df.at[index, 'Long_Signal'] = 1
                elif row['Close'] < df['Support'].shift(1).iloc[index]:  # Lệnh Short
                    df.at[index, 'Short_Signal'] = -1
    return df


def execute_trades(df, RR_ratio=2):
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


def save_trades_to_file(trades, filename):
    trades_df = pd.DataFrame(trades)
    trades_df.to_csv(filename, index=False)
    


def evaluate_trades(df, trades):
    results = []
    
    for trade in trades:
        entry_price = trade['Entry']
        stoploss = trade['Stoploss']
        take_profit = trade['Take_Profit']
        entry_time = trade['Time']
        trade_type = trade['Type']
        
        future_data = df[df['Time'] >= entry_time]
        
        if trade_type == 'Long':
            # Kiểm tra theo thứ tự thời gian
            for _, future_row in future_data.iterrows():
                if future_row['Low'] <= stoploss:
                    status = 'loss'
                    break
                if future_row['High'] >= take_profit:
                    status = 'win'
                    break
            else:
                status = 'OnGoing'
        
        elif trade_type == 'Short':
            # Tương tự cho lệnh Short
            for _, future_row in future_data.iterrows():
                if future_row['High'] >= stoploss:
                    status = 'loss'
                    break
                if future_row['Low'] <= take_profit:
                    status = 'win'
                    break
            else:
                status = 'OnGoing'
                
        trade['Status'] = status
        results.append(trade)
    
    return results

def process_and_update_files(data_folder, result_folder, RR_ratio):
    print(f"Bắt đầu xử lý dữ liệu tại: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        for file_name in os.listdir(data_folder):
            if file_name.endswith(".csv"):
                file_path = os.path.join(data_folder, file_name)
                result_path = os.path.join(result_folder, file_name)
                
                # Đọc dữ liệu hiện tại
                df = load_data(file_path)
                window = 24
                
                # Tìm hỗ trợ và kháng cự
                df = find_support_resistance(df, window=window)

                # Nhận diện nến đảo chiều
                df = check_reversal_candles(df)

                # Giả định có dữ liệu về "order blocks"
                order_blocks = df['Support'].values.tolist() + df['Resistance'].values.tolist()

                # Kiểm tra tín hiệu Long và Short
                df = check_breakout_and_retest(df, order_blocks)
            
                # Thực hiện giao dịch
                trades = execute_trades(df, RR_ratio=RR_ratio)
                
                # Đánh giá kết quả của các lệnh
                results = evaluate_trades(df, trades)

                # Lưu kết quả vào file CSV
                save_trades_to_file(results, result_path)
        print(f"Hoàn thành xử lý dữ liệu tại: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    except Exception as e:
        print(f"Lỗi trong quá trình xử lý: {e}")

# Hàm chính để chạy liên tục
def main_static():
    data_folder = "./data"  # Thư mục chứa các file CSV đầu vào
    result_folder = "./output"  # Thư mục lưu kết quả
    RR_ratio = 3  # Tỷ lệ R/R
    # Xóa sạch dữ liệu cũ trong thư mục kết quả
    remove_all_files(result_folder)
    
    # Lập lịch chạy hàm xử lý mỗi 1 phút
    schedule.every(3).minutes.do(process_and_update_files, 
                                 data_folder=data_folder, 
                                 result_folder=result_folder, 
                                 RR_ratio=RR_ratio)
    
    print("Bắt đầu chạy chương trình. Nhấn Ctrl+C để dừng.")
    
    # Vòng lặp chính để chạy liên tục
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\nDừng chương trình.")

if __name__ == '__main__':
    main_static()