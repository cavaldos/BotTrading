"""
Xử lý đặt lệnh market order trên Bybit
- Validate symbol
- Đặt lệnh market với stop loss
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


def validate_and_place_order(
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float = None,
    leverage: int = 20,
    risk_usd: float = 5,
) -> dict:
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
        
        print(f"\n✓ Symbol {symbol} hợp lệ ({formatted_symbol})")
        
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
            print(f"\n⏳ Waiting for position to settle... (2s)")
            time.sleep(2)
            
            # Đặt SL/TP cho position
            print(f"\n📌 Setting SL/TP...")
            sl_tp_result = set_position_sl_tp(
                exchange=exchange,
                symbol=clean_sym,
                side=side.lower(),
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
            
            if sl_tp_result['success']:
                result['sl_tp_result'] = sl_tp_result
                print(f"✓ SL/TP đã được đặt thành công!")
            else:
                print(f"⚠️  Cảnh báo: Không thể đặt SL/TP - {sl_tp_result.get('error', 'Unknown error')}")
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
    
    result = validate_and_place_order(
        symbol='BTC_1m',
        side='buy',
        entry_price=89853.42,
        stop_loss=89796.57,
        take_profit=91015.12,
        leverage=50,
        risk_usd=5,
    )
    
    print(f"\nResult:")
    print(f"  Success: {result['success']}")
    if result['success']:
        print(f"  Order ID: {result.get('order', {}).get('id', 'N/A')}")
        print(f"  Position: {result.get('position_info', {})}")
    else:
        print(f"  Error: {result['error']}")
