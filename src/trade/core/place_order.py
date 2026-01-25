import os
import sys
import yaml
import pandas as pd
from datetime import datetime
from pathlib import Path


from src.trade.bybit.order_executor import validate_and_place_order as bybit_place_order, bybit_format_order_message
from src.trade.okx.order_executor import validate_and_place_order as okx_place_order, okx_format_order_message



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
    risk_per_trade: float = None,
    exchanges: list = None,
) -> dict:
    """    
    Args:
        risk_per_trade: load from config
        exchanges:  ['bybit', 'okx']
    
    Returns:
        dict: result of placing order on each exchange
    """
    
    if exchanges is None:
        exchanges = ['bybit', 'okx'] 
    
    order_type = order_type.strip().upper()
    side = 'buy' if order_type == 'LONG' else 'sell'
    
    results = {
        'success': False,
        'bybit': None,
        'okx': None,
        'any_success': False,
    }
    
    # Đặt lệnh trên Bybit
    if 'bybit' in exchanges:
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
            results['bybit'] = bybit_result
            if bybit_result.get('success'):
                results['any_success'] = True
        except Exception as e:
            results['bybit'] = {
                'success': False,
                'error': str(e),
                'exchange': 'Bybit',
            }
    
    # Đặt lệnh trên OKX
    if 'okx' in exchanges:
        try:
            okx_result = okx_place_order(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                leverage=leverage,
                risk_per_trade=risk_per_trade,
            )
            results['okx'] = okx_result
            if okx_result.get('success'):
                results['any_success'] = True
        except Exception as e:
            results['okx'] = {
                'success': False,
                'error': str(e),
                'exchange': 'OKX',
            }
    
    # Đánh dấu success nếu ít nhất 1 sàn thành công
    results['success'] = results['any_success']
    
    return results 

