
import os
import pandas as pd
import numpy as np
import shutil

def load_data(filename):
    df = pd.read_csv(filename, parse_dates=['Time'])
    df['Time'] = pd.to_datetime(df['Time'])
    return df

def find_support_resistance(df, window=24):
    df['Resistance'] = df['High'].rolling(window=window).max()
    df['Support'] = df['Low'].rolling(window=window).min()
    return df

def check_reversal_candles(df):
    df['Hanging_Man'] = np.where((df['Close'] < df['Open']) & 
                                  (df['High'] - df['Close'] > 2 * (df['Close'] - df['Open'])), 1, 0)
    return df

def check_breakout_and_retest(df, order_blocks, min_reversal_signal=1):
    df['Long_Signal'] = 0
    df['Short_Signal'] = 0
    
    for index, row in df.iterrows():
        if (row['Support'] in order_blocks and row['Resistance'] in order_blocks):
            if row['Hanging_Man'] >= min_reversal_signal :
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
            take_profit = row['Close'] + RR_ratio * (row['Close'] - stoploss) 
            trades.append({
                'Time': row['Time'],
                'Type': 'Long',
                'Entry': row['Close'],
                'Stoploss': stoploss,
                'Take_Profit': take_profit,
                'Status': 'Ongoing'
            })
        elif row['Short_Signal'] == -1:
            stoploss = row['Resistance']  
            take_profit = row['Close'] - RR_ratio * (stoploss - row['Close']) 
            trades.append({
                'Time': row['Time'],
                'Type': 'Short',
                'Entry': row['Close'],
                'Stoploss': stoploss,
                'Take_Profit': take_profit,
                'Status': 'Ongoing'  
            })
    return trades

def save_trades_to_file(sumary_name,trades, filename, result, RR_ratio):
    trades_df = pd.DataFrame(trades)
    trades_df.to_csv(filename, index=False)
    win_rate, wins, losses, ongoing = calculate_win_rate(result)
    
 
    result_text = (
        
        f"##  {filename}:\n"
        f"Tỉ lệ R/R: {RR_ratio}\n"
        f"Tỉ lệ win: {win_rate * 100:.2f}%\n"
        f"ROE (Return on Equity): {win_rate * RR_ratio * 100:.2f}%\n"
        f"Số lệnh win: {wins}\n"
        f"Số lệnh loss: {losses}\n"
        f"Số lệnh OnGoing (OnGoing): {ongoing}\n"

    )
    with open(sumary_name, "a") as file:  
        file.write(result_text)

# claude
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

def calculate_win_rate(results):
    wins = sum([1 for result in results if result['Status'] == 'win'])
    losses = sum([1 for result in results if result['Status'] == 'loss'])
    ongoing = sum([1 for result in results if result['Status'] == 'OnGoing'])
    
    total_trades = wins + losses
    win_rate = wins / total_trades if total_trades > 0 else 0.0
    
    return win_rate, wins, losses, ongoing


def remove_all_files(folder_path):
    if not os.path.exists(folder_path):
        print(f"Thư mục {folder_path} không tồn tại.")
        return

    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f'Lỗi khi xóa {file_path}. Lý do: {e}')

def process_all_files(data_folder, result_folder,RR_ratio):
    for file_name in os.listdir(data_folder):
        if file_name.endswith(".csv"):
            file_path = os.path.join(data_folder, file_name)
            df = load_data(file_path)
            window = 20
            # Tìm hỗ trợ và kháng cự
            df = find_support_resistance(df, window=window)
            
            df = check_reversal_candles(df)

            order_blocks = df['Support'].values.tolist() + df['Resistance'].values.tolist()


            df = check_breakout_and_retest(df, order_blocks)
        
            trades = execute_trades(df, RR_ratio=RR_ratio) 
            
            results = evaluate_trades(df, trades) # Đánh giá kết quả của các lệnh

            # Lưu kết quả vào file CSV trong thư mục results
            result_file_name = os.path.join(result_folder, file_name)
            # Lưu kết quả summary vào file txt
            sumary_name='src/sum/result.md'
            save_trades_to_file(sumary_name,results, result_file_name,results, RR_ratio)

if __name__ == '__main__':
    data_folder = "src/data" 
    result_folder = "src/res"  
    remove_all_files(result_folder)
    process_all_files(data_folder, result_folder, 3)
