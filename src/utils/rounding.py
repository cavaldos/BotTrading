def round_price(price: float) -> float:
    if price < 0:
        price = abs(price)
    
    # Determine max decimals allowed
    if price >= 1000:
        max_decimals = 2
    elif price >= 100:
        max_decimals = 3
    elif price >= 1:
        max_decimals = 4
    else:
        max_decimals = 5
    
    from decimal import Decimal
    
    # Convert to Decimal for precise handling
    d = Decimal(str(price))
    
    # Simply remove trailing zeros, don't round
    d = d.normalize()
    
    return float(d)


def get_price_precision(price: float) -> int:
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
    test_prices = [1.5600, 250.4, 45.6789, 0.123456, -0.98765]
    for price in test_prices:
        rounded = round_price(price)
        precision = get_price_precision(price)
        print(f"Original: {price}, Rounded: {rounded}, Precision: {precision} decimals")