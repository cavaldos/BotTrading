"""
Orders on the exchange accounts for the web chart's Orders tab (GET /api/exchange/orders).

Unlike /api/orders (the orders this bot placed and follows), this lists every
order on the account: the bot's, the web's and the ones placed by hand on the
exchange, plus open positions. Read-only.

Answers are cached for `ttl` seconds per (exchange, symbol, days): several browser
tabs polling at once cost the exchange one request set (Lighter allows 60 per minute).
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Iterable, Optional

from src.exchanges import account_views, active_exchange_name, get_account_view
from src.exchanges.trades import build_trades
from src.live.web.orders import OrderError

MAX_DAYS = 30
DEFAULT_DAYS = 7
MAX_ORDERS = 300
TRADES_DAYS = 30  # history read to rebuild the last trades
MAX_TRADES = 100


def _short_error(exc: Exception) -> str:
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    text = lines[0] if lines else type(exc).__name__
    return text[:300]


class ExchangeOrdersService:
    def __init__(
        self,
        symbols: Iterable[str] = (),
        ttl: float = 10.0,
        clock: Callable[[], float] = time.time,
        views: Optional[Callable[[], list[dict]]] = None,
        loader: Optional[Callable[[str], object]] = None,
        known_levels: Optional[Callable[[], dict]] = None,
    ):
        self.symbols = [str(s).upper() for s in symbols]
        self.ttl = float(ttl)
        self.clock = clock
        self._views = views or account_views
        self._loader = loader or get_account_view
        # {exchange order id: {stopLoss, takeProfit}} placed by the bot: fills in levels the exchange forgot
        self._known_levels = known_levels or (lambda: {})
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self._locks: dict[tuple, threading.Lock] = {}
        self._lock = threading.Lock()
        self._accounts: Optional[list[dict]] = None

    def accounts(self) -> list[dict]:
        """[{name, label, configured, active}]; active = the exchange the bot trades on."""
        if self._accounts is None:
            active = active_exchange_name()
            self._accounts = [dict(v, active=v["name"] == active) for v in self._views()]
        return self._accounts

    def fetch(self, exchange: str, symbol: Optional[str] = None, days: Optional[float] = None, force: bool = False) -> dict:
        names = {a["name"]: a for a in self.accounts()}
        name = str(exchange or "").strip().lower() or next((a["name"] for a in self.accounts() if a["active"]), "")
        account = names.get(name)
        if account is None:
            raise OrderError(f"Unknown exchange '{exchange}'. Known: {', '.join(names)}")
        if not account["configured"]:
            raise OrderError(f"{account['label']}: no API key in the bot's .env")
        symbol = str(symbol or "").strip().upper() or None
        if symbol and not symbol.replace("_", "").isalnum():
            raise OrderError("Invalid symbol")
        try:
            days = float(days) if days not in (None, "") else DEFAULT_DAYS
        except (TypeError, ValueError):
            raise OrderError("days must be a number")
        days = min(max(days, 1.0), MAX_DAYS)

        key = (name, symbol, days)
        with self._lock:
            key_lock = self._locks.setdefault(key, threading.Lock())
        with key_lock:  # one exchange call per key at a time; others wait for its answer
            cached = self._cache.get(key)
            now = self.clock()
            # Refresh button: a fresh read, but not more than one per 2 s
            if cached and now - cached[0] < (min(2.0, self.ttl) if force else self.ttl):
                return cached[1]
            since_ms = int((now - days * 86400) * 1000)
            try:
                data = self._loader(name).fetch_account(symbol=symbol, since_ms=since_ms, limit=MAX_ORDERS, symbols=self.symbols)
            except Exception as exc:
                raise ExchangeReadError(f"{account['label']}: {_short_error(exc)}") from exc
            result = {
                "exchange": name,
                "label": account["label"],
                "symbol": symbol,
                "days": days,
                "fetchedAt": self.clock(),
                "orders": data.get("orders") or [],
                "positions": data.get("positions") or [],
            }
            self._cache[key] = (now, result)
            return result


    def trades(self, exchange: str, symbol: Optional[str] = None, limit: Optional[int] = 30, force: bool = False) -> dict:
        """Open trades + the `limit` latest closed ones (entry -> exit, with SL / TP) for the chart,
        with the working orders and positions (for the price lines)."""
        try:
            limit = int(limit) if limit not in (None, "") else 30
        except (TypeError, ValueError):
            raise OrderError("limit must be a number")
        limit = min(max(limit, 1), MAX_TRADES)
        data = self.fetch(exchange, symbol, TRADES_DAYS, force=force)
        return {
            "exchange": data["exchange"],
            "label": data["label"],
            "symbol": data["symbol"],
            "days": data["days"],
            "fetchedAt": data["fetchedAt"],
            "trades": build_trades(data["orders"], data["positions"], limit=limit, now=self.clock(), known_levels=self._levels()),
            "orders": [o for o in data["orders"] if o["status"] == "open"],
            "positions": data["positions"],
        }


    def _levels(self) -> dict:
        try:
            return self._known_levels() or {}
        except Exception:  # the bot's log files must not break the chart
            return {}


class ExchangeReadError(Exception):
    """The exchange could not be read (network, API key, rate limit)."""
