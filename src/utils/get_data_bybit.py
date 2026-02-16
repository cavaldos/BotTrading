import requests # pyright: ignore[reportMissingModuleSource]
import pandas as pd # pyright: ignore[reportMissingModuleSource]
import numpy as np # pyright: ignore[reportMissingImports]
from src.config import load_config
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, ALL_COMPLETED
import os
import shutil
import time
from src.utils.remove_all_files import remove_all_files


# Default configuration values
DEFAULT_CONFIG = {
    'currencies': ['BTCUSDT'],
    'timeframes': ['5m', '15m'],
    'folder_path': 'res/data',
    'candles_limit': 2000,
    'start_date': '2026-01-01',
    'mode': 'by_candles'
}


def _get_env_float(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        print(f"Invalid {name}='{value}', using default {default}")
        return default


def _get_env_int(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        print(f"Invalid {name}='{value}', using default {default}")
        return default


HTTP_CONNECT_TIMEOUT = _get_env_float('HTTP_CONNECT_TIMEOUT', 5.0)
HTTP_READ_TIMEOUT = _get_env_float('HTTP_READ_TIMEOUT', 20.0)
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)
HTTP_MAX_RETRIES = max(1, _get_env_int('HTTP_MAX_RETRIES', 3))
HTTP_RETRY_BACKOFF_SECONDS = max(0.0, _get_env_float('HTTP_RETRY_BACKOFF_SECONDS', 0.5))
PARALLEL_WAVE_TIMEOUT_SECONDS = max(1.0, _get_env_float('PARALLEL_WAVE_TIMEOUT_SECONDS', 90.0))


def _http_get_with_retry(url, params, exchange_name='Bybit'):
    symbol = params.get('symbol', 'UNKNOWN')
    interval = params.get('interval', 'UNKNOWN')

    for attempt in range(1, HTTP_MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            if attempt >= HTTP_MAX_RETRIES:
                print(
                    f"Error connecting to {exchange_name} API for {symbol}_{interval} "
                    f"after {HTTP_MAX_RETRIES} attempts: {e}"
                )
                return None

            delay = HTTP_RETRY_BACKOFF_SECONDS * attempt
            print(
                f"{exchange_name} API request failed for {symbol}_{interval} "
                f"(attempt {attempt}/{HTTP_MAX_RETRIES}): {e}. Retrying in {delay:.1f}s..."
            )
            if delay > 0:
                time.sleep(delay)

    return None


def _safe_json(response, exchange_name='Bybit'):
    try:
        return response.json()
    except ValueError as e:
        print(f"Invalid JSON response from {exchange_name} API: {e}")
        return None


def _batch_timeout(total_tasks, max_workers):
    workers = max(1, max_workers)
    waves = max(1, (total_tasks + workers - 1) // workers)
    return waves * PARALLEL_WAVE_TIMEOUT_SECONDS

# Bybit interval mapping (Bybit uses different interval format)
INTERVAL_MAP = {
    '1m': '1',
    '3m': '3',
    '5m': '5',
    '15m': '15',
    '30m': '30',
    '1h': '60',
    '2h': '120',
    '4h': '240',
    '6h': '360',
    '12h': '720',
    '1d': 'D',
    '1w': 'W',
    '1M': 'M'
}


def _convert_interval(interval):
    """Convert Binance-style interval to Bybit interval format"""
    return INTERVAL_MAP.get(interval, interval)


def _format_candles(candles):
    """Convert Bybit candle format to Binance-like format.
    
    Bybit: [startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]
    Binance: [openTime, open, high, low, close, volume, ...]
    """
    formatted = []
    for c in candles:
        formatted.append([
            int(c[0]),      # timestamp
            c[1],           # open
            c[2],           # high
            c[3],           # low
            c[4],           # close
            c[5],           # volume
        ])
    return formatted


# Fetch candlestick data from Bybit
def get_binance_candles(symbol, interval='1h', start_time=None, end_time=None, limit=10000):
    url = 'https://api.bybit.com/v5/market/kline'
    bybit_interval = _convert_interval(interval)
    
    params = {
        'category': 'spot',
        'symbol': symbol,
        'interval': bybit_interval,
        'limit': min(limit, 1000)  # Bybit max is 1000
    }

    # Add start_time and end_time to params if provided
    if start_time:
        params['start'] = start_time
    if end_time:
        params['end'] = end_time

    response = _http_get_with_retry(url, params, exchange_name='Bybit')
    if response is None:
        return []

    data = _safe_json(response, exchange_name='Bybit')
    if not data:
        return []

    if data.get('retCode') != 0:
        print(f"Error connecting to Bybit API: {data.get('retMsg')}")
        return []

    # Bybit returns data in descending order, reverse to ascending
    candles = data.get('result', {}).get('list', [])
    candles = candles[::-1]  # Reverse to ascending order

    return _format_candles(candles)


# Fetch latest candles (supports fetching multiple candles)
def get_latest_candles(symbol, interval='1h', limit=100):
    # Bybit API has a maximum limit of 1000 candles per request
    MAX_API_LIMIT = 1000
    bybit_interval = _convert_interval(interval)
    url = 'https://api.bybit.com/v5/market/kline'
    
    # If limit <= 1000, only one API request is needed
    if limit <= MAX_API_LIMIT:
        params = {
            'category': 'spot',
            'symbol': symbol,
            'interval': bybit_interval,
            'limit': limit
        }
        
        response = _http_get_with_retry(url, params, exchange_name='Bybit')
        if response is None:
            return []

        data = _safe_json(response, exchange_name='Bybit')
        if not data:
            return []

        if data.get('retCode') != 0:
            print(f"Error connecting to Bybit API: {data.get('retMsg')}")
            return []

        candles = data.get('result', {}).get('list', [])
        candles = candles[::-1]  # Reverse to ascending order

        return _format_candles(candles)
    
    # If limit > 1000, multiple API requests are needed
    else:
        all_candles = []
        remaining = limit
        
        # First, fetch MAX_API_LIMIT most recent candles
        params = {
            'category': 'spot',
            'symbol': symbol,
            'interval': bybit_interval,
            'limit': MAX_API_LIMIT
        }
        
        response = _http_get_with_retry(url, params, exchange_name='Bybit')
        if response is None:
            return []

        data = _safe_json(response, exchange_name='Bybit')
        if not data:
            return []

        if data.get('retCode') != 0:
            print(f"Error connecting to Bybit API: {data.get('retMsg')}")
            return []

        candles = data.get('result', {}).get('list', [])

        if not candles:
            return []

        # Bybit returns newest first, convert and reverse
        formatted_candles = _format_candles(candles)
        formatted_candles = formatted_candles[::-1]  # Reverse to ascending

        all_candles.extend(formatted_candles)
        remaining -= len(formatted_candles)

        # If more candles needed, continue fetching historical data
        while remaining > 0:
            # Get endTime from the first candle of the previous fetch
            end_time = all_candles[0][0] - 1  # Start time of first candle - 1ms

            params = {
                'category': 'spot',
                'symbol': symbol,
                'interval': bybit_interval,
                'limit': min(MAX_API_LIMIT, remaining),
                'end': end_time
            }

            response = _http_get_with_retry(url, params, exchange_name='Bybit')
            if response is None:
                break

            data = _safe_json(response, exchange_name='Bybit')
            if not data or data.get('retCode') != 0:
                break

            candles = data.get('result', {}).get('list', [])

            if not candles:
                break

            formatted_candles = _format_candles(candles)
            formatted_candles = formatted_candles[::-1]  # Reverse to ascending

            all_candles = formatted_candles + all_candles  # Prepend to maintain ascending time order
            remaining -= len(formatted_candles)

            # Add small delay to avoid API rate limiting
            time.sleep(0.1)

        # Trim if fetched more than requested
        if len(all_candles) > limit:
            all_candles = all_candles[-limit:]

        return all_candles

# Convert timestamp and save to CSV file (atomic write)
def save_to_csv(candles, filename, max_retries=3):
    """Save candles to CSV using atomic write to prevent race conditions.
    
    Writes to a temp file first, then atomically replaces the target file.
    This ensures readers never see a partially written file.
    Includes retry logic and validation for robustness.
    """
    temp_filename = filename + '.tmp'
    folder_path = os.path.dirname(filename)
    
    for attempt in range(max_retries):
        try:
            # Ensure folder exists
            if folder_path and not os.path.exists(folder_path):
                os.makedirs(folder_path, exist_ok=True)
            
            with open(temp_filename, 'w') as file:
                # Write header
                file.write("Time,Open,High,Low,Close,Volume\n")

                # Write each data row
                for candle in candles:
                    timestamp = datetime.fromtimestamp(candle[0] / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    open_price = float(candle[1])
                    high_price = float(candle[2])
                    low_price = float(candle[3])
                    close_price = float(candle[4])
                    volume = float(candle[5])

                    # Write data to file in required format
                    file.write(f"{timestamp}, {open_price}, {high_price}, {low_price}, {close_price}, {volume}\n")
            
            # Validate temp file exists before replace
            if not os.path.exists(temp_filename):
                raise FileNotFoundError(f"Temp file {temp_filename} was not created")
            
            # Ensure folder still exists before atomic replace
            if folder_path and not os.path.exists(folder_path):
                os.makedirs(folder_path, exist_ok=True)
            
            # Atomic replace - this is atomic on most OS (POSIX)
            os.replace(temp_filename, filename)
            return True
            
        except (FileNotFoundError, OSError) as e:
            # Cleanup temp file if exists
            try:
                if os.path.exists(temp_filename):
                    os.remove(temp_filename)
            except Exception:
                pass
            
            if attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            else:
                print(f"Failed to save {filename} after {max_retries} attempts: {e}")
                return False
        except Exception as e:
            # Cleanup temp file for unexpected errors
            try:
                if os.path.exists(temp_filename):
                    os.remove(temp_filename)
            except Exception:
                pass
            print(f"Unexpected error saving {filename}: {e}")
            return False
    
    return False

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
                print(f"Saved {len(latest_candles)} candles to {filename}")
            else:
                print(f"Failed to fetch data for {currency}_{timeframe}")


def _fetch_single_candle(currency, timeframe, folder_path, limit):
    """Fetch and save candles for a single currency/timeframe pair (for parallel execution)"""
    filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
    latest_candles = get_latest_candles(currency, interval=timeframe, limit=limit)
    if latest_candles:
        save_to_csv(latest_candles, filename)
        return (currency, timeframe, True)
    else:
        return (currency, timeframe, False)


def fetch_and_save_candles_parallel(currencies, timeframes, folder_path, limit, max_workers=5):
    """Fetch and save candles for multiple currencies in parallel using ThreadPoolExecutor"""
    tasks = [(c, tf, folder_path, limit) for c in currencies for tf in timeframes]

    if not tasks:
        return

    executor = ThreadPoolExecutor(max_workers=max_workers)
    timed_out = False

    try:
        futures = [executor.submit(_fetch_single_candle, *task) for task in tasks]
        timeout_seconds = _batch_timeout(len(tasks), max_workers)
        done, not_done = wait(set(futures), timeout=timeout_seconds, return_when=ALL_COMPLETED)

        if not_done:
            timed_out = True
            for future in not_done:
                future.cancel()
            print(
                f"fetch_and_save_candles_parallel timed out after {timeout_seconds:.1f}s "
                f"({len(not_done)} task(s) cancelled)"
            )

        for future in done:
            try:
                result = future.result()
            except Exception as e:
                print(f"Parallel fetch worker failed: {e}")
                continue

            if result and not result[2]:
                print(f"Failed to fetch data for {result[0]}_{result[1]}")
    finally:
        executor.shutdown(wait=not timed_out, cancel_futures=timed_out)


def append_latest_candle(currencies, timeframes, folder_path):
    """Fetch only the latest candle and append to existing files, removing the second row.
    
    Uses atomic write to prevent race conditions when multiple threads
    read/write files concurrently.
    """
    for currency in currencies:
        for timeframe in timeframes:
            _append_single_candle(currency, timeframe, folder_path)


def _extract_timestamp_from_line(line):
    """Extract timestamp string from a CSV data line.
    
    Returns the timestamp part (e.g., '2026-01-22 05:15:00') or None if invalid.
    """
    try:
        # Format: "2026-01-22 05:15:00, 892.95, 892.95, 892.49, 892.49, 31.511"
        parts = line.strip().split(',')
        if len(parts) >= 1:
            return parts[0].strip()
    except Exception:
        pass
    return None


def _append_single_candle(currency, timeframe, folder_path, max_retries=3):
    """Append or update latest candle for a single currency/timeframe pair.
    
    If the latest candle has the same timestamp as the last candle in the file,
    it will UPDATE (replace) that candle instead of appending a new one.
    This prevents duplicate entries when polling faster than the candle interval.
    
    Includes retry logic and proper error handling to handle race conditions
    when files/folders may be deleted or modified by other threads.
    """
    filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
    temp_filename = filename + '.tmp'
    
    for attempt in range(max_retries):
        try:
            # Ensure folder exists before any file operations
            if not os.path.exists(folder_path):
                os.makedirs(folder_path, exist_ok=True)
            
            # Fetch only 1 latest candle
            latest_candle = get_latest_candles(currency, interval=timeframe, limit=1)
            
            if not latest_candle:
                print(f"Failed to fetch latest candle for {currency}_{timeframe}")
                return False
            
            # Convert candle to CSV row format
            candle = latest_candle[0]
            timestamp = datetime.fromtimestamp(candle[0] / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            open_price = float(candle[1])
            high_price = float(candle[2])
            low_price = float(candle[3])
            close_price = float(candle[4])
            volume = float(candle[5])
            new_row = f"{timestamp}, {open_price}, {high_price}, {low_price}, {close_price}, {volume}\n"
            
            # Check if file exists
            if not os.path.exists(filename):
                # If file doesn't exist, create it with header and the new candle
                with open(temp_filename, 'w') as f:
                    f.write("Time,Open,High,Low,Close,Volume\n")
                    f.write(new_row)
            else:
                # Read existing file
                with open(filename, 'r') as f:
                    lines = f.readlines()
                
                # Check if file has data (more than just header)
                if len(lines) > 1:
                    # Get timestamp of the last candle in file
                    last_line = lines[-1]
                    last_timestamp = _extract_timestamp_from_line(last_line)
                    
                    if last_timestamp == timestamp:
                        # Same timestamp: UPDATE the last candle (replace it)
                        lines[-1] = new_row
                    else:
                        # Different timestamp: This is a NEW candle
                        # Remove the oldest data row (index 1, after header) to maintain fixed size
                        if len(lines) > 2:
                            lines = [lines[0]] + lines[2:]
                        # Append the new candle
                        lines.append(new_row)
                else:
                    # Only header exists, just append the new candle
                    lines.append(new_row)
                
                # Write to temp file first (atomic write)
                with open(temp_filename, 'w') as f:
                    f.writelines(lines)
            
            # Validate temp file exists before replace
            if not os.path.exists(temp_filename):
                raise FileNotFoundError(f"Temp file {temp_filename} was not created")
            
            # Ensure folder still exists before atomic replace
            if not os.path.exists(folder_path):
                os.makedirs(folder_path, exist_ok=True)
            
            # Atomic replace
            os.replace(temp_filename, filename)
            return True
            
        except (FileNotFoundError, OSError) as e:
            # Cleanup temp file if exists
            try:
                if os.path.exists(temp_filename):
                    os.remove(temp_filename)
            except Exception:
                pass
            
            if attempt < max_retries - 1:
                # Exponential backoff before retry
                time.sleep(0.5 * (attempt + 1))
                continue
            else:
                print(f"Failed after {max_retries} attempts for {currency}_{timeframe}: {e}")
                return False
        except Exception as e:
            # Cleanup temp file for unexpected errors
            try:
                if os.path.exists(temp_filename):
                    os.remove(temp_filename)
            except Exception:
                pass
            print(f"Unexpected error for {currency}_{timeframe}: {e}")
            return False
    
    return False


def append_latest_candle_parallel(currencies, timeframes, folder_path, max_workers=5):
    """Append latest candle for multiple currencies in parallel using ThreadPoolExecutor"""
    tasks = [(c, tf, folder_path) for c in currencies for tf in timeframes]

    if not tasks:
        return

    executor = ThreadPoolExecutor(max_workers=max_workers)
    timed_out = False

    try:
        futures = [executor.submit(_append_single_candle, *task) for task in tasks]
        timeout_seconds = _batch_timeout(len(tasks), max_workers)
        done, not_done = wait(set(futures), timeout=timeout_seconds, return_when=ALL_COMPLETED)

        if not_done:
            timed_out = True
            for future in not_done:
                future.cancel()
            print(
                f"append_latest_candle_parallel timed out after {timeout_seconds:.1f}s "
                f"({len(not_done)} task(s) cancelled)"
            )

        for future in done:
            try:
                future.result()  # Wait for completion, errors will be printed inside _append_single_candle
            except Exception as e:
                print(f"Parallel append worker failed: {e}")
    finally:
        executor.shutdown(wait=not timed_out, cancel_futures=timed_out)

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


def _getdata_single(currency, timeframe, folder_path, mode, config):
    """Fetch and save data for a single currency/timeframe pair (for parallel execution in getdata)"""
    filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
    
    if mode == 'by_date':
        start_date = config.get('start_date', DEFAULT_CONFIG['start_date'])
        get_and_save_data(currency, start_date, filename, time_interval=timeframe)
    else:
        candles_limit = config.get('candles_limit', DEFAULT_CONFIG['candles_limit'])
        latest_candles = get_latest_candles(currency, interval=timeframe, limit=candles_limit)
        if latest_candles:
            save_to_csv(latest_candles, filename)
            return (currency, timeframe, True)
        else:
            return (currency, timeframe, False)
    return (currency, timeframe, True)


def getdata(config_path=None, max_workers=5):
    from tqdm import tqdm  # pyright: ignore[reportMissingModuleSource]
    config_path = 'src/config/dev.yaml'
    config = load_config(config_path)
    mode = config.get('mode', 'by_candles')
    
    currencies = config.get('currencies', DEFAULT_CONFIG['currencies'])
    timeframes = config.get('timeframes', DEFAULT_CONFIG['timeframes'])
    folder_path = config.get('folder_path', DEFAULT_CONFIG['folder_path'])
    
    remove_all_files(folder_path)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    
    total_tasks = len(currencies) * len(timeframes)
    tasks = [(c, tf, folder_path, mode, config) for c in currencies for tf in timeframes]
    
    completed = 0
    with tqdm(total=total_tasks, desc="Downloading data") as pbar:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_getdata_single, *task): task for task in tasks}
            for future in as_completed(futures):
                task = futures[future]
                currency, timeframe = task[0], task[1]
                pbar.set_description(f"Downloaded {currency}_{timeframe}")
                
                try:
                    result = future.result()
                except Exception as e:
                    tqdm.write(f"Worker failed for {currency}_{timeframe}: {e}")
                    completed += 1
                    pbar.update(1)
                    continue

                if result and not result[2]:
                    tqdm.write(f"Failed to fetch data for {currency}_{timeframe}")
                
                completed += 1
                pbar.update(1)
    
    print("Done!")


if __name__ == '__main__':
    import sys
    # Support passing config path from command line
    if len(sys.argv) > 1:
        getdata(sys.argv[1])
    else:
        getdata()
