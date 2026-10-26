"""
Market orders on Lighter (same functions and result keys as src/exchanges/bybit/market_order.py)
- Validate symbol
- Market entry with SL and TP sent together (TP from the RR ratio when not given)
"""

import time
from typing import Optional

from src.exchanges.messages import format_market_order_message as _format_common_market_message
from src.exchanges.lighter.exchange import (
    clean_symbol,
    get_exchange,
    place_market_order_with_sl,
    validate_symbol,
)
from src.utils.rounding import round_price


def _take_profit_from_rr(side: str, entry_price: float, stop_loss: float, risk_reward_ratio: float) -> float:
    risk = abs(entry_price - stop_loss)
    if side.lower() in ('buy', 'long'):
        return round_price(entry_price + risk * risk_reward_ratio)
    return round_price(entry_price - risk * risk_reward_ratio)


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
        symbol: Symbol giao dịch (BTCUSDT, BTCUSDT_1m, BTC đều được)
        side: 'buy' (LONG) hoặc 'sell' (SHORT)
        entry_price: Giá entry dự kiến (dùng để tính khối lượng)
        stop_loss: Giá stop loss
        take_profit: Giá take profit (nếu None sẽ tự động tính theo RR ratio)
        leverage: Đòn bẩy
        risk_per_trade: Rủi ro USD
        risk_reward_ratio: Tỉ lệ Risk:Reward (mặc định 2.0 = RR 1:2)
        signal_timestamp: Thời điểm nhận tín hiệu (time.time()), dùng để đo latency

    Returns:
        dict: Kết quả đặt lệnh (cùng key với bản Bybit)
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

        market_symbol = clean_symbol(symbol)
        exchange = get_exchange()
        if not validate_symbol(exchange, market_symbol):
            return {
                'success': False,
                'error': f'Symbol không có trên Lighter: {symbol} ({market_symbol})',
                'symbol': market_symbol,
            }

        if take_profit is None:
            take_profit = _take_profit_from_rr(side, entry_price, stop_loss, risk_reward_ratio)

        result = place_market_order_with_sl(
            exchange=exchange,
            symbol=market_symbol,
            side=side,
            leverage=leverage,
            stop_loss=stop_loss,
            risk_per_trade=risk_per_trade,
            expected_entry=entry_price,
            take_profit=take_profit,
        )
        result['original_sl'] = stop_loss
        result['original_tp'] = take_profit

        if result.get('success'):
            order_filled_time = time.time()
            actual_entry = result.get('entry_price') or entry_price
            quantity = result.get('contracts') or 0
            stop = result.get('stop_loss') or stop_loss
            target = result.get('take_profit') or take_profit

            # SL/TP stay at the signal's levels (like Bybit); a worse fill lowers the real RR
            result['final_quantity'] = quantity
            result['sl_tp_adjustment'] = {
                'mode': 'keep_original_levels',
                'expected_entry': round_price(entry_price),
                'actual_entry': round_price(actual_entry),
                'expected_sl': round_price(stop_loss),
                'adjusted_sl': round_price(stop),
                'expected_tp': round_price(take_profit),
                'adjusted_tp': round_price(target),
                'side': side.upper(),
            }
            result['actual_risk'] = round(quantity * abs(actual_entry - stop), 2)
            result['expected_profit'] = round(quantity * abs(target - actual_entry), 2)
            if entry_price > 0:
                result['slippage_info'] = {
                    'expected_entry': entry_price,
                    'actual_entry': actual_entry,
                    'slippage_pct': round((actual_entry - entry_price) / entry_price * 100, 4),
                }
            result['sl_tp_result'] = {
                'success': 'sl_tp_warning' not in result,
                'sl_order_id': result.get('sl_order_id'),
                'tp_order_id': result.get('tp_order_id'),
            }

            result['timing'] = {
                'order_execution_seconds': round(order_filled_time - execution_start, 3),
                'total_execution_seconds': round(time.time() - execution_start, 3),
            }
            if signal_timestamp is not None:
                result['timing']['signal_to_order_seconds'] = round(order_filled_time - signal_timestamp, 3)
                result['timing']['signal_to_execution_start_seconds'] = round(signal_to_execution_start, 3)

        return result

    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'symbol': clean_symbol(symbol),
        }


def _warning_lines(order_result: dict) -> str:
    warnings = [
        order_result.get('sl_tp_warning'),
        order_result.get('leverage_warning'),
        (order_result.get('position_info') or {}).get('min_size_notice'),
    ]
    return "".join(f"\n⚠️ {text}" for text in warnings if text)


def format_market_order_message(order_result: dict) -> str:
    """Same message as Bybit, plus Lighter warnings (SL/TP not seen, leverage, minimum size)."""
    message = _format_common_market_message(order_result)
    if order_result.get('success'):
        message += _warning_lines(order_result)
    return message


if __name__ == '__main__':
    print("Đặt lệnh THẬT trên Lighter. Sửa tham số rồi bỏ comment để chạy.")
    # result = validate_and_place_order(
    #     symbol='BTCUSDT_1m',
    #     side='buy',
    #     entry_price=80000,
    #     stop_loss=79000,
    #     leverage=20,
    #     risk_per_trade=1,
    #     risk_reward_ratio=2.0,
    # )
    # print(format_market_order_message(result))
