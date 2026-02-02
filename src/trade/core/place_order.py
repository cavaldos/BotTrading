import yaml
import time
from datetime import datetime
from pathlib import Path


from src.trade.bybit.order_executor import (
    validate_and_place_order as bybit_place_order,
    validate_and_place_limit_order as bybit_place_limit_order,
    bybit_format_order_message,
    bybit_format_limit_order_message,
    cancel_limit_order as bybit_cancel_limit_order,
    check_limit_order_status as bybit_check_limit_order_status,
)



# Load config
def load_config() -> dict:
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, "r") as f:
        return yaml.safe_load(f) or {}


CONFIG = load_config()
risk_per_trade = CONFIG.get("strategy", {}).get("risk_per_trade", 5)
risk_reward_ratio = CONFIG.get("strategy", {}).get("risk_reward_ratio", 2.0)


def place_order(
    symbol: str,
    order_type: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    entry_time: str,
    leverage: int = 20,
    risk_per_trade: float = risk_per_trade,
    signal_timestamp: float = None,
) -> dict:
    """    
    Args:
        risk_per_trade: load from config
        signal_timestamp: Thời điểm nhận tín hiệu (time.time()), dùng để đo latency
    
    Returns:
        dict: result of placing order on Bybit
    """
    order_type = order_type.strip().upper()
    side = 'buy' if order_type in ('BUY', 'LONG') else 'sell'
    
    result = {
        'success': False,
        'bybit': None,
    }
    
    try:
        bybit_result = bybit_place_order(
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
    take_profit: float = None,
    entry_time: str = None,
    leverage: int = 20,
    risk_per_trade: float = risk_per_trade,
    signal_timestamp: float = None,
    time_in_force: str = 'GTC',
) -> dict:
    """
    Đặt lệnh LIMIT trên Bybit.
    
    Args:
        symbol: Symbol giao dịch (e.g., 'BTCUSDT_1m')
        order_type: 'BUY', 'LONG', 'SELL', 'SHORT'
        entry_price: Giá limit để vào lệnh
        stop_loss: Giá stop loss
        take_profit: Giá take profit (optional, tự tính nếu None)
        entry_time: Thời gian entry (optional)
        leverage: Đòn bẩy (default 20)
        risk_per_trade: Rủi ro USD (load from config)
        signal_timestamp: Thời điểm nhận tín hiệu (time.time()), dùng để đo latency
        time_in_force: 'GTC', 'IOC', 'FOK', 'PostOnly' (default 'GTC')
    
    Returns:
        dict: result of placing limit order on Bybit with order_id
    """
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
    """
    Hủy lệnh limit đang chờ.
    
    Args:
        symbol: Symbol giao dịch
        order_id: ID của lệnh cần hủy
    
    Returns:
        dict: Kết quả hủy lệnh
    """
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
    """
    Kiểm tra trạng thái lệnh.
    
    Args:
        symbol: Symbol giao dịch
        order_id: ID của lệnh cần kiểm tra
    
    Returns:
        dict: Trạng thái lệnh
    """
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
        risk_per_trade=5,
    )

    if order_result.get('bybit'):
        print("\n[BYBIT]")
        print(bybit_format_order_message(order_result.get('bybit')))
    