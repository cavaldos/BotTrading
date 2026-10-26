"""
Bybit Symbol utilities
- Fetch all FUTURES symbols from Bybit
- Analyze symbol formats
- Export symbols to markdown
"""

import re


def get_all_bybit_symbols(exchange=None, show_info: bool = True) -> dict:
    """
    Fetch all FUTURES symbols from Bybit exchange.
    
    Args:
        exchange: CCXT Bybit instance (optional, will create if not provided)
        show_info: Print symbol analysis if True
    
    Returns:
        dict: {
            'symbols': list of all symbols,
            'sample_symbols': first 10 symbols,
            'total_count': total number of symbols,
            'format_patterns': examples of different symbol patterns
        }
    """
    try:
        import ccxt
        
        if exchange is None:
            exchange = ccxt.bybit({
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'linear',  # Use USDT linear futures
                }
            })
        
        # Load markets if not loaded
        if not exchange.markets:
            exchange.load_markets()
        
        # Filter symbols - Bybit linear PERPETUAL futures (no expiry date)
        symbols = []
        for symbol in exchange.markets.keys():
            market = exchange.markets[symbol]
            market_type = market.get('type', '')
            
            # Filter for linear perpetual futures (not dated contracts)
            if market_type in ['future', 'linear'] and '/' in symbol:
                # Perpetual futures: BTC/USDT:USDT (no date)
                # Dated contracts: BTC/USDT:USDT-260328 (with date)
                if not re.search(r'-\d{6}$', symbol):  # No date suffix
                    symbols.append(symbol)
        
        # If no type filtering worked, fallback
        if not symbols:
            symbols = [s for s in exchange.markets.keys() 
                      if ('USDT:USDT' in s or 'USD:USD' in s) and not re.search(r'-\d{6}$', s)]
        
        # Sort symbols for consistency
        symbols.sort()
        
        # Analyze symbol patterns
        patterns = {}
        for symbol in symbols[:100]:  # Analyze first 100
            # Bybit format can be: BTCUSDT or BTC/USDT
            match = re.search(r'([A-Z0-9]+)[:/]?(USDT|USD)$', symbol)
            if match:
                pattern = f"{match.group(1)}-{match.group(2)}"
                patterns[pattern] = patterns.get(pattern, 0) + 1
        
        result = {
            'symbols': symbols,
            'sample_symbols': symbols[:10],
            'total_count': len(symbols),
            'exchange': 'Bybit',
            'type': 'FUTURES (Linear)',
            'format_patterns': dict(sorted(patterns.items(), key=lambda x: x[1], reverse=True)[:5])
        }
        
        if show_info:
            print(f"\n{'='*70}")
            print(f"Bybit PERPETUAL Futures Symbols Analysis (Linear)")
            print(f"{'='*70}")
            print(f"Total Symbols: {len(symbols)}")
            print(f"\nFirst 10 symbols:")
            for i, sym in enumerate(symbols[:10], 1):
                market = exchange.markets.get(sym, {})
                base = market.get('base', 'N/A')
                quote = market.get('quote', 'N/A')
                print(f"  {i:2}. {sym:20} (base: {base:8} quote: {quote:8})")
            
            print(f"\nCommon Symbol Patterns:")
            for pattern, count in sorted(patterns.items(), key=lambda x: x[1], reverse=True)[:5]:
                print(f"  - {pattern:30} ({count} occurrences)")
            
            print(f"\nSymbol Format Examples:")
            sample_set = set()
            for sym in symbols:
                if len(sample_set) >= 8:
                    break
                sample_set.add(sym)
            
            for sym in sorted(sample_set):
                print(f"  - {sym}")
        
        return result
        
    except Exception as e:
        print(f"Error fetching Bybit symbols: {e}")
        return {
            'symbols': [],
            'sample_symbols': [],
            'total_count': 0,
            'exchange': 'Bybit',
            'error': str(e)
        }


def save_symbols_to_markdown(symbols_result: dict, exchange=None, output_file: str = "docs/exchanges/bybit_futures_symbols.md") -> str:
    """
    Save Bybit symbols to markdown file.
    
    Args:
        symbols_result: Result from get_all_bybit_symbols()
        exchange: CCXT Bybit instance (for detailed market info)
        output_file: Output markdown file path
    
    Returns:
        str: Path to saved file
    """
    try:
        import ccxt
        
        if exchange is None:
            exchange = ccxt.bybit({
                'enableRateLimit': True,
                'options': {'defaultType': 'linear'}
            })
            exchange.load_markets()
        
        total_count = symbols_result.get('total_count', 0)
        symbols = symbols_result.get('symbols', [])
        
        with open(output_file, 'w') as f:
            f.write("# Bybit PERPETUAL Futures Symbols (Linear)\n\n")
            f.write(f"**Total Symbols:** {total_count}\n\n")
            f.write("## Format\n")
            f.write("- CCXT Format: `BASE/QUOTE:QUOTE` (e.g., BTC/USDT:USDT, ETH/USDT:USDT)\n")
            f.write("- Bybit API: `BASEUOTE` (e.g., BTCUSDT, ETHUSDT)\n\n")
            f.write("## All Symbols\n\n")
            f.write("| # | Symbol | Base | Quote |\n")
            f.write("|---|--------|------|-------|\n")
            
            for i, sym in enumerate(symbols, 1):
                market = exchange.markets.get(sym, {})
                base = market.get('base', 'N/A')
                quote = market.get('quote', 'N/A')
                f.write(f"| {i} | `{sym}` | {base} | {quote} |\n")
        
        return output_file
        
    except Exception as e:
        print(f"Error saving symbols: {e}")
        raise


if __name__ == "__main__":
    # Test: Fetch and save Bybit FUTURES symbols
    print("Bybit FUTURES Symbols (Linear)")
    print("=" * 70)
    try:
        import ccxt
        
        exchange = ccxt.bybit({
            'enableRateLimit': True,
            'options': {'defaultType': 'linear'}
        })
        
        symbols_result = get_all_bybit_symbols(exchange=exchange, show_info=True)
        
        output_file = save_symbols_to_markdown(symbols_result, exchange=exchange, output_file="docs/exchanges/bybit_futures_symbols.md")
        print(f"\n✓ Saved {symbols_result['total_count']} symbols to {output_file}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
