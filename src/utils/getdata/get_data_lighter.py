"""Candle data from Lighter (zk-rollup perp DEX), public REST, no API key needed.

Same public interface as get_data_binance / get_data_bybit, so get_data.py can
switch to it with EXCHANGE=lighter. CSV files keep the symbol from the config
(e.g. BTCUSDT_1h.csv) while the request goes to the matching Lighter market (BTC).

API notes (checked 2026-10):
- GET /api/v1/orderBooks lists markets: symbol, market_id, market_type (perp/spot).
- GET /api/v1/candles?market_id&resolution&start_timestamp&end_timestamp&count_back
  returns the NEWEST <=500 candles in [start, end], filtered by open time.
  The last candle can still be open.
- Resolutions: 1m 5m 15m 30m 1h 4h 12h 1d.
- Unauthenticated limit: 60 requests/minute per IP, so requests are throttled.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, ALL_COMPLETED
from datetime import datetime, timezone

import requests  # pyright: ignore[reportMissingModuleSource]

from src.config import load_backtest_config, load_yaml_config
from src.utils.remove_all_files import remove_all_files
from src.utils.getdata.get_data_binance import (
    save_to_csv,
    _extract_timestamp_from_line,
    _get_last_closed_candle_end_time,
    _timeframe_to_milliseconds,
)

DEFAULT_CONFIG = {
    'currencies': ['BTCUSDT'],
    'timeframes': ['5m', '15m'],
    'folder_path': 'res/data',
    'candles_limit': 2000,
    'start_date': '2026-01-01',
    'mode': 'by_candles'
}

SUPPORTED_RESOLUTIONS = {'1m', '5m', '15m', '30m', '1h', '4h', '12h', '1d'}
MAX_API_LIMIT = 500


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


def _get_base_url():
    if str(os.getenv('LIGHTER_TESTNET', '')).strip().lower() in {'1', 'true', 'yes', 'on'}:
        return 'https://testnet.zklighter.elliot.ai'
    return os.getenv('LIGHTER_BASE_URL', 'https://mainnet.zklighter.elliot.ai').rstrip('/')


def _get_market_type():
    value = str(os.getenv('LIGHTER_MARKET_TYPE', 'perp')).strip().lower()
    if value not in {'perp', 'spot'}:
        print(f"Invalid LIGHTER_MARKET_TYPE='{value}', using default perp")
        return 'perp'
    return value


HTTP_CONNECT_TIMEOUT = _get_env_float('HTTP_CONNECT_TIMEOUT', 5.0)
HTTP_READ_TIMEOUT = _get_env_float('HTTP_READ_TIMEOUT', 20.0)
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)
HTTP_MAX_RETRIES = max(1, _get_env_int('LIGHTER_HTTP_MAX_RETRIES', 4))
HTTP_RETRY_BACKOFF_SECONDS = max(0.0, _get_env_float('HTTP_RETRY_BACKOFF_SECONDS', 0.5))
RATE_LIMIT_COOLDOWN_SECONDS = max(1.0, _get_env_float('LIGHTER_RATE_LIMIT_COOLDOWN', 15.0))
# 60 req/min per IP -> 1 request per second shared across all threads
MIN_REQUEST_INTERVAL = max(0.0, _get_env_float('LIGHTER_MIN_REQUEST_INTERVAL', 1.0))
PARALLEL_WAVE_TIMEOUT_SECONDS = max(1.0, _get_env_float('PARALLEL_WAVE_TIMEOUT_SECONDS', 90.0))

_throttle_lock = threading.Lock()
_last_request_at = 0.0

_markets_lock = threading.Lock()
_markets = {}  # (market_type, symbol) -> market_id


def _throttle():
    global _last_request_at
    with _throttle_lock:
        wait_for = _last_request_at + MIN_REQUEST_INTERVAL - time.monotonic()
        if wait_for > 0:
            time.sleep(wait_for)
        _last_request_at = time.monotonic()


def _http_get_json(path, params, label):
    url = f"{_get_base_url()}{path}"

    for attempt in range(1, HTTP_MAX_RETRIES + 1):
        _throttle()
        try:
            response = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
            if response.status_code in (405, 429):
                # Lighter answers 429 (or 405 from the firewall) when rate limited
                raise requests.exceptions.HTTPError(f"rate limited ({response.status_code})", response=response)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and data.get('code') not in (None, 200):
                print(f"Lighter API error for {label}: {data.get('code')} {data.get('message', '').strip()}")
                return None
            return data
        except (requests.exceptions.RequestException, ValueError) as e:
            if attempt >= HTTP_MAX_RETRIES:
                print(f"Error connecting to Lighter API for {label} after {HTTP_MAX_RETRIES} attempts: {e}")
                return None

            rate_limited = getattr(getattr(e, 'response', None), 'status_code', None) in (405, 429)
            if rate_limited:
                delay = min(60.0, RATE_LIMIT_COOLDOWN_SECONDS * attempt)
            else:
                delay = HTTP_RETRY_BACKOFF_SECONDS * attempt
            print(
                f"Lighter API request failed for {label} "
                f"(attempt {attempt}/{HTTP_MAX_RETRIES}): {e}. Retrying in {delay:.1f}s..."
            )
            if delay > 0:
                time.sleep(delay)

    return None


def _batch_timeout(total_tasks, max_workers):
    workers = max(1, max_workers)
    waves = max(1, (total_tasks + workers - 1) // workers)
    # Requests are serialized by the throttle, so allow for the queue as well
    return waves * PARALLEL_WAVE_TIMEOUT_SECONDS + total_tasks * MIN_REQUEST_INTERVAL


# ---------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------

def load_markets(force=False):
    """Load and cache {(market_type, symbol): market_id} from /api/v1/orderBooks."""
    with _markets_lock:
        if _markets and not force:
            return dict(_markets)

        data = _http_get_json('/api/v1/orderBooks', {}, 'orderBooks')
        books = (data or {}).get('order_books') or []
        if books:
            _markets.clear()
            for book in books:
                if book.get('status') != 'active':
                    continue
                key = (str(book.get('market_type', 'perp')).lower(), str(book.get('symbol', '')).upper())
                _markets[key] = int(book['market_id'])
        return dict(_markets)


def list_markets(market_type=None):
    market_type = market_type or _get_market_type()
    return sorted(symbol for (mtype, symbol) in load_markets() if mtype == market_type)


def _symbol_candidates(symbol, market_type):
    raw = str(symbol).strip().upper()
    base = raw.split(':', 1)[0].split('/', 1)[0].split('_', 1)[0]
    for suffix in ('-PERP', 'PERP', 'USDT', 'USDC', 'USD'):
        if base.endswith(suffix) and len(base) > len(suffix):
            base = base[:-len(suffix)]
            break

    if market_type == 'spot':
        return [raw, f"{base}/USDC"]
    return [raw, base]


def get_market_id(symbol, market_type=None):
    """Map a config symbol (BTCUSDT, BTC/USDC:USDC, BTC) to a Lighter market_id."""
    market_type = market_type or _get_market_type()
    candidates = _symbol_candidates(symbol, market_type)

    for force in (False, True):  # retry once with a fresh market list (new listings)
        markets = load_markets(force=force)
        for candidate in candidates:
            market_id = markets.get((market_type, candidate))
            if market_id is not None:
                return market_id
        if not markets:
            break

    raise ValueError(f"Lighter has no active {market_type} market for '{symbol}' (tried {candidates})")


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------

def _check_resolution(interval):
    interval = str(interval).strip()
    if interval not in SUPPORTED_RESOLUTIONS:
        raise ValueError(f"Lighter does not support timeframe '{interval}'. Use one of {sorted(SUPPORTED_RESOLUTIONS)}")
    return interval


def _fetch_range(symbol, interval, start_time, end_time, count=MAX_API_LIMIT):
    """Newest <=500 candles with open time in [start_time, end_time], as
    [open_time_ms, open, high, low, close, base_volume], oldest first."""
    params = {
        'market_id': get_market_id(symbol),
        'resolution': _check_resolution(interval),
        'start_timestamp': int(max(0, start_time)),
        'end_timestamp': int(end_time),
        'count_back': int(max(1, min(count, MAX_API_LIMIT))),
    }
    data = _http_get_json('/api/v1/candles', params, f"{symbol}_{interval}")
    raw = (data or {}).get('c') or []

    candles = [
        [int(c['t']), float(c['o']), float(c['h']), float(c['l']), float(c['c']), float(c.get('v', 0.0))]
        for c in raw
        if start_time <= int(c['t']) <= end_time
    ]
    candles.sort(key=lambda c: c[0])
    return candles


def get_binance_candles(symbol, interval='1h', start_time=None, end_time=None, limit=MAX_API_LIMIT):
    """Up to `limit` (max 500) candles starting at start_time.

    Named like the Binance function so get_data.py can dispatch to it.
    """
    limit = max(1, min(int(limit or MAX_API_LIMIT), MAX_API_LIMIT))
    now_ms = int(time.time() * 1000)

    if start_time is None:
        return get_latest_candles(symbol, interval=interval, limit=limit, end_time=end_time)

    interval_ms = _timeframe_to_milliseconds(interval)
    window_end = int(start_time) + limit * interval_ms - 1
    end_time = min(int(end_time) if end_time is not None else now_ms, window_end)
    return _fetch_range(symbol, interval, int(start_time), end_time, limit)[:limit]


def get_latest_candles(symbol, interval='1h', limit=100, end_time=None):
    """Most recent `limit` candles with open time <= end_time (default: now)."""
    limit = max(1, int(limit))
    end_time = int(end_time) if end_time is not None else int(time.time() * 1000)
    all_candles = []

    while len(all_candles) < limit:
        batch_size = min(MAX_API_LIMIT, limit - len(all_candles))
        # start_timestamp=0: the API returns the newest candles in range, so this
        # still works across gaps (maintenance, delistings)
        batch = _fetch_range(symbol, interval, 0, end_time, batch_size)
        if all_candles:
            batch = [c for c in batch if c[0] < all_candles[0][0]]
        if not batch:
            break

        batch = batch[-batch_size:]
        all_candles = batch + all_candles
        end_time = batch[0][0] - 1

        if len(batch) < batch_size:
            break  # reached the start of the market's history

    return all_candles[-limit:]


def get_and_save_data(symbol, start_date, filename, time_interval='1h'):
    """Download every candle from start_date (UTC) until now and save to CSV."""
    start_ms = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    interval_ms = _timeframe_to_milliseconds(time_interval)
    now_ms = int(time.time() * 1000)

    all_candles = []
    while start_ms <= now_ms:
        window_end = min(now_ms, start_ms + MAX_API_LIMIT * interval_ms - 1)
        candles = _fetch_range(symbol, time_interval, start_ms, window_end)
        if candles:
            all_candles.extend(candles)
        # Move on even if the window was empty (market not listed yet / gap)
        start_ms = window_end + 1

    save_to_csv(all_candles, filename)


def get_and_save_multiple_data(currencies, timeframes, start_date, folder_path):
    for currency in currencies:
        for timeframe in timeframes:
            filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
            print(f"Downloading Lighter data for {currency} with timeframe {timeframe}...")
            get_and_save_data(currency, start_date, filename, time_interval=timeframe)


def _fetch_single_currency(currency, timeframe, folder_path, limit, closed_only=False):
    filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
    end_time = _get_last_closed_candle_end_time(timeframe) if closed_only else None
    try:
        candles = get_latest_candles(currency, interval=timeframe, limit=limit, end_time=end_time)
    except ValueError as e:
        print(e)
        return (currency, timeframe, False)
    if candles:
        save_to_csv(candles, filename)
        return (currency, timeframe, True)
    return (currency, timeframe, False)


def fetch_and_save_candles(currencies, timeframes, folder_path, limit, closed_only=False):
    for currency in currencies:
        for timeframe in timeframes:
            _, _, success = _fetch_single_currency(currency, timeframe, folder_path, limit, closed_only)
            if not success:
                print(f"Failed to fetch data for {currency}_{timeframe}")


def _run_parallel(worker, tasks, max_workers, label):
    if not tasks:
        return

    executor = ThreadPoolExecutor(max_workers=max_workers)
    timed_out = False
    try:
        futures = {executor.submit(worker, *task): task for task in tasks}
        timeout_seconds = _batch_timeout(len(tasks), max_workers)
        done, not_done = wait(set(futures), timeout=timeout_seconds, return_when=ALL_COMPLETED)

        if not_done:
            timed_out = True
            for future in not_done:
                future.cancel()
            print(f"{label} timed out after {timeout_seconds:.1f}s ({len(not_done)} task(s) cancelled)")

        for future in done:
            task = futures[future]
            try:
                result = future.result()
            except Exception as e:
                print(f"{label} failed for {task[0]}_{task[1]}: {e}")
                continue
            if result is False or (isinstance(result, tuple) and not result[2]):
                print(f"Failed to fetch data for {task[0]}_{task[1]}")
    finally:
        executor.shutdown(wait=not timed_out, cancel_futures=timed_out)


def fetch_and_save_candles_parallel(currencies, timeframes, folder_path, limit, max_workers=5, closed_only=False):
    tasks = [(c, tf, folder_path, limit, closed_only) for c in currencies for tf in timeframes]
    _run_parallel(_fetch_single_currency, tasks, max_workers, 'fetch_and_save_candles_parallel')


def _append_single_candle(currency, timeframe, folder_path, max_retries=3, closed_only=False):
    """Append the newest candle to the CSV, or update the last row if it has the
    same timestamp. Drops the oldest row so the file keeps a fixed size."""
    filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
    temp_filename = filename + '.tmp'

    end_time = _get_last_closed_candle_end_time(timeframe) if closed_only else None
    try:
        latest = get_latest_candles(currency, interval=timeframe, limit=1, end_time=end_time)
    except ValueError as e:
        print(e)
        return False
    if not latest:
        return False

    candle = latest[0]
    timestamp = datetime.fromtimestamp(candle[0] / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    new_row = (
        f"{timestamp}, {float(candle[1])}, {float(candle[2])}, {float(candle[3])}, "
        f"{float(candle[4])}, {float(candle[5])}\n"
    )

    for attempt in range(max_retries):
        try:
            os.makedirs(folder_path, exist_ok=True)

            if os.path.exists(filename):
                with open(filename, 'r') as f:
                    lines = f.readlines()
            else:
                lines = ["Time,Open,High,Low,Close,Volume\n"]

            if len(lines) > 1 and _extract_timestamp_from_line(lines[-1]) == timestamp:
                lines[-1] = new_row
            else:
                if len(lines) > 2:
                    lines = [lines[0]] + lines[2:]
                lines.append(new_row)

            with open(temp_filename, 'w') as f:
                f.writelines(lines)
            os.replace(temp_filename, filename)
            return True
        except OSError as e:
            try:
                if os.path.exists(temp_filename):
                    os.remove(temp_filename)
            except OSError:
                pass
            if attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            print(f"Failed after {max_retries} attempts for {currency}_{timeframe}: {e}")
            return False

    return False


def append_latest_candle(currencies, timeframes, folder_path, closed_only=False):
    for currency in currencies:
        for timeframe in timeframes:
            _append_single_candle(currency, timeframe, folder_path, closed_only=closed_only)


def append_latest_candle_parallel(currencies, timeframes, folder_path, max_workers=5, closed_only=False):
    tasks = [(c, tf, folder_path, 3, closed_only) for c in currencies for tf in timeframes]
    _run_parallel(_append_single_candle, tasks, max_workers, 'append_latest_candle_parallel')


# ---------------------------------------------------------------------------
# Backtest download entry points (same config keys as Binance/Bybit)
# ---------------------------------------------------------------------------

def _load_config(config):
    if config is None:
        return load_backtest_config(include_strategy=False)
    if isinstance(config, str):
        return load_yaml_config(config, default_name='backtest.yaml')
    return config


def _prepare_folder(folder_path):
    remove_all_files(folder_path)
    os.makedirs(folder_path, exist_ok=True)


def getdata_by_date(config=None):
    config = _load_config(config)
    folder_path = config.get('folder_path', DEFAULT_CONFIG['folder_path'])
    start_date = config.get('start_date', DEFAULT_CONFIG['start_date'])
    _prepare_folder(folder_path)

    print(f"Starting to fetch Lighter data from {start_date}...")
    get_and_save_multiple_data(
        config.get('currencies', DEFAULT_CONFIG['currencies']),
        config.get('timeframes', DEFAULT_CONFIG['timeframes']),
        start_date,
        folder_path,
    )
    print("Completed fetching data by date!")


def getdata_by_candles(config=None):
    config = _load_config(config)
    folder_path = config.get('folder_path', DEFAULT_CONFIG['folder_path'])
    candles_limit = config.get('candles_limit', DEFAULT_CONFIG['candles_limit'])
    _prepare_folder(folder_path)

    print(f"Starting to fetch {candles_limit} most recent Lighter candles...")
    fetch_and_save_candles(
        config.get('currencies', DEFAULT_CONFIG['currencies']),
        config.get('timeframes', DEFAULT_CONFIG['timeframes']),
        folder_path,
        candles_limit,
    )
    print("Done")


def _getdata_single(currency, timeframe, folder_path, mode, config):
    if mode == 'by_date':
        filename = os.path.join(folder_path, f"{currency}_{timeframe}.csv")
        get_and_save_data(currency, config.get('start_date', DEFAULT_CONFIG['start_date']), filename, timeframe)
        return (currency, timeframe, True)
    candles_limit = config.get('candles_limit', DEFAULT_CONFIG['candles_limit'])
    return _fetch_single_currency(currency, timeframe, folder_path, candles_limit)


def getdata(config_path=None, max_workers=5):
    from tqdm import tqdm  # pyright: ignore[reportMissingModuleSource]

    config = load_yaml_config(config_path or 'backtest', default_name='backtest.yaml')
    mode = config.get('mode', 'by_candles')
    currencies = config.get('currencies', DEFAULT_CONFIG['currencies'])
    timeframes = config.get('timeframes', DEFAULT_CONFIG['timeframes'])
    folder_path = config.get('folder_path', DEFAULT_CONFIG['folder_path'])
    _prepare_folder(folder_path)

    tasks = [(c, tf, folder_path, mode, config) for c in currencies for tf in timeframes]
    with tqdm(total=len(tasks), desc="Downloading Lighter data") as pbar:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_getdata_single, *task): task for task in tasks}
            for future in as_completed(futures):
                currency, timeframe = futures[future][0], futures[future][1]
                pbar.set_description(f"Downloaded {currency}_{timeframe}")
                try:
                    result = future.result()
                    if result and not result[2]:
                        tqdm.write(f"Failed to fetch data for {currency}_{timeframe}")
                except Exception as e:
                    tqdm.write(f"Worker failed for {currency}_{timeframe}: {e}")
                pbar.update(1)

    print("Done!")


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        getdata(sys.argv[1])
    else:
        getdata()
