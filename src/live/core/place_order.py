import time
from datetime import datetime
from typing import Optional

from src.config import load_strategy_config

from src.live.bybit.market_order import (
    validate_and_place_order as bybit_place_market_order,
    format_market_order_message as bybit_format_market_message,
)
from src.live.bybit.limit_order import (
    validate_and_place_limit_order as bybit_place_limit_order,
    format_limit_order_message as bybit_format_limit_message,
    cancel_limit_order as bybit_cancel_limit_order,
    check_limit_order_status as bybit_check_limit_order_status,
)
STRATEGY_CONFIG = load_strategy_config()
DEFAULT_RISK_PER_TRADE = float(STRATEGY_CONFIG.get("risk_per_trade", 5) or 5)
DEFAULT_RISK_REWARD_RATIO = float(STRATEGY_CONFIG.get("risk_reward_ratio", 2.0) or 2.0)
risk_per_trade = DEFAULT_RISK_PER_TRADE
risk_reward_ratio = DEFAULT_RISK_REWARD_RATIO


def place_market_order(
    symbol: str,
    order_type: str,
    entry_price: float,
    stop_loss: float,
    take_profit: Optional[float],
    entry_time: str,
    leverage: int = 20,
    risk_per_trade: float = DEFAULT_RISK_PER_TRADE,
    risk_reward_ratio: float = DEFAULT_RISK_REWARD_RATIO,
    signal_timestamp: Optional[float] = None,
) -> dict:
    order_type = order_type.strip().upper()
    side = 'buy' if order_type in ('BUY', 'LONG') else 'sell'
    
    result = {
        'success': False,
        'bybit': None,
    }
    
    try:
        bybit_result = bybit_place_market_order(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=leverage,
            risk_per_trade=risk_per_trade,
            risk_reward_ratio=risk_reward_ratio,
            signal_timestamp=signal_timestamp,
        )
        result['bybit'] = bybit_result
        result['success'] = bybit_result.get('success', False)
    except Exception as e:
        result['bybit'] = {
            'success': False,
            'error': str(e),
            'exchange': 'Bybit',
        }
    
    return result 


def place_limit_order(
    symbol: str,
    order_type: str,
    entry_price: float,
    stop_loss: float,
    take_profit: Optional[float] = None,
    entry_time: Optional[str] = None,
    leverage: int = 20,
    risk_per_trade: float = DEFAULT_RISK_PER_TRADE,
    risk_reward_ratio: float = DEFAULT_RISK_REWARD_RATIO,
    signal_timestamp: Optional[float] = None,
    time_in_force: str = 'GTC',
) -> dict:
    order_type = order_type.strip().upper()
    side = 'buy' if order_type in ('BUY', 'LONG') else 'sell'
    
    result = {
        'success': False,
        'bybit': None,
        'order_type': 'limit',
    }
    
    try:
        bybit_result = bybit_place_limit_order(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=leverage,
            risk_per_trade=risk_per_trade,
            risk_reward_ratio=risk_reward_ratio,
            signal_timestamp=signal_timestamp,
            time_in_force=time_in_force,
        )
        result['bybit'] = bybit_result
        result['success'] = bybit_result.get('success', False)
        result['order_id'] = bybit_result.get('order_id')
    except Exception as e:
        result['bybit'] = {
            'success': False,
            'error': str(e),
            'exchange': 'Bybit',
        }
    
    return result


def cancel_order(symbol: str, order_id: str) -> dict:
    result = {
        'success': False,
        'bybit': None,
    }
    
    try:
        bybit_result = bybit_cancel_limit_order(symbol, order_id)
        result['bybit'] = bybit_result
        result['success'] = bybit_result.get('success', False)
    except Exception as e:
        result['bybit'] = {
            'success': False,
            'error': str(e),
        }
    
    return result


def check_order_status(symbol: str, order_id: str) -> dict:
    result = {
        'success': False,
        'bybit': None,
    }
    
    try:
        bybit_result = bybit_check_limit_order_status(symbol, order_id)
        result['bybit'] = bybit_result
        result['success'] = bybit_result.get('success', False)
        result['status'] = bybit_result.get('status')
        result['filled'] = bybit_result.get('filled', 0)
    except Exception as e:
        result['bybit'] = {
            'success': False,
            'error': str(e),
        }
    
    return result


if __name__ == "__main__":
    order_result = place_limit_order(
        symbol="BCHUSDT_1m",
        order_type="SELL",
        entry_price=514,
        stop_loss=530,
        take_profit=None,
        entry_time=datetime.utcnow().isoformat(),
        leverage=10,
        risk_reward_ratio=2.0,
        risk_per_trade=5,
    )

    bybit_result = order_result.get('bybit')
    if isinstance(bybit_result, dict):
        print("\n[BYBIT]")
        print(bybit_format_limit_message(bybit_result))
    
