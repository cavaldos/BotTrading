import requests # pyright: ignore[reportMissingModuleSource]
import pandas as pd # pyright: ignore[reportMissingModuleSource]
import numpy as np # pyright: ignore[reportMissingImports]
from datetime import datetime, timedelta
import os
import shutil
import time


# Hàm lấy dữ liệu nến (candlestick) từ Binance
def get_binance_candles(symbol, interval='1h', start_time=None, end_time=None, limit=10000):
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

# Hàm lấy dữ liệu nến mới nhất (hỗ trợ lấy nhiều cây nến)
def get_latest_candles(symbol, interval='1h', limit=100):
    """
    Lấy dữ liệu nến mới nhất từ Binance API.
    
    Args:
        symbol: Mã cặp tiền tệ (ví dụ: 'BTCUSDT')
        interval: Khung thời gian (ví dụ: '1h', '5m')
        limit: Số lượng cây nến cần lấy
    
    Returns:
        Danh sách các cây nến, mỗi cây nến là một mảng theo định dạng Binance API
    """
    # API Binance có giới hạn tối đa 1000 cây nến mỗi yêu cầu
    MAX_API_LIMIT = 1000
    url = f'https://api.binance.com/api/v3/klines'
    
    # Nếu limit <= 1000, chỉ cần một yêu cầu API
    if limit <= MAX_API_LIMIT:
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Lỗi khi kết nối tới Binance API: {e}")
            return []
    
    # Nếu limit > 1000, cần phải thực hiện nhiều yêu cầu API
    else:
        all_candles = []
        remaining = limit
        
        # Đầu tiên lấy MAX_API_LIMIT cây nến mới nhất
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': MAX_API_LIMIT
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            candles = response.json()
            
            if not candles:
                return []
                
            all_candles.extend(candles)
            remaining -= len(candles)
            
            # Nếu cần thêm cây nến, tiếp tục lấy về quá khứ
            while remaining > 0:
                # Lấy endTime từ cây nến đầu tiên của lần lấy trước
                end_time = candles[0][0] - 1  # Thời điểm bắt đầu của cây nến đầu tiên - 1ms
                
                params = {
                    'symbol': symbol,
                    'interval': interval,
                    'limit': min(MAX_API_LIMIT, remaining),
                    'endTime': end_time
                }
                
                response = requests.get(url, params=params)
                response.raise_for_status()
                candles = response.json()
                
                if not candles:
                    break
                    
                all_candles = candles + all_candles  # Thêm vào đầu để duy trì thứ tự thời gian tăng dần
                remaining -= len(candles)
                
                # Thêm độ trễ nhỏ để tránh quá tải API
                time.sleep(0.1)
            
            # Cắt bớt nếu lấy quá số lượng yêu cầu
            if len(all_candles) > limit:
                all_candles = all_candles[-limit:]
                
            return all_candles
            
        except requests.exceptions.RequestException as e:
            print(f"Lỗi khi kết nối tới Binance API: {e}")
            return all_candles if all_candles else []

# Chuyển đổi timestamp và lưu vào file .txt
def save_to_csv(candles, filename):
    with open(filename, 'w') as file:
        # Viết header
        file.write("Time,Open,High,Low,Close,Volume\n")

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


def get_and_save_multiple_data(currencies, timeframes, start_date, folder_path):
    for currency in currencies:
        for timeframe in timeframes:
            filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
            print(f"Đang tải dữ liệu cho {currency} với khung thời gian {timeframe}...")
            get_and_save_data(currency, start_date, filename, time_interval=timeframe)

def continuous_data_collection(currencies, timeframes, folder_path, limit=100, sleep_time=60):

    # Tạo thư mục nếu chưa tồn tại
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    
    print(f"Bắt đầu thu thập dữ liệu liên tục với {limit} cây nến gần nhất...")
    
    try:
        while True:
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            print(f"Đang cập nhật dữ liệu vào {current_time}")
            for currency in currencies:
                for timeframe in timeframes:
                    filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
                    print(f"Đang lấy {limit} cây nến cho {currency} với khung thời gian {timeframe}...")
                    
                    # Lấy số lượng cây nến theo yêu cầu
                    latest_candles = get_latest_candles(currency, interval=timeframe, limit=limit)
                    
                    if latest_candles:
                        # Lưu dữ liệu vào file
                        save_to_csv(latest_candles, filename)
                        print(f"Đã cập nhật {len(latest_candles)} cây nến cho {currency}_{timeframe}")
                    else:
                        print(f"Không lấy được dữ liệu cho {currency}_{timeframe}")
            
            # Đợi một khoảng thời gian trước khi lấy dữ liệu mới
            print(f"Đợi {sleep_time} giây cho lần cập nhật tiếp theo...")
            time.sleep(sleep_time)
            
    except KeyboardInterrupt:
        print("Đã dừng thu thập dữ liệu.")


def getdata():
    # Cấu hình dễ dàng thay đổi
    currencies = ['BTCUSDT']
    timeframes = ['15m']
    folder_path = 'src/data'  # Remove 'src/' prefix
    candles_limit = 10000  # Số lượng cây nến muốn lấy
    update_interval = 60    # Thời gian cập nhật (giây)
    
    # Xóa dữ liệu cũ (nếu có)
    remove_all_files(folder_path) 
    
    # Tạo thư mục nếu chưa tồn tại
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    
    # # Bắt đầu thu thập dữ liệu liên tục
    # continuous_data_collection(currencies, timeframes, folder_path, limit=candles_limit, sleep_time=update_interval)


if __name__ == '__main__':
    getdata()