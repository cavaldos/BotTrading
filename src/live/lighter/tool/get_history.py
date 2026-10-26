"""
Trade history on Lighter (same functions as src/live/bybit/tool/get_history.py).

    python -m src.live.lighter.tool.get_history                # realized P&L, last 30 days
    python -m src.live.lighter.tool.get_history --orders       # filled / cancelled orders
    python -m src.live.lighter.tool.get_history --symbol BTC --days 7 --limit 100

Needs the API key (LIGHTER_API_PRIVATE_KEY): Lighter only shows orders and fills with an auth token.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.live.lighter.exchange import LighterAPIError, LighterSignerError, get_exchange

GREEN, RED, RESET = '\033[92m', '\033[91m', '\033[0m'


def _since_ms(days: int) -> int:
    return int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)


def get_order_history(
    symbol: str = None,
    limit: int = 50,
    days: int = 7,
    order_status: Optional[str] = 'filled',
) -> list:
    """
    Closed orders, newest first.

    Args:
        symbol: Symbol (optional, None = all markets)
        limit: max records
        days: how many days back
        order_status: 'filled', 'canceled' (any cancel reason), or None for all
    """
    exchange = get_exchange()
    orders = exchange.fetch_closed_orders(symbol=symbol, limit=limit if order_status is None else limit * 3, since=_since_ms(days))
    if order_status == 'filled':
        orders = [o for o in orders if o['raw_status'] == 'filled']
    elif order_status:
        orders = [o for o in orders if o['raw_status'].startswith(order_status)]
    return orders[:limit]


def get_trade_history(symbol: str = None, limit: int = 100, days: int = 7) -> list:
    """Every fill of the account, newest first."""
    return get_exchange().fetch_my_trades(symbol=symbol, limit=limit, since=_since_ms(days))


def get_closed_pnl_history(symbol: str = None, limit: int = 50, days: int = 7) -> list:
    """
    Realized P&L per closing order, newest first. Keys: order_id, symbol, side (side of the
    closed position), qty, avg_entry_price, avg_exit_price, closed_pnl, time (ms), type.
    """
    return get_exchange().fetch_closed_pnl(symbol=symbol, limit=limit, since=_since_ms(days))


def print_pnl_history(symbol: str = None, limit: int = 50, days: int = 7):
    records = get_closed_pnl_history(symbol=symbol, limit=limit, days=days)
    if not records:
        print("Không có lịch sử giao dịch nào.")
        return

    print("\n" + "=" * 140)
    print(f"{'Order ID':<20} {'Symbol':<10} {'Side':<6} {'Entry Price':<14} {'Exit Price':<14} {'Qty':<12} {'PnL (USDC)':<15} {'Time'}")
    print("=" * 140)
    total_pnl = 0.0
    wins = 0
    for record in records:
        pnl = record['closed_pnl']
        total_pnl += pnl
        wins += pnl > 0
        color = GREEN if pnl >= 0 else RED
        time_str = datetime.fromtimestamp(record['time'] / 1000).strftime('%Y-%m-%d %H:%M:%S') if record['time'] else '-'
        label = record['symbol'] + (' LIQ' if record['type'] == 'liquidation' else '')
        print(
            f"{record['order_id']:<20} {label:<10} {record['side']:<6} {record['avg_entry_price']:<14.4f} "
            f"{record['avg_exit_price']:<14.4f} {record['qty']:<12.4f} {color}{pnl:<+15.4f}{RESET} {time_str}"
        )
    print("=" * 140)
    total_color = GREEN if total_pnl >= 0 else RED
    print(f"{'TOTAL PnL:':<80} {total_color}{total_pnl:<+15.4f}{RESET}")
    print(f"Số lượng giao dịch: {len(records)} (thắng {wins}, tỉ lệ {wins / len(records) * 100:.1f}%)")
    print("=" * 140 + "\n")


def print_order_history(symbol: str = None, limit: int = 50, days: int = 7, order_status: Optional[str] = None):
    orders = get_order_history(symbol=symbol, limit=limit, days=days, order_status=order_status)
    if not orders:
        print("Không có lệnh nào.")
        return
    print(f"\n{'Order ID':<18} {'Symbol':<9} {'Side':<5} {'Type':<18} {'Amount':>12} {'Filled':>12} {'Avg':>12} {'Status':<28} {'Time'}")
    print("-" * 140)
    for order in orders:
        time_str = datetime.fromtimestamp(order['timestamp'] / 1000).strftime('%m-%d %H:%M:%S') if order['timestamp'] else '-'
        print(
            f"{order['id']:<18} {order['symbol']:<9} {order['side'].upper():<5} {order['type']:<18} "
            f"{order['amount']:>12g} {order['filled']:>12g} {order['average'] or 0:>12g} {order['raw_status']:<28} {time_str}"
        )
    print(f"\nTổng: {len(orders)} lệnh\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lighter trade history")
    parser.add_argument("--symbol", default=None, help="BTC, BTCUSDT, ... (mặc định: tất cả)")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--orders", action="store_true", help="xem lệnh đã đóng thay vì P&L")
    parser.add_argument("--status", default=None, help="với --orders: filled hoặc canceled")
    args = parser.parse_args()

    try:
        if args.orders:
            print_order_history(symbol=args.symbol, limit=args.limit, days=args.days, order_status=args.status)
        else:
            print_pnl_history(symbol=args.symbol, limit=args.limit, days=args.days)
    except (LighterAPIError, LighterSignerError, ValueError) as e:
        print(f"❌ {e}")


if __name__ == '__main__':
    main()
