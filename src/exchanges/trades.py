"""
Round trips (entry -> exit) rebuilt from exchange account orders, for the web
chart's Exchange source: each trade is drawn like a backtest trade (TP / SL boxes,
"#3 LONG" entry arrow, win / loss exit dot).

Input: the normalized orders and positions of src/exchanges/account.py.
Per symbol and position side, filled orders are replayed in time order: entries
grow the position, exits (stop loss, take profit, reduce-only close) shrink it, and
the trade closes when the size is back to zero. Its SL / TP are the first stop /
target orders placed during the trade (the plan), or the ones working now for an
open trade. Orders before the history window are not known: an exit whose entry is
older is skipped, and an open position without an entry in the window becomes an
open trade from the position itself.

Exchanges forget part of the history: Bybit no longer lists the cancelled SL (or TP)
of a trade after a few days, only the order that filled. `known_levels` (the SL / TP
the bot itself placed, by exchange order id: its order history and tracker) fills in
a level the exchange no longer reports.

Trades use the chart file format (src/evaluate/export_live_data.py): type, entryTime,
exitTime (unix seconds, not snapped to bars), entryPrice, exitPrice, stopLoss,
takeProfit, status open | closed, result Win | Loss | Pending.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping, Optional

from src.exchanges.account import AccountOrder, AccountPosition

PROTECT_KINDS = ("stop_loss", "take_profit")
EXIT_REASON = {"stop_loss": "SL", "take_profit": "TP", "close": "Close"}
# Margin for SL / TP orders placed just before the entry fill is reported
PROTECT_LEAD_SECONDS = 2.0


def _fill_time(order: AccountOrder) -> float:
    return order.get("updated") or order.get("time") or 0.0


def _weighted(parts: list[tuple[float, float]]) -> Optional[float]:
    size = sum(q for _, q in parts)
    return sum(p * q for p, q in parts) / size if size > 0 else None


def _level(order: AccountOrder) -> Optional[float]:
    return order.get("triggerPrice") or order.get("price")


def _plan_level(protect: list[AccountOrder], kind: str, start: float, end: float) -> Optional[float]:
    """The first SL / TP order placed between the entry and the exit."""
    placed = [
        o for o in protect
        if o["kind"] == kind and o.get("time") is not None
        and start - PROTECT_LEAD_SECONDS <= o["time"] <= end + 1
    ]
    placed.sort(key=lambda o: o["time"])
    return _level(placed[0]) if placed else None


def _working_level(protect: list[AccountOrder], kind: str) -> Optional[float]:
    working = sorted((o for o in protect if o["kind"] == kind and o["status"] == "open"), key=lambda o: o.get("time") or 0)
    return _level(working[-1]) if working else None


class _Trade:
    def __init__(self, symbol: str, side: str):
        self.symbol = symbol
        self.side = side
        self.entries: list[tuple[AccountOrder, float]] = []
        self.exits: list[tuple[AccountOrder, float]] = []
        self.size = 0.0
        self.peak = 0.0

    @property
    def entry_time(self) -> Optional[float]:
        return (_fill_time(self.entries[0][0]) or None) if self.entries else None

    @property
    def exit_time(self) -> Optional[float]:
        return _fill_time(self.exits[-1][0]) if self.exits else None


def _bot_levels(t: _Trade, known_levels: Mapping[str, dict]) -> dict:
    """SL / TP the bot recorded for one of the trade's entry orders (by order id or client id)."""
    for order, _ in t.entries:
        for oid in (order.get("id"), order.get("clientId")):
            levels = known_levels.get(str(oid)) if oid else None
            if levels:
                return levels
    return {}


def _trade_dict(
    t: _Trade,
    protect: list[AccountOrder],
    position: Optional[AccountPosition],
    now: float,
    known_levels: Mapping[str, dict],
) -> dict:
    sign = 1 if t.side == "LONG" else -1
    entry_price = _weighted([(o["average"], q) for o, q in t.entries])
    closed = position is None
    exit_price = _weighted([(o["average"], q) for o, q in t.exits]) if t.exits else None
    start = t.entry_time
    end = t.exit_time if closed else now
    first = t.entries[0][0] if t.entries else None

    if closed:
        stop = _plan_level(protect, "stop_loss", start, end) or (first or {}).get("stopLoss")
        target = _plan_level(protect, "take_profit", start, end) or (first or {}).get("takeProfit")
    else:
        # Open: the levels working on the exchange now
        entry_price = position.get("entryPrice") or entry_price
        stop = position.get("stopLoss") or _working_level(protect, "stop_loss") or _plan_level(protect, "stop_loss", start or 0, now)
        target = position.get("takeProfit") or _working_level(protect, "take_profit") or _plan_level(protect, "take_profit", start or 0, now)

    # Levels the exchange no longer lists: the ones the bot placed with this entry
    if stop is None or target is None:
        bot = _bot_levels(t, known_levels)
        stop = stop or bot.get("stopLoss")
        target = target or bot.get("takeProfit")

    if closed and entry_price is not None:
        pnl = sum((o["average"] - entry_price) * q * sign for o, q in t.exits)
    else:
        pnl = (position or {}).get("unrealizedPnl")
    pnl_r = None
    if closed and entry_price and exit_price and stop and abs(entry_price - stop) > 0:
        pnl_r = (exit_price - entry_price) * sign / abs(entry_price - stop)

    order_ids = [o["id"] for o, _ in t.entries + t.exits]
    order_ids += [o["id"] for o in protect if start is not None and start - PROTECT_LEAD_SECONDS <= (o.get("time") or 0) <= end + 1]
    return {
        "id": f"{t.symbol}:{t.side}:{first['id'] if first else 'position'}",
        "symbol": t.symbol,
        "type": t.side,
        "timeframe": None,
        "entryTime": start,
        "exitTime": t.exit_time if closed else None,
        "entryPrice": entry_price,
        "exitPrice": exit_price if closed else None,
        "stopLoss": stop,
        "takeProfit": target,
        "size": t.peak if closed else (position or {}).get("size") or t.size,
        "status": "closed" if closed else "open",
        "result": ("Win" if pnl and pnl > 0 else "Loss") if closed else "Pending",
        "exitReason": EXIT_REASON.get(t.exits[-1][0]["kind"]) if closed and t.exits else None,
        "pnl": pnl,
        "pnlR": pnl_r,
        "orders": list(dict.fromkeys(order_ids)),
    }


def build_trades(
    orders: Iterable[AccountOrder],
    positions: Iterable[AccountPosition],
    limit: int = 30,
    now: float = 0.0,
    known_levels: Optional[Mapping[str, dict]] = None,
) -> list[dict]:
    """Open trades plus the `limit` most recent closed ones, oldest first (chart order).

    known_levels: {exchange order id: {"stopLoss", "takeProfit"}} placed by the bot."""
    known_levels = known_levels or {}
    groups: dict[tuple[str, str], list[AccountOrder]] = defaultdict(list)
    for order in orders:
        if order.get("positionSide") in ("LONG", "SHORT"):
            groups[(order["symbol"], order["positionSide"])].append(order)
    open_positions = {(p["symbol"], p["side"]): p for p in positions if p.get("size")}
    now = now or max([_fill_time(o) for g in groups.values() for o in g] + [0.0])

    closed: list[dict] = []
    opened: list[dict] = []
    for key in set(groups) | set(open_positions):
        symbol, side = key
        items = groups.get(key, [])
        protect = [o for o in items if o["kind"] in PROTECT_KINDS]
        # Same second: entries first (Lighter times are whole seconds)
        fills = sorted((o for o in items if o["filled"] > 0 and o.get("average")),
                       key=lambda o: (_fill_time(o), o["kind"] != "entry"))
        current: Optional[_Trade] = None
        for order in fills:
            if order["kind"] == "entry":
                current = current or _Trade(symbol, side)
                current.entries.append((order, order["filled"]))
                current.size += order["filled"]
                current.peak = max(current.peak, current.size)
                continue
            if current is None:
                continue  # closes a position opened before the history window
            qty = min(order["filled"], current.size)
            current.exits.append((order, qty))
            current.size -= qty
            if current.size <= current.peak * 1e-6:
                closed.append(_trade_dict(current, protect, None, now, known_levels))
                current = None

        position = open_positions.get(key)
        if position is not None:
            if current is None:
                # Entry older than the history: start the trade at its first working SL / TP order
                current = _Trade(symbol, side)
                times = [o["time"] for o in protect if o["status"] == "open" and o.get("time")]
                started = min(times) if times else position.get("time")
                current.entries.append(({"id": "position", "average": position.get("entryPrice"), "time": started, "updated": started}, position["size"]))
                current.size = current.peak = position["size"]
            opened.append(_trade_dict(current, protect, position, now, known_levels))
        # A trade still open here without a position on the exchange lost its exit to the
        # history limit: left out rather than drawn as a position that does not exist

    closed.sort(key=lambda t: t["exitTime"] or 0)
    trades = closed[-limit:] + opened if limit > 0 else opened
    return sorted(trades, key=lambda t: t["entryTime"] or now)
