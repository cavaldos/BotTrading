import os
from typing import Optional

import ccxt
from dotenv import load_dotenv

load_dotenv()

_exchange_instance: Optional[ccxt.binance] = None


def create_binance_exchange(api_key: str, secret: str) -> ccxt.binance:
    """
    Create a Binance exchange instance for REAL TRADING ACCOUNT
    (Not demo/sandbox mode)
    """
    exchange = ccxt.binance({
        'apiKey': api_key,
        'secret': secret,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future',  # Use futures (not spot)
            'adjustForTimeDifference': True,
            'warnOnFetchOpenOrdersWithoutSymbol': False,  # Suppress rate limit warning
        }
    })
    
    # This connects to REAL Binance account, not sandbox/demo
    # Ensure API keys have proper permissions for trading
    return exchange


def get_exchange(force_new: bool = False) -> ccxt.binance:
    global _exchange_instance
    
    if _exchange_instance is not None and not force_new:
        return _exchange_instance
    
    api_key = os.getenv('BINANCE_API_KEY')
    secret = os.getenv('BINANCE_SECRET')
    
    if not api_key or not secret:
        raise ValueError(
            "Thieu API Key hoac Secret Key!\n"
            "Vui long cau hinh trong file .env:\n"
            "  BINANCE_API_KEY=your_api_key\n"
            "  BINANCE_SECRET=your_secret_key"
        )
    
    _exchange_instance = create_binance_exchange(api_key, secret)
    _exchange_instance.load_markets()
    
    return _exchange_instance


def get_account_info(exchange: ccxt.binance) -> dict:
    """Get account info including username"""
    try:
        # Try to fetch account info
        account = exchange.fetch_account()
        return account
    except Exception as e:
        try:
            # Fallback: use private API to get account info
            info = exchange.privateGetApiV3Account()
            return info
        except Exception:
            return {}


def get_market_info(exchange: ccxt.binance, symbol: str) -> dict:
    if symbol not in exchange.markets:
        exchange.load_markets(True)
    
    if symbol not in exchange.markets:
        return {}
    
    market = exchange.markets[symbol]
    
    return {
        'symbol': symbol,
        'base': market.get('base', ''),
        'quote': market.get('quote', ''),
        'settle': market.get('settle', ''),
        'contract_size': market.get('contractSize', 1),
        'min_amount': market.get('limits', {}).get('amount', {}).get('min', 0),
        'max_amount': market.get('limits', {}).get('amount', {}).get('max', 0),
        'min_cost': market.get('limits', {}).get('cost', {}).get('min', 0),
        'price_precision': market.get('precision', {}).get('price', 2),
        'amount_precision': market.get('precision', {}).get('amount', 3),
        'max_leverage': market.get('limits', {}).get('leverage', {}).get('max', 125),
    }


def validate_symbol(exchange: ccxt.binance, symbol: str) -> bool:
    if not exchange.markets:
        exchange.load_markets()
    
    return symbol in exchange.markets


def format_symbol(symbol: str) -> str:
    symbol = symbol.upper().strip()
    
    if ':USDT' in symbol:
        return symbol
    
    if '/' in symbol:
        base_quote = symbol.split('/')[0]
        return f"{base_quote}/USDT:USDT"
    
    if symbol.endswith('USDT'):
        symbol = symbol[:-4]
    
    return f"{symbol}/USDT:USDT"


if __name__ == '__main__':
    exchange = get_exchange()
