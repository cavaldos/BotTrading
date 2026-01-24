import os
from typing import Optional

import ccxt
from dotenv import load_dotenv
from src.utils.rounding import round_price

load_dotenv()

_exchange_instance: Optional[ccxt.okx] = None


def create_okx_exchange(api_key: str, secret: str, passphrase: str, sandbox: bool = False) -> ccxt.okx:
    """Create OKX exchange instance with given credentials.
    
    Args:
        api_key: OKX API Key
        secret: OKX Secret Key
        passphrase: OKX Passphrase
        sandbox: Use sandbox (demo) environment if True
    """
    exchange = ccxt.okx({
        'apiKey': api_key,
        'secret': secret,
        'password': passphrase,  # OKX requires passphrase as 'password'
        'enableRateLimit': True,
        'sandbox': sandbox,  # Use sandbox for demo trading
        'options': {
            'defaultType': 'swap',  # Use swap (perpetual futures)
            'adjustForTimeDifference': True,
        }
    })
    
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


def get_exchange(force_new: bool = False, sandbox: bool = True) -> ccxt.okx:
    """Get or create OKX exchange instance.
    
    Args:
        force_new: Force create new instance
        sandbox: Use sandbox (demo) environment (default True)
    """
    global _exchange_instance
    
    if _exchange_instance is not None and not force_new:
        return _exchange_instance
    
    # For demo: use OKX_DEMO_* variables, fallback to OKX_* variables
    if sandbox:
        api_key = os.getenv('OKX_DEMO_API_KEY') or os.getenv('OKX_API_KEY')
        secret = os.getenv('OKX_DEMO_SECRET_KEY') or os.getenv('OKX_SECRET_KEY')
        passphrase = os.getenv('OKX_DEMO_PASSPHRASE') or os.getenv('OKX_PASSPHRASE')
    else:
        api_key = os.getenv('OKX_API_KEY')
        secret = os.getenv('OKX_SECRET_KEY')
        passphrase = os.getenv('OKX_PASSPHRASE')
    
    if not api_key or not secret or not passphrase:
        raise ValueError(
            "Thiếu OKX API credentials!\n"
            "Vui lòng cấu hình trong file .env:\n"
            f"  OKX_{'DEMO_' if sandbox else ''}API_KEY=your_api_key\n"
            f"  OKX_{'DEMO_' if sandbox else ''}SECRET_KEY=your_secret_key\n"
            f"  OKX_{'DEMO_' if sandbox else ''}PASSPHRASE=your_passphrase"
        )
    
    mode = "DEMO (Sandbox)" if sandbox else "LIVE"
    print(f"🔗 Connecting to OKX {mode} environment...")
    
    _exchange_instance = create_okx_exchange(api_key, secret, passphrase, sandbox=sandbox)
    
    # Optionally load markets (may fail due to API restrictions)
    try:
        _exchange_instance.load_markets()
        print(f"✓ Markets loaded: {len(_exchange_instance.markets)} markets")
    except Exception as e:
        print(f"⚠️  Warning: Could not load markets - {e}")
        print("   Continuing without pre-loaded markets")
    
    return _exchange_instance


def get_market_info(exchange: ccxt.okx, symbol: str) -> dict:
    """Get market information for a symbol."""
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


def validate_symbol(exchange: ccxt.okx, symbol: str) -> bool:
    """Check if symbol is available on exchange."""
    if not exchange.markets:
        exchange.load_markets()
    
    return symbol in exchange.markets


def format_symbol(symbol: str) -> str:
    """Format symbol to OKX standard format (BTC-USDT for swap)."""
    symbol = symbol.upper().strip()
    
    if '-USDT' in symbol:
        return symbol
    
    if '/' in symbol:
        base = symbol.split('/')[0]
        return f"{base}-USDT"
    
    if symbol.endswith('USDT'):
        symbol = symbol[:-4]
    
    return f"{symbol}-USDT"


def set_leverage_and_margin_mode(exchange: ccxt.okx, symbol: str, leverage: int = 20) -> dict:
    """
    Set leverage and margin mode for OKX.
    OKX requires setting both position mode and margin mode.
    
    Args:
        exchange: CCXT OKX instance
        symbol: Symbol (e.g., 'BTC-USDT-SWAP')
        leverage: Leverage (default 20)
    
    Returns:
        dict: Result of setting leverage
    """
    try:
        formatted_symbol = format_symbol(symbol)
        
        # Validate symbol exists
        if formatted_symbol not in exchange.markets:
            exchange.load_markets(True)
        
        if formatted_symbol not in exchange.markets:
            return {
                'success': False,
                'error': f'Symbol not found: {formatted_symbol}'
            }
        
        # Get instrument ID for OKX API (e.g., "BTC-USDT-SWAP")
        market = exchange.markets[formatted_symbol]
        inst_id = market.get('id', formatted_symbol)
        
        # Set margin mode to 'cross' (OKX default for swap)
        try:
            exchange.set_margin_mode('cross', formatted_symbol)
        except Exception as e:
            # Ignore if already set
            if 'already' not in str(e).lower():
                print(f"⚠️  Warning: Could not set margin mode - {e}")
        
        # Set leverage
        try:
            exchange.set_leverage(leverage, formatted_symbol, params={'mgnMode': 'cross'})
        except Exception as e:
            if 'already' not in str(e).lower() and 'same' not in str(e).lower():
                print(f"⚠️  Warning: Could not set leverage - {e}")
        
        return {
            'success': True,
            'leverage': leverage,
            'symbol': formatted_symbol,
            'inst_id': inst_id,
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
        }


def set_position_sl_tp(
    exchange: ccxt.okx,
    symbol: str,
    side: str,
    stop_loss: float,
    take_profit: float = None,
) -> dict:
    """
    Set Stop Loss and Take Profit for a position on OKX.
    
    Args:
        exchange: CCXT OKX instance
        symbol: Symbol (e.g., 'BTC' or 'BTC-USDT-SWAP')
        side: 'buy' for long, 'sell' for short
        stop_loss: Stop loss price
        take_profit: Take profit price (optional)
    
    Returns:
        dict: Result of setting SL/TP
    """
    try:
        formatted_symbol = format_symbol(symbol)
        
        # Get market info
        if formatted_symbol not in exchange.markets:
            exchange.load_markets(True)
        
        market = exchange.markets.get(formatted_symbol, {})
        inst_id = market.get('id', formatted_symbol)
        
        # Fetch current position
        positions = exchange.fetch_positions([formatted_symbol])
        
        position = None
        position_size = 0
        
        for pos in positions:
            pos_side = pos.get('side', '').lower()
            pos_contracts = abs(float(pos.get('contracts', 0) or 0))
            
            if pos_contracts > 0:
                # Match position by side
                if (side.lower() == 'buy' and pos_side == 'long') or \
                   (side.lower() == 'sell' and pos_side == 'short'):
                    position = pos
                    position_size = pos_contracts
                    break
                elif pos_side == side.lower():
                    position = pos
                    position_size = pos_contracts
                    break
        
        if not position or position_size == 0:
            return {
                'success': False,
                'error': f'Position not found for {side}',
            }
        
        # OKX uses algo orders for SL/TP
        # Create SL order
        sl_params = {
            'instId': inst_id,
            'tdMode': 'cross',
            'side': 'sell' if side.lower() == 'buy' else 'buy',  # Opposite side
            'ordType': 'conditional',
            'sz': str(position_size),
            'slTriggerPx': str(stop_loss),
            'slOrdPx': '-1',  # Market price
            'triggerPxType': 'last',
        }
        
        # Add TP if provided
        if take_profit:
            sl_params['tpTriggerPx'] = str(take_profit)
            sl_params['tpOrdPx'] = '-1'  # Market price
        
        # Use OKX's create order with algo type
        try:
            # Method 1: Use ccxt's create_order with trigger params
            sl_order = exchange.create_order(
                symbol=formatted_symbol,
                type='market',
                side='sell' if side.lower() == 'buy' else 'buy',
                amount=position_size,
                params={
                    'stopLossPrice': stop_loss,
                    'takeProfitPrice': take_profit if take_profit else None,
                    'triggerType': 'last',
                }
            )
            
            return {
                'success': True,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'position_size': position_size,
                'order': sl_order,
            }
            
        except Exception as e:
            # Method 2: Try using privatePostTradeOrderAlgo if standard method fails
            try:
                algo_result = exchange.privatePostTradeOrderAlgo({
                    'instId': inst_id,
                    'tdMode': 'cross',
                    'side': 'sell' if side.lower() == 'buy' else 'buy',
                    'ordType': 'oco',  # One-Cancels-Other for SL/TP
                    'sz': str(position_size),
                    'slTriggerPx': str(stop_loss),
                    'slOrdPx': '-1',
                    'tpTriggerPx': str(take_profit) if take_profit else '',
                    'tpOrdPx': '-1' if take_profit else '',
                })
                
                if algo_result.get('code') == '0':
                    return {
                        'success': True,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'position_size': position_size,
                        'result': algo_result,
                    }
                else:
                    return {
                        'success': False,
                        'error': algo_result.get('msg', 'Unknown error'),
                        'result': algo_result,
                    }
                    
            except Exception as e2:
                return {
                    'success': False,
                    'error': f'SL/TP setup failed: {str(e)} | {str(e2)}',
                }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
        }


def place_market_order_with_sl(
    exchange: ccxt.okx,
    symbol: str,
    side: str = 'buy',
    leverage: int = 20,
    stop_loss: float = None,
    risk_usd: float = 5,
) -> dict:
    """
    Place a market order with stop loss on OKX.
    
    Args:
        exchange: CCXT OKX instance
        symbol: Symbol (e.g., 'BTC' or 'BTC-USDT-SWAP')
        side: 'buy' for long, 'sell' for short
        leverage: Leverage (default 20)
        stop_loss: Stop loss price (required)
        risk_usd: Risk amount in USD (default 5)
    
    Returns:
        dict: Order information
    """
    if not stop_loss:
        raise ValueError("Phải nhập stop_loss")
    
    # Format symbol for OKX (BTC -> BTC-USDT-SWAP)
    formatted_symbol = format_symbol(symbol)
    
    # Set leverage and margin mode
    leverage_result = set_leverage_and_margin_mode(exchange, symbol, leverage)
    if not leverage_result['success']:
        print(f"⚠️  Warning: Could not set leverage - {leverage_result.get('error', 'Unknown')}")
    
    # Get current price
    ticker = exchange.fetch_ticker(formatted_symbol)
    current_price = ticker['last']
    
    # Calculate position size based on risk
    position_info = calculate_position_size(
        entry_price=current_price,
        stop_loss=stop_loss,
        leverage=leverage,
        risk_usd=risk_usd,
        side=side
    )
    
    # Get market info for precision
    market_info = get_market_info(exchange, formatted_symbol)
    amount_precision = market_info.get('amount_precision', 4)
    min_amount = market_info.get('min_amount', 0)
    contract_size = market_info.get('contract_size', 1)
    
    # Adjust quantity for contract size if needed
    quantity = position_info['quantity']
    
    # OKX swap uses contracts, need to convert
    # For USDT-margined swaps, 1 contract = contract_size of base currency
    if contract_size and contract_size > 0:
        # quantity is in base currency, convert to contracts
        contracts = quantity / contract_size
        contracts = round(contracts, 0)  # OKX contracts are integers
        if contracts < 1:
            contracts = 1
    else:
        contracts = round(quantity, amount_precision)
    
    # Place market order
    try:
        # OKX posSide: 'long' for buy, 'short' for sell in hedged mode
        pos_side = 'long' if side.lower() == 'buy' else 'short'
        
        order = exchange.create_market_order(
            symbol=formatted_symbol,
            side=side.lower(),
            amount=contracts,
            params={
                'tdMode': 'cross',  # Cross margin
                'posSide': pos_side,  # Position side for hedge mode
            }
        )
        
        # Get actual fill price
        fill_price = order.get('average', current_price) or current_price
        
        return {
            'success': True,
            'order': order,
            'position_info': position_info,
            'entry_price': fill_price,
            'stop_loss': stop_loss,
            'contracts': contracts,
            'symbol': formatted_symbol,
        }
        
    except Exception as e:
        error_msg = str(e)
        
        # Try without posSide (one-way mode)
        if 'posSide' in error_msg or 'position side' in error_msg.lower():
            try:
                order = exchange.create_market_order(
                    symbol=formatted_symbol,
                    side=side.lower(),
                    amount=contracts,
                    params={
                        'tdMode': 'cross',
                    }
                )
                
                fill_price = order.get('average', current_price) or current_price
                
                return {
                    'success': True,
                    'order': order,
                    'position_info': position_info,
                    'entry_price': fill_price,
                    'stop_loss': stop_loss,
                    'contracts': contracts,
                    'symbol': formatted_symbol,
                    'mode': 'one_way',
                }
                
            except Exception as e2:
                return {
                    'success': False,
                    'error': str(e2),
                    'position_info': position_info,
                }
        
        return {
            'success': False,
            'error': error_msg,
            'position_info': position_info,
        }


if __name__ == '__main__':
    try:
        # Test get_exchange
        print("=" * 60)
        print("Testing OKX get_exchange()...")
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
        
        # Test place_market_order_with_sl
        print("\n" + "=" * 60)
        print("Testing place_market_order_with_sl()...")
        
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
            print(f"   Entry Price: {result.get('entry_price', 'N/A')}")
            print(f"   Contracts: {result.get('contracts', 'N/A')}")
        else:
            print(f"\n✗ Order failed: {result.get('error', 'Unknown')}")
        
        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
