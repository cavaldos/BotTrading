import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os
import time
import threading
from util.config_file import remove_all_files

# Hàm lấy dữ liệu nến (candlestick) từ Binance
def get_binance_candles(symbol, interval='1h', start_time=None, end_time=None, limit=1000):
    url = f'https://api.binance.com/api/v3/klines'
    params = {
        'symbol': symbol,
        'interval': interval,
        'limit': limit
    }

    # Nếu có start_time và end_time thì thêm vào params
    if start_time:
        params['startTime'] = start_time
    if end_time:
        params['endTime'] = end_time

    try:
        # Gửi yêu cầu GET tới Binance API
        response = requests.get(url, params=params)
        response.raise_for_status()
        candles = response.json()
        return candles
    except requests.exceptions.RequestException as e:
        print(f"Lỗi khi kết nối tới Binance API: {e}")
        return []

# Chuyển đổi timestamp và lưu vào file .csv
def save_to_csv(candles, filename):
    file_exists = os.path.exists(filename)

    with open(filename, 'a') as file:
        if not file_exists:
            file.write("Time,Open,High,Low,Close,Volume\n")
        # Viết dữ liệu vào file, nếu file không tồn tại thì sẽ tạo mới
        for candle in candles:
            timestamp = datetime.utcfromtimestamp(candle[0] / 1000).strftime('%Y-%m-%d %H:%M:%S')
            open_price = float(candle[1])
            high_price = float(candle[2])
            low_price = float(candle[3])
            close_price = float(candle[4])
            volume = float(candle[5])

            # Làm tròn các giá trị, loại bỏ các số 0 không cần thiết
            open_price = round(open_price, 2)
            high_price = round(high_price, 2)
            low_price = round(low_price, 2)
            close_price = round(close_price, 2)

            # Ghi dữ liệu vào file theo định dạng yêu cầu
            file.write(f"{timestamp}, {open_price}, {high_price}, {low_price}, {close_price}, {volume}\n")

# Hàm lấy dữ liệu từ Binance và lưu vào file liên tục
def get_and_save_data_real_time(symbol, start_date, filename, time_interval='1h'):
    # Chuyển đổi ngày bắt đầu thành timestamp
    start_timestamp = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)

    while True:
        # Lấy dữ liệu mới từ Binance API
        candles = get_binance_candles(symbol, interval=time_interval, start_time=start_timestamp)

        if not candles:
            print(f"waiting ...")
            time.sleep(60)  # Chờ 1 phút trước khi thử lại
            continue

        # Lưu dữ liệu vào file
        save_to_csv(candles, filename)
        print(f"Lưu dữ liệu thành công vào file {filename}")

        # Cập nhật thời gian bắt đầu cho lần lấy dữ liệu tiếp theo
        start_timestamp = candles[-1][0] + 1  # +1 ms để lấy dữ liệu tiếp theo

        # Chờ 1 phút (hoặc thời gian tương ứng với khung thời gian nến) trước khi lấy dữ liệu tiếp theo
        time.sleep(60)

# Hàm chạy song song các đồng coin với các thread
def run_in_thread(currency, timeframe, folder_path, start_date):
    filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
    print(f"Bắt đầu tải dữ liệu cho {currency} với khung thời gian {timeframe}...")
    get_and_save_data_real_time(currency, start_date, filename, time_interval=timeframe)



def getdata():
    currencies = ['NEARUSDT', 'SOLUSDT']
    timeframes = [ '15m']
    folder_path = 'src/data'
    start_date = "2022-01-01"
    remove_all_files(folder_path)
    # Tạo và bắt đầu các thread cho từng đồng coin và khung thời gian
    threads = []
    for currency in currencies:
        for timeframe in timeframes:
            thread = threading.Thread(target=run_in_thread, args=(currency, timeframe, folder_path, start_date))
            threads.append(thread)
            thread.start()

    # Đảm bảo tất cả các thread đều hoàn thành
    for thread in threads:
        thread.join()

    print("Tất cả dữ liệu đã được tải và lưu thành công!")


if __name__ == '__main__':
    getdata()