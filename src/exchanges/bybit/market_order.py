"""
Handle placing market orders on Bybit
- Validate symbol
- Place market orders with stop loss
"""

import time
from typing import Optional

from src.exchanges.bybit.exchange import (
    get_exchange,
    format_symbol,
    validate_symbol,
    place_market_order_with_sl,
    set_position_sl_tp,
)
from src.exchanges.bybit.risk_calculator import (
    clean_symbol,
)
from src.utils.rounding import round_price
from src.exchanges.messages import format_market_order_message


def validate_and_place_order(
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    take_profit: Optional[float] = None,
    leverage: int = 20,
    risk_per_trade: float = 5,
    risk_reward_ratio: float = 2.0,
    signal_timestamp: Optional[float] = None,
) -> dict:
    """
    Args:
        symbol: Symbol giao dịch
        side: 'buy' (LONG) hoặc 'sell' (SHORT)
        entry_price: Giá entry dự kiến
        stop_loss: Giá stop loss
        take_profit: Giá take profit (nếu None sẽ tự động tính theo RR ratio)
        leverage: Đòn bẩy
        risk_per_trade: Rủi ro USD
        risk_reward_ratio: Tỉ lệ Risk:Reward (mặc định 2.0 = RR 1:2)
        signal_timestamp: Thời điểm nhận tín hiệu (time.time()), dùng để đo latency
    
    Returns:
        dict: Kết quả đặt lệnh
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
            }
        
        clean_sym = clean_symbol(symbol)
        exchange = get_exchange()
        formatted_symbol = format_symbol(clean_sym)
        
        if not validate_symbol(exchange, formatted_symbol):
            return {
                'success': False,
                'error': f'Symbol không hợp lệ: {symbol} ({formatted_symbol})',
                'symbol': clean_sym,
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
        
        result = place_market_order_with_sl(
            exchange=exchange,
            symbol=clean_sym,
            side=side.lower(),
            leverage=leverage,
            stop_loss=stop_loss,
            risk_per_trade=risk_per_trade,
            expected_entry=entry_price,
        )

        result['original_sl'] = original_stop_loss
        result['original_tp'] = original_take_profit
        
        if result['success']:
            order_filled_time = time.time()
            order_execution_time = order_filled_time - execution_start
            
            # Tăng thời gian chờ để position được tạo hoàn toàn
            time.sleep(3)
            
            # Sử dụng actual entry từ order result (đã được fix trong exchange.py)
            actual_entry = result.get('entry_price') or entry_price
            expected_entry_from_signal = result.get('expected_entry') or entry_price
            position_info = result.get('position_info', {})
            current_quantity = position_info.get('quantity', 0)

            # Giữ nguyên các mức SL/TP đã tính toán từ signal, không re-anchor theo actual entry.
            # Nếu market fill lệch giá thì RR thực tế có thể xấu đi, nhưng các mức giá bảo vệ
            # trên sàn vẫn phải đúng theo kế hoạch ban đầu của trader.
            sl_tp_adjustment = {
                'mode': 'keep_original_levels',
                'expected_entry': round_price(expected_entry_from_signal),
                'actual_entry': round_price(actual_entry),
                'expected_sl': round_price(original_stop_loss),
                'adjusted_sl': round_price(original_stop_loss),
                'expected_tp': round_price(original_take_profit),
                'adjusted_tp': round_price(original_take_profit),
                'side': side.upper(),
            }

            adjusted_sl = original_stop_loss
            adjusted_tp = original_take_profit

            def to_price_precision(price: float) -> float:
                try:
                    return float(exchange.price_to_precision(formatted_symbol, price))
                except Exception:
                    return round_price(price)

            adjusted_sl = to_price_precision(adjusted_sl)
            adjusted_tp = to_price_precision(adjusted_tp)

            result['stop_loss'] = adjusted_sl
            result['take_profit'] = adjusted_tp
            result['final_quantity'] = current_quantity
            result['sl_tp_adjustment'] = sl_tp_adjustment
            
            actual_distance_to_sl = abs(actual_entry - adjusted_sl)
            actual_risk = current_quantity * actual_distance_to_sl
            result['actual_risk'] = round(actual_risk, 2)

            actual_distance_to_tp = abs(adjusted_tp - actual_entry)
            expected_profit = current_quantity * actual_distance_to_tp
            result['expected_profit'] = round(expected_profit, 2)
            
            if entry_price > 0:
                slippage_pct = ((actual_entry - entry_price) / entry_price) * 100
                result['slippage_info'] = {
                    'expected_entry': entry_price,
                    'actual_entry': actual_entry,
                    'slippage_pct': round(slippage_pct, 4),
                }
            
            # Retry mechanism cho set SL/TP (tối đa 3 lần)
            max_retries = 3
            sl_tp_result = None
            
            for retry_count in range(max_retries):
                sl_tp_result = set_position_sl_tp(
                    exchange=exchange,
                    symbol=clean_sym,
                    side=side.lower(),
                    stop_loss=adjusted_sl,
                    take_profit=adjusted_tp,
                )
                
                if sl_tp_result['success']:
                    result['sl_tp_result'] = sl_tp_result
                    break
                else:
                    # Nếu lỗi và còn retry, đợi 2 giây rồi thử lại
                    if retry_count < max_retries - 1:
                        time.sleep(2)
                    else:
                        # Hết lần retry, log error
                        result['sl_tp_warning'] = sl_tp_result.get('error', 'Failed to set SL/TP')
                        result['sl_tp_retries'] = retry_count + 1
            
            total_execution_time = time.time() - execution_start
            result['timing'] = {
                'order_execution_seconds': round(order_execution_time, 3),
                'total_execution_seconds': round(total_execution_time, 3),
            }
            
            if signal_timestamp is not None:
                signal_to_order_filled = order_filled_time - signal_timestamp
                result['timing']['signal_to_order_seconds'] = round(signal_to_order_filled, 3)
                result['timing']['signal_to_execution_start_seconds'] = round(signal_to_execution_start, 3)
        
        return result
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'symbol': clean_symbol(symbol),
        }




if __name__ == '__main__':
    print("="*70)
    print("Test validate_and_place_order()")
    print("="*70)
    
    print("\n[TEST 1] LONG với RR 1:2 (auto TP)")
    result = validate_and_place_order(
        symbol='BTCUSDT_1m',
        side='sell',
        entry_price=78532,
        stop_loss=79000,
        leverage=20,
        risk_per_trade=50,
        risk_reward_ratio=2.0,
    )
    print(format_market_order_message(result))
