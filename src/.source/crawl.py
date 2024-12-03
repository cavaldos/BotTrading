
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os

from util.config_file import remove_all_files

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
    
    
def save_to_csv(candles, filename):
    with open(filename, 'w') as file:
        # Viết header
        file.write("Time, Open, High, Low, Close, Volume\n")

        # Viết từng dòng dữ liệu
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

def get_and_save_data(symbol, start_date, filename, time_interval='1h'):
    # Chuyển đổi ngày bắt đầu thành timestamp
    start_timestamp = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)

    all_candles = []
    while True:
        candles = get_binance_candles(symbol, interval=time_interval, start_time=start_timestamp)

        if not candles:
            break

        # Thêm dữ liệu vào danh sách
        all_candles.extend(candles)

        # Cập nhật thời gian bắt đầu cho lần lấy tiếp theo (cây nến cuối cùng)
        start_timestamp = candles[-1][0] + 1  # +1 ms để lấy dữ liệu tiếp theo

    save_to_csv(all_candles, filename)
    
    
    
def get_and_save_multiple_data(currencies, timeframes, start_date, folder_path):
    for currency in currencies:
        for timeframe in timeframes:
            filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
            print(f"Đang tải dữ liệu cho {currency} với khung thời gian {timeframe}...")
            get_and_save_data(currency, start_date, filename, time_interval=timeframe)


# fuction run_crawl have parameter start_date, folder_path, currencies, timeframes 
def run_crawl(start_date, folder_path, currencies, timeframes):
    remove_all_files(folder_path)
    get_and_save_multiple_data(currencies, timeframes, start_date=start_date, folder_path=folder_path)


if __name__ == '__main__':
    currencies = ['NEARUSDT']
    timeframes = [ '15m']
    folder_path = 'data'  
    remove_all_files(folder_path)
    
    ## Tải và lưu dữ liệu
    get_and_save_multiple_data(currencies, timeframes, start_date="2024-10-28", folder_path=folder_path)