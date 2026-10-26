"""
Lighter symbol utilities (same role as the Bybit / Binance symbol helpers)
- Fetch all PERPETUAL markets from Lighter
- Check which config currencies (live.yaml / backtest.yaml) exist on Lighter
- Export markets to markdown

    python -m src.exchanges.lighter.tools.symbol_helper
"""

from __future__ import annotations

from typing import Iterable, Optional

from src.exchanges.lighter.exchange import LighterExchange, clean_symbol, create_lighter_exchange


def _exchange(exchange: Optional[LighterExchange]) -> LighterExchange:
    exchange = exchange or create_lighter_exchange()
    exchange.load_markets()
    return exchange


def get_all_lighter_symbols(exchange: Optional[LighterExchange] = None, show_info: bool = True) -> dict:
    """
    Fetch all active PERPETUAL markets from Lighter.

    Returns:
        dict: {'symbols', 'sample_symbols', 'total_count', 'exchange', 'type'}
    """
    try:
        exchange = _exchange(exchange)
        symbols = sorted(exchange.markets)
        result = {
            'symbols': symbols,
            'sample_symbols': symbols[:10],
            'total_count': len(symbols),
            'exchange': 'Lighter',
            'type': 'PERPETUAL (USDC)',
        }
        if show_info:
            tickers = exchange.fetch_tickers()
            top = sorted(symbols, key=lambda s: tickers.get(s, {}).get('quoteVolume') or 0, reverse=True)
            print(f"\n{'=' * 70}")
            print("Lighter PERPETUAL Markets (USDC)")
            print(f"{'=' * 70}")
            print(f"Total Symbols: {len(symbols)}")
            print("\nTop 15 by 24h volume:")
            for i, symbol in enumerate(top[:15], 1):
                market = exchange.markets[symbol]
                volume = tickers.get(symbol, {}).get('quoteVolume') or 0
                print(f"  {i:2}. {symbol:10} id {market['id']:<4} max {market['max_leverage'] or 0:>4.0f}x  "
                      f"min ${market['min_cost']:<6g} vol ${volume / 1e6:,.1f}M")
        return result
    except Exception as e:
        print(f"Error fetching Lighter symbols: {e}")
        return {'symbols': [], 'sample_symbols': [], 'total_count': 0, 'exchange': 'Lighter', 'error': str(e)}


def check_symbols(currencies: Iterable[str], exchange: Optional[LighterExchange] = None) -> dict:
    """Map config symbols (BTCUSDT, ...) to Lighter markets. Returns {'found': {cfg: market}, 'missing': [...]}."""
    exchange = _exchange(exchange)
    found, missing = {}, []
    for currency in currencies:
        if exchange.has_market(currency):
            found[currency] = exchange.market(currency)['symbol']
        else:
            missing.append(currency)
    return {'found': found, 'missing': missing}


def save_symbols_to_markdown(symbols_result: dict, exchange: Optional[LighterExchange] = None,
                             output_file: str = "docs/exchanges/lighter_perp_symbols.md") -> str:
    exchange = _exchange(exchange)
    symbols = symbols_result.get('symbols', [])
    with open(output_file, 'w') as f:
        f.write("# Lighter PERPETUAL Markets (USDC)\n\n")
        f.write(f"**Total Symbols:** {symbols_result.get('total_count', 0)}\n\n")
        f.write("## Format\n")
        f.write("- Lighter market: base asset only (e.g., BTC, ETH)\n")
        f.write("- Config symbols map automatically: `BTCUSDT`, `BTCUSDT_1m`, `BTC/USDC:USDC` -> `BTC`\n")
        f.write("- Max leverage = 1 / min initial margin fraction; orders below Min $ are rejected\n\n")
        f.write("## All Symbols\n\n")
        f.write("| # | Symbol | Market ID | Max Leverage | Min Size | Min $ | Size Step | Price Step |\n")
        f.write("|---|--------|-----------|--------------|----------|-------|-----------|------------|\n")
        for i, symbol in enumerate(symbols, 1):
            m = exchange.markets[symbol]
            f.write(f"| {i} | `{symbol}` | {m['id']} | {m['max_leverage'] or 0:.0f}x | {m['min_amount']:g} | "
                    f"{m['min_cost']:g} | {m['amount_step']:g} | {m['price_step']:g} |\n")
    return output_file


def _config_currencies() -> dict[str, list[str]]:
    from src.config import load_backtest_config, load_live_config

    result = {}
    for name, loader in (('live.yaml', load_live_config), ('backtest.yaml', load_backtest_config)):
        try:
            config = loader(include_strategy=False)
        except Exception:
            continue
        currencies = (config.get('data') or {}).get('currencies') or config.get('currencies') or []
        if currencies:
            result[name] = list(currencies)
    return result


if __name__ == "__main__":
    lighter = create_lighter_exchange()
    symbols_result = get_all_lighter_symbols(exchange=lighter, show_info=True)
    if symbols_result['total_count']:
        output = save_symbols_to_markdown(symbols_result, exchange=lighter)
        print(f"\n✓ Saved {symbols_result['total_count']} symbols to {output}")

    for config_name, currencies in _config_currencies().items():
        checked = check_symbols(currencies, exchange=lighter)
        print(f"\n{config_name}: {len(checked['found'])}/{len(currencies)} symbol có trên Lighter")
        for currency, market in checked['found'].items():
            print(f"  ✅ {currency:14} -> {market}")
        for currency in checked['missing']:
            print(f"  ❌ {currency:14} (không có market '{clean_symbol(currency)}')")
