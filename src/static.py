import os
import pandas as pd
import numpy as np
import shutil
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent

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
    df['Hanging_Man'] = np.where((df['Close'] < df['Open']) & 
                                  (df['High'] - df['Close'] > 2 * (df['Close'] - df['Open'])), 1, 0)
    df['Shooting_Star'] = np.where((df['Close'] < df['Open']) & 
                                    (df['High'] - df['Open'] > 2 * (df['Close'] - df['Open'])), 1, 0)
    df['Morning_Star'] = np.where(
        (df['Open'].shift(2) > df['Close'].shift(2)) & 
        (df['Close'].shift(1) < df['Open'].shift(1)) &
        (df['Close'] > df['Open']) &
        (df['Close'] > (df['Open'].shift(2) + df['Close'].shift(2)) / 2), 1, 0)
    return df

def check_breakout_and_retest(df, order_blocks, min_reversal_signal=1):
    df['Long_Signal'] = 0
    df['Short_Signal'] = 0
    
    for index, row in df.iterrows():
        # cac mau nen dao chieu co the su dung: Hammer, Hanging,
        if (row['Support'] in order_blocks and row['Resistance'] in order_blocks):
            if row['Doji'] >= min_reversal_signal or row['Hammer'] >= min_reversal_signal:
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

def save_trades_to_file(sumary_name,trades, filename, result, RR_ratio):
    trades_df = pd.DataFrame(trades)
    trades_df.to_csv(filename, index=False)
    win_rate, wins, losses, ongoing = calculate_win_rate(result)
    

    result_text = (
        

        f"## {filename}:\n"
        f"Tỉ lệ R/R: {RR_ratio}\n"
        f"Tỉ lệ win: {win_rate * 100:.2f}%\n"
        f"ROE (Return on Equity): {win_rate * RR_ratio * 100:.2f}%\n"
        f"Số lệnh win: {wins}\n"
        f"Số lệnh loss: {losses}\n"
        f"Số lệnh OnGoing (OnGoing): {ongoing}\n"

    )
    # Mở file và ghi kết quả
    with open(sumary_name, "a") as file:  # 'a' để append vào file nếu đã tồn tại
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


# Tính toán tỉ lệ win
def calculate_win_rate(results):
    wins = sum([1 for result in results if result['Status'] == 'win'])
    losses = sum([1 for result in results if result['Status'] == 'loss'])
    ongoing = sum([1 for result in results if result['Status'] == 'OnGoing'])
    
    total_trades = wins + losses
    win_rate = wins / total_trades if total_trades > 0 else 0.0
    
    return win_rate, wins, losses, ongoing


def remove_all_files(folder_path):
    # Kiểm tra xem thư mục có tồn tại không
    if not os.path.exists(folder_path):
        print(f"Thư mục {folder_path} không tồn tại.")
        return

    # Lặp và xóa tất cả các file
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f'Lỗi khi xóa {file_path}. Lý do: {e}')


# Xử lý tất cả các file trong thư mục "data"
def process_all_files(data_folder, result_folder, RR_ratio):
    if not os.path.exists(data_folder):
        print(f"Data folder {data_folder} does not exist yet. Waiting for files...")
        return
        
    files = [f for f in os.listdir(data_folder) if f.endswith(".csv")]
    if not files:
        print(f"No CSV files found in {data_folder}. Waiting for files to be created...")
        return
        
    for file_name in files:
        try:
            process_single_file(data_folder, result_folder, file_name, RR_ratio)
        except Exception as e:
            print(f"Error processing file {file_name}: {str(e)}")

# Function to process a single file
def process_single_file(data_folder, result_folder, file_name, RR_ratio):
    if file_name.endswith(".csv"):
        file_path = os.path.join(data_folder, file_name)
        
        # Check if file exists and is not empty
        if not os.path.exists(file_path):
            print(f"File {file_path} does not exist. Skipping...")
            return
            
        try:
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                print(f"File {file_path} is empty. Skipping...")
                return
                
            print(f"Processing file: {file_path}")
            df = load_data(file_path)
            window = 20
            # Tìm hỗ trợ và kháng cự
            df = find_support_resistance(df, window=window)
            
            # Nhận diện nến đảo chiều
            df = check_reversal_candles(df)

            # Giả định có dữ liệu về "order blocks"
            order_blocks = df['Support'].values.tolist() + df['Resistance'].values.tolist()

            # Kiểm tra tín hiệu Long và Short
            df = check_breakout_and_retest(df, order_blocks)
        
            trades = execute_trades(df, RR_ratio=RR_ratio) 
            
            results = evaluate_trades(df, trades)

            # Lưu kết quả vào file CSV trong thư mục results
            result_file_name = os.path.join(result_folder, file_name)
            # Lưu kết quả summary vào file txt
            sumary_name='sum/result.md'
            save_trades_to_file(sumary_name, results, result_file_name, results, RR_ratio)
            print(f"Results saved to: {result_file_name}")
        except Exception as e:
            print(f"Error processing file {file_path}: {str(e)}")

# File watcher class to monitor changes in data folder
class DataFolderHandler(FileSystemEventHandler):
    def __init__(self, data_folder, result_folder, rr_ratio):
        self.data_folder = os.path.abspath(data_folder)  # Convert to absolute path
        self.result_folder = result_folder
        self.rr_ratio = rr_ratio
        self.last_processed = {}  # Dictionary to track last processing time for each file
        self.processing_cooldown = 5  # Seconds to wait before processing the same file again
        print(f"Watching directory: {self.data_folder}")
    
    def on_modified(self, event):
        self._process_event(event, "modified")
            
    def on_created(self, event):
        self._process_event(event, "created")
    
    def on_moved(self, event):
        if not event.is_directory and event.dest_path.endswith(".csv"):
            file_name = os.path.basename(event.dest_path)
            print(f"File moved: {file_name}. Processing...")
            self.last_processed[file_name] = time.time()
            try:
                process_single_file(self.data_folder, self.result_folder, file_name, self.rr_ratio)
            except Exception as e:
                print(f"Error processing moved file {file_name}: {str(e)}")
    
    def _process_event(self, event, event_type):
        if not event.is_directory:
            file_path = os.path.abspath(event.src_path)
            file_name = os.path.basename(file_path)
            
            # Debug information
            print(f"Event {event_type} detected for: {file_path}")
            print(f"File name: {file_name}")
            print(f"Is CSV: {file_name.endswith('.csv')}")
            print(f"In data folder: {os.path.dirname(file_path) == self.data_folder}")
            
            # Check if this is a CSV file in our data folder
            if file_name.endswith(".csv") and os.path.dirname(file_path) == self.data_folder:
                current_time = time.time()
                
                # Check if we recently processed this file
                if file_name in self.last_processed:
                    time_since_last_process = current_time - self.last_processed[file_name]
                    if time_since_last_process < self.processing_cooldown:
                        print(f"Skipping {file_name} - processed {time_since_last_process:.1f}s ago")
                        return
                
                # Update the last processed time
                self.last_processed[file_name] = current_time
                
                print(f"Change detected in file: {file_name}. Processing...")
                try:
                    process_single_file(self.data_folder, self.result_folder, file_name, self.rr_ratio)
                except Exception as e:
                    print(f"Error processing file {file_name}: {str(e)}")

def start_file_watcher(data_folder, result_folder, rr_ratio):
    # Convert to absolute paths
    data_folder = os.path.abspath(data_folder)
    result_folder = os.path.abspath(result_folder)
    
    # Ensure the folders exist
    if not os.path.exists(data_folder):
        os.makedirs(data_folder)
    if not os.path.exists(result_folder):
        os.makedirs(result_folder)
    
    # Create event handler and observer
    event_handler = DataFolderHandler(data_folder, result_folder, rr_ratio)
    observer = Observer()
    observer.schedule(event_handler, path=data_folder, recursive=False)
    
    # Start the observer
    observer.start()
    print(f"Monitoring {data_folder} for changes. Results will be saved to {result_folder}")
    print("Press Ctrl+C to stop monitoring")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

def mainstatic():
    # Use absolute paths
    data_folder = os.path.abspath("src/data")
    result_folder = os.path.abspath("src/results")
    rr_ratio = 2
    
    # Create the summary directory if it doesn't exist
    summary_dir = os.path.dirname('sum/result.md')
    if not os.path.exists(summary_dir):
        os.makedirs(summary_dir)
    
    # Make sure the result folder exists and is empty
    if not os.path.exists(result_folder):
        os.makedirs(result_folder)
    else:
        remove_all_files(result_folder)
    
    # Ensure data folder exists
    if not os.path.exists(data_folder):
        os.makedirs(data_folder)
        print(f"Created data folder: {data_folder}")
    
    # Process existing files first
    print("Checking for existing files...")
    process_all_files(data_folder, result_folder, rr_ratio)
    
    # Start monitoring the data folder for changes
    start_file_watcher(data_folder, result_folder, rr_ratio)

if __name__ == "__main__":
    mainstatic()
