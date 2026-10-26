"""
Order history for the web chart (GET /api/history), rebuilt from the bot's files so it
survives restarts:

  sent_orders.json      bot signals sent for approval (or traded) and what happened to them
  manual_orders.json    orders placed from the web
  order tracker         fill status of the orders placed on the exchange

Each item (newest first):
  id, source ("signal" | "web"), symbol, side, timeframe, orderType,
  time         when it was decided / placed (unix seconds)
  entryTime    bot signals: open time (UTC) of the candle the signal entered on
  entry, stopLoss, takeProfit, risk
  status       PENDING (waiting for approval), WAITING / PARTIAL (on the exchange),
               FILLED, NOT_FILLED, CANCELLED, UNKNOWN, PLACED (sent, fill not followed),
               FAILED, EXPIRED, REJECTED, WIN / LOSS (older runs)
  fillPrice, via ("web" | "telegram"), reason (why it failed / did not fill), orderId
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _local_ts(text: Any) -> Optional[float]:
    """'YYYY-MM-DD HH:MM:SS' in the bot's local time (datetime.now()) -> unix seconds."""
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d %H:%M:%S").timestamp()
    except (TypeError, ValueError):
        return None


def _utc_ts(text: Any) -> Optional[int]:
    """Candle time 'YYYY-MM-DD HH:MM:SS' (UTC) -> unix seconds."""
    try:
        return calendar.timegm(datetime.strptime(str(text).strip()[:19], "%Y-%m-%d %H:%M:%S").timetuple())
    except (TypeError, ValueError):
        return None


def _one_line(text: Any, limit: int = 300) -> str:
    msg = re.search(r'"retMsg"\s*:\s*"([^"]+)"', str(text or ""))
    if msg:
        code = re.search(r'"retCode"\s*:\s*(\d+)', str(text))
        return f"{msg.group(1)}{f' (Bybit {code.group(1)})' if code else ''}"[:limit]
    lines = [re.sub(r"^[\s└├─❌⚠️✅🟡]+", "", ln).strip() for ln in str(text or "").splitlines()]
    lines = [ln for ln in lines if ln and ln.upper() not in ("BYBIT", "THẤT BẠI")]
    return " · ".join(ln.split("Lỗi:", 1)[-1].strip() for ln in lines)[:limit]


def _signal_items(sent: Iterable[dict], tracked_by_key: dict[str, dict]) -> list[dict]:
    items = []
    for o in sent:
        if not isinstance(o, dict) or not o.get("key"):
            continue
        key = str(o["key"])
        parts = key.split("|")
        tracked = tracked_by_key.get(key)
        status = str(o.get("status") or "OPEN").upper()
        if tracked:
            status = tracked.get("status") or status
        elif status == "OPEN":
            status = str(o.get("fill_status") or "PLACED").upper()
        edited = bool(o.get("edited"))
        time = (_local_ts(o.get("approved_at")) or _local_ts(o.get("decided_at")) or _local_ts(o.get("sent_at")))
        items.append({
            "id": f"sig:{key}",
            "source": "signal",
            "symbol": o.get("symbol") or parts[0],
            "side": str(o.get("type") or (parts[1] if len(parts) > 1 else "")).upper(),
            "timeframe": o.get("signal_timeframe") or (parts[3] if len(parts) > 3 else None),
            "orderType": o.get("order_mode") or (tracked or {}).get("orderType") or "market",
            "time": time,
            "sentAt": _local_ts(o.get("sent_at")),
            "entryTime": _utc_ts(parts[2]) if len(parts) > 2 else None,
            "entry": _num(o.get("placed_entry") if edited else None) or _num(o.get("entry_price")),
            "stopLoss": _num(o.get("placed_stop_loss") if edited else None) or _num(o.get("stop_loss")),
            "takeProfit": _num(o.get("placed_take_profit") if edited else None) or _num(o.get("take_profit")),
            "botLevels": {
                "entry": _num(o.get("entry_price")),
                "stopLoss": _num(o.get("stop_loss")),
                "takeProfit": _num(o.get("take_profit")),
            } if edited else None,
            "risk": _num(o.get("placed_risk_per_trade") if edited else None) or _num(o.get("risk_per_trade")),
            "status": status,
            "fillPrice": _num((tracked or {}).get("avgPrice")) or _num(o.get("fill_price")),
            "via": o.get("approved_via") or None,
            "reason": o.get("reason") or (tracked or {}).get("reason") or "",
            "orderId": o.get("order_id") or (tracked or {}).get("orderId"),
        })
    return items


def _web_items(records: Iterable[dict], tracked_by_id: dict[str, dict]) -> list[dict]:
    items = []
    for r in records:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        tracked = tracked_by_id.get(str(r.get("trackedId") or ""))
        success = bool(r.get("success"))
        status = (tracked or {}).get("status") or ("PLACED" if success else "FAILED")
        items.append({
            "id": f"web:{r['id']}",
            "source": "web",
            "symbol": r.get("symbol"),
            "side": str(r.get("side") or "").upper(),
            "timeframe": None,
            "orderType": r.get("orderType") or "market",
            "time": _num(r.get("createdAt")),
            "sentAt": _num(r.get("createdAt")),
            "entryTime": None,
            "entry": _num((tracked or {}).get("price")) or _num(r.get("entry")),
            "stopLoss": _num(r.get("stopLoss")),
            "takeProfit": _num(r.get("takeProfit")),
            "botLevels": None,
            "risk": _num(r.get("risk")),
            "status": status,
            "fillPrice": _num((tracked or {}).get("avgPrice")),
            "via": "web",
            "reason": (tracked or {}).get("reason") or ("" if success else _one_line(r.get("message"))),
            "orderId": r.get("orderId"),
        })
    return items


def load_history(sent_orders_file: str, manual_orders_file: str, tracked: list[dict], limit: int = 100) -> list[dict]:
    """Bot signals and web orders, newest first (see module docstring)."""
    tracked = [t for t in tracked or [] if isinstance(t, dict)]
    by_key = {t["key"]: t for t in tracked if t.get("key")}
    by_id = {t["id"]: t for t in tracked if t.get("id")}

    sent = _read(Path(sent_orders_file))
    sent = sent.get("sent_orders", []) if isinstance(sent, dict) else []
    manual = _read(Path(manual_orders_file))
    manual = manual if isinstance(manual, list) else []

    items = _signal_items(sent, by_key) + _web_items(manual, by_id)
    items.sort(key=lambda i: i.get("time") or 0, reverse=True)
    return items[: max(1, int(limit))]
