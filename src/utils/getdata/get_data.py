import os
from dotenv import load_dotenv  # pyright: ignore[reportMissingModuleSource]

from src.utils.getdata import get_data_binance, get_data_bybit, get_data_lighter

load_dotenv()

EXCHANGES = {
    'binance': get_data_binance,
    'bybit': get_data_bybit,
    'lighter': get_data_lighter,
}

binance = get_data_binance
bybit = get_data_bybit
lighter = get_data_lighter


def _get_exchange_from_env():
    return os.getenv('EXCHANGE', 'binance').lower()


def _get_current_module():
    return EXCHANGES[_get_exchange_from_env()]


# Re-export all public functions from the selected exchange module
def get_binance_candles(*args, **kwargs):
    return _get_current_module().get_binance_candles(*args, **kwargs)

def get_latest_candles(*args, **kwargs):
    return _get_current_module().get_latest_candles(*args, **kwargs)

def save_to_csv(*args, **kwargs):
    return _get_current_module().save_to_csv(*args, **kwargs)

def get_and_save_data(*args, **kwargs):
    return _get_current_module().get_and_save_data(*args, **kwargs)

def get_and_save_multiple_data(*args, **kwargs):
    return _get_current_module().get_and_save_multiple_data(*args, **kwargs)

def fetch_and_save_candles(*args, **kwargs):
    return _get_current_module().fetch_and_save_candles(*args, **kwargs)

def fetch_and_save_candles_parallel(*args, **kwargs):
    return _get_current_module().fetch_and_save_candles_parallel(*args, **kwargs)

def append_latest_candle(*args, **kwargs):
    return _get_current_module().append_latest_candle(*args, **kwargs)

def append_latest_candle_parallel(*args, **kwargs):
    return _get_current_module().append_latest_candle_parallel(*args, **kwargs)

def getdata_by_date(*args, **kwargs):
    return _get_current_module().getdata_by_date(*args, **kwargs)

def getdata_by_candles(*args, **kwargs):
    return _get_current_module().getdata_by_candles(*args, **kwargs)

def getdata(*args, **kwargs):
    return _get_current_module().getdata(*args, **kwargs)


def get_data_module(exchange: str = None):
    if exchange is None:
        exchange = _get_exchange_from_env()
    else:
        exchange = exchange.lower()
    
    if exchange not in EXCHANGES:
        raise ValueError(f"Unknown exchange: {exchange}. Available: {list(EXCHANGES.keys())}")
    return EXCHANGES[exchange]


def get_function(func_name: str, exchange: str = None):
    module = get_data_module(exchange)
    if not hasattr(module, func_name):
        raise AttributeError(f"Function '{func_name}' not found in {exchange or _get_exchange_from_env()} module")
    return getattr(module, func_name)


def list_functions(exchange: str = None):
    public_funcs = lambda m: [f for f in dir(m) if not f.startswith('_') and callable(getattr(m, f))]
    
    if exchange:
        return {exchange: public_funcs(get_data_module(exchange))}
    return {name: public_funcs(mod) for name, mod in EXCHANGES.items()}


if __name__ == '__main__':
    import sys
    
    exchange_name = _get_exchange_from_env()
    print(f"📊 Exchange: {exchange_name.upper()}")
    
    module = get_data_module()
    if len(sys.argv) > 1:
        module.getdata(sys.argv[1])
    else:
        module.getdata()
