import requests # pyright: ignore[reportMissingModuleSource]
import pandas as pd # pyright: ignore[reportMissingModuleSource]
import numpy as np # pyright: ignore[reportMissingImports]
import yaml # pyright: ignore[reportMissingModuleSource]
from datetime import datetime, timedelta
import os
import shutil
import time


# Default configuration values
DEFAULT_CONFIG = {
    'currencies': ['BTCUSDT'],
    'timeframes': ['5m', '15m'],
    'folder_path': 'res/data',
    'candles_limit': 2000,
    'start_date': '2026-01-01',
    'mode': 'by_candles'
}


def load_config(config_path=None):
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    
    if not os.path.exists(config_path):
        print(f"Config file not found at {config_path}. Using default config.")
        return DEFAULT_CONFIG.copy()
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            # Merge with default config to ensure all keys are present
            merged_config = DEFAULT_CONFIG.copy()
            if config:
                merged_config.update(config)
            return merged_config
    except Exception as e:
        print(f"Error reading config file: {e}. Using default config.")
        return DEFAULT_CONFIG.copy()


# Fetch candlestick data from Binance
def get_binance_candles(symbol, interval='1h', start_time=None, end_time=None, limit=10000):
    url = f'https://api.binance.com/api/v3/klines'
    params = {
        'symbol': symbol,
        'interval': interval,
        'limit': limit
    }

    # Add start_time and end_time to params if provided
    if start_time:
        params['startTime'] = start_time
    if end_time:
        params['endTime'] = end_time

    try:
        # Send GET request to Binance API
        response = requests.get(url, params=params)
        response.raise_for_status()
        candles = response.json()
        return candles
    except requests.exceptions.RequestException as e:
        print(f"Error connecting to Binance API: {e}")
        return []

# Fetch latest candles (supports fetching multiple candles)
def get_latest_candles(symbol, interval='1h', limit=100):
    # Binance API has a maximum limit of 1000 candles per request
    MAX_API_LIMIT = 1000
    url = f'https://api.binance.com/api/v3/klines'
    
    # If limit <= 1000, only one API request is needed
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
            print(f"Error connecting to Binance API: {e}")
            return []
    
    # If limit > 1000, multiple API requests are needed
    else:
        all_candles = []
        remaining = limit
        
        # First, fetch MAX_API_LIMIT most recent candles
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
            
            # If more candles needed, continue fetching historical data
            while remaining > 0:
                # Get endTime from the first candle of the previous fetch
                end_time = candles[0][0] - 1  # Start time of first candle - 1ms
                
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
                    
                all_candles = candles + all_candles  # Prepend to maintain ascending time order
                remaining -= len(candles)
                
                # Add small delay to avoid API rate limiting
                time.sleep(0.1)
            
            # Trim if fetched more than requested
            if len(all_candles) > limit:
                all_candles = all_candles[-limit:]
                
            return all_candles
            
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to Binance API: {e}")
            return all_candles if all_candles else []

# Convert timestamp and save to CSV file
def save_to_csv(candles, filename):
    with open(filename, 'w') as file:
        # Write header
        file.write("Time,Open,High,Low,Close,Volume\n")

        # Write each data row
        for candle in candles:
            timestamp = datetime.utcfromtimestamp(candle[0] / 1000).strftime('%Y-%m-%d %H:%M:%S')
            open_price = float(candle[1])
            high_price = float(candle[2])
            low_price = float(candle[3])
            close_price = float(candle[4])
            volume = float(candle[5])

            # Write data to file in required format
            file.write(f"{timestamp}, {open_price}, {high_price}, {low_price}, {close_price}, {volume}\n")

def get_and_save_data(symbol, start_date, filename, time_interval='1h'):
    # Convert start date to timestamp
    start_timestamp = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)

    all_candles = []
    while True:
        candles = get_binance_candles(symbol, interval=time_interval, start_time=start_timestamp)

        if not candles:
            break

        # Add data to list
        all_candles.extend(candles)

        # Update start time for next fetch (last candle)
        start_timestamp = candles[-1][0] + 1  # +1 ms to fetch next data

    save_to_csv(all_candles, filename)


def remove_all_files(folder_path):
    # Check if folder exists
    if not os.path.exists(folder_path):
        print(f"Folder {folder_path} does not exist.")
        return

    # Loop and delete all files
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f'Error deleting {file_path}. Reason: {e}')


def get_and_save_multiple_data(currencies, timeframes, start_date, folder_path):
    for currency in currencies:
        for timeframe in timeframes:
            filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
            print(f"Downloading data for {currency} with timeframe {timeframe}...")
            get_and_save_data(currency, start_date, filename, time_interval=timeframe)

def fetch_and_save_candles(currencies, timeframes, folder_path, limit):
    for currency in currencies:
        for timeframe in timeframes:
            filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
            
            latest_candles = get_latest_candles(currency, interval=timeframe, limit=limit)
            
            if latest_candles:
                save_to_csv(latest_candles, filename)
            else:
                print(f"Failed to fetch data for {currency}_{timeframe}")

def getdata_by_date(config=None):
    if config is None:
        config = load_config()
    elif isinstance(config, str):
        config = load_config(config)
    
    currencies = config.get('currencies', DEFAULT_CONFIG['currencies'])
    timeframes = config.get('timeframes', DEFAULT_CONFIG['timeframes'])
    folder_path = config.get('folder_path', DEFAULT_CONFIG['folder_path'])
    start_date = config.get('start_date', DEFAULT_CONFIG['start_date'])
    
    remove_all_files(folder_path) 
    
    # Create folder if it doesn't exist
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    
    print(f"Starting to fetch data from {start_date}...")
    get_and_save_multiple_data(currencies, timeframes, start_date, folder_path)
    print("Completed fetching data by date!")


def getdata_by_candles(config=None):
    if config is None:
        config = load_config()
    elif isinstance(config, str):
        config = load_config(config)
    
    currencies = config.get('currencies', DEFAULT_CONFIG['currencies'])
    timeframes = config.get('timeframes', DEFAULT_CONFIG['timeframes'])
    folder_path = config.get('folder_path', DEFAULT_CONFIG['folder_path'])
    candles_limit = config.get('candles_limit', DEFAULT_CONFIG['candles_limit'])
    
    remove_all_files(folder_path) 
    
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    
    print(f"Starting to fetch {candles_limit} most recent candles...")
    fetch_and_save_candles(currencies, timeframes, folder_path, candles_limit)
    print("Done")


def getdata(config_path=None):
    from tqdm import tqdm  # pyright: ignore[reportMissingModuleSource]
    
    config = load_config(config_path)
    mode = config.get('mode', 'by_candles')
    
    currencies = config.get('currencies', DEFAULT_CONFIG['currencies'])
    timeframes = config.get('timeframes', DEFAULT_CONFIG['timeframes'])
    folder_path = config.get('folder_path', DEFAULT_CONFIG['folder_path'])
    
    remove_all_files(folder_path)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    
    total_tasks = len(currencies) * len(timeframes)
    
    with tqdm(total=total_tasks, desc="Downloading data") as pbar:
        for currency in currencies:
            for timeframe in timeframes:
                filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
                pbar.set_description(f"Downloading {currency}_{timeframe}")
                
                if mode == 'by_date':
                    start_date = config.get('start_date', DEFAULT_CONFIG['start_date'])
                    get_and_save_data(currency, start_date, filename, time_interval=timeframe)
                else:
                    candles_limit = config.get('candles_limit', DEFAULT_CONFIG['candles_limit'])
                    latest_candles = get_latest_candles(currency, interval=timeframe, limit=candles_limit)
                    if latest_candles:
                        save_to_csv(latest_candles, filename)
                    else:
                        tqdm.write(f"Failed to fetch data for {currency}_{timeframe}")
                
                pbar.update(1)
    
    print("Done!")


if __name__ == '__main__':
    import sys
    # Support passing config path from command line
    if len(sys.argv) > 1:
        getdata(sys.argv[1])
    else:
        getdata()