"""
Manual orders placed from the web chart (Long/Short position tool or order ticket).

Every request is checked here, never trusted from the browser:
  - symbol must be one of the bot's symbols
  - stop < entry < target (long) / target < entry < stop (short), price not already past stop/target
  - risk per trade <= max_risk_per_trade, stop not absurdly close to entry
  - margin must fit the free balance, one order at a time with a cooldown
  - `confirm: true` and a client id (a double-submit returns the first result)
Each placed order is saved to manual_orders.json, announced on Telegram + Discord and
handed to the order tracker, which reports whether it fills (and cancels a limit order
still unfilled after `fillTimeoutMinutes`).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from src.core.symbols import strip_timeframe as clean_symbol
from src.exchanges import get_exchange
from src.live.core.place_order import format_order_message, place_limit_order, place_market_order

if TYPE_CHECKING:
    from src.live.core.order_tracker import OrderTracker


WEB_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "host": "127.0.0.1",          # keep local; expose through the chart dev server proxy
    "port": 8787,
    "leverage": 20,
    "max_risk_per_trade": 0,      # 0 = 2 x strategy risk_per_trade
    "order_cooldown_seconds": 3,
    "manual_orders_file": "res/logs/manual_orders.json",
}

MIN_STOP_PCT = 0.05      # % of entry
WIDE_STOP_PCT = 30.0
BALANCE_CACHE_SECONDS = 10
MAX_FILL_TIMEOUT_MINUTES = 7 * 24 * 60


def load_web_config(live_config: dict | None) -> dict[str, Any]:
    raw = (live_config or {}).get("web") or {}
    cfg = dict(WEB_DEFAULTS)
    if isinstance(raw, dict):
        cfg.update({k: v for k, v in raw.items() if v is not None})
    return cfg


class OrderError(Exception):
    """Bad request: shown to the user as is."""


def _float(body: dict, key: str, label: str, required: bool = True) -> Optional[float]:
    value = body.get(key)
    if value in (None, ""):
        if required:
            raise OrderError(f"{label} is required")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise OrderError(f"{label} must be a number")
    if not number > 0:
        raise OrderError(f"{label} must be > 0")
    return number


def parse_fill_timeout(body: dict) -> Optional[float]:
    """Minutes before an unfilled limit order is cancelled: None = tracker default, 0 = never."""
    value = body.get("fillTimeoutMinutes") if isinstance(body, dict) else None
    if value in (None, ""):
        return None
    try:
        minutes = float(value)
    except (TypeError, ValueError):
        raise OrderError("Fill timeout must be a number of minutes")
    if not 0 <= minutes <= MAX_FILL_TIMEOUT_MINUTES:
        raise OrderError(f"Fill timeout must be between 0 and {MAX_FILL_TIMEOUT_MINUTES} minutes")
    return minutes


class ManualOrderService:
    def __init__(
        self,
        symbols: list[str],
        default_risk: float,
        max_risk: float,
        leverage: int,
        cooldown_seconds: float,
        log_file: str,
        notify: Optional[Callable[[str], None]] = None,
        tracker: Optional["OrderTracker"] = None,
    ):
        self.symbols = sorted({clean_symbol(str(s)).upper() for s in symbols})
        self.default_risk = float(default_risk)
        self.max_risk = float(max_risk)
        self.leverage = int(leverage)
        self.cooldown_seconds = float(cooldown_seconds)
        self.log_file = Path(log_file)
        self.notify = notify or (lambda text: None)
        self.tracker = tracker
        self._lock = threading.Lock()          # one order at a time
        self._file_lock = threading.Lock()
        self._last_order_at = 0.0
        self._done: dict[str, dict] = {}       # client id -> result
        self._balance: tuple[float, Optional[float], str] = (0.0, None, "")

    # ---------------- info ----------------

    def config(self) -> dict:
        return {
            "symbols": self.symbols,
            "defaultRisk": self.default_risk,
            "maxRisk": self.max_risk,
            "leverage": self.leverage,
            "limitTimeoutMinutes": self.tracker.limit_timeout_minutes if self.tracker is not None else None,
        }

    # ---------------- checks ----------------

    def _parse(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise OrderError("Invalid request")
        symbol = clean_symbol(str(body.get("symbol") or "")).upper()
        if symbol not in self.symbols:
            raise OrderError(f"{symbol or 'Symbol'} is not one of the bot symbols")
        side = str(body.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            raise OrderError("Side must be LONG or SHORT")
        order_type = str(body.get("orderType") or "market").lower()
        if order_type not in ("market", "limit"):
            raise OrderError("Order type must be market or limit")
        risk = _float(body, "risk", "Risk", required=False) or self.default_risk
        if risk > self.max_risk:
            raise OrderError(f"Risk ${risk:g} is above the limit ${self.max_risk:g} (web.max_risk_per_trade)")
        return {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "entry": _float(body, "entry", "Entry price", required=order_type == "limit"),
            "stopLoss": _float(body, "stopLoss", "Stop loss"),
            "takeProfit": _float(body, "takeProfit", "Take profit"),
            "risk": risk,
        }

    def _free_balance(self, exchange) -> tuple[Optional[float], str]:
        """(free margin in the quote currency, error). Also a cheap check that the API key works before ordering."""
        at, value, error = self._balance
        if time.time() - at < BALANCE_CACHE_SECONDS:
            return value, error
        try:
            value, error = exchange.free_balance(), ""
        except Exception as exc:
            value, error = None, exchange.short_error(str(exc)) or str(exc)
        self._balance = (time.time(), value, error)
        return value, error

    def preview(self, body: dict) -> dict:
        o = self._parse(body)
        exchange = get_exchange()
        if not exchange.has_market(o["symbol"]):
            raise OrderError(f"{o['symbol']} is not tradable on {exchange.label}")
        price = float(exchange.last_price(o["symbol"]) or 0)
        if price <= 0:
            raise OrderError("Could not read the current price")

        is_long = o["side"] == "LONG"
        entry = o["entry"] if o["orderType"] == "limit" else price
        sl, tp = o["stopLoss"], o["takeProfit"]
        errors: list[str] = []
        warnings: list[str] = []

        if is_long and not (sl < entry < tp):
            errors.append("Long needs stop < entry < target")
        if not is_long and not (tp < entry < sl):
            errors.append("Short needs target < entry < stop")
        if o["orderType"] == "market":
            if (is_long and price <= sl) or (not is_long and price >= sl):
                errors.append(f"Price {price:g} is already past the stop")
            if (is_long and price >= tp) or (not is_long and price <= tp):
                errors.append(f"Price {price:g} is already past the target")
        elif (is_long and entry > price) or (not is_long and entry < price):
            warnings.append("Limit is on the wrong side of the current price: it will fill immediately")

        stop_pct = abs(entry - sl) / entry * 100
        if stop_pct < MIN_STOP_PCT:
            errors.append(f"Stop is too close to entry ({stop_pct:.3f}% < {MIN_STOP_PCT}%)")
        elif stop_pct > WIDE_STOP_PCT:
            warnings.append(f"Very wide stop ({stop_pct:.1f}%)")

        risk_dist = abs(entry - sl)
        rr = abs(tp - entry) / risk_dist if risk_dist > 0 else 0
        if 0 < rr < 1:
            warnings.append(f"R:R is below 1 ({rr:.2f})")

        size = None
        if risk_dist > 0:
            size = exchange.position_size(
                symbol=o["symbol"],
                entry_price=entry,
                stop_loss=sl,
                leverage=self.leverage,
                risk_per_trade=o["risk"],
                side="buy" if is_long else "sell",
            )
            if size["quantity"] <= 0:
                errors.append("Size rounds to zero on the exchange")
            elif size["actual_risk"] > self.max_risk:
                # The exchange minimum size can push the real risk far above the requested one
                errors.append(
                    f"Exchange minimum size ({size['quantity']}) makes the risk ${size['actual_risk']:.2f}, "
                    f"above the limit ${self.max_risk:g}: move the stop closer"
                )
            elif size["actual_risk"] > o["risk"] * 1.1:
                warnings.append(f"Exchange minimum size: actual risk ${size['actual_risk']:.2f}")

        balance, balance_error = self._free_balance(exchange)
        if balance_error:
            warnings.append(f"Exchange account check failed: {balance_error}")
        if size and balance is not None and size["margin_required"] > balance:
            errors.append(f"Margin ${size['margin_required']:.2f} is above free balance ${balance:.2f}")

        quantity = size["quantity"] if size else 0
        return {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
            "symbol": o["symbol"],
            "side": o["side"],
            "orderType": o["orderType"],
            "price": price,
            "entry": entry,
            "stopLoss": sl,
            "takeProfit": tp,
            "risk": o["risk"],
            "rr": round(rr, 2),
            "quantity": quantity,
            "notional": size["notional_value"] if size else 0,
            "margin": size["margin_required"] if size else 0,
            "leverage": self.leverage,
            "riskUsd": size["actual_risk"] if size else 0,
            "rewardUsd": round(quantity * abs(tp - entry), 2),
            "balance": balance,
        }

    # ---------------- actions ----------------

    def place(self, body: dict) -> dict:
        if not isinstance(body, dict) or body.get("confirm") is not True:
            raise OrderError("Order not confirmed")
        client_id = str(body.get("clientId") or "")[:64]
        if not client_id:
            raise OrderError("Missing client id")
        if client_id in self._done:
            return self._done[client_id]
        fill_timeout = parse_fill_timeout(body)

        with self._lock:
            if client_id in self._done:
                return self._done[client_id]
            wait = self.cooldown_seconds - (time.time() - self._last_order_at)
            if wait > 0:
                raise OrderError(f"Wait {wait:.0f}s between orders")
            check = self.preview(body)
            if not check["ok"]:
                return {"success": False, "message": "; ".join(check["errors"]), "preview": check}

            kwargs = dict(
                symbol=check["symbol"],
                order_type=check["side"],
                entry_price=check["entry"],
                stop_loss=check["stopLoss"],
                take_profit=check["takeProfit"],
                entry_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                leverage=self.leverage,
                risk_per_trade=check["risk"],
                signal_timestamp=time.time(),
            )
            if check["orderType"] == "limit":
                result = place_limit_order(**kwargs)
            else:
                result = place_market_order(**kwargs)
            text = format_order_message(result, check["orderType"])
            self._last_order_at = time.time()

        success = bool(result.get("success"))
        is_limit = check["orderType"] == "limit"
        record = {
            "id": uuid.uuid4().hex[:12],
            "clientId": client_id,
            "createdAt": time.time(),
            "symbol": check["symbol"],
            "side": check["side"],
            "orderType": check["orderType"],
            "entry": check["entry"],
            "stopLoss": check["stopLoss"],
            "takeProfit": check["takeProfit"],
            "risk": check["risk"],
            "quantity": check["quantity"],
            "success": success,
            "orderId": result.get("order_id"),
            "status": "PLACED" if success else "FAILED",
            "fillTimeoutMinutes": fill_timeout if is_limit else None,
            "message": text,
        }
        order = None
        if success and record["orderId"] and self.tracker is not None:
            try:
                tracked = self.tracker.track(
                    source="web",
                    ref=record["id"],
                    symbol=check["symbol"],
                    side=check["side"],
                    order_type=check["orderType"],
                    order_id=record["orderId"],
                    price=check["entry"] if is_limit else (result.get("entry_price") or check["entry"]),
                    stop_loss=result.get("stop_loss") or check["stopLoss"],
                    take_profit=result.get("take_profit") or check["takeProfit"],
                    quantity=result.get("final_quantity") or result.get("contracts") or check["quantity"],
                    risk=check["risk"],
                    timeout_minutes=fill_timeout,
                    sl_tp_missing="" if is_limit else str(result.get("sl_tp_warning") or ""),
                )
                record["trackedId"] = tracked.id
                order = tracked.to_dict()
            except Exception as exc:  # the order is placed: report it as such
                text += f"\n⚠️ Order tracking failed: {exc}"
        self._append(record)
        headline = f"🖥 LỆNH TỪ WEB: {check['side']} {check['symbol']} ({check['orderType'].upper()})"
        self.notify(f"{headline}\n{text}")
        response = {"success": success, "message": text, "order": order}
        self._done[client_id] = response
        return response

    # ---------------- storage ----------------

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.log_file.read_text(encoding="utf-8"))
            return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, records: list[dict]) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    def _append(self, record: dict) -> None:
        with self._file_lock:
            records = self._load()
            records.append(record)
            self._save(records)
