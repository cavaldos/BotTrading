"""
Approval hub: bot signals wait here until someone approves or rejects them.

Channels (the web chart, Telegram) only show signals and forward clicks:
  hub.submit(signal)                       -> listeners get ("new", signal)
  hub.decide(id, "approve"|"reject", ...)  -> first decision wins, listeners get ("update", signal)
  no decision before `expires_at`          -> status EXPIRED, listeners get ("update", signal)

An approval can carry `changes` (order type, entry, SL, TP, risk edited on the web);
they replace the signal's values before the order is placed.
On approve, `signal.on_approve(signal)` places the order and returns (success, text).
Listeners run on one dispatcher thread, so a slow channel never blocks a decision.

Auto mode (toggled on the web chart): new signals are approved at once (via "auto"),
still through `on_approve`, so the same late-entry checks apply. The mode is saved in
`auto_file` and kept after a restart; mode listeners get (enabled, by) on each change.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Optional


# PENDING -> PLACING -> PLACED | FAILED,  PENDING -> REJECTED | EXPIRED
FINAL_STATUSES = ("PLACED", "FAILED", "REJECTED", "EXPIRED")

# Signal fields an approval may change (see `decide`)
EDITABLE_FIELDS = ("order_type", "entry", "stop_loss", "take_profit", "risk_per_trade", "leverage", "fill_timeout_minutes")


@dataclass
class Signal:
    key: str                      # sent_orders.json key
    label: str                    # e.g. "BTCUSDT_5m| LONG"
    symbol: str
    side: str                     # LONG | SHORT
    signal_timeframe: str
    entry_time: str               # UTC "YYYY-MM-DD HH:MM:SS"
    entry: float
    stop_loss: float
    take_profit: Optional[float]
    risk_per_trade: float
    message_kwargs: dict          # kwargs of the entry message formatters
    on_approve: Callable[["Signal"], tuple[bool, str]]
    on_reject: Callable[["Signal"], None]
    on_expire: Callable[["Signal"], None]
    image: Optional[bytes] = None
    order_type: str = "market"                    # market | limit (limit only when edited on the web)
    leverage: Optional[int] = None                # None = the order module default
    fill_timeout_minutes: Optional[float] = None  # limit orders; None = order tracker default
    edited: bool = False                          # values changed on the web before approval
    tracked_id: str = ""                          # order tracker id once the order is placed
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    status: str = "PENDING"
    decided_by: str = ""
    decided_via: str = ""
    decided_at: float = 0.0
    result_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        risk = abs(self.entry - self.stop_loss)
        rr = abs(self.take_profit - self.entry) / risk if self.take_profit is not None and risk > 0 else None
        return {
            "id": self.id,
            "key": self.key,
            "label": self.label,
            "symbol": self.symbol,
            "side": self.side,
            "signalTimeframe": self.signal_timeframe,
            "entryTime": self.entry_time,
            "entry": self.entry,
            "stopLoss": self.stop_loss,
            "takeProfit": self.take_profit,
            "rr": round(rr, 2) if rr is not None else None,
            "riskPerTrade": self.risk_per_trade,
            "createdAt": self.created_at,
            "expiresAt": self.expires_at,
            "status": self.status,
            "decidedBy": self.decided_by,
            "decidedVia": self.decided_via,
            "decidedAt": self.decided_at,
            "resultText": self.result_text,
            "hasImage": bool(self.image),
            "orderType": self.order_type,
            "edited": self.edited,
            "trackedId": self.tracked_id,
        }


Listener = Callable[[str, Signal], None]
ModeListener = Callable[[bool, str], None]


class ApprovalHub:
    def __init__(
        self,
        timeout_minutes: float = 5,
        history: int = 30,
        on_log: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        auto_approve: bool = False,
        auto_file: Optional[str] = None,
    ):
        self.timeout_minutes = float(timeout_minutes)
        self.history = int(history)
        self.on_log = on_log or (lambda msg: None)
        self.on_error = on_error or (lambda msg: print(f"Approval error: {msg}"))
        self._signals: "OrderedDict[str, Signal]" = OrderedDict()
        self._listeners: list[Listener] = []
        self._mode_listeners: list[ModeListener] = []
        self.auto_file = auto_file
        self._auto = self._load_auto(bool(auto_approve))
        self._events: Queue = Queue()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._expiry_thread = threading.Thread(target=self._expiry_loop, daemon=True, name="ApprovalExpiry")
        self._dispatch_thread = threading.Thread(target=self._dispatch_loop, daemon=True, name="ApprovalDispatch")

    # ---------------- lifecycle ----------------

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def add_mode_listener(self, listener: ModeListener) -> None:
        self._mode_listeners.append(listener)

    def start(self) -> "ApprovalHub":
        self._expiry_thread.start()
        self._dispatch_thread.start()
        return self

    def stop(self) -> None:
        """Expire everything still pending (no order after shutdown) and stop the threads."""
        with self._lock:
            pending = [s for s in self._signals.values() if s.status == "PENDING"]
            for sig in pending:
                sig.status = "EXPIRED"
                sig.decided_at = time.time()
                sig.result_text = "Bot stopped"
        for sig in pending:
            self._safe(sig.on_expire, sig)
            self._emit("update", sig)
        deadline = time.time() + 3
        while not self._events.empty() and time.time() < deadline:
            time.sleep(0.05)
        self._stop.set()

    # ---------------- public ----------------

    @property
    def auto_approve(self) -> bool:
        return self._auto

    def set_auto_approve(self, enabled: bool, by: str = "web") -> bool:
        """Turn auto mode on/off. Signals already waiting keep waiting for a decision."""
        enabled = bool(enabled)
        with self._lock:
            changed = enabled != self._auto
            self._auto = enabled
        if not changed:
            return enabled
        self._save_auto(enabled, by)
        self.on_log(f"Auto mode {'ON: new signals are traded without approval' if enabled else 'OFF: signals wait for approval'} ({by})")
        for listener in list(self._mode_listeners):
            self._safe(listener, enabled, by)
        return enabled

    def submit(self, sig: Signal) -> Signal:
        sig.expires_at = sig.created_at + self.timeout_minutes * 60
        auto = self._auto
        with self._lock:
            self._signals[sig.id] = sig
            self._prune_locked()
        if not auto:
            self.on_log(f"{sig.label}: waiting for approval")
        self._emit("new", sig)
        if auto:
            # Own thread: placing the order must not hold up the caller (order scan / snapshot worker)
            threading.Thread(
                target=self.decide, args=(sig.id, "approve"), kwargs={"via": "auto", "by": "auto"},
                daemon=True, name="AutoApprove",
            ).start()
        return sig

    def get(self, signal_id: str) -> Optional[Signal]:
        with self._lock:
            return self._signals.get(signal_id)

    def snapshot(self) -> dict[str, list[dict]]:
        with self._lock:
            items = list(self._signals.values())
        pending = [s.to_dict() for s in items if s.status in ("PENDING", "PLACING")]
        recent = [s.to_dict() for s in reversed(items) if s.status in FINAL_STATUSES]
        return {"pending": pending, "recent": recent}

    def decide(
        self,
        signal_id: str,
        action: str,
        via: str,
        by: str,
        changes: Optional[dict] = None,
    ) -> tuple[bool, str, Optional[Signal]]:
        """Apply the first decision. Returns (ok, message, signal). Blocks while the order is placed.

        `changes` (approve only): new values for EDITABLE_FIELDS, already validated by the caller.
        """
        if action not in ("approve", "reject"):
            return False, "Unknown action", None
        with self._lock:
            sig = self._signals.get(signal_id)
            if sig is None:
                return False, "Signal not found (expired or bot restarted)", None
            if sig.status != "PENDING":
                return False, f"Signal already {sig.status.lower()}", sig
            expired = time.time() > sig.expires_at
            if expired:
                sig.status = "EXPIRED"
            else:
                if action == "approve" and changes:
                    for name in EDITABLE_FIELDS:
                        if name in changes:
                            setattr(sig, name, changes[name])
                    sig.edited = True
                sig.status = "PLACING" if action == "approve" else "REJECTED"
                sig.decided_by = by
                sig.decided_via = via
            sig.decided_at = time.time()

        if expired:
            self._safe(sig.on_expire, sig)
            self._emit("update", sig)
            return False, "Signal expired", sig

        self._emit("update", sig)
        if sig.status == "REJECTED":
            self._safe(sig.on_reject, sig)
            self.on_log(f"{sig.label}: rejected by {by} ({via})")
            return True, "Rejected", sig

        edited = f", edited: {sig.order_type} SL {sig.stop_loss:g} TP {sig.take_profit:g}" if sig.edited and sig.take_profit else ""
        approved = "auto-approved" if via == "auto" else f"approved by {by} ({via})"
        self.on_log(f"{sig.label}: {approved}{edited}, placing order")
        outcome = self._safe(sig.on_approve, sig)
        success, text = outcome if outcome else (False, "Order failed (see bot log)")
        with self._lock:
            sig.status = "PLACED" if success else "FAILED"
            sig.result_text = text
        self._emit("update", sig)
        return success, text, sig

    # ---------------- internals ----------------

    def _load_auto(self, default: bool) -> bool:
        if not self.auto_file:
            return default
        try:
            data = json.loads(Path(self.auto_file).read_text(encoding="utf-8"))
            return bool(data.get("auto_approve", default))
        except FileNotFoundError:
            return default
        except Exception as exc:
            self.on_error(f"cannot read {self.auto_file}: {exc}")
            return default

    def _save_auto(self, enabled: bool, by: str) -> None:
        if not self.auto_file:
            return
        try:
            path = Path(self.auto_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"auto_approve": enabled, "changed_by": by, "changed_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            self.on_error(f"cannot save {self.auto_file}: {exc}")

    def _prune_locked(self) -> None:
        done = [k for k, s in self._signals.items() if s.status in FINAL_STATUSES]
        for key in done[: max(0, len(done) - self.history)]:
            self._signals.pop(key, None)

    def _safe(self, fn: Callable, *args) -> Any:
        try:
            return fn(*args)
        except Exception as exc:
            self.on_error(f"callback failed: {exc}")
            return None

    def _emit(self, event: str, sig: Signal) -> None:
        self._events.put((event, sig))

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                event, sig = self._events.get(timeout=0.5)
            except Empty:
                continue
            for listener in list(self._listeners):
                try:
                    listener(event, sig)
                except Exception as exc:
                    self.on_error(f"listener failed: {exc}")

    def _expiry_loop(self) -> None:
        while not self._stop.wait(1.0):
            now = time.time()
            with self._lock:
                due = [s for s in self._signals.values() if s.status == "PENDING" and s.expires_at <= now]
                for sig in due:
                    sig.status = "EXPIRED"
                    sig.decided_at = now
                self._prune_locked()
            for sig in due:
                self._safe(sig.on_expire, sig)
                self.on_log(f"{sig.label}: expired")
                self._emit("update", sig)
