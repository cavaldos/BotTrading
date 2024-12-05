import os
import pandas as pd
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import time
from util.notify import entry_point

# Đọc file CSV vào DataFrame
def load_data(filename):
    return pd.read_csv(filename)

# Lọc các lệnh có trạng thái 'OnGoing' và gọi entry_point
def process_ongoing_trades(file_path, last_sent_time):
    # Đọc dữ liệu từ file
    df = load_data(file_path)
    
    # Lọc các dòng có trạng thái là 'OnGoing'
    ongoing_trades = df[df['Status'] == 'OnGoing']
    
    if not ongoing_trades.empty:
        # Sắp xếp theo thời gian để lấy lệnh mới nhất
        ongoing_trades['Time'] = pd.to_datetime(ongoing_trades['Time'])
        latest_trade = ongoing_trades.sort_values(by='Time', ascending=False).iloc[0]
        
        # Kiểm tra nếu lệnh mới nhất chưa được gửi
        if latest_trade['Time'] > last_sent_time:
            entry_point(
                latest_trade['Type'],
                latest_trade['Entry'],
                latest_trade['Stoploss'],
                latest_trade['Take_Profit'],
                latest_trade['Time'],
                file_path
            )
            last_sent_time = latest_trade['Time']  # Cập nhật thời gian lệnh đã gửi
        else:
            print(f"Không có lệnh mới để gửi trong file: {file_path}")
    else:
        print(f"Không có lệnh 'OnGoing' trong file: {file_path}\n")
    
    return last_sent_time

# Xử lý sự kiện thay đổi trong thư mục
class WatcherHandler(FileSystemEventHandler):
    def __init__(self, folder_path, last_sent_time):
        self.folder_path = folder_path
        self.last_sent_time = last_sent_time

    def on_modified(self, event):
        # Kiểm tra nếu là file CSV bị sửa đổi
        if event.is_directory or not event.src_path.endswith('.csv'):
            return
        # Gửi thông báo và xử lý khi file thay đổi
        self.last_sent_time = process_ongoing_trades(event.src_path, self.last_sent_time)

    def on_created(self, event):
        # Kiểm tra nếu là file CSV mới được tạo
        if event.is_directory or not event.src_path.endswith('.csv'):
            return
        # Gửi thông báo và xử lý khi file mới được tạo
        self.last_sent_time = process_ongoing_trades(event.src_path, self.last_sent_time)

    def on_deleted(self, event):
        # Kiểm tra nếu là file CSV bị xóa
        if event.is_directory or not event.src_path.endswith('.csv'):
            return
        print(f"Thông báo: File {event.src_path} đã bị xóa.")

# Hàm theo dõi thư mục
def monitor_folder(folder_path):
    # Lưu thời gian của lệnh đã gửi cuối cùng
    last_sent_time = pd.to_datetime('2010-01-01 00:00:00')  
    
    # Lần đầu, duyệt qua các file để gửi tất cả lệnh "OnGoing" hiện tại
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        
        if os.path.isfile(file_path) and filename.endswith('.csv'):
            last_sent_time = process_ongoing_trades(file_path, last_sent_time)

    event_handler = WatcherHandler(folder_path, last_sent_time)
    observer = Observer()
    observer.schedule(event_handler, folder_path, recursive=False)
    observer.start()
    
    try:
        while True:
            time.sleep(1)  # Kiểm tra thư mục mỗi giây
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == '__main__':
    folder_path = './output'  # Thư mục bạn muốn theo dõi
    monitor_folder(folder_path)
