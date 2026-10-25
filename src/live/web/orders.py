"""
Manual orders placed from the web chart (Long/Short position tool or order ticket).

Every request is checked here, never trusted from the browser:
  - symbol must be one of the bot's symbols
  - stop < entry < target (long) / target < entry < stop (short), price not already past stop/target
  - risk per trade <= max_risk_per_trade, stop not absurdly close to entry
  - margin must fit the free balance, one order at a time with a cooldown
  - `confirm: true` and a client id (a double-submit returns the first result)
Each placed order is saved to manual_orders.json and announced on Telegram + Discord.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from src.live.bybit.exchange import calculate_position_size, format_symbol, get_exchange, validate_symbol
from src.live.bybit.limit_order import format_limit_order_message
from src.live.bybit.market_order import format_market_order_message
from src.live.bybit.risk_calculator import clean_symbol
from src.live.core.place_order import cancel_order, place_limit_order, place_market_order


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
        on_event: Optional[Callable[[str, dict], None]] = None,
    ):
        self.symbols = sorted({clean_symbol(str(s)).upper() for s in symbols})
        self.default_risk = float(default_risk)
        self.max_risk = float(max_risk)
        self.leverage = int(leverage)
        self.cooldown_seconds = float(cooldown_seconds)
        self.log_file = Path(log_file)
        self.notify = notify or (lambda text: None)
        self.on_event = on_event or (lambda event, data: None)
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
        }

    def recent(self, limit: int = 30) -> list[dict]:
        return list(reversed(self._load()))[:limit]

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
        """(free USDT, error). Also a cheap check that the API key works before ordering."""
        at, value, error = self._balance
        if time.time() - at < BALANCE_CACHE_SECONDS:
            return value, error
        try:
            balance = exchange.fetch_balance()
            value, error = float((balance.get("USDT") or {}).get("free") or 0), ""
        except Exception as exc:
            value, error = None, str(exc)
            match = re.search(r'"retMsg":"([^"]+)"', error)
            if match:
                error = match.group(1)
        self._balance = (time.time(), value, error)
        return value, error

    def preview(self, body: dict) -> dict:
        o = self._parse(body)
        exchange = get_exchange()
        market = format_symbol(o["symbol"])
        if not validate_symbol(exchange, market):
            raise OrderError(f"{o['symbol']} is not tradable on the exchange")
        price = float(exchange.fetch_ticker(market).get("last") or 0)
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
            size = calculate_position_size(
                entry_price=entry,
                stop_loss=sl,
                leverage=self.leverage,
                risk_per_trade=o["risk"],
                side="buy" if is_long else "sell",
                skip_validation=True,
                exchange=exchange,
                symbol=market,
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
                text = format_limit_order_message(result.get("bybit") or {})
            else:
                result = place_market_order(**kwargs)
                text = format_market_order_message(result.get("bybit") or {})
            self._last_order_at = time.time()

        bybit = result.get("bybit") or {}
        success = bool(result.get("success"))
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
            "orderId": bybit.get("order_id") or result.get("order_id"),
            "status": ("OPEN" if check["orderType"] == "limit" else "FILLED") if success else "FAILED",
            "message": text,
        }
        self._append(record)
        headline = f"🖥 LỆNH TỪ WEB: {check['side']} {check['symbol']} ({check['orderType'].upper()})"
        self.notify(f"{headline}\n{text}")
        self.on_event("order", record)
        response = {"success": success, "message": text, "order": record}
        self._done[client_id] = response
        return response

    def cancel(self, record_id: str) -> dict:
        with self._file_lock:
            records = self._load()
            record = next((r for r in records if r.get("id") == record_id), None)
        if record is None:
            raise OrderError("Order not found")
        if record.get("orderType") != "limit" or record.get("status") != "OPEN" or not record.get("orderId"):
            raise OrderError("Only open limit orders can be cancelled here")
        result = cancel_order(record["symbol"], record["orderId"])
        success = bool(result.get("success"))
        error = (result.get("bybit") or {}).get("error") or ""
        if success:
            record = self._update(record_id, status="CANCELLED") or record
            self.notify(f"🖥 HỦY LỆNH TỪ WEB: {record['side']} {record['symbol']} LIMIT @ {record['entry']}")
            self.on_event("order", record)
        return {"success": success, "message": "Cancelled" if success else f"Cancel failed: {error}", "order": record}

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

    def _update(self, record_id: str, **fields) -> Optional[dict]:
        with self._file_lock:
            records = self._load()
            for record in records:
                if record.get("id") == record_id:
                    record.update(fields)
                    self._save(records)
                    return record
        return None
