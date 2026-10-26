"""
Order placement for the live bot, on whichever exchange is active (live.yaml `exchange`).

Every function returns the adapter's order result dict (see src/exchanges/base.py)
plus `exchange` (adapter name) and never raises: errors come back as
{'success': False, 'error': ...}.
"""

from datetime import datetime, timezone
from typing import Optional

from src.config import load_strategy_config
from src.exchanges import get_exchange

STRATEGY_CONFIG = load_strategy_config()
DEFAULT_RISK_PER_TRADE = float(STRATEGY_CONFIG.get("risk_per_trade", 5) or 5)
DEFAULT_RISK_REWARD_RATIO = float(STRATEGY_CONFIG.get("risk_reward_ratio", 2.0) or 2.0)
risk_per_trade = DEFAULT_RISK_PER_TRADE
risk_reward_ratio = DEFAULT_RISK_REWARD_RATIO


def _side(order_type: str) -> str:
    return 'buy' if str(order_type).strip().upper() in ('BUY', 'LONG') else 'sell'


def _failed(error: Exception, **extra) -> dict:
    try:
        name = get_exchange().name
    except Exception:
        name = ""
    return {'success': False, 'error': str(error), 'exchange': name, **extra}


def exchange_label() -> str:
    """Display name of the active exchange ('Bybit', 'Lighter', ...)."""
    try:
        return get_exchange().label
    except Exception as error:
        return f"Exchange ({error})"


def format_order_message(result: dict, order_type: str = "market") -> str:
    """Readable text of an order result, formatted by the exchange that placed it."""
    try:
        return get_exchange(result.get('exchange') or None).format_order_message(result, order_type)
    except Exception:
        error = result.get('error') or 'Unknown error'
        return f"❌ THẤT BẠI\n└─ Lỗi: {error}"


def short_error(text: str) -> str:
    """One line from an exchange error message."""
    try:
        return get_exchange().short_error(text)
    except Exception:
        return str(text or "")[:300]


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
    try:
        exchange = get_exchange()
        result = exchange.place_market_order(
            symbol=symbol,
            side=_side(order_type),
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=leverage,
            risk_per_trade=risk_per_trade,
            risk_reward_ratio=risk_reward_ratio,
            signal_timestamp=signal_timestamp,
        )
        return dict(result, exchange=exchange.name)
    except Exception as e:
        return _failed(e)


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
    try:
        exchange = get_exchange()
        result = exchange.place_limit_order(
            symbol=symbol,
            side=_side(order_type),
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=leverage,
            risk_per_trade=risk_per_trade,
            risk_reward_ratio=risk_reward_ratio,
            signal_timestamp=signal_timestamp,
            time_in_force=time_in_force,
        )
        return dict(result, exchange=exchange.name, order_type='limit')
    except Exception as e:
        return _failed(e, order_type='limit')


def cancel_order(symbol: str, order_id: str) -> dict:
    try:
        exchange = get_exchange()
        return dict(exchange.cancel_order(symbol, order_id), exchange=exchange.name)
    except Exception as e:
        return _failed(e)


def check_order_status(symbol: str, order_id: str) -> dict:
    try:
        exchange = get_exchange()
        return dict(exchange.order_status(symbol, order_id), exchange=exchange.name)
    except Exception as e:
        return _failed(e)


if __name__ == "__main__":
    # Places a REAL order on the active exchange. Edit the values, then run:
    #   python -m src.live.core.place_order
    order_result = place_limit_order(
        symbol="BCHUSDT_1m",
        order_type="SELL",
        entry_price=514,
        stop_loss=530,
        take_profit=None,
        entry_time=datetime.now(timezone.utc).isoformat(),
        leverage=10,
        risk_reward_ratio=2.0,
        risk_per_trade=5,
    )
    print(f"\n[{exchange_label().upper()}]")
    print(format_order_message(order_result, "limit"))
