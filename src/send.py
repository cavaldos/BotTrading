import os
import pandas as pd
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import time
from util.notify import entry_point, send_message_to_chat

class TradeMonitor:
    def __init__(self, folder_path):
        self.folder_path = folder_path
        self.last_processed_time = None
        self.processed_files = set()  # To track which files have been processed

    def load_data(self, filename):
        # Parse dates correctly
        df = pd.read_csv(filename, parse_dates=['Time'])
        return df

    def process_initial_ongoing_trades(self, file_path):
        # Đọc dữ liệu từ file
        df = self.load_data(file_path)
        
        # Lọc các lệnh có trạng thái 'OnGoing'
        ongoing_trades = df[df['Status'] == 'OnGoing']
        
        if not ongoing_trades.empty:
            # Gọi entry_point cho các lệnh OnGoing
            for index, row in ongoing_trades.iterrows():
                entry_point(
                    row['Type'], 
                    row['Entry'], 
                    row['Stoploss'], 
                    row['Take_Profit'], 
                    # Convert Timestamp to string in the correct format
                    row['Time'].strftime('%Y-%m-%d %H:%M:%S'),
                    file_path
                )
            
            # Lưu thời gian của lệnh OnGoing mới nhất
            self.last_processed_time = ongoing_trades['Time'].max()
            self.processed_files.add(file_path)
            print(f"Processed initial ongoing trades. Last processed time: {self.last_processed_time}")
        else:
            print(f"Không có lệnh 'OnGoing' trong file: {file_path}\n")

    def process_ongoing_trades(self, file_path):
        # Kiểm tra xem file đã được xử lý chưa
        if file_path in self.processed_files:
            return

        # Đọc dữ liệu từ file
        df = self.load_data(file_path)
        
        # Lọc các lệnh có trạng thái 'OnGoing' và sau thời điểm cuối cùng đã xử lý
        if self.last_processed_time is not None:
            ongoing_trades = df[
                (df['Status'] == 'OnGoing') & 
                (df['Time'] > self.last_processed_time)
            ]
        else:
            ongoing_trades = df[df['Status'] == 'OnGoing']
        
        if not ongoing_trades.empty:
            # Gọi entry_point cho các lệnh OnGoing mới
            for index, row in ongoing_trades.iterrows():
                entry_point(
                    row['Type'], 
                    row['Entry'], 
                    row['Stoploss'], 
                    row['Take_Profit'], 
                    # Convert Timestamp to string in the correct format
                    row['Time'].strftime('%Y-%m-%d %H:%M:%S'),
                    file_path
                )
            
            # Cập nhật thời gian của lệnh OnGoing mới nhất
            self.last_processed_time = ongoing_trades['Time'].max()
            self.processed_files.add(file_path)
            print(f"Processed new ongoing trades. Last processed time: {self.last_processed_time}")
        else:
            print(f"Không có lệnh 'OnGoing' mới trong file: {file_path}\n")

def monitor_folder(folder_path):
    # Khởi tạo TradeMonitor
    trade_monitor = TradeMonitor(folder_path)
    
    # Kiểm tra và xử lý các lệnh OnGoing ban đầu
    for filename in os.listdir(folder_path):
        if filename.endswith('.csv'):
            file_path = os.path.join(folder_path, filename)
            trade_monitor.process_initial_ongoing_trades(file_path)
    
    # Vòng lặp liên tục kiểm tra thư mục
    try:
        while True:
            # Kiểm tra các file mới trong thư mục
            for filename in os.listdir(folder_path):
                if filename.endswith('.csv'):
                    file_path = os.path.join(folder_path, filename)
                    trade_monitor.process_ongoing_trades(file_path)
            
            # Chờ 5 giây trước khi kiểm tra lại
            time.sleep(5)
    except KeyboardInterrupt:
        print("Monitoring stopped.")

def main_send():
    send_message_to_chat("Start monitoring folder...")
    folder_path = './output'  # Thư mục bạn muốn theo dõi
    monitor_folder(folder_path)

if __name__ == '__main__':
    main_send()