"""
Handle placing market orders on Bybit
- Validate symbol
- Place market orders with stop loss
"""

import time
from pathlib import Path

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
    risk_usd: float = 5,
    risk_reward_ratio: float = 2.0,
) -> dict:
    """
    Args:
        symbol: Symbol giao dịch
        side: 'buy' (LONG) hoặc 'sell' (SHORT)
        entry_price: Giá entry dự kiến
        stop_loss: Giá stop loss
        take_profit: Giá take profit (nếu None sẽ tự động tính theo RR ratio)
        leverage: Đòn bẩy
        risk_usd: Rủi ro USD
        risk_reward_ratio: Tỉ lệ Risk:Reward (mặc định 2.0 = RR 1:2)
    
    Returns:
        dict: Kết quả đặt lệnh
    """
    try:
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
        
        # Đặt lệnh market order
        result = place_market_order_with_sl(
            exchange=exchange,
            symbol=clean_sym,
            side=side.lower(),
            leverage=leverage,
            stop_loss=stop_loss,
            risk_usd=risk_usd,
        )
        
        if result['success']:
            # Đợi position settle trên server (1-2 giây)
            time.sleep(2)
            
            # Đặt SL/TP cho position
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
        
        # Thêm thông tin TP vào result
        if take_profit:
            result['take_profit'] = take_profit
        
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
    
    # Test LONG với RR 1:2 (mặc định)
    print("\n[TEST 1] LONG với RR 1:2 (auto TP)")
    result = validate_and_place_order(
        symbol='BTC_1m',
        side='buy',
        entry_price=88599,
        stop_loss=88019,  # Risk = 1000
        # TP auto = 100000 + (1000 * 2) = 102000
        leverage=20,
        risk_usd=5,
    )
    
    print(f"  Entry: {result.get('entry_price', 'N/A')}")
    print(f"  Stop Loss: {result.get('stop_loss', 'N/A')}")
    print(f"  Take Profit: {result.get('take_profit', 'N/A')}")
    print(f"  Success: {result['success']}")
    if not result['success']:
        print(f"  Error: {result['error']}")
    