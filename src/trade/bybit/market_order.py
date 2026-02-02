"""
Handle placing market orders on Bybit
- Validate symbol
- Place market orders with stop loss
"""

import time

from src.trade.bybit.exchange import (
    get_exchange,
    format_symbol,
    validate_symbol,
    place_market_order_with_sl,
    set_position_sl_tp,
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
            
            time.sleep(2)
            
            actual_entry = result.get('entry_price') or entry_price
            position_info = result.get('position_info', {})
            current_quantity = position_info.get('quantity', 0)
            
            result['stop_loss'] = stop_loss
            result['take_profit'] = take_profit
            result['final_quantity'] = current_quantity
            
            actual_distance_to_sl = abs(actual_entry - stop_loss)
            actual_risk = current_quantity * actual_distance_to_sl
            result['actual_risk'] = round(actual_risk, 2)
            
            actual_distance_to_tp = abs(take_profit - actual_entry)
            expected_profit = current_quantity * actual_distance_to_tp
            result['expected_profit'] = round(expected_profit, 2)
            
            if entry_price > 0:
                slippage_pct = ((actual_entry - entry_price) / entry_price) * 100
                result['slippage_info'] = {
                    'expected_entry': entry_price,
                    'actual_entry': actual_entry,
                    'slippage_pct': round(slippage_pct, 4),
                }
            
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


def format_market_order_message(order_result: dict) -> str:
    """
    Format kết quả đặt lệnh market thành message dễ đọc.
    """
    if not order_result.get('success'):
        error = order_result.get('error', 'Unknown error')
        return f"❌ THẤT BẠI\n└─ Lỗi: {error}"
    
    symbol = order_result.get('symbol', 'N/A')
    entry_price = order_result.get('entry_price') or 0
    stop_loss = order_result.get('stop_loss') or 0
    take_profit = order_result.get('take_profit') or 0
    
    slippage_info = order_result.get('slippage_info', {})
    slippage_pct = slippage_info.get('slippage_pct', 0)
    expected_entry = slippage_info.get('expected_entry', entry_price)
    
    position_info = order_result.get('position_info', {})
    side = position_info.get('side', 'N/A').upper()
    leverage = position_info.get('leverage', 'N/A')
    contracts = order_result.get('contracts', position_info.get('quantity', 0))
    
    rr_ratio = 0
    if side == 'BUY':
        risk = abs(entry_price - stop_loss) if entry_price and stop_loss else 0
        reward = abs(take_profit - entry_price) if take_profit and entry_price else 0
    else:
        risk = abs(stop_loss - entry_price) if stop_loss and entry_price else 0
        reward = abs(entry_price - take_profit) if entry_price and take_profit else 0
    
    if risk > 0 and reward > 0:
        rr_ratio = reward / risk
    
    side_emoji = "🟢" if side == "BUY" else "🔴"
    side_text = "LONG" if side == "BUY" else "SHORT"
    
    slippage_line = ""
    if slippage_pct != 0:
        slippage_emoji = "⚠️" if abs(slippage_pct) > 0.3 else "ℹ️"
        slippage_line = f"\n├─ Slippage: {slippage_emoji} {slippage_pct:+.3f}%"
    
    actual_risk = order_result.get('actual_risk', 0)
    expected_profit = order_result.get('expected_profit', 0)
    
    timing = order_result.get('timing', {})
    timing_line = ""
    if timing:
        order_exec = timing.get('order_execution_seconds', 0)
        signal_to_order = timing.get('signal_to_order_seconds')
        
        if signal_to_order is not None:
            timing_line = f"\n│\n├─ ⏱️ Signal → Order: {signal_to_order:.2f}s"
            timing_line += f"\n├─ ⏱️ Order Execution: {order_exec:.2f}s"
        else:
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


# Backward compatibility alias
bybit_format_order_message = format_market_order_message


if __name__ == '__main__':
    print("="*70)
    print("Test validate_and_place_order()")
    print("="*70)
    
    print("\n[TEST 1] LONG với RR 1:2 (auto TP)")
    result = validate_and_place_order(
        symbol='XRPUSDT_1m',
        side='sell',
        entry_price=1.6480,
        stop_loss=1.6444,
        leverage=20,
        risk_per_trade=50,
        risk_reward_ratio=1.0,
    )
    print(format_market_order_message(result))
