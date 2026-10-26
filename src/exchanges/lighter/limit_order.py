"""
Limit orders on Lighter (same functions and result keys as src/exchanges/bybit/limit_order.py)
- Validate symbol
- Limit entry; SL and TP are sent with it and activate when it fills
- Cancel and check order status
"""

import time
from typing import Optional

from src.exchanges.messages import format_limit_order_message as _format_common_limit_message
from src.exchanges.lighter.exchange import (
    cancel_order,
    clean_symbol,
    get_exchange,
    get_order_status,
    place_limit_order_with_sl,
    validate_symbol,
)
from src.exchanges.lighter.market_order import _take_profit_from_rr, _warning_lines


def validate_and_place_limit_order(
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    take_profit: Optional[float] = None,
    leverage: int = 20,
    risk_per_trade: float = 5,
    risk_reward_ratio: float = 2.0,
    signal_timestamp: Optional[float] = None,
    time_in_force: str = 'GTC',
) -> dict:
    """
    Validate và đặt lệnh LIMIT trên Lighter.

    Args:
        symbol: Symbol giao dịch (BTCUSDT, BTCUSDT_1m, BTC đều được)
        side: 'buy' (LONG) hoặc 'sell' (SHORT)
        entry_price: Giá limit entry
        stop_loss: Giá stop loss
        take_profit: Giá take profit (nếu None sẽ tự động tính theo RR ratio)
        leverage: Đòn bẩy
        risk_per_trade: Rủi ro USD
        risk_reward_ratio: Tỉ lệ Risk:Reward (mặc định 2.0 = RR 1:2)
        signal_timestamp: Thời điểm nhận tín hiệu (time.time()), dùng để đo latency
        time_in_force: 'GTC' (trên Lighter giữ tối đa 28 ngày), 'PostOnly', 'IOC'

    Returns:
        dict: Kết quả đặt lệnh với order_id để theo dõi
    """
    try:
        execution_start = time.time()
        signal_to_execution_start = None
        if signal_timestamp is not None:
            signal_to_execution_start = execution_start - signal_timestamp

        if entry_price is None or stop_loss is None:
            return {
                'success': False,
                'error': f'entry_price và stop_loss không được là None (entry={entry_price}, sl={stop_loss})',
                'symbol': symbol,
                'order_type': 'limit',
            }

        market_symbol = clean_symbol(symbol)
        exchange = get_exchange()
        if not validate_symbol(exchange, market_symbol):
            return {
                'success': False,
                'error': f'Symbol không có trên Lighter: {symbol} ({market_symbol})',
                'symbol': market_symbol,
                'order_type': 'limit',
            }

        if take_profit is None:
            take_profit = _take_profit_from_rr(side, entry_price, stop_loss, risk_reward_ratio)

        result = place_limit_order_with_sl(
            exchange=exchange,
            symbol=market_symbol,
            side=side,
            leverage=leverage,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_per_trade=risk_per_trade,
            time_in_force=time_in_force,
        )
        result['original_sl'] = stop_loss
        result['original_tp'] = take_profit
        result['symbol'] = market_symbol

        if result.get('success'):
            order_placed_time = time.time()
            quantity = (result.get('position_info') or {}).get('quantity', 0)
            limit_price = result.get('entry_price') or entry_price
            result['final_quantity'] = quantity
            result['contracts'] = quantity
            result['expected_risk'] = round(quantity * abs(limit_price - result['stop_loss']), 2)
            result['expected_profit'] = round(quantity * abs(result['take_profit'] - limit_price), 2)

            result['timing'] = {
                'order_execution_seconds': round(order_placed_time - execution_start, 3),
                'total_execution_seconds': round(time.time() - execution_start, 3),
            }
            if signal_timestamp is not None:
                result['timing']['signal_to_order_seconds'] = round(order_placed_time - signal_timestamp, 3)
                result['timing']['signal_to_execution_start_seconds'] = round(signal_to_execution_start, 3)

        return result

    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'symbol': clean_symbol(symbol),
            'order_type': 'limit',
        }


def format_limit_order_message(order_result: dict) -> str:
    """Same message as Bybit, plus Lighter warnings."""
    message = _format_common_limit_message(order_result)
    if order_result.get('success'):
        message += _warning_lines(order_result)
    return message


def cancel_limit_order(symbol: str, order_id: str) -> dict:
    try:
        market_symbol = clean_symbol(symbol)
        result = cancel_order(get_exchange(), market_symbol, order_id)
        result['symbol'] = market_symbol
        return result
    except Exception as e:
        return {'success': False, 'error': str(e), 'symbol': clean_symbol(symbol), 'order_id': order_id}


def cancel_limit_order_by_id(order_id: str) -> dict:
    from src.exchanges.lighter.tools.order_manager import cancel_order_by_id
    return cancel_order_by_id(order_id)


def check_limit_order_status(symbol: str, order_id: str) -> dict:
    """Kiểm tra trạng thái lệnh limit."""
    try:
        market_symbol = clean_symbol(symbol)
        result = get_order_status(get_exchange(), market_symbol, order_id)
        result['symbol'] = market_symbol
        return result
    except Exception as e:
        return {'success': False, 'error': str(e), 'symbol': clean_symbol(symbol), 'order_id': order_id}


if __name__ == '__main__':
    print("Đặt lệnh THẬT trên Lighter. Sửa tham số rồi bỏ comment để chạy.")
    # result = validate_and_place_limit_order(
    #     symbol='ETHUSDT_1m',
    #     side='buy',
    #     entry_price=2300,
    #     stop_loss=2280,
    #     leverage=20,
    #     risk_per_trade=1,
    #     risk_reward_ratio=2.0,
    # )
    # print(format_limit_order_message(result))
