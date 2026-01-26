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
    close_position_market,
)
from src.trade.okx.risk_calculator import clean_symbol, adjust_levels_for_slippage, validate_slippage
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
    """
    try:
        # ✅ Validate required inputs TRƯỚC TIÊN
        if entry_price is None:
            return {
                'success': False,
                'error': 'entry_price không được là None',
                'symbol': symbol,
                'exchange': 'OKX',
            }
        
        if stop_loss is None:
            return {
                'success': False,
                'error': 'stop_loss không được là None',
                'symbol': symbol,
                'exchange': 'OKX',
            }
        
        # ✅ Đảm bảo entry_price và stop_loss là số
        try:
            entry_price = float(entry_price)
            stop_loss = float(stop_loss)
        except (TypeError, ValueError) as e:
            return {
                'success': False,
                'error': f'entry_price hoặc stop_loss không phải là số hợp lệ: {e}',
                'symbol': symbol,
                'exchange': 'OKX',
            }
        
        # Convert Binance format (BTCUSDT_1m) → CCXT format (BTC/USDT)
        clean_sym = clean_symbol(symbol, to_ccxt_format=True) 
        
        # Get exchange instance
        exchange = get_exchange()
        
        # Format symbol theo chuẩn OKX (BTC/USDT → BTC/USDT:USDT)
        formatted_symbol = format_symbol(clean_sym)
        
        # Validate symbol TRƯỚC khi đặt lệnh
        if not validate_symbol(exchange, formatted_symbol):
            return {
                'success': False,
                'error': f'Symbol không hợp lệ: {symbol} ({formatted_symbol})',
                'symbol': clean_sym,
                'exchange': 'OKX',
            }
        
        # Lấy giá hiện tại để validate
        ticker = exchange.fetch_ticker(formatted_symbol)
        current_price = ticker.get('last')
        
        # ✅ Validate current_price
        if current_price is None:
            return {
                'success': False,
                'error': f'Không lấy được giá hiện tại từ sàn cho {formatted_symbol}',
                'symbol': formatted_symbol,
                'exchange': 'OKX',
            }
        
        # ✅ Convert current_price sang float
        try:
            current_price = float(current_price)
        except (TypeError, ValueError):
            return {
                'success': False,
                'error': f'Giá từ sàn không hợp lệ: {current_price}',
                'symbol': formatted_symbol,
                'exchange': 'OKX',
            }
        
        # ✅ Validate entry_price != 0 để tránh chia cho 0
        if entry_price == 0:
            return {
                'success': False,
                'error': 'entry_price không được bằng 0',
                'symbol': formatted_symbol,
                'exchange': 'OKX',
            }
        
        # Validate entry_price có gần giá thị trường không (tránh nhầm symbol)
        price_diff_pct = abs(current_price - entry_price) / entry_price * 100
        if price_diff_pct > 5:  # Nếu chênh lệch > 5% thì cảnh báo
            return {
                'success': False,
                'error': f'Entry price ({entry_price}) chênh lệch quá lớn so với giá thị trường ({current_price}). '
                        f'Có thể bạn đang nhầm symbol? Diff: {price_diff_pct:.2f}%',
                'symbol': formatted_symbol,
                'current_price': current_price,
                'exchange': 'OKX',
            }
        
        # Validate SL logic trước khi đặt lệnh
        if side.lower() == 'buy':  # LONG
            if stop_loss >= entry_price:
                return {
                    'success': False,
                    'error': f'LONG: Stop loss ({stop_loss}) phải nhỏ hơn entry price ({entry_price})',
                    'symbol': formatted_symbol,
                    'exchange': 'OKX',
                }
        else:  # SHORT
            if stop_loss <= entry_price:
                return {
                    'success': False,
                    'error': f'SHORT: Stop loss ({stop_loss}) phải lớn hơn entry price ({entry_price})',
                    'symbol': formatted_symbol,
                    'exchange': 'OKX',
                }
        
        # Tính TP trước nếu chưa có
        if take_profit is None:
            if side.lower() == 'buy':  # LONG
                risk = entry_price - stop_loss
                take_profit = round_price(entry_price + (risk * risk_reward_ratio))
            else:  # SHORT
                risk = stop_loss - entry_price
                take_profit = round_price(entry_price - (risk * risk_reward_ratio))
        
        original_tp = take_profit
        
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
            # Đợi position settle trên server
            time.sleep(2)
            
            # ✅ Lấy giá fill thực tế với fallback an toàn
            actual_entry = result.get('entry_price')
            if actual_entry is None:
                actual_entry = entry_price
            
            try:
                actual_entry = float(actual_entry)
            except (TypeError, ValueError):
                actual_entry = entry_price
            
            # ✅ Validate actual_entry không lệch quá xa expected entry
            if actual_entry != 0 and entry_price != 0:
                actual_diff_pct = abs(actual_entry - entry_price) / entry_price * 100
                if actual_diff_pct > 2:  # Slippage > 2% là bất thường
                    close_result = close_position_market(
                        exchange=exchange,
                        symbol=clean_sym,
                        side=side.lower(),
                        contracts=result.get('contracts'),
                        margin_mode=margin_mode,
                        hedge_mode=result.get('hedge_mode', hedge_mode),
                    )
                    return {
                        'success': False,
                        'error': f'Slippage quá lớn ({actual_diff_pct:.2f}%). Đã đóng vị thế.',
                        'symbol': formatted_symbol,
                        'expected_entry': entry_price,
                        'actual_entry': actual_entry,
                        'close_result': close_result,
                        'exchange': 'OKX',
                    }
            
            # Kiểm tra slippage và điều chỉnh SL/TP
            slippage_info = adjust_levels_for_slippage(
                expected_entry=entry_price,
                actual_entry=actual_entry,
                expected_sl=stop_loss,
                expected_tp=take_profit,
                side=side.lower(),
            )
            
            adjusted_sl = slippage_info['adjusted_sl']
            adjusted_tp = slippage_info['adjusted_tp']
            
            # Thêm thông tin slippage vào result
            result['slippage_info'] = slippage_info
            result['original_sl'] = stop_loss
            result['original_tp'] = original_tp
            
            # Cảnh báo nếu slippage > 0.5%
            if not validate_slippage(slippage_info['slippage_pct'], max_slippage_pct=0.5):
                result['slippage_warning'] = f"⚠️ Slippage cao: {slippage_info['slippage_pct']:.3f}%"
            
            # ✅ Validate TP trước khi đặt SL/TP
            if side.lower() == 'buy':  # LONG
                if adjusted_tp <= actual_entry:
                    adjusted_tp = actual_entry * 1.01  # TP ít nhất +1%
            else:  # SHORT
                if adjusted_tp >= actual_entry:
                    adjusted_tp = actual_entry * 0.99  # TP ít nhất -1%
            
            adjusted_tp = round_price(adjusted_tp)
            
            # Đặt SL/TP
            sl_tp_result = set_position_sl_tp(
                exchange=exchange,
                symbol=clean_sym,
                side=side.lower(),
                stop_loss=adjusted_sl,
                take_profit=adjusted_tp,
                margin_mode=margin_mode,
            )

            if sl_tp_result['success']:
                result['sl_tp_result'] = sl_tp_result
                result['stop_loss'] = adjusted_sl
                result['take_profit'] = adjusted_tp
            else:
                result['sl_tp_warning'] = sl_tp_result.get('error', 'Failed to set SL/TP')
                result['success'] = False
                
                close_result = close_position_market(
                    exchange=exchange,
                    symbol=clean_sym,
                    side=side.lower(),
                    contracts=result.get('contracts'),
                    margin_mode=margin_mode,
                    hedge_mode=result.get('hedge_mode', hedge_mode),
                )
                result['close_on_sl_tp_fail'] = close_result
        
        result['exchange'] = 'OKX'
        return result
        
    except Exception as e:
        import traceback
        return {
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc(),  # ✅ Thêm traceback để debug
            'symbol': clean_symbol(symbol) if symbol else 'Unknown',
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
    
    # Lấy thông tin slippage
    slippage_info = result.get('slippage_info', {})
    original_sl = result.get('original_sl', stop_loss)
    original_tp = result.get('original_tp', take_profit)
    slippage_pct = slippage_info.get('slippage_pct', 0)
    expected_entry = slippage_info.get('expected_entry', entry_price)
    
    # Lấy side từ position_info
    position_info = result.get('position_info', {})
    side = position_info.get('side', 'N/A').upper()
    leverage = position_info.get('leverage', 'N/A')
    contracts = result.get('contracts', position_info.get('quantity', 0))
    
    # Tính Risk/Reward ratio
    rr_ratio = 0
    risk = 0
    reward = 0
    sl_pct = 0
    tp_pct = 0
    if side == 'BUY':  # LONG
        risk = abs(entry_price - stop_loss) if entry_price and stop_loss else 0
        reward = abs(take_profit - entry_price) if take_profit and entry_price else 0
        if entry_price and entry_price != 0:
            sl_pct = (stop_loss - entry_price) / entry_price * 100 if stop_loss else 0
            tp_pct = (take_profit - entry_price) / entry_price * 100 if take_profit else 0
    elif side == 'SELL':  # SHORT
        risk = abs(stop_loss - entry_price) if stop_loss and entry_price else 0
        reward = abs(entry_price - take_profit) if entry_price and take_profit else 0
        if entry_price and entry_price != 0:
            sl_pct = (entry_price - stop_loss) / entry_price * 100 if stop_loss else 0
            tp_pct = (entry_price - take_profit) / entry_price * 100 if take_profit else 0
    
    if risk > 0 and reward is not None and reward > 0:
        rr_ratio = reward / risk
    else:
        rr_ratio = 0
    
    # Format message
    side_emoji = "🟢" if side == "BUY" else "🔴"
    side_text = "LONG" if side == "BUY" else "SHORT"
    
    # Thêm thông tin slippage nếu có
    slippage_line = ""
    adjusted_line = ""
    if slippage_pct != 0:
        slippage_emoji = "⚠️" if abs(slippage_pct) > 0.3 else "ℹ️"
        slippage_line = f"\n├─ Slippage: {slippage_emoji} {slippage_pct:+.3f}%"
        if original_tp is not None and (original_sl != stop_loss or original_tp != take_profit):
            adjusted_line = f"\n├─ (SL/TP đã điều chỉnh từ ${original_sl:,.4f} / ${original_tp:,.4f})"
    
    message = f"""✅ THÀNH CÔNG

{side_emoji} {side_text} {symbol}
├─ Leverage: {leverage}x
├─ Contracts: {contracts}
│
├─ Expected Entry: ${expected_entry:,.4f}
├─ Actual Entry:   ${entry_price:,.4f}{slippage_line}
│
├─ SL:     ${stop_loss:,.4f} ({sl_pct:+.2f}%)
├─ TP:     ${take_profit:,.4f} ({tp_pct:+.2f}%){adjusted_line}
│
└─ RR:     1:{rr_ratio:.2f}"""
    
    return message


if __name__ == "__main__":
    # Example usage
    order_result = validate_and_place_order(
        symbol="BTCUSDT_1m",
        side="sell",
        entry_price= 87995.12,
        stop_loss=88074.48,
        take_profit=None,
        leverage=10,
        risk_per_trade=10,
        risk_reward_ratio=2.0,
    )
    print(order_result)
    print("\n" + "="*40 + "\n")
    print(okx_format_order_message(order_result))
    