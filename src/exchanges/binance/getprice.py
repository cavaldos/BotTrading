from src.exchanges.binance.exchange import get_exchange
import ccxt


def parse_symbol(symbol: str) -> str:
    """
    Parse symbol từ format 'BTCUSDT_1m' -> 'BTC/USDT:USDT'
    Hỗ trợ các format: BTCUSDT_1m, BTCUSDT, BTC/USDT:USDT, BTC
    """
    symbol = symbol.upper().strip()
    
    # Loại bỏ timeframe suffix (_1m, _5m, _1h, _4h, _1d, ...)
    if '_' in symbol:
        symbol = symbol.split('_')[0]
    
    # Đã đúng format futures
    if ':USDT' in symbol:
        return symbol
    
    # Format BTC/USDT
    if '/' in symbol:
        base = symbol.split('/')[0]
        return f"{base}/USDT:USDT"
    
    # Format BTCUSDT -> BTC
    if symbol.endswith('USDT'):
        symbol = symbol[:-4]
    
    return f"{symbol}/USDT:USDT"


def get_current_price(symbol: str) -> float:
    """Lấy giá hiện tại của symbol futures"""
    exchange = get_exchange()
    symbol = parse_symbol(symbol)
    ticker = exchange.fetch_ticker(symbol)
    return ticker['last']


def get_mark_price(symbol: str) -> float:
    """Lấy mark price của symbol futures"""
    exchange = get_exchange()
    symbol = parse_symbol(symbol)
    ticker = exchange.fetch_ticker(symbol)
    return ticker.get('info', {}).get('markPrice', ticker['last'])


def get_bid_ask(symbol: str) -> dict:
    """Lấy giá bid/ask của symbol futures"""
    exchange = get_exchange()
    symbol = parse_symbol(symbol)
    ticker = exchange.fetch_ticker(symbol)
    return {
        'bid': ticker['bid'],
        'ask': ticker['ask'],
        'spread': ticker['ask'] - ticker['bid'] if ticker['bid'] and ticker['ask'] else 0
    }


def get_ticker(symbol: str) -> dict:
    """Lấy thông tin ticker đầy đủ của symbol futures"""
    exchange = get_exchange()
    symbol = parse_symbol(symbol)
    ticker = exchange.fetch_ticker(symbol)
    return {
        'symbol': symbol,
        'last': ticker['last'],
        'bid': ticker['bid'],
        'ask': ticker['ask'],
        'high': ticker['high'],
        'low': ticker['low'],
        'volume': ticker['quoteVolume'],
        'change_24h': ticker['percentage'],
        'mark_price': float(ticker.get('info', {}).get('markPrice', ticker['last'])),
        'index_price': float(ticker.get('info', {}).get('indexPrice', 0)),
        'funding_rate': float(ticker.get('info', {}).get('lastFundingRate', 0)),
    }


def get_multiple_prices(symbols: list[str]) -> dict:
    """Lấy giá của nhiều symbol cùng lúc"""
    exchange = get_exchange()
    formatted_symbols = [parse_symbol(s) for s in symbols]
    tickers = exchange.fetch_tickers(formatted_symbols)
    return {s: t['last'] for s, t in tickers.items()}


def get_orderbook(symbol: str, limit: int = 10) -> dict:
    """Lấy orderbook của symbol futures"""
    exchange = get_exchange()
    symbol = parse_symbol(symbol)
    orderbook = exchange.fetch_order_book(symbol, limit)
    return {
        'bids': orderbook['bids'][:limit],
        'asks': orderbook['asks'][:limit],
        'timestamp': orderbook['timestamp']
    }


if __name__ == '__main__':
    symbol = 'BCHUSDT_1m'
    
    print(f"=== Thông tin giá {symbol} Futures ===")
    print(f"Giá hiện tại: {get_current_price(symbol)}")
    print(f"Mark price: {get_mark_price(symbol)}")
    print(f"Bid/Ask: {get_bid_ask(symbol)}")
    print(f"\nTicker đầy đủ:")
    ticker = get_ticker(symbol)
    for k, v in ticker.items():
        print(f"  {k}: {v}")
