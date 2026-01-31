import yaml
from datetime import datetime
from pathlib import Path


from src.trade.bybit.order_executor import validate_and_place_order as bybit_place_order, bybit_format_order_message



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


def place_order(
    symbol: str,
    order_type: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    entry_time: str,
    leverage: int = 20,
    risk_per_trade: float = risk_per_trade,
) -> dict:
    """    
    Args:
        risk_per_trade: load from config
    
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

if __name__ == "__main__":
    order_result = place_order(
        symbol="BCHUSDT_1m",
        order_type="LONG",
        entry_price=575.1,
        stop_loss=576.8,
        take_profit=None,
        entry_time=datetime.utcnow().isoformat(),
        leverage=10,
        risk_per_trade=5,
    )

    if order_result.get('bybit'):
        print("\n[BYBIT]")
        print(bybit_format_order_message(order_result.get('bybit')))
    