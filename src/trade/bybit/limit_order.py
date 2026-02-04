"""
Handle placing limit orders on Bybit
- Validate symbol
- Place limit orders with stop loss and take profit
- Cancel and check order status
"""

import time

from src.trade.bybit.exchange import (
    get_exchange,
    format_symbol,
    validate_symbol,
    place_limit_order_with_sl,
    cancel_order,
    get_order_status,
)
from src.trade.bybit.risk_calculator import clean_symbol
from src.utils.rounding import round_price


def validate_and_place_limit_order(
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float = None,
    leverage: int = 20,
    risk_per_trade: float = 5,
    risk_reward_ratio: float = 2.0,
    signal_timestamp: float = None,
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


def format_limit_order_message(order_result: dict) -> str:
    """
    Format kết quả đặt lệnh LIMIT thành message dễ đọc.
    """
    if not order_result.get('success'):
        error = order_result.get('error', 'Unknown error')
        return f"❌ THẤT BẠI\n└─ Lỗi: {error}"
    
    symbol = order_result.get('symbol', 'N/A')
    entry_price = order_result.get('entry_price') or 0
    current_price = order_result.get('current_price') or 0
    stop_loss = order_result.get('stop_loss') or 0
    take_profit = order_result.get('take_profit') or 0
    order_id = order_result.get('order_id', 'N/A')
    order_status = order_result.get('order_status') or 'open'
    time_in_force = order_result.get('time_in_force') or 'GTC'
    
    position_info = order_result.get('position_info', {})
    side = position_info.get('side', 'N/A').upper()
    leverage = position_info.get('leverage', 'N/A')
    contracts = order_result.get('contracts', position_info.get('quantity', 0))
    
    # Tính RR thực tế từ entry, SL, TP
    actual_rr_ratio = 0
    if side == 'BUY':
        risk = abs(entry_price - stop_loss) if entry_price and stop_loss else 0
        reward = abs(take_profit - entry_price) if take_profit and entry_price else 0
    else:
        risk = abs(stop_loss - entry_price) if stop_loss and entry_price else 0
        reward = abs(entry_price - take_profit) if entry_price and take_profit else 0
    
    if risk > 0 and reward > 0:
        actual_rr_ratio = reward / risk
    
    side_emoji = "🟢" if side == "BUY" else "🔴"
    side_text = "LONG" if side == "BUY" else "SHORT"
    
    price_diff_pct = 0
    if current_price > 0:
        price_diff_pct = ((entry_price - current_price) / current_price) * 100
    
    expected_risk = order_result.get('expected_risk', 0)
    expected_profit = order_result.get('expected_profit', 0)
    
    timing = order_result.get('timing', {})
    timing_line = ""
    if timing:
        order_exec = timing.get('order_execution_seconds', 0)
        signal_to_order = timing.get('signal_to_order_seconds')
        
        if signal_to_order is not None:
            timing_line = f"\n│\n├─ ⏱️ Signal → Order: {signal_to_order:.2f}s"
            timing_line += f"\n├─ ⏱️ Order Placed: {order_exec:.2f}s"
        else:
            timing_line = f"\n│\n├─ ⏱️ Order Placed: {order_exec:.2f}s"
    
    price_warning = order_result.get('price_warning', '')
    warning_line = ""
    if price_warning:
        warning_line = f"\n├─ ⚠️ {price_warning}"
    
    message = f"""📝 LIMIT ORDER PLACED

{side_emoji} {side_text} {symbol}
├─ Order ID: {order_id}
├─ Status: {order_status.upper()}
├─ Time in Force: {time_in_force}
├─ Leverage: {leverage}x
├─ Contracts: {contracts}
│
├─ Limit Price:   ${entry_price:,.4f}
├─ Current Price: ${current_price:,.4f} ({price_diff_pct:+.2f}%){warning_line}
│
├─ SL:     ${stop_loss:,.4f}
├─ TP:     ${take_profit:,.4f}
│
├─ Risk:   ${expected_risk:.2f}
├─ Profit: ${expected_profit:.2f}
└─ RR:     1:{actual_rr_ratio:.2f}{timing_line}"""
    
    return message


# Backward compatibility alias
bybit_format_limit_order_message = format_limit_order_message


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
    from src.trade.bybit.tool.order_manager import cancel_order_by_id
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