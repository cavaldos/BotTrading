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

    def load_data(self, filename):
        return pd.read_csv(filename)

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
                    row['Time'],
                    file_path
                )
            
            # Lưu thời gian của lệnh OnGoing mới nhất
            self.last_processed_time = ongoing_trades['Time'].max()
            print(f"Processed initial ongoing trades. Last processed time: {self.last_processed_time}")
        else:
            print(f"Không có lệnh 'OnGoing' trong file: {file_path}\n")

    def process_ongoing_trades(self, file_path):
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
                    row['Time'],
                    file_path
                )
            
            # Cập nhật thời gian của lệnh OnGoing mới nhất
            self.last_processed_time = ongoing_trades['Time'].max()
            print(f"Processed new ongoing trades. Last processed time: {self.last_processed_time}")
        else:
            print(f"Không có lệnh 'OnGoing' mới trong file: {file_path}\n")

class WatcherHandler(FileSystemEventHandler):
    def __init__(self, trade_monitor):
        self.trade_monitor = trade_monitor

    def on_modified(self, event):
        # Kiểm tra nếu là file CSV bị sửa đổi
        if event.is_directory or not event.src_path.endswith('.csv'):
            return
        # Xử lý các lệnh OnGoing mới
        self.trade_monitor.process_ongoing_trades(event.src_path)

    def on_created(self, event):
        # Kiểm tra nếu là file CSV mới được tạo
        if event.is_directory or not event.src_path.endswith('.csv'):
            return
        # Xử lý các lệnh OnGoing mới
        self.trade_monitor.process_ongoing_trades(event.src_path)

def monitor_folder(folder_path):
    # Khởi tạo TradeMonitor
    trade_monitor = TradeMonitor(folder_path)
    
    # Kiểm tra và xử lý các lệnh OnGoing ban đầu
    for filename in os.listdir(folder_path):
        if filename.endswith('.csv'):
            file_path = os.path.join(folder_path, filename)
            trade_monitor.process_initial_ongoing_trades(file_path)
    
    # Thiết lập bộ theo dõi
    event_handler = WatcherHandler(trade_monitor)
    observer = Observer()
    observer.schedule(event_handler, folder_path, recursive=False)
    observer.start()
    
    try:
        while True:
            time.sleep(1)  # Kiểm tra thư mục mỗi giây
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
def main_send():
    send_message_to_chat("Start monitoring folder...")
    folder_path = './output'  # Thư mục bạn muốn theo dõi
    monitor_folder(folder_path)
if __name__ == '__main__':
    main_send()