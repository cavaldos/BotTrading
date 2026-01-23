from src.utils.rounding import round_price


def calculate_trade_size(entry: float, stoploss: float, takeprofit: float, leverage=50, risk_per_trade=5):
    entry = float(entry)
    stoploss = float(stoploss)
    takeprofit = float(takeprofit)

    # Xác định khoảng cách từ entry đến stoploss
    risk_distance = abs(entry - stoploss)

    if risk_distance == 0:
        raise ValueError("Entry và Stoploss không được bằng nhau.")

    # Tính khối lượng (position size) dựa trên mức độ rủi ro cố định mỗi lệnh
    position_size = risk_per_trade / risk_distance

    # Tính toán margin cần thiết với đòn bẩy cố định là 50
    required_margin = (position_size * entry) / leverage

    return round_price(required_margin)*leverage, leverage, risk_per_trade 


if __name__ == '__main__':
    # Test
    trade_info = calculate_trade_size(entry=86397, stoploss=85765, takeprofit=87573)
    print(trade_info)

