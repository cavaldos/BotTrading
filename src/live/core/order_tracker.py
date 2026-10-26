"""
Follows orders on the exchange after they are placed (approved bot signals and
manual web orders) and reports whether they fill.

  tracker.track(...)                      -> listeners get ("placed", order)
  first partial fill                      -> ("partial", order)
  fully filled                            -> ("filled", order)
  nothing filled: cancelled or rejected on the exchange, still unfilled at its
  deadline, or price reached the target first (the last two are cancelled here)
                                          -> ("not_filled", order)
  cancelled from the web                  -> ("cancelled", order)
  the exchange never knows the order id   -> ("unknown", order)

The exchange is polled every `poll_seconds`. State is saved to a JSON file, so
orders still waiting are followed again after a restart.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Optional


TRACKING_DEFAULTS: dict[str, Any] = {
    "poll_seconds": 5,
    "limit_timeout_minutes": 30,     # 0 = keep a limit order until it fills or is cancelled
    "cancel_if_target_hit": True,    # cancel a limit order when price reaches its target first
    "file": "res/logs/tracked_orders.json",
}

WAITING = "WAITING"          # on the exchange, nothing filled yet
PARTIAL = "PARTIAL"          # partly filled, the rest still open
FILLED = "FILLED"            # fully filled, or partly filled and the rest cancelled
NOT_FILLED = "NOT_FILLED"    # closed with nothing filled
CANCELLED = "CANCELLED"      # cancelled from the web with nothing filled
UNKNOWN = "UNKNOWN"          # the exchange does not know the order id
OPEN_STATUSES = (WAITING, PARTIAL)
FINAL_EVENTS = {FILLED: "filled", NOT_FILLED: "not_filled", CANCELLED: "cancelled", UNKNOWN: "unknown"}

MISSING_LIMIT = 6            # "not found" answers in a row ...
MISSING_SECONDS = 60         # ... over at least this long -> UNKNOWN
ERROR_LOG_SECONDS = 300      # log the same order's API error at most this often
HISTORY = 100                # finished orders kept in the file


def load_tracking_config(live_config: dict | None) -> dict[str, Any]:
    raw = (live_config or {}).get("order_tracking") or {}
    cfg = dict(TRACKING_DEFAULTS)
    if isinstance(raw, dict):
        cfg.update({k: v for k, v in raw.items() if v is not None})
    return cfg


def _num(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # drop NaN


def fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "—"
    size = abs(value)
    digits = 2 if size >= 1000 else 3 if size >= 10 else 4 if size >= 1 else 5 if size >= 0.01 else 7
    return f"{value:,.{digits}f}"


def fmt_qty(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:g}"


def fmt_duration(seconds: float, vi: bool = False) -> str:
    minutes = max(1, round(seconds / 60))
    hours, rest = divmod(minutes, 60)
    m, h = ("phút", "giờ") if vi else ("min", "h")
    if not hours:
        return f"{minutes} {m}"
    return f"{hours} {h}" + (f" {rest} {m}" if rest else "")


def _short_error(exc: Exception) -> str:
    text = str(exc)
    for marker in ('"retMsg":"', "retMsg': '"):
        if marker in text:
            return text.split(marker, 1)[1].split('"', 1)[0].split("'", 1)[0]
    return text[:200]


@dataclass
class TrackedOrder:
    source: str                     # "signal" (approved bot signal) | "web" (manual order)
    ref: str                        # signal id / manual order id
    symbol: str
    side: str                       # LONG | SHORT
    order_type: str                 # market | limit
    order_id: str                   # exchange order id
    price: float                    # limit price, or expected price of a market order
    stop_loss: float
    take_profit: Optional[float]
    quantity: float
    risk: float = 0.0
    key: str = ""                   # sent_orders.json key of a bot signal
    timeframe: str = ""
    sl_tp_missing: str = ""         # error when SL/TP could not be set on the position
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    placed_at: float = field(default_factory=time.time)
    expires_at: float = 0.0         # limit orders: cancelled when still unfilled by then (0 = never)
    status: str = WAITING
    filled: float = 0.0
    avg_price: Optional[float] = None
    filled_at: float = 0.0
    closed_at: float = 0.0
    updated_at: float = field(default_factory=time.time)
    reason: str = ""
    reason_vi: str = ""
    missing: int = 0
    missing_since: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_limit(self) -> bool:
        return self.order_type == "limit"

    @property
    def label(self) -> str:
        return f"{self.side} {self.symbol} {self.order_type}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "ref": self.ref,
            "symbol": self.symbol,
            "side": self.side,
            "orderType": self.order_type,
            "orderId": self.order_id,
            "timeframe": self.timeframe,
            "price": self.price,
            "stopLoss": self.stop_loss,
            "takeProfit": self.take_profit,
            "quantity": self.quantity,
            "risk": self.risk,
            "placedAt": self.placed_at,
            "expiresAt": self.expires_at,
            "status": self.status,
            "filled": self.filled,
            "avgPrice": self.avg_price,
            "filledAt": self.filled_at,
            "closedAt": self.closed_at,
            "updatedAt": self.updated_at,
            "reason": self.reason,
            "slTpMissing": self.sl_tp_missing,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrackedOrder":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def format_order_message(order: TrackedOrder, event: str) -> str:
    """Telegram / Discord text for a fill event."""
    head = {
        "filled": "✅ LỆNH ĐÃ KHỚP",
        "partial": "🟡 LỆNH KHỚP MỘT PHẦN",
        "not_filled": "⌛ LỆNH KHÔNG KHỚP",
        "cancelled": "🚫 ĐÃ HỦY LỆNH",
        "unknown": "⚠️ KHÔNG XÁC NHẬN ĐƯỢC LỆNH",
    }.get(event, event.upper())
    source = f"Bot {order.timeframe}".strip() if order.source == "signal" else "Web"
    lines = [f"{head}: {order.side} {order.symbol} ({order.order_type.upper()}, {source})"]
    if order.filled > 0:
        lines.append(f"├─ Giá khớp: {fmt_price(order.avg_price or order.price)}")
        lines.append(f"├─ Khối lượng: {fmt_qty(order.filled)} / {fmt_qty(order.quantity)}")
    else:
        lines.append(f"├─ Giá {'limit' if order.is_limit else 'dự kiến'}: {fmt_price(order.price)}")
    lines.append(f"├─ SL: {fmt_price(order.stop_loss)} | TP: {fmt_price(order.take_profit)}")
    if order.reason_vi or order.reason:
        lines.append(f"├─ Lý do: {order.reason_vi or order.reason}")
    if order.sl_tp_missing and order.filled > 0:
        lines.append(f"├─ ⚠️ CHƯA ĐẶT ĐƯỢC SL/TP TRÊN SÀN: {order.sl_tp_missing}")
    lines.append(f"└─ Order ID: {order.order_id}")
    return "\n".join(lines)


def _default_exchange():
    from src.live.bybit.exchange import get_exchange
    return get_exchange()


def _default_market(symbol: str) -> str:
    from src.live.bybit.exchange import format_symbol
    from src.live.bybit.risk_calculator import clean_symbol
    return format_symbol(clean_symbol(symbol))


Listener = Callable[[str, TrackedOrder], None]


class OrderTracker:
    def __init__(
        self,
        file: str = TRACKING_DEFAULTS["file"],
        poll_seconds: float = TRACKING_DEFAULTS["poll_seconds"],
        limit_timeout_minutes: float = TRACKING_DEFAULTS["limit_timeout_minutes"],
        cancel_if_target_hit: bool = TRACKING_DEFAULTS["cancel_if_target_hit"],
        exchange: Optional[Callable[[], Any]] = None,
        market_of: Optional[Callable[[str], str]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self.file = Path(file)
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.limit_timeout_minutes = max(0.0, float(limit_timeout_minutes or 0))
        self.cancel_if_target_hit = bool(cancel_if_target_hit)
        self._exchange = exchange or _default_exchange
        self._market_of = market_of or _default_market
        self.on_log = on_log or (lambda msg: None)
        self.on_error = on_error or (lambda msg: print(f"Order tracker error: {msg}"))
        self._orders: dict[str, TrackedOrder] = {}
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()        # _orders and the file
        self._work = threading.RLock()       # one exchange action at a time (poll vs cancel)
        self._events: Queue = Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._error_logged: dict[str, float] = {}
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="OrderTracker")
        self._dispatch_thread = threading.Thread(target=self._dispatch_loop, daemon=True, name="OrderTrackerDispatch")

    # ---------------- lifecycle ----------------

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def start(self) -> "OrderTracker":
        self._load()
        waiting = sum(1 for o in self._orders.values() if o.is_open)
        if waiting:
            self.on_log(f"Following {waiting} order(s) left open by the last run")
        self._dispatch_thread.start()
        self._poll_thread.start()
        return self

    def stop(self) -> None:
        deadline = time.time() + 3
        while not self._events.empty() and time.time() < deadline:
            time.sleep(0.05)
        self._stop.set()
        self._wake.set()

    # ---------------- public ----------------

    def track(
        self,
        *,
        source: str,
        ref: str,
        symbol: str,
        side: str,
        order_type: str,
        order_id: str,
        price: float,
        stop_loss: float,
        take_profit: Optional[float],
        quantity: float,
        risk: float = 0.0,
        key: str = "",
        timeframe: str = "",
        timeout_minutes: Optional[float] = None,
        sl_tp_missing: str = "",
    ) -> TrackedOrder:
        """Start following an order that the exchange accepted."""
        order = TrackedOrder(
            source=source,
            ref=str(ref),
            symbol=str(symbol).upper(),
            side=str(side).upper(),
            order_type=str(order_type).lower(),
            order_id=str(order_id),
            price=float(price),
            stop_loss=float(stop_loss),
            take_profit=_num(take_profit),
            quantity=float(quantity or 0),
            risk=float(risk or 0),
            key=key,
            timeframe=timeframe,
            sl_tp_missing=sl_tp_missing or "",
        )
        minutes = self.limit_timeout_minutes if timeout_minutes is None else max(0.0, float(timeout_minutes))
        if order.is_limit and minutes > 0:
            order.expires_at = order.placed_at + minutes * 60
        with self._lock:
            self._orders[order.id] = order
            self._save_locked()
        self.on_log(f"{order.label}: following order {order.order_id}")
        self._emit("placed", order)
        self._wake.set()  # first check right away (market orders fill at once)
        return order

    def get(self, tracked_id: str) -> Optional[TrackedOrder]:
        with self._lock:
            return self._orders.get(tracked_id)

    def recent(self, limit: int = 30) -> list[dict]:
        """Orders still waiting first (newest first), then finished ones (latest first)."""
        with self._lock:
            items = list(self._orders.values())
        waiting = sorted((o for o in items if o.is_open), key=lambda o: o.placed_at, reverse=True)
        done = sorted((o for o in items if not o.is_open), key=lambda o: o.closed_at or o.updated_at, reverse=True)
        return [o.to_dict() for o in (waiting + done)[:limit]]

    def cancel(self, tracked_id: str) -> tuple[bool, str, Optional[TrackedOrder]]:
        """Cancel a limit order still waiting to fill. Returns (ok, message, order)."""
        with self._work:
            order = self.get(tracked_id)
            if order is None:
                return False, "Order not found", None
            if not order.is_open:
                return False, f"Order already {order.status.lower().replace('_', ' ')}", order
            if not order.is_limit:
                return False, "Only limit orders can be cancelled", order
            ok, error = self._cancel(
                order, CANCELLED,
                "Cancelled from the web", "Đã hủy từ web",
            )
            if ok:
                return True, "Cancelled", order
            if not order.is_open:
                return False, f"Order already {order.status.lower().replace('_', ' ')}", order
            return False, f"Cancel failed: {error}", order

    def check_all(self) -> None:
        """One polling pass over the orders still waiting."""
        with self._lock:
            waiting = [o for o in self._orders.values() if o.is_open]
        prices: dict[str, Optional[float]] = {}
        for order in waiting:
            if self._stop.is_set():
                return
            self._check(order, prices)

    # ---------------- exchange ----------------

    def _read(self, order: TrackedOrder) -> Optional[dict]:
        """The order as the exchange sees it, or None when the exchange does not know the id (yet)."""
        exchange = self._exchange()
        market = self._market_of(order.symbol)
        params = {"orderId": order.order_id}
        found = self._match(exchange.fetch_open_orders(market, None, None, params), order.order_id)
        if found is None:
            # Order history: filled, cancelled and rejected orders (fetch_closed_orders only returns filled ones)
            history = getattr(exchange, "fetch_canceled_and_closed_orders", None)
            if history is not None:
                found = self._match(history(market, None, None, params), order.order_id)
        if found is None:
            return None
        info = found.get("info") or {}
        reject = str(info.get("rejectReason") or "")
        return {
            "status": str(found.get("status") or "").lower(),
            "filled": _num(found.get("filled")) or 0.0,
            "amount": _num(found.get("amount")) or order.quantity,
            "average": _num(found.get("average")),
            "reject_reason": "" if reject in ("", "EC_NoError") else reject,
        }

    @staticmethod
    def _match(orders, order_id: str) -> Optional[dict]:
        for item in orders or []:
            if str(item.get("id")) == str(order_id):
                return item
        return None

    def _last_price(self, order: TrackedOrder, prices: dict) -> Optional[float]:
        if order.symbol not in prices:
            try:
                ticker = self._exchange().fetch_ticker(self._market_of(order.symbol))
                prices[order.symbol] = _num(ticker.get("last"))
            except Exception as exc:
                self._log_error(order, f"price check failed: {_short_error(exc)}")
                prices[order.symbol] = None
        return prices[order.symbol]

    # ---------------- state changes ----------------

    def _check(self, order: TrackedOrder, prices: dict) -> None:
        with self._work:
            if not order.is_open:
                return
            try:
                state = self._read(order)
            except Exception as exc:
                self._log_error(order, f"status check failed: {_short_error(exc)}")
                return
            if state is None:
                self._missing(order)
                return
            order.missing = 0
            order.missing_since = 0.0
            self._apply(order, state)
            if not order.is_open:
                return
            reason = self._stale_reason(order, prices)
            if reason:
                ok, error = self._cancel(order, NOT_FILLED, *reason)
                if not ok and order.is_open:
                    self._log_error(order, f"auto-cancel failed: {error}")

    def _apply(self, order: TrackedOrder, state: dict) -> None:
        """Record what the exchange reports (fills, final status)."""
        amount = state["amount"] or order.quantity
        status = state["status"]
        if state["average"]:
            order.avg_price = state["average"]
        if status == "closed" and state["filled"] <= 0:
            state = dict(state, filled=amount)  # "closed" means filled; trust it when the size is missing
        grew = state["filled"] > order.filled
        order.filled = max(order.filled, state["filled"])
        full = amount > 0 and order.filled >= amount * (1 - 1e-6)

        if status == "open" and not full:
            if order.filled > 0 and order.status == WAITING:
                order.status = PARTIAL
                order.filled_at = time.time()
                self._touch(order)
                self.on_log(f"{order.label}: partly filled {fmt_qty(order.filled)}/{fmt_qty(amount)}")
                self._emit("partial", order)
            elif grew:
                self._touch(order)
                self._emit("update", order)
            return

        if full or order.filled > 0:
            if full:
                self._finish(order, FILLED)
            else:
                self._finish(
                    order, FILLED,
                    f"Partly filled {fmt_qty(order.filled)} of {fmt_qty(amount)}; the rest was cancelled",
                    f"Khớp một phần {fmt_qty(order.filled)}/{fmt_qty(amount)}, phần còn lại đã bị hủy",
                )
            return

        if status == "rejected":
            reason, reason_vi = "Rejected by the exchange", "Sàn từ chối lệnh"
        elif status == "expired":
            reason, reason_vi = "Expired on the exchange", "Lệnh hết hạn trên sàn"
        else:
            reason, reason_vi = "Cancelled on the exchange", "Lệnh bị hủy trên sàn"
        if state["reject_reason"]:
            reason += f" ({state['reject_reason']})"
            reason_vi += f" ({state['reject_reason']})"
        self._finish(order, NOT_FILLED, reason, reason_vi)

    def _stale_reason(self, order: TrackedOrder, prices: dict) -> Optional[tuple[str, str]]:
        """Why a waiting limit order should be cancelled now, or None."""
        if not order.is_limit:
            return None
        if order.expires_at and time.time() >= order.expires_at:
            span = order.expires_at - order.placed_at
            return (
                f"Not filled within {fmt_duration(span)}, order cancelled",
                f"Quá {fmt_duration(span, vi=True)} chưa khớp, đã hủy lệnh",
            )
        if self.cancel_if_target_hit and order.take_profit:
            price = self._last_price(order, prices)
            if price is None:
                return None
            hit = price >= order.take_profit if order.side == "LONG" else price <= order.take_profit
            if hit:
                return (
                    f"Price reached the target {fmt_price(order.take_profit)} before the order filled, order cancelled",
                    f"Giá chạm TP {fmt_price(order.take_profit)} trước khi khớp, đã hủy lệnh",
                )
        return None

    def _cancel(self, order: TrackedOrder, unfilled_status: str, reason: str, reason_vi: str) -> tuple[bool, str]:
        """Cancel on the exchange and record the final state. Returns (cancelled, error)."""
        error = ""
        try:
            self._exchange().cancel_order(order.order_id, self._market_of(order.symbol))
        except Exception as exc:
            error = _short_error(exc)
        try:
            state = self._read(order)
        except Exception:
            state = None
        status = state["status"] if state else ""

        if error:
            if state is not None and status not in ("", "open"):
                self._apply(order, state)  # it filled or closed meanwhile
            return False, error

        # Cancel accepted (the order may still show as open for a moment)
        if state is not None:
            order.filled = max(order.filled, state["filled"])
            if state["average"]:
                order.avg_price = state["average"]
        if order.filled > 0:
            amount = state["amount"] if state else order.quantity
            self._finish(
                order, FILLED,
                f"Partly filled {fmt_qty(order.filled)} of {fmt_qty(amount)}; rest cancelled: {reason}",
                f"Khớp một phần {fmt_qty(order.filled)}/{fmt_qty(amount)}; phần còn lại: {reason_vi}",
            )
        else:
            self._finish(order, unfilled_status, reason, reason_vi)
        return True, ""

    def _missing(self, order: TrackedOrder) -> None:
        now = time.time()
        if not order.missing:
            order.missing_since = now
        order.missing += 1
        if order.missing >= MISSING_LIMIT and now - order.missing_since >= MISSING_SECONDS:
            self._finish(
                order, UNKNOWN,
                "The exchange does not know this order; check it on the exchange",
                "Sàn không tìm thấy lệnh này, hãy kiểm tra trực tiếp trên sàn",
            )

    def _finish(self, order: TrackedOrder, status: str, reason: str = "", reason_vi: str = "") -> None:
        now = time.time()
        with self._lock:
            if order.status not in OPEN_STATUSES:
                return
            order.status = status
            order.reason = reason
            order.reason_vi = reason_vi
            order.closed_at = now
            order.updated_at = now
            if status == FILLED and not order.filled_at:
                order.filled_at = now
            self._prune_locked()
            self._save_locked()
        self.on_log(f"{order.label}: {status.lower().replace('_', ' ')}" + (f" ({reason})" if reason else ""))
        self._emit(FINAL_EVENTS[status], order)

    def _touch(self, order: TrackedOrder) -> None:
        with self._lock:
            order.updated_at = time.time()
            self._save_locked()

    def _log_error(self, order: TrackedOrder, message: str) -> None:
        now = time.time()
        if now - self._error_logged.get(order.id, 0) >= ERROR_LOG_SECONDS:
            self._error_logged[order.id] = now
            self.on_error(f"{order.label}: {message}")

    # ---------------- storage ----------------

    def _load(self) -> None:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        items = data.get("orders") if isinstance(data, dict) else None
        with self._lock:
            for item in items or []:
                try:
                    order = TrackedOrder.from_dict(item)
                except (TypeError, ValueError):
                    continue
                self._orders[order.id] = order

    def _save_locked(self) -> None:
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(self.file.suffix + ".tmp")
            payload = {"orders": [asdict(o) for o in self._orders.values()]}
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.file)
        except OSError as exc:
            self.on_error(f"save failed: {exc}")

    def _prune_locked(self) -> None:
        done = sorted((o for o in self._orders.values() if not o.is_open), key=lambda o: o.closed_at)
        for order in done[: max(0, len(done) - HISTORY)]:
            self._orders.pop(order.id, None)

    # ---------------- threads ----------------

    def _emit(self, event: str, order: TrackedOrder) -> None:
        self._events.put((event, order))

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                event, order = self._events.get(timeout=0.5)
            except Empty:
                continue
            for listener in list(self._listeners):
                try:
                    listener(event, order)
                except Exception as exc:
                    self.on_error(f"listener failed: {exc}")

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.poll_seconds)
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self.check_all()
            except Exception as exc:
                self.on_error(f"poll failed: {exc}")
