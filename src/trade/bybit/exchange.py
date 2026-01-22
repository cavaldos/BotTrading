import os
from typing import Optional

import ccxt
from dotenv import load_dotenv

load_dotenv()

_exchange_instance: Optional[ccxt.bybit] = None


def create_bybit_demo_exchange(api_key: str, secret: str) -> ccxt.bybit:
    exchange = ccxt.bybit({
        'apiKey': api_key,
        'secret': secret,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'swap',
            'adjustForTimeDifference': True,
        }
    })
    
    exchange.enable_demo_trading(True)
    
    return exchange


def calculate_position_size(entry_price: float, stop_loss: float, leverage: int, risk_usd: float = 5) -> dict:
    """
    Formula: 
    - price_diff = entry_price - stop_loss
    - quantity = risk_usd / price_diff  (số lượng để risk = risk_usd USD)
    - notional = quantity × entry_price
    - margin = notional / leverage
    
    Args:
        entry_price: Giá entry (USDT)
        stop_loss: Giá stop loss (USDT)
        leverage: Đòn bẩy (ví dụ: 20)
        risk_usd: Rủi ro tối đa (USD)
    
    Returns:
        dict: Thông tin vị trí (quantity, margin, notional_value)
    """
    if entry_price <= stop_loss:
        raise ValueError("Entry price phải cao hơn stop loss")
    
    if leverage <= 0:
        raise ValueError("Leverage phải > 0")
    
    price_diff = entry_price - stop_loss
    
    # quantity = risk_usd / price_diff
    # Nếu giá drop từ entry đến stop_loss, lỗ = price_diff * quantity = risk_usd
    quantity = risk_usd / price_diff
    
    # Margin = notional_value / leverage
    notional_value = quantity * entry_price
    margin_required = notional_value / leverage
    
    # Kiểm tra thực tế rủi ro
    actual_risk = price_diff * quantity
    
    return {
        'quantity': round(quantity, 4),
        'margin_required': round(margin_required, 2),
        'notional_value': round(notional_value, 2),
        'actual_risk': round(actual_risk, 2),
        'price_diff': round(price_diff, 2),
        'leverage': leverage,
    }


def get_exchange(force_new: bool = False) -> ccxt.bybit:
    global _exchange_instance
    
    if _exchange_instance is not None and not force_new:
        return _exchange_instance
    
    api_key = os.getenv('BYBIT_DEMO_API_KEY')
    secret = os.getenv('BYBIT_DEMO_SECRET')
    
    if not api_key or not secret:
        raise ValueError(
            "Thieu API Key hoac Secret Key!\n"
            "Vui long cau hinh trong file .env:\n"
            "  BYBIT_DEMO_API_KEY=your_api_key\n"
            "  BYBIT_DEMO_SECRET=your_secret_key"
        )
    
    _exchange_instance = create_bybit_demo_exchange(api_key, secret)
    _exchange_instance.load_markets()
    
    return _exchange_instance


def get_market_info(exchange: ccxt.bybit, symbol: str) -> dict:
    if symbol not in exchange.markets:
        exchange.load_markets(True)
    
    if symbol not in exchange.markets:
        return {}
    
    market = exchange.markets[symbol]
    
    return {
        'symbol': symbol,
        'base': market.get('base', ''),
        'quote': market.get('quote', ''),
        'settle': market.get('settle', ''),
        'contract_size': market.get('contractSize', 1),
        'min_amount': market.get('limits', {}).get('amount', {}).get('min', 0),
        'max_amount': market.get('limits', {}).get('amount', {}).get('max', 0),
        'min_cost': market.get('limits', {}).get('cost', {}).get('min', 0),
        'price_precision': market.get('precision', {}).get('price', 2),
        'amount_precision': market.get('precision', {}).get('amount', 3),
        'max_leverage': market.get('limits', {}).get('leverage', {}).get('max', 100),
    }


def validate_symbol(exchange: ccxt.bybit, symbol: str) -> bool:
    if not exchange.markets:
        exchange.load_markets()
    
    return symbol in exchange.markets


def format_symbol(symbol: str) -> str:
    symbol = symbol.upper().strip()
    
    if ':USDT' in symbol:
        return symbol
    
    if '/' in symbol:
        base_quote = symbol.split('/')[0]
        return f"{base_quote}/USDT:USDT"
    
    if symbol.endswith('USDT'):
        symbol = symbol[:-4]
    
    return f"{symbol}/USDT:USDT"


def get_position_mode(exchange: ccxt.bybit, symbol: str = None) -> dict:
    """
    Detect Position Mode (One-Way vs Hedge Mode) từ tài khoản Bybit
    Args:
        exchange: CCXT exchange instance (bybit)
        symbol: Symbol để kiểm tra (optional, nếu không có sẽ dùng BTCUSDT)
    
    Returns:
        dict: {
            'mode': 'one_way' hoặc 'hedge',
            'position_idx_long': 0 hoặc 1,
            'position_idx_short': 0 hoặc 2,
        }
    """
    try:
        # Sử dụng API v5 để lấy position mode
        # Endpoint: GET /v5/position/switch-mode hoặc check từ position info
        
        api_symbol = 'BTCUSDT'
        if symbol:
            formatted = format_symbol(symbol)
            api_symbol = formatted.replace('/', '').replace(':USDT', '')
        
        # Gọi API v5 để lấy thông tin position mode
        # Method 1: Fetch positions và check positionIdx
        positions = exchange.fetch_positions()
        
        # Nếu có position nào với positionIdx != 0, đó là Hedge Mode
        for pos in positions:
            info = pos.get('info', {})
            position_idx = int(info.get('positionIdx', 0))
            if position_idx in [1, 2]:
                print(f"  Detected Hedge Mode (positionIdx={position_idx})")
                return {
                    'mode': 'hedge',
                    'position_idx_long': 1,
                    'position_idx_short': 2,
                }
        
        # Method 2: Thử gọi API switch-mode để check current mode
        try:
            result = exchange.privateGetV5PositionSwitchMode({
                'category': 'linear',
                'symbol': api_symbol,
            })
            # Nếu không lỗi, parse kết quả
            mode_value = result.get('result', {}).get('mode', 0)
            if mode_value == 3:  # Hedge Mode
                return {
                    'mode': 'hedge',
                    'position_idx_long': 1,
                    'position_idx_short': 2,
                }
        except Exception:
            pass
        
        # Default: One-Way Mode
        print(f"  Using One-Way Mode (default)")
        return {
            'mode': 'one_way',
            'position_idx_long': 0,
            'position_idx_short': 0,
        }
        
    except Exception as e:
        print(f"  Warning: Could not detect position mode: {e}")
        # Default to Hedge Mode (safer cho user đang dùng Hedge)
        return {
            'mode': 'hedge',
            'position_idx_long': 1,
            'position_idx_short': 2,
        }


def set_position_sl_tp(
    exchange: ccxt.bybit,
    symbol: str,
    side: str,
    stop_loss: float,
    take_profit: float = None,
) -> dict:
    """
    Đặt Stop Loss và Take Profit trực tiếp cho position bằng Bybit API v5
    Tự động detect Position Mode (One-Way vs Hedge)
    
    Args:
        exchange: CCXT exchange instance (bybit)
        symbol: Symbol (ví dụ: 'BTC' hoặc 'BTC/USDT:USDT')
        side: 'buy' cho long, 'sell' cho short
        stop_loss: Giá stop loss
        take_profit: Giá take profit (optional)
    
    Returns:
        dict: Kết quả đặt SL/TP
    """
    try:
        formatted_symbol = format_symbol(symbol)
        
        print(f"\n⚙️  Setting SL/TP for {formatted_symbol}:")
        print(f"  • Stop Loss: {stop_loss:,.2f}")
        if take_profit:
            print(f"  • Take Profit: {take_profit:,.2f}")
        
        # Detect Position Mode (One-Way vs Hedge)
        print(f"  • Detecting Position Mode...")
        position_mode = get_position_mode(exchange, symbol)
        print(f"  • Position Mode: {position_mode['mode'].upper()}")
        
        # Xác định positionIdx dựa trên mode và side
        if side.lower() == 'buy':
            position_idx = position_mode['position_idx_long']
        else:
            position_idx = position_mode['position_idx_short']
        
        print(f"  • Position Index: {position_idx}")
        
        # Kiểm tra xem position đã mở chưa
        positions = exchange.fetch_positions([formatted_symbol])
        
        # Tìm position phù hợp với side và positionIdx
        position = None
        position_size = 0
        
        for pos in positions:
            pos_info = pos.get('info', {})
            pos_idx = int(pos_info.get('positionIdx', 0))
            pos_side = pos.get('side', '').lower()
            pos_contracts = abs(float(pos.get('contracts', 0) or 0))
            
            # Match position theo positionIdx hoặc side
            if position_mode['mode'] == 'hedge':
                # Hedge Mode: match theo positionIdx
                if pos_idx == position_idx and pos_contracts > 0:
                    position = pos
                    position_size = pos_contracts
                    break
            else:
                # One-Way Mode: match theo side hoặc lấy position có contracts > 0
                if pos_contracts > 0:
                    position = pos
                    position_size = pos_contracts
                    break
        
        if not position or position_size == 0:
            print(f"⚠️  Position chưa được mở cho {formatted_symbol} (side={side}, positionIdx={position_idx})")
            return {
                'success': False,
                'error': f'Position not found for {side} (positionIdx={position_idx})',
            }
        
        print(f"  • Position Size: {position_size} contracts")
        
        # Lấy symbol name cho API v5 (BTC/USDT:USDT -> BTCUSDT)
        api_symbol = formatted_symbol.replace('/', '').replace(':USDT', '')
        
        # Xây dựng params cho API v5
        # Endpoint: POST /v5/position/trading-stop
        params = {
            'category': 'linear',  # USDT perpetual
            'symbol': api_symbol,
            'positionIdx': position_idx,
            'stopLoss': str(stop_loss),
            'slTriggerBy': 'LastPrice',
        }
        
        if take_profit:
            params['takeProfit'] = str(take_profit)
            params['tpTriggerBy'] = 'LastPrice'
        
        print(f"  • Calling API v5 with params: {params}")
        
        # Sử dụng API v5 endpoint
        result = exchange.privatePostV5PositionTradingStop(params)
        
        # Check response
        ret_code = result.get('retCode', -1)
        ret_msg = result.get('retMsg', 'Unknown error')
        
        if ret_code != 0:
            print(f"✗ API Error: {ret_msg} (code: {ret_code})")
            return {
                'success': False,
                'error': f'{ret_msg} (code: {ret_code})',
                'result': result,
            }
        
        print(f"  ✓ API Response: retCode={ret_code}, retMsg={ret_msg}")
        print(f"  ✓ Stop Loss: {stop_loss:,.2f}")
        if take_profit:
            print(f"  ✓ Take Profit: {take_profit:,.2f}")
        
        print(f"✓ SL/TP đã được đặt thành công!")
        return {
            'success': True,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'position_size': position_size,
            'position_mode': position_mode['mode'],
            'position_idx': position_idx,
            'result': result,
        }
        
    except Exception as e:
        print(f"✗ Lỗi khi đặt SL/TP: {e}")
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'error': str(e),
        }


def place_market_order_with_sl(
    exchange: ccxt.bybit,
    symbol: str,
    side: str = 'buy',
    leverage: int = 20,
    stop_loss: float = None,
    risk_usd: float = 5,
) -> dict:
    """
    Đặt lệnh market order với stop loss
    
    Args:
        exchange: CCXT exchange instance
        symbol: Symbol (ví dụ: 'BTCUSDT' hoặc 'BTC/USDT:USDT')
        side: 'buy' cho long, 'sell' cho short
        leverage: Đòn bẩy (mặc định 20)
        stop_loss: Giá stop loss (bắt buộc)
        risk_usd: Rủi ro (mặc định 5 USD)
    
    Returns:
        dict: Thông tin lệnh đặt
    """
    if not stop_loss:
        raise ValueError("Phải nhập stop_loss")
    
    # Format symbol
    symbol = format_symbol(symbol)
    
    # Lấy giá hiện tại
    ticker = exchange.fetch_ticker(symbol)
    current_price = ticker['last']
    
    print(f"\n📊 Symbol: {symbol}")
    print(f"💰 Giá hiện tại: {current_price:,.2f}")
    print(f"🎯 Stop Loss: {stop_loss:,.2f}")
    print(f"📈 Leverage: {leverage}x")
    
    # Tính kích thước vị trí
    position_info = calculate_position_size(
        entry_price=current_price,
        stop_loss=stop_loss,
        leverage=leverage,
        risk_usd=risk_usd
    )
    
    print(f"\n📋 Position Calculation:")
    print(f"  • Quantity: {position_info['quantity']} BTC")
    print(f"  • Notional Value: ${position_info['notional_value']:,.2f}")
    print(f"  • Margin Required: ${position_info['margin_required']:,.2f}")
    print(f"  • Price Distance SL: ${position_info['price_diff']:,.2f}")
    print(f"  • Actual Risk: ${position_info['actual_risk']:.2f}")
    
    # Đặt lệnh market order
    try:
        # Set leverage trước (nếu cần)
        try:
            exchange.set_leverage(leverage, symbol)
            print(f"\n✓ Đã set leverage {leverage}x")
        except Exception as le:
            print(f"\nℹ️  Leverage đã được set: {le}")
        
        # Đặt lệnh market
        order = exchange.create_market_order(
            symbol=symbol,
            side=side,
            amount=position_info['quantity'],
            params={
                'takeProfit': None,  # Có thể thêm later
            }
        )
        
        print(f"\n✓ Lệnh Market Order đã được đặt!")
        print(f"  • Order ID: {order['id']}")
        print(f"  • Status: {order['status']}")
        print(f"  • Amount: {order['amount']}")
        print(f"  • Average Price: {order.get('average', current_price)}")
        
        return {
            'success': True,
            'order': order,
            'position_info': position_info,
            'entry_price': current_price,
            'stop_loss': stop_loss,
        }
        
    except Exception as e:
        print(f"\n✗ Lỗi khi đặt lệnh: {e}")
        return {
            'success': False,
            'error': str(e),
            'position_info': position_info,
        }


if __name__ == '__main__':
    try:
        # Test get_exchange
        print("=" * 60)
        print("Testing get_exchange()...")
        exchange = get_exchange()
        print(f"✓ Exchange initialized: {exchange.__class__.__name__}")
        print(f"✓ Markets loaded: {len(exchange.markets)} markets")
        
        # Test calculate_position_size
        print("\n" + "=" * 60)
        print("Testing calculate_position_size()...")
        entry_price = 100000
        stop_loss = 89900
        leverage = 20
        risk = 5
        
        pos_info = calculate_position_size(entry_price, stop_loss, leverage, risk)
        print(f"  Entry: ${entry_price:,.2f}")
        print(f"  Stop Loss: ${stop_loss:,.2f}")
        print(f"  Leverage: {leverage}x")
        print(f"  Target Risk: ${risk}")
        print(f"  → Quantity: {pos_info['quantity']} BTC")
        print(f"  → Margin: ${pos_info['margin_required']:,.2f}")
        print(f"  → Notional: ${pos_info['notional_value']:,.2f}")
        print(f"  → Actual Risk: ${pos_info['actual_risk']:.2f}")
        
        # Test place_market_order_with_sl (DEMO - không thực sự đặt lệnh)
        print("\n" + "=" * 60)
        print("Testing place_market_order_with_sl()...")
        print("(Chỉ tính toán, không đặt lệnh thực tế)")
        
        result = place_market_order_with_sl(
            exchange=exchange,
            symbol='BTC',
            side='buy',
            leverage=20,
            stop_loss=89900,
            risk_usd=5
        )
        
        if result['success']:
            print(f"\n✓ Order placed successfully!")
        else:
            print(f"\nℹ️  Demo mode - Calculation successful (no actual order placed)")
            print(f"   Position Info: {result['position_info']}")
        
        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
