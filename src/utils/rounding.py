"""
Dynamic rounding utility for cryptocurrency prices.
Rounds to appropriate decimal places based on price magnitude.

Binance price precision rules:
- Price >= 1000: Round to 2 decimals (e.g., 89899.91)
- Price >= 100: Round to 3 decimals (e.g., 123.456)
- Price >= 1: Round to 4 decimals (e.g., 45.6789)
- Price < 1: Round to 5+ decimals (e.g., 0.12345)
"""

def round_price(price: float) -> float:
    """
    Round price to appropriate decimal places based on magnitude.
    
    Args:
        price: Price to round
        
    Returns:
        Rounded price with appropriate precision
    """
    if price < 0:
        price = abs(price)
    
    if price >= 1000:
        # Large prices (BTC, ETH when high): 2 decimals
        decimals = 2
    elif price >= 100:
        # Medium-large prices: 3 decimals
        decimals = 3
    elif price >= 1:
        # Medium prices (most altcoins): 4 decimals
        decimals = 4
    else:
        # Small prices (highly fractional altcoins): 5 decimals
        decimals = 5
    
    return round(price, decimals)


def get_price_precision(price: float) -> int:
    """
    Get the number of decimal places for a given price.
    
    Args:
        price: Price to check
        
    Returns:
        Number of decimal places to use
    """
    if price < 0:
        price = abs(price)
    
    if price >= 1000:
        return 2
    elif price >= 100:
        return 3
    elif price >= 1:
        return 4
    else:
        return 5

if __name__ == "__main__":
    # Example usages
    test_prices = [15000.56789, 250.4567, 45.6789, 0.123456, -0.98765]
    for price in test_prices:
        rounded = round_price(price)
        precision = get_price_precision(price)
        print(f"Original: {price}, Rounded: {rounded}, Precision: {precision} decimals")