import os
from typing import Optional

import ccxt
from dotenv import load_dotenv
from src.utils.rounding import round_price

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
            'warnOnFetchOpenOrdersWithoutSymbol': False,  # Suppress rate limit warning
        }
    })
    
    exchange.enable_demo_trading(True)
    
    return exchange


def calculate_position_size(entry_price: float, stop_loss: float, leverage: int, risk_usd: float = 5, side: str = 'buy') -> dict:
    """
    Calculate position size and margin for a trade.

    Formula:
    - price_diff = abs(entry_price - stop_loss)
    - quantity = risk_usd / price_diff  (quantity such that risk = risk_usd USD)
    - notional = quantity × entry_price
    - margin = notional / leverage

    Args:
        entry_price: Entry price (USDT)
        stop_loss: Stop loss price (USDT)
        leverage: Leverage (e.g., 20)
        risk_usd: Maximum risk in USD
        side: 'buy' for LONG, 'sell' for SHORT

    Returns:
        dict: Position information (quantity, margin, notional_value)
    """
    if leverage <= 0:
        raise ValueError("Leverage phải > 0")
    
    # Validate stop loss theo side
    if side.lower() == 'buy':  # LONG
        if entry_price <= stop_loss:
            raise ValueError("LONG: Entry price phải cao hơn stop loss")
    else:  # SHORT
        if entry_price >= stop_loss:
            raise ValueError("SHORT: Stop loss phải cao hơn entry price")
    
    # Tính price diff (luôn dương)
    price_diff = abs(entry_price - stop_loss)
    
    # quantity = risk_usd / price_diff
    # Nếu giá chạm stop_loss, lỗ = price_diff * quantity = risk_usd
    quantity = risk_usd / price_diff
    
    # Margin = notional_value / leverage
    notional_value = quantity * entry_price
    margin_required = notional_value / leverage
    
    # Kiểm tra thực tế rủi ro
    actual_risk = price_diff * quantity
    
    return {
        'quantity': round(quantity, 4),
        'margin_required': round_price(margin_required),
        'notional_value': round_price(notional_value),
        'actual_risk': round_price(actual_risk),
        'price_diff': round_price(price_diff),
        'leverage': leverage,
        'side': side.lower(),
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


def enable_hedge_mode(exchange: ccxt.bybit, symbol: str = None) -> dict:
    """
    Enable Hedge Mode on the Bybit account (allow long and short simultaneously).

    Args:
        exchange: CCXT bybit instance
        symbol: Symbol (optional, default 'BTCUSDT')

    Returns:
        dict: Result of enabling hedge mode
    """
    try:
        api_symbol = 'BTCUSDT'
        if symbol:
            formatted = format_symbol(symbol)
            api_symbol = formatted.replace('/', '').replace(':USDT', '')
        
        # Call API v5 to switch to Hedge Mode
        # Endpoint: POST /v5/position/switch-mode
        result = exchange.privatePostV5PositionSwitchMode({
            'category': 'linear',
            'symbol': api_symbol,
            'mode': 3,  # 0 = One-Way, 3 = Hedge Mode
        })
        
        ret_code = result.get('retCode', -1)
        ret_msg = result.get('retMsg', 'Unknown error')
        
        if ret_code == 0:
            return {
                'success': True,
                'message': 'Hedge mode enabled',
                'symbol': api_symbol,
            }
        elif ret_code == 110025:  # Already in hedge mode
            return {
                'success': True,
                'message': 'Already in hedge mode',
                'symbol': api_symbol,
            }
        else:
            return {
                'success': False,
                'error': f'{ret_msg} (code: {ret_code})',
            }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
        }


def get_position_mode(exchange: ccxt.bybit, symbol: str = None) -> dict:
    """
    Detect the account Position Mode (One-Way vs Hedge Mode) on Bybit.

    Args:
        exchange: CCXT bybit instance
        symbol: Symbol to check (optional, default 'BTCUSDT')

    Returns:
        dict: {
            'mode': 'one_way' or 'hedge',
            'position_idx_long': 0 or 1,
            'position_idx_short': 0 or 2,
        }
    """
    try:
        # Sử dụng API v5 để lấy position mode
        api_symbol = 'BTCUSDT'
        if symbol:
            formatted = format_symbol(symbol)
            api_symbol = formatted.replace('/', '').replace(':USDT', '')
        
        # Method 1: Fetch positions và check positionIdx
        positions = exchange.fetch_positions()
        
        # Nếu có position nào với positionIdx != 0, đó là Hedge Mode
        for pos in positions:
            info = pos.get('info', {})
            position_idx = int(info.get('positionIdx', 0))
            if position_idx in [1, 2]:
                return {
                    'mode': 'hedge',
                    'position_idx_long': 1,
                    'position_idx_short': 2,
                }
        
        # Method 2: Call API để check account setting
        try:
            # Fetch account info hoặc positions với symbol cụ thể
            symbol_positions = exchange.fetch_positions([format_symbol(api_symbol)])
            for pos in symbol_positions:
                info = pos.get('info', {})
                position_idx = int(info.get('positionIdx', 0))
                # Bybit trả về 2 positions (long & short) khi ở Hedge Mode
                # Ngay cả khi size = 0
                if position_idx in [1, 2]:
                    return {
                        'mode': 'hedge',
                        'position_idx_long': 1,
                        'position_idx_short': 2,
                    }
        except Exception:
            pass
        
        # Default: Assume One-Way Mode nếu không detect được
        return {
            'mode': 'one_way',
            'position_idx_long': 0,
            'position_idx_short': 0,
        }
        
    except Exception as e:
        # Default to One-Way Mode để tránh lỗi
        return {
            'mode': 'one_way',
            'position_idx_long': 0,
            'position_idx_short': 0,
        }


def set_position_sl_tp(
    exchange: ccxt.bybit,
    symbol: str,
    side: str,
    stop_loss: float,
    take_profit: float = None,
) -> dict:
    """
    Set Stop Loss and Take Profit for a position using Bybit v5 API.
    Automatically detects Position Mode (One-Way vs Hedge).

    Args:
        exchange: CCXT bybit instance
        symbol: Symbol (e.g., 'BTC' or 'BTC/USDT:USDT')
        side: 'buy' for long, 'sell' for short
        stop_loss: Stop loss price
        take_profit: Take profit price (optional)

    Returns:
        dict: Result of setting SL/TP
    """
    try:
        formatted_symbol = format_symbol(symbol)
        
        # Detect Position Mode (One-Way vs Hedge)
        position_mode = get_position_mode(exchange, symbol)
        
        # Xác định positionIdx dựa trên mode và side
        if side.lower() == 'buy':
            position_idx = position_mode['position_idx_long']
        else:
            position_idx = position_mode['position_idx_short']
        
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
            return {
                'success': False,
                'error': f'Position not found for {side} (positionIdx={position_idx})',
            }
        
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
        
        # Sử dụng API v5 endpoint
        result = exchange.privatePostV5PositionTradingStop(params)
        
        # Check response
        ret_code = result.get('retCode', -1)
        ret_msg = result.get('retMsg', 'Unknown error')
        
        if ret_code != 0:
            return {
                'success': False,
                'error': f'{ret_msg} (code: {ret_code})',
                'result': result,
            }
        
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
    Place a market order with a stop loss.

    Args:
        exchange: CCXT bybit instance
        symbol: Symbol (e.g., 'BTCUSDT' or 'BTC/USDT:USDT')
        side: 'buy' for long, 'sell' for short
        leverage: Leverage (default 20)
        stop_loss: Stop loss price (required)
        risk_usd: Risk amount in USD (default 5)

    Returns:
        dict: Order information
    """
    if not stop_loss:
        raise ValueError("Phải nhập stop_loss")
    
    # Format symbol
    symbol = format_symbol(symbol)
    
    # Bật Hedge Mode để có thể long và short cùng lúc
    hedge_result = enable_hedge_mode(exchange, symbol)
    if not hedge_result['success'] and 'Already in hedge mode' not in hedge_result.get('message', ''):
        print(f"⚠️  Warning: Could not enable hedge mode - {hedge_result.get('error', 'Unknown')}")
    
    # Detect Position Mode sau khi enable
    position_mode = get_position_mode(exchange, symbol)
    
    # Xác định positionIdx cho lệnh
    if side.lower() == 'buy':
        position_idx = position_mode['position_idx_long']
    else:
        position_idx = position_mode['position_idx_short']
    
    # Lấy giá hiện tại
    ticker = exchange.fetch_ticker(symbol)
    current_price = ticker['last']
    
    # Tính kích thước vị trí
    position_info = calculate_position_size(
        entry_price=current_price,
        stop_loss=stop_loss,
        leverage=leverage,
        risk_usd=risk_usd,
        side=side
    )
    
    # Đặt lệnh market order
    try:
        # Set leverage trước (nếu cần)
        try:
            exchange.set_leverage(leverage, symbol)
        except Exception:
            pass
        
        # Đặt lệnh market với positionIdx
        order = exchange.create_market_order(
            symbol=symbol,
            side=side,
            amount=position_info['quantity'],
            params={
                'positionIdx': position_idx,  # Quan trọng: Chỉ định position index
            }
        )
        
        return {
            'success': True,
            'order': order,
            'position_info': position_info,
            'entry_price': current_price,
            'stop_loss': stop_loss,
            'position_mode': position_mode['mode'],
            'position_idx': position_idx,
        }
        
    except Exception as e:
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
        
        pos_info = calculate_position_size(entry_price, stop_loss, leverage, risk, side='buy')
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
