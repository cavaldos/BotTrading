"""
Handle placing limit orders on Bybit
- Validate symbol
- Place limit orders with stop loss and take profit
- Cancel and check order status
"""

import time
from typing import Optional

from src.exchanges.bybit.exchange import (
    get_exchange,
    format_symbol,
    validate_symbol,
    place_limit_order_with_sl,
    cancel_order,
    get_order_status,
)
from src.exchanges.bybit.risk_calculator import clean_symbol
from src.utils.rounding import round_price
from src.exchanges.messages import format_limit_order_message


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
    Validate và đặt lệnh LIMIT trên Bybit.
    
    Args:
        symbol: Symbol giao dịch
        side: 'buy' (LONG) hoặc 'sell' (SHORT)
        entry_price: Giá limit entry
        stop_loss: Giá stop loss
        take_profit: Giá take profit (nếu None sẽ tự động tính theo RR ratio)
        leverage: Đòn bẩy
        risk_per_trade: Rủi ro USD
        risk_reward_ratio: Tỉ lệ Risk:Reward (mặc định 2.0 = RR 1:2)
        signal_timestamp: Thời điểm nhận tín hiệu (time.time()), dùng để đo latency
        time_in_force: 'GTC' (Good Till Cancel), 'IOC', 'FOK', 'PostOnly'
    
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
        
        clean_sym = clean_symbol(symbol)
        exchange = get_exchange()
        formatted_symbol = format_symbol(clean_sym)
        
        if not validate_symbol(exchange, formatted_symbol):
            return {
                'success': False,
                'error': f'Symbol không hợp lệ: {symbol} ({formatted_symbol})',
                'symbol': clean_sym,
                'order_type': 'limit',
            }
        
        original_stop_loss = stop_loss
        original_take_profit = take_profit

        if take_profit is None:
            if side.lower() == 'buy':
                risk = entry_price - stop_loss
                reward = risk * risk_reward_ratio
                take_profit = entry_price + reward
            else:
                risk = stop_loss - entry_price
                reward = risk * risk_reward_ratio
                take_profit = entry_price - reward
            
            take_profit = round_price(take_profit)

        original_take_profit = take_profit
        
        result = place_limit_order_with_sl(
            exchange=exchange,
            symbol=clean_sym,
            side=side.lower(),
            leverage=leverage,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_per_trade=risk_per_trade,
            time_in_force=time_in_force,
        )

        result['original_sl'] = original_stop_loss
        result['original_tp'] = original_take_profit
        result['symbol'] = clean_sym
        
        if result['success']:
            order_placed_time = time.time()
            order_execution_time = order_placed_time - execution_start
            
            position_info = result.get('position_info', {})
            current_quantity = position_info.get('quantity', 0)
            
            result['stop_loss'] = stop_loss
            result['take_profit'] = take_profit
            result['final_quantity'] = current_quantity
            result['contracts'] = current_quantity
            
            distance_to_sl = abs(entry_price - stop_loss)
            expected_risk = current_quantity * distance_to_sl
            result['expected_risk'] = round(expected_risk, 2)
            
            distance_to_tp = abs(take_profit - entry_price)
            expected_profit = current_quantity * distance_to_tp
            result['expected_profit'] = round(expected_profit, 2)
            
            total_execution_time = time.time() - execution_start
            result['timing'] = {
                'order_execution_seconds': round(order_execution_time, 3),
                'total_execution_seconds': round(total_execution_time, 3),
            }
            
            if signal_timestamp is not None:
                signal_to_order_placed = order_placed_time - signal_timestamp
                result['timing']['signal_to_order_seconds'] = round(signal_to_order_placed, 3)
                result['timing']['signal_to_execution_start_seconds'] = round(signal_to_execution_start, 3)
        
        return result
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'symbol': clean_symbol(symbol),
            'order_type': 'limit',
        }




def cancel_limit_order(symbol: str, order_id: str) -> dict:
    try:
        clean_sym = clean_symbol(symbol)
        exchange = get_exchange()
        
        result = cancel_order(exchange, clean_sym, order_id)
        result['symbol'] = clean_sym
        
        return result
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'symbol': clean_symbol(symbol),
            'order_id': order_id,
        }


def cancel_limit_order_by_id(order_id: str) -> dict:
    from src.exchanges.bybit.tools.order_manager import cancel_order_by_id
    return cancel_order_by_id(order_id)


def check_limit_order_status(symbol: str, order_id: str) -> dict:
    """
    Kiểm tra trạng thái lệnh limit.
    """
    try:
        clean_sym = clean_symbol(symbol)
        exchange = get_exchange()
        
        result = get_order_status(exchange, clean_sym, order_id)
        result['symbol'] = clean_sym
        
        return result
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'symbol': clean_symbol(symbol),
            'order_id': order_id,
        }


if __name__ == '__main__':
    print("="*70)
    print("Test validate_and_place_limit_order()")
    print("="*70)
    
    print("\n[TEST] LIMIT order")
    result = validate_and_place_limit_order(
        symbol='ETHUSDT_1m',
        side='sell',
        entry_price=2353,
        stop_loss=2363,
        leverage=20,
        risk_per_trade=50,
        risk_reward_ratio=1.0,
    )
    print(format_limit_order_message(result))
    # cancel_limit_order_by_id(order_id='dca8b34b-a77e-4fed-929a-8ba6afa2d580')
