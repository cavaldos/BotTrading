"""
Handle placing market and limit orders on Bybit
- Validate symbol
- Place market orders with stop loss
- Place limit orders with stop loss and take profit
"""

import time
from pathlib import Path

from src.trade.bybit.exchange import (
    get_exchange,
    format_symbol,
    validate_symbol,
    place_market_order_with_sl,
    place_limit_order_with_sl,
    set_position_sl_tp,
    cancel_order,
    get_order_status,
)
from src.trade.bybit.risk_calculator import clean_symbol
from src.utils.rounding import round_price


def validate_and_place_order(
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float = None,
    leverage: int = 20,
    risk_per_trade: float = 5,
    risk_reward_ratio: float = 2.0,
    signal_timestamp: float = None,
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
        # Bắt đầu đếm thời gian thực thi
        execution_start = time.time()
        
        # Nếu có signal_timestamp, tính thời gian từ khi nhận tín hiệu
        signal_to_execution_start = None
        if signal_timestamp is not None:
            signal_to_execution_start = execution_start - signal_timestamp
        # Validate required inputs
        if entry_price is None or stop_loss is None:
            return {
                'success': False,
                'error': f'entry_price và stop_loss không được là None (entry={entry_price}, sl={stop_loss})',
                'symbol': symbol,
            }
        
        # Làm sạch symbol
        clean_sym = clean_symbol(symbol)
        
        # Get exchange instance
        exchange = get_exchange()
        
        # Format symbol theo chuẩn ccxt
        formatted_symbol = format_symbol(clean_sym)
        
        # Validate symbol
        if not validate_symbol(exchange, formatted_symbol):
            return {
                'success': False,
                'error': f'Symbol không hợp lệ: {symbol} ({formatted_symbol})',
                'symbol': clean_sym,
            }
        
        original_stop_loss = stop_loss
        original_take_profit = take_profit

        # Tính take_profit tự động nếu không được truyền vào
        if take_profit is None:
            if side.lower() == 'buy':  # LONG
                risk = entry_price - stop_loss
                reward = risk * risk_reward_ratio
                take_profit = entry_price + reward
            else:  # SHORT (sell)
                risk = stop_loss - entry_price
                reward = risk * risk_reward_ratio
                take_profit = entry_price - reward
            
            take_profit = round_price(take_profit)

        original_take_profit = take_profit
        
        # Đặt lệnh market order
        # Tính volume dựa trên entry/SL dự kiến để đảm bảo risk = risk_per_trade
        # Volume này sẽ được giữ cố định, không điều chỉnh khi giá thực tế khác
        result = place_market_order_with_sl(
            exchange=exchange,
            symbol=clean_sym,
            side=side.lower(),
            leverage=leverage,
            stop_loss=stop_loss,
            risk_per_trade=risk_per_trade,
            expected_entry=entry_price,  # Dùng entry dự kiến để tính volume cố định
        )

        result['original_sl'] = original_stop_loss
        result['original_tp'] = original_take_profit
        
        if result['success']:
            # Ghi nhận thời điểm đặt lệnh thành công (trước khi sleep)
            order_filled_time = time.time()
            order_execution_time = order_filled_time - execution_start
            
            # Đợi position settle trên server
            time.sleep(2)
            
            # Lấy giá fill thực tế từ sàn
            actual_entry = result.get('entry_price') or entry_price
            position_info = result.get('position_info', {})
            current_quantity = position_info.get('quantity', 0)
            
            # Giữ nguyên SL/TP gốc, không điều chỉnh theo slippage
            result['stop_loss'] = stop_loss
            result['take_profit'] = take_profit
            result['final_quantity'] = current_quantity
            
            # Tính actual risk và expected profit với giá entry thực tế
            actual_distance_to_sl = abs(actual_entry - stop_loss)
            actual_risk = current_quantity * actual_distance_to_sl
            result['actual_risk'] = round(actual_risk, 2)
            
            actual_distance_to_tp = abs(take_profit - actual_entry)
            expected_profit = current_quantity * actual_distance_to_tp
            result['expected_profit'] = round(expected_profit, 2)
            
            # Tính slippage để thông báo (không điều chỉnh)
            if entry_price > 0:
                slippage_pct = ((actual_entry - entry_price) / entry_price) * 100
                result['slippage_info'] = {
                    'expected_entry': entry_price,
                    'actual_entry': actual_entry,
                    'slippage_pct': round(slippage_pct, 4),
                }
            
            # Đặt SL/TP gốc (không điều chỉnh)
            sl_tp_result = set_position_sl_tp(
                exchange=exchange,
                symbol=clean_sym,
                side=side.lower(),
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
            
            if sl_tp_result['success']:
                result['sl_tp_result'] = sl_tp_result
            else:
                result['sl_tp_warning'] = sl_tp_result.get('error', 'Failed to set SL/TP')
            
            # Thêm thông tin timing
            total_execution_time = time.time() - execution_start
            result['timing'] = {
                'order_execution_seconds': round(order_execution_time, 3),
                'total_execution_seconds': round(total_execution_time, 3),
            }
            
            # Thêm thời gian từ signal nếu có
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

def bybit_format_order_message(order_result: dict) -> str:
    """
    Format kết quả đặt lệnh thành message dễ đọc.
    
    Args:
        order_result: Dict kết quả từ validate_and_place_order()
    
    Returns:
        str: Message đã format
    """
    if not order_result.get('success'):
        error = order_result.get('error', 'Unknown error')
        return f"❌ THẤT BẠI\n└─ Lỗi: {error}"
    
    # Lấy thông tin
    symbol = order_result.get('symbol', 'N/A')
    entry_price = order_result.get('entry_price') or 0
    stop_loss = order_result.get('stop_loss') or 0
    take_profit = order_result.get('take_profit') or 0
    
    # Lấy thông tin slippage (chỉ để hiển thị, không điều chỉnh)
    slippage_info = order_result.get('slippage_info', {})
    slippage_pct = slippage_info.get('slippage_pct', 0)
    expected_entry = slippage_info.get('expected_entry', entry_price)
    
    # Lấy side từ position_info
    position_info = order_result.get('position_info', {})
    side = position_info.get('side', 'N/A').upper()
    leverage = position_info.get('leverage', 'N/A')
    contracts = order_result.get('contracts', position_info.get('quantity', 0))
    
    # Tính Risk/Reward ratio thực tế
    rr_ratio = 0
    if side == 'BUY':  # LONG
        risk = abs(entry_price - stop_loss) if entry_price and stop_loss else 0
        reward = abs(take_profit - entry_price) if take_profit and entry_price else 0
    else:  # SHORT
        risk = abs(stop_loss - entry_price) if stop_loss and entry_price else 0
        reward = abs(entry_price - take_profit) if entry_price and take_profit else 0
    
    if risk > 0 and reward > 0:
        rr_ratio = reward / risk
    
    # Format message
    side_emoji = "🟢" if side == "BUY" else "🔴"
    side_text = "LONG" if side == "BUY" else "SHORT"
    
    # Thêm thông tin slippage nếu có
    slippage_line = ""
    if slippage_pct != 0:
        slippage_emoji = "⚠️" if abs(slippage_pct) > 0.3 else "ℹ️"
        slippage_line = f"\n├─ Slippage: {slippage_emoji} {slippage_pct:+.3f}%"
    
    # Lấy actual risk và expected profit
    actual_risk = order_result.get('actual_risk', 0)
    expected_profit = order_result.get('expected_profit', 0)
    
    # Lấy thông tin timing
    timing = order_result.get('timing', {})
    timing_line = ""
    if timing:
        order_exec = timing.get('order_execution_seconds', 0)
        signal_to_order = timing.get('signal_to_order_seconds')
        
        if signal_to_order is not None:
            # Có signal timestamp - hiển thị đầy đủ
            timing_line = f"\n│\n├─ ⏱️ Signal → Order: {signal_to_order:.2f}s"
            timing_line += f"\n├─ ⏱️ Order Execution: {order_exec:.2f}s"
        else:
            # Chỉ có thời gian execution
            timing_line = f"\n│\n├─ ⏱️ Order Execution: {order_exec:.2f}s"
    
    message = f"""✅ THÀNH CÔNG

{side_emoji} {side_text} {symbol}
├─ Leverage: {leverage}x
├─ Contracts: {contracts}
│
├─ Expected Entry: ${expected_entry:,.4f}
├─ Actual Entry:   ${entry_price:,.4f}{slippage_line}
│
├─ SL:     ${stop_loss:,.4f}
├─ TP:     ${take_profit:,.4f}
│
├─ Risk:   ${actual_risk:.2f}
├─ Profit: ${expected_profit:.2f}
└─ RR:     1:{rr_ratio:.2f}{timing_line}"""
    
    return message


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
        # Bắt đầu đếm thời gian thực thi
        execution_start = time.time()
        
        # Nếu có signal_timestamp, tính thời gian từ khi nhận tín hiệu
        signal_to_execution_start = None
        if signal_timestamp is not None:
            signal_to_execution_start = execution_start - signal_timestamp
        
        # Validate required inputs
        if entry_price is None or stop_loss is None:
            return {
                'success': False,
                'error': f'entry_price và stop_loss không được là None (entry={entry_price}, sl={stop_loss})',
                'symbol': symbol,
                'order_type': 'limit',
            }
        
        # Làm sạch symbol
        clean_sym = clean_symbol(symbol)
        
        # Get exchange instance
        exchange = get_exchange()
        
        # Format symbol theo chuẩn ccxt
        formatted_symbol = format_symbol(clean_sym)
        
        # Validate symbol
        if not validate_symbol(exchange, formatted_symbol):
            return {
                'success': False,
                'error': f'Symbol không hợp lệ: {symbol} ({formatted_symbol})',
                'symbol': clean_sym,
                'order_type': 'limit',
            }
        
        original_stop_loss = stop_loss
        original_take_profit = take_profit

        # Tính take_profit tự động nếu không được truyền vào
        if take_profit is None:
            if side.lower() == 'buy':  # LONG
                risk = entry_price - stop_loss
                reward = risk * risk_reward_ratio
                take_profit = entry_price + reward
            else:  # SHORT (sell)
                risk = stop_loss - entry_price
                reward = risk * risk_reward_ratio
                take_profit = entry_price - reward
            
            take_profit = round_price(take_profit)

        original_take_profit = take_profit
        
        # Đặt lệnh limit order
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
            # Ghi nhận thời điểm đặt lệnh thành công
            order_placed_time = time.time()
            order_execution_time = order_placed_time - execution_start
            
            # Lấy thông tin từ result
            position_info = result.get('position_info', {})
            current_quantity = position_info.get('quantity', 0)
            
            # Cập nhật thông tin
            result['stop_loss'] = stop_loss
            result['take_profit'] = take_profit
            result['final_quantity'] = current_quantity
            result['contracts'] = current_quantity
            
            # Tính expected risk và profit (với giá limit entry)
            distance_to_sl = abs(entry_price - stop_loss)
            expected_risk = current_quantity * distance_to_sl
            result['expected_risk'] = round(expected_risk, 2)
            
            distance_to_tp = abs(take_profit - entry_price)
            expected_profit = current_quantity * distance_to_tp
            result['expected_profit'] = round(expected_profit, 2)
            
            # Thêm thông tin timing
            total_execution_time = time.time() - execution_start
            result['timing'] = {
                'order_execution_seconds': round(order_execution_time, 3),
                'total_execution_seconds': round(total_execution_time, 3),
            }
            
            # Thêm thời gian từ signal nếu có
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


def bybit_format_limit_order_message(order_result: dict) -> str:
    """
    Format kết quả đặt lệnh LIMIT thành message dễ đọc.
    
    Args:
        order_result: Dict kết quả từ validate_and_place_limit_order()
    
    Returns:
        str: Message đã format
    """
    if not order_result.get('success'):
        error = order_result.get('error', 'Unknown error')
        return f"❌ THẤT BẠI\n└─ Lỗi: {error}"
    
    # Lấy thông tin
    symbol = order_result.get('symbol', 'N/A')
    entry_price = order_result.get('entry_price') or 0
    current_price = order_result.get('current_price') or 0
    stop_loss = order_result.get('stop_loss') or 0
    take_profit = order_result.get('take_profit') or 0
    order_id = order_result.get('order_id', 'N/A')
    order_status = order_result.get('order_status', 'open')
    time_in_force = order_result.get('time_in_force', 'GTC')
    
    # Lấy side từ position_info
    position_info = order_result.get('position_info', {})
    side = position_info.get('side', 'N/A').upper()
    leverage = position_info.get('leverage', 'N/A')
    contracts = order_result.get('contracts', position_info.get('quantity', 0))
    
    # Tính Risk/Reward ratio
    rr_ratio = 0
    if side == 'BUY':  # LONG
        risk = abs(entry_price - stop_loss) if entry_price and stop_loss else 0
        reward = abs(take_profit - entry_price) if take_profit and entry_price else 0
    else:  # SHORT
        risk = abs(stop_loss - entry_price) if stop_loss and entry_price else 0
        reward = abs(entry_price - take_profit) if entry_price and take_profit else 0
    
    if risk > 0 and reward > 0:
        rr_ratio = reward / risk
    
    # Format message
    side_emoji = "🟢" if side == "BUY" else "🔴"
    side_text = "LONG" if side == "BUY" else "SHORT"
    
    # Tính % chênh lệch giữa limit price và current price
    price_diff_pct = 0
    if current_price > 0:
        price_diff_pct = ((entry_price - current_price) / current_price) * 100
    
    # Lấy expected risk và profit
    expected_risk = order_result.get('expected_risk', 0)
    expected_profit = order_result.get('expected_profit', 0)
    
    # Lấy thông tin timing
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
    
    # Warning nếu có
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
└─ RR:     1:{rr_ratio:.2f}{timing_line}"""
    
    return message


def cancel_limit_order(symbol: str, order_id: str) -> dict:
    """
    Hủy lệnh limit đang chờ.
    
    Args:
        symbol: Symbol giao dịch
        order_id: ID của lệnh cần hủy
    
    Returns:
        dict: Kết quả hủy lệnh
    """
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


def check_limit_order_status(symbol: str, order_id: str) -> dict:
    """
    Kiểm tra trạng thái lệnh limit.
    
    Args:
        symbol: Symbol giao dịch
        order_id: ID của lệnh cần kiểm tra
    
    Returns:
        dict: Trạng thái lệnh (open, closed, canceled, expired, filled)
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
    print("Test validate_and_place_order()")
    print("="*70)
    
    # Test LONG với RR 1:2 (mặc định)
    print("\n[TEST 1] LONG với RR 1:2 (auto TP)")
    result = validate_and_place_order(
        symbol='BCHUSDT_1m',
        side='sell',
        entry_price=582.1000,
        stop_loss=582.2840,  # Risk = 1000
        # TP auto = 100000 + (1000 * 2) = 102000
        leverage=20,
        risk_per_trade=5,
        risk_reward_ratio=2.0,
    )
    print(bybit_format_order_message(result))
    
