"""
Handle placing market orders on OKX
- Validate symbol
- Place market orders with stop loss
"""

import time
from pathlib import Path

from src.trade.okx.exchange import (
    get_exchange,
    format_symbol,
    validate_symbol,
    place_market_order_with_sl,
    set_position_sl_tp,
    set_position_mode,
    set_leverage_and_margin_mode,
)
from src.trade.okx.risk_calculator import clean_symbol
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
    margin_mode: str = 'isolated',
    hedge_mode: bool = True,
) -> dict:
    """
    Validate và đặt lệnh market order trên OKX với SL/TP.
    
    Args:
        symbol: Symbol giao dịch
        side: 'buy' (LONG) hoặc 'sell' (SHORT)
        entry_price: Giá entry dự kiến
        stop_loss: Giá stop loss
        take_profit: Giá take profit (nếu None sẽ tự động tính theo RR ratio)
        leverage: Đòn bẩy
        risk_per_trade: Rủi ro USD
        risk_reward_ratio: Tỉ lệ Risk:Reward (mặc định 2.0 = RR 1:2)
        margin_mode: 'isolated' hoặc 'cross' (default 'isolated')
        hedge_mode: True để bật hedge mode (long/short cùng lúc)
    
    Returns:
        dict: Kết quả đặt lệnh
    """
    try:
        # Convert Binance format (BTCUSDT_1m) → CCXT format (BTC/USDT)
        clean_sym = clean_symbol(symbol, to_ccxt_format=True) 
        
        # Get exchange instance
        exchange = get_exchange()
        
        # Format symbol theo chuẩn OKX (BTC/USDT → BTC-USDT-SWAP)
        formatted_symbol = format_symbol(clean_sym)
        
        # Validate symbol
        if not validate_symbol(exchange, formatted_symbol):
            return {
                'success': False,
                'error': f'Symbol không hợp lệ: {symbol} ({formatted_symbol})',
                'symbol': clean_sym,
                'exchange': 'OKX',
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
            risk_per_trade=risk_per_trade,
            margin_mode=margin_mode,
            hedge_mode=hedge_mode,
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
                margin_mode=margin_mode,
            )
            
            if sl_tp_result['success']:
                result['sl_tp_result'] = sl_tp_result
            else:
                result['sl_tp_warning'] = sl_tp_result.get('error', 'Failed to set SL/TP')
        
        # Thêm thông tin bổ sung
        result['exchange'] = 'OKX'
        if take_profit:
            result['take_profit'] = take_profit
        
        return result
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'symbol': clean_symbol(symbol),
            'exchange': 'OKX',
        }

def okx_format_order_message(result: dict) -> str:
    """
    Format kết quả đặt lệnh thành message dễ đọc.
    
    Args:
        result: Dict kết quả từ validate_and_place_order()
    
    Returns:
        str: Message đã format
    """
    if not result.get('success'):
        error = result.get('error', 'Unknown error')
        return f"❌ THẤT BẠI\n└─ Lỗi: {error}"
    
    # Lấy thông tin
    symbol = result.get('symbol', 'N/A')
    entry_price = result.get('entry_price', 0)
    stop_loss = result.get('stop_loss', 0)
    take_profit = result.get('take_profit', 0)
    
    # Lấy side từ position_info
    position_info = result.get('position_info', {})
    side = position_info.get('side', 'N/A').upper()
    leverage = position_info.get('leverage', 'N/A')
    contracts = result.get('contracts', position_info.get('quantity', 0))
    
    # Tính Risk/Reward ratio
    rr_ratio = 0
    if side == 'BUY':  # LONG
        risk = abs(entry_price - stop_loss) if entry_price and stop_loss else 0
        reward = abs(take_profit - entry_price) if take_profit and entry_price else 0
    else:  # SHORT
        risk = abs(stop_loss - entry_price) if stop_loss and entry_price else 0
        reward = abs(entry_price - take_profit) if entry_price and take_profit else 0
    
    if risk > 0:
        rr_ratio = reward / risk
    
    # Format message
    side_emoji = "🟢" if side == "BUY" else "🔴"
    side_text = "LONG" if side == "BUY" else "SHORT"
    
    message = f"""✅ THÀNH CÔNG

{side_emoji} {side_text} {symbol}
├─ Leverage: {leverage}x
├─ Contracts: {contracts}
│
├─ Entry:  ${entry_price:,.4f}
├─ SL:     ${stop_loss:,.4f}
├─ TP:     ${take_profit:,.4f}
│
└─ RR:     1:{rr_ratio:.2f}"""
    
    return message


if __name__ == "__main__":
    # Example usage
    order_result = validate_and_place_order(
        symbol="BTCUSDT_1m",
        side="buy",
        entry_price=89647,
        stop_loss=88864,
        take_profit=None,
        leverage=10,
        risk_per_trade=10,
        risk_reward_ratio=2.0,
    )
    print(order_result)
    print("\n" + "="*40 + "\n")
    print(okx_format_order_message(order_result))


