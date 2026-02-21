import ccxt

from src.live.binance.exchange import calculate_position_size, get_exchange
from src.live.binance.getprice import parse_symbol


def calculate_take_profit(entry_price: float, stop_loss: float, rr_ratio: float, side: str) -> float:
    """Tính take profit theo RR ratio"""
    if side.lower() == 'buy':
        risk = entry_price - stop_loss
        return entry_price + (risk * rr_ratio)
    risk = stop_loss - entry_price
    return entry_price - (risk * rr_ratio)


def close_position_market(exchange: ccxt.binance, symbol: str, side: str, quantity: float) -> dict:
    """Đóng vị thế bằng market order (reduce-only)"""
    close_side = 'sell' if side.lower() == 'buy' else 'buy'
    try:
        order = exchange.create_order(
            symbol=symbol,
            type='MARKET',
            side=close_side,
            amount=quantity,
            params={'reduceOnly': True},
        )
        return {'success': True, 'order': order}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def set_sl_tp_orders(
    exchange: ccxt.binance,
    symbol: str,
    side: str,
    quantity: float,
    stop_loss: float,
    take_profit: float,
) -> dict:
    """Đặt lệnh SL/TP cho Binance Futures"""
    exit_side = 'sell' if side.lower() == 'buy' else 'buy'

    try:
        sl_order = exchange.create_order(
            symbol=symbol,
            type='STOP_MARKET',
            side=exit_side,
            amount=quantity,
            params={
                'stopPrice': stop_loss,
                'reduceOnly': True,
                'workingType': 'MARK_PRICE',
            },
        )
    except Exception as e:
        return {'success': False, 'error': f'SL đặt thất bại: {e}'}

    try:
        tp_order = exchange.create_order(
            symbol=symbol,
            type='TAKE_PROFIT_MARKET',
            side=exit_side,
            amount=quantity,
            params={
                'stopPrice': take_profit,
                'reduceOnly': True,
                'workingType': 'MARK_PRICE',
            },
        )
    except Exception as e:
        try:
            exchange.cancel_order(sl_order.get('id'), symbol)
        except Exception:
            pass
        return {'success': False, 'error': f'TP đặt thất bại: {e}', 'sl_order': sl_order}

    return {'success': True, 'sl_order': sl_order, 'tp_order': tp_order}


def place_market_order(
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    rr_ratio: float,
    leverage: int,
    risk_per_trade: float,
) -> dict:
    """Đặt lệnh market futures với SL/TP theo RR ratio"""
    if entry_price is None or stop_loss is None:
        return {
            'success': False,
            'error': 'entry_price và stop_loss không được là None',
            'symbol': symbol,
        }

    exchange = get_exchange()
    formatted_symbol = parse_symbol(symbol)

    if formatted_symbol not in exchange.markets:
        exchange.load_markets(True)

    if formatted_symbol not in exchange.markets:
        return {
            'success': False,
            'error': f'Symbol không hợp lệ: {symbol} ({formatted_symbol})',
            'symbol': formatted_symbol,
        }

    if side.lower() == 'buy' and stop_loss >= entry_price:
        return {
            'success': False,
            'error': f'LONG: Stop loss ({stop_loss}) phải nhỏ hơn entry price ({entry_price})',
            'symbol': formatted_symbol,
        }

    if side.lower() == 'sell' and stop_loss <= entry_price:
        return {
            'success': False,
            'error': f'SHORT: Stop loss ({stop_loss}) phải lớn hơn entry price ({entry_price})',
            'symbol': formatted_symbol,
        }

    try:
        exchange.set_leverage(leverage, formatted_symbol)
    except Exception:
        pass

    take_profit = calculate_take_profit(entry_price, stop_loss, rr_ratio, side)

    position_info = calculate_position_size(
        entry_price=entry_price,
        stop_loss=stop_loss,
        leverage=leverage,
        risk_per_trade=risk_per_trade,
        side=side,
        skip_validation=False,
    )

    try:
        order = exchange.create_order(
            symbol=formatted_symbol,
            type='MARKET',
            side=side.lower(),
            amount=position_info['quantity'],
        )

        sl_tp_result = set_sl_tp_orders(
            exchange=exchange,
            symbol=formatted_symbol,
            side=side,
            quantity=position_info['quantity'],
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        if not sl_tp_result.get('success'):
            close_result = close_position_market(
                exchange=exchange,
                symbol=formatted_symbol,
                side=side,
                quantity=position_info['quantity'],
            )
            return {
                'success': False,
                'error': sl_tp_result.get('error', 'Không đặt được SL/TP'),
                'order': order,
                'close_result': close_result,
                'symbol': formatted_symbol,
                'entry_price': entry_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'position_info': position_info,
                'order_type': 'market',
            }

        return {
            'success': True,
            'order': order,
            'symbol': formatted_symbol,
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'position_info': position_info,
            'order_type': 'market',
            'sl_tp_result': sl_tp_result,
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'symbol': formatted_symbol,
            'position_info': position_info,
        }


def place_limit_order(
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    rr_ratio: float,
    leverage: int,
    risk_per_trade: float,
) -> dict:
    """Đặt lệnh limit futures với SL/TP theo RR ratio"""
    if entry_price is None or stop_loss is None:
        return {
            'success': False,
            'error': 'entry_price và stop_loss không được là None',
            'symbol': symbol,
        }

    exchange = get_exchange()
    formatted_symbol = parse_symbol(symbol)

    if formatted_symbol not in exchange.markets:
        exchange.load_markets(True)

    if formatted_symbol not in exchange.markets:
        return {
            'success': False,
            'error': f'Symbol không hợp lệ: {symbol} ({formatted_symbol})',
            'symbol': formatted_symbol,
        }

    if side.lower() == 'buy' and stop_loss >= entry_price:
        return {
            'success': False,
            'error': f'LONG: Stop loss ({stop_loss}) phải nhỏ hơn entry price ({entry_price})',
            'symbol': formatted_symbol,
        }

    if side.lower() == 'sell' and stop_loss <= entry_price:
        return {
            'success': False,
            'error': f'SHORT: Stop loss ({stop_loss}) phải lớn hơn entry price ({entry_price})',
            'symbol': formatted_symbol,
        }

    try:
        exchange.set_leverage(leverage, formatted_symbol)
    except Exception:
        pass

    take_profit = calculate_take_profit(entry_price, stop_loss, rr_ratio, side)

    position_info = calculate_position_size(
        entry_price=entry_price,
        stop_loss=stop_loss,
        leverage=leverage,
        risk_per_trade=risk_per_trade,
        side=side,
        skip_validation=False,
    )

    try:
        order = exchange.create_order(
            symbol=formatted_symbol,
            type='LIMIT',
            side=side.lower(),
            amount=position_info['quantity'],
            price=entry_price,
            params={
                'timeInForce': 'GTC',
            },
        )

        sl_tp_result = set_sl_tp_orders(
            exchange=exchange,
            symbol=formatted_symbol,
            side=side,
            quantity=position_info['quantity'],
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        if not sl_tp_result.get('success'):
            try:
                exchange.cancel_order(order.get('id'), formatted_symbol)
            except Exception:
                pass
            return {
                'success': False,
                'error': sl_tp_result.get('error', 'Không đặt được SL/TP'),
                'order': order,
                'symbol': formatted_symbol,
                'entry_price': entry_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'position_info': position_info,
                'order_type': 'limit',
            }

        return {
            'success': True,
            'order': order,
            'symbol': formatted_symbol,
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'position_info': position_info,
            'order_type': 'limit',
            'sl_tp_result': sl_tp_result,
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'symbol': formatted_symbol,
            'position_info': position_info,
        }
