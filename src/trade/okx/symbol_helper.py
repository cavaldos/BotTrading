"""
OKX Symbol utilities
- Fetch all symbols from OKX exchange
- Analyze symbol formats (SPOT, SWAP, FUTURES)
- Export symbols to markdown
"""

import re


def get_all_okx_symbols(exchange=None, inst_type: str = 'SWAP', show_info: bool = True) -> dict:
    """
    Fetch all symbols from OKX exchange to understand symbol format.
    
    Args:
        exchange: CCXT OKX instance (optional, will create if not provided)
        inst_type: Instrument type - 'SPOT', 'SWAP', 'FUTURES', 'OPTION' (default 'SWAP')
        show_info: Print symbol analysis if True
    
    Returns:
        dict: {
            'symbols': list of all symbols,
            'sample_symbols': first 10 symbols,
            'total_count': total number of symbols,
            'inst_type': instrument type used,
            'format_patterns': examples of different symbol patterns
        }
    """
    try:
        # Import here to avoid circular import
        from src.trade.okx.exchange import get_exchange
        
        if exchange is None:
            exchange = get_exchange(sandbox=True)
        
        # Load markets if not loaded
        if not exchange.markets:
            exchange.load_markets()
        
        # Filter symbols by type
        symbols = []
        for symbol in exchange.markets.keys():
            market = exchange.markets[symbol]
            
            # OKX uses 'type' or 'info' to indicate instrument type
            market_type = market.get('type', '')
            
            # Check if it matches requested type (case-insensitive)
            if inst_type.upper() in market_type.upper():
                symbols.append(symbol)
        
        # If no symbols found by type filter, get all symbols
        if not symbols:
            symbols = list(exchange.markets.keys())
        
        # Sort symbols for consistency
        symbols.sort()
        
        # Analyze symbol patterns
        patterns = {}
        for symbol in symbols[:100]:  # Analyze first 100
            match = re.search(r'([A-Z]+)[-/]?([A-Z]+)([-/]?(SWAP|FUTURES|MARGIN|SPOT))?', symbol)
            if match:
                pattern = f"{match.group(1)}-{match.group(2)}" + (f"-{match.group(4)}" if match.group(4) else "")
                patterns[pattern] = patterns.get(pattern, 0) + 1
        
        result = {
            'symbols': symbols,
            'sample_symbols': symbols[:10],
            'total_count': len(symbols),
            'inst_type': inst_type,
            'format_patterns': dict(sorted(patterns.items(), key=lambda x: x[1], reverse=True)[:5])
        }
        
        if show_info:
            print(f"\n{'='*70}")
            print(f"OKX Symbol Analysis ({inst_type})")
            print(f"{'='*70}")
            print(f"Total Symbols: {len(symbols)}")
            print(f"\nFirst 10 symbols (CCXT format → OKX ID format):")
            for i, sym in enumerate(symbols[:10], 1):
                market = exchange.markets.get(sym, {})
                okx_id = market.get('id', 'N/A')
                base = market.get('base', 'N/A')
                quote = market.get('quote', 'N/A')
                print(f"  {i:2}. {sym:25} → {okx_id:30} (base: {base:5} quote: {quote:5})")
            
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
        print(f"Error fetching OKX symbols: {e}")
        return {
            'symbols': [],
            'sample_symbols': [],
            'total_count': 0,
            'inst_type': inst_type,
            'error': str(e)
        }


def save_symbols_to_markdown(symbols_result: dict, exchange=None, output_file: str = "docs/okx_symbols.md") -> str:
    """
    Save symbols to markdown file.
    
    Args:
        symbols_result: Result from get_all_okx_symbols()
        exchange: CCXT OKX instance (for detailed market info)
        output_file: Output markdown file path
    
    Returns:
        str: Path to saved file
    """
    try:
        from src.trade.okx.exchange import get_exchange
        
        if exchange is None:
            exchange = get_exchange(sandbox=True)
        
        inst_type = symbols_result.get('inst_type', 'UNKNOWN')
        total_count = symbols_result.get('total_count', 0)
        symbols = symbols_result.get('symbols', [])
        
        with open(output_file, 'w') as f:
            f.write(f"# OKX {inst_type} Symbols\n\n")
            f.write(f"**Total Symbols:** {total_count}\n\n")
            f.write("## Format\n")
            
            if inst_type == 'SPOT':
                f.write("- CCXT: `BASE/QUOTE` (e.g., BTC/USDT)\n")
                f.write("- OKX API: `BASE-QUOTE` (e.g., BTC-USDT)\n\n")
            elif inst_type == 'SWAP':
                f.write("- CCXT: `BASE/QUOTE:QUOTE` (e.g., BTC/USDT:USDT)\n")
                f.write("- OKX API: `BASE-QUOTE-SWAP` (e.g., BTC-USDT-SWAP)\n\n")
            elif inst_type == 'FUTURES':
                f.write("- CCXT: `BASE/QUOTE-YYMMDD` (e.g., BTC/USDT-250328)\n")
                f.write("- OKX API: `BASE-QUOTE-YYMMDD` (e.g., BTC-USDT-250328)\n\n")
            
            f.write("## All Symbols\n\n")
            f.write("| # | CCXT Symbol | OKX ID | Base | Quote |\n")
            f.write("|---|---|---|---|---|\n")
            
            for i, sym in enumerate(symbols, 1):
                market = exchange.markets.get(sym, {})
                okx_id = market.get('id', 'N/A')
                base = market.get('base', 'N/A')
                quote = market.get('quote', 'N/A')
                f.write(f"| {i} | `{sym}` | `{okx_id}` | {base} | {quote} |\n")
        
        return output_file
        
    except Exception as e:
        print(f"Error saving symbols: {e}")
        raise


if __name__ == "__main__":
    # Test: Fetch and save FUTURES symbols
    print("OKX FUTURES Symbols")
    print("=" * 70)
    try:
        from src.trade.okx.exchange import get_exchange
        
        exchange = get_exchange(sandbox=True)
        symbols_result = get_all_okx_symbols(exchange=exchange, inst_type='FUTURES', show_info=True)
        
        output_file = save_symbols_to_markdown(symbols_result, exchange=exchange, output_file="docs/okx_futures_symbols.md")
        print(f"\n✓ Saved {symbols_result['total_count']} symbols to {output_file}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
