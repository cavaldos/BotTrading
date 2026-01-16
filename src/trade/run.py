import os

from src.utils.get_data import (remove_all_files, continuous_data_collection)
from src.trade.execute import execute
from src.trade.send import check_and_send_open_orders

DATA_FOLDER = "res/data"
OUTPUT_FOLDER = "res/output"
LOG_FILE = "res/logs/sent_orders.json"
def getdata():
    # Cấu hình dễ dàng thay đổi
    currencies = ['BTCUSDT']
    timeframes = ['5m', '15m']
    folder_path = DATA_FOLDER
    candles_limit = 2000  # Số lượng cây nến muốn lấy
    update_interval = 60    # Thời gian cập nhật (giây)
    
    # Xóa dữ liệu cũ (nếu có)
    remove_all_files(folder_path) 
    remove_all_files(OUTPUT_FOLDER)
    
    # Tạo thư mục nếu chưa tồn tại
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    
    # # Bắt đầu thu thập dữ liệu liên tục
    continuous_data_collection(currencies, timeframes, folder_path, limit=candles_limit, sleep_time=update_interval)


def main():
    print("Welcome to the Trade Application!")
    getdata()
    print("Data collection completed.")
    print("Executing trade strategy...")
    execute(DATA_FOLDER, OUTPUT_FOLDER)
    print("Trade execution completed.")
    print("Checking and sending open orders via Telegram...")
    new_orders_sent = check_and_send_open_orders(OUTPUT_FOLDER, LOG_FILE)
    print(f"Number of new open orders sent: {new_orders_sent}")
    print("Process completed.")


if __name__ == "__main__":
    main()
