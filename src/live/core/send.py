import os
import sys
import time
import threading
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List

from src.utils.JsonDB import JsonDB

MAX_ORDER_AGE_MINUTES = 10
SKIPPED_ORDERS_FILE = "res/logs/skipped_orders.json"
SENT_ORDERS_METADATA_KEY = "_metadata"


from src.live.core.approval import Signal
from src.live.core.discord_notify import (
    entry_title,
    send_custom_message,
    send_entry_discord,
)

# Set by the live runner when chart snapshots are enabled (see src/live/core/snapshot.py)
_snapshot_worker = None
# Set by the live runner when `approval.enabled` (see src/live/core/approval.py).
# When set, orders are placed only after a signal is approved on the web chart or Telegram.
_approval_hub = None
# Set by the live runner with the approval hub or the web API (see src/live/core/order_tracker.py).
# Approved orders are followed on the exchange until they fill or not.
_order_tracker = None

# Guards sent_orders.json: the order scan and the approval threads both write it.
_SENT_ORDERS_LOCK = threading.RLock()

# Statuses of signals that never became a position on the exchange (or are still waiting).
NOT_PLACED_STATUSES = ('PENDING', 'REJECTED', 'EXPIRED', 'FAILED', 'NOT_FILLED')


def set_snapshot_worker(worker) -> None:
    global _snapshot_worker
    _snapshot_worker = worker


def set_approval_hub(hub) -> None:
    global _approval_hub
    _approval_hub = hub


def set_order_tracker(tracker) -> None:
    global _order_tracker
    _order_tracker = tracker


def notify_entry(row: pd.Series, message_kwargs: dict, sender=None) -> None:
    """Send the entry message; with a chart image when the snapshot worker is running.

    `sender(message_kwargs, image)` delivers it; default: Discord entry message.
    """
    worker = _snapshot_worker
    if worker is not None and worker.submit(row.to_dict(), message_kwargs, sender):
        return
    if sender is not None:
        sender(message_kwargs, None)
    else:
        send_entry_discord(**message_kwargs)

from src.core.symbols import strip_timeframe
from src.exchanges import get_exchange
from .place_order import (
    exchange_label,
    format_order_message,
    place_limit_order,
    place_market_order as place_order,
    risk_per_trade as default_risk_per_trade,
    short_error,
)


def load_sent_orders(log_file: Path) -> tuple[set, list]:
    """Load sent orders from log file using JsonDB. Returns (order keys set, order details list)."""
    db = JsonDB(str(log_file))
    data = db.all()
    if isinstance(data, dict):
        orders = data.get("sent_orders", [])
    else:
        orders = data
    orders = [item for item in orders if isinstance(item, dict) and item.get("key")]
    keys = set(order.get("key", "") for order in orders if isinstance(order, dict))
    return keys, orders


def save_sent_orders(log_file: Path, sent_orders: list) -> None:
    """Save sent orders to log file."""
    import json
    from datetime import datetime
    log_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "sent_orders": sent_orders,
        "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    with open(log_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def append_sent_order(log_file, order_detail: dict) -> None:
    """Append one order to the saved list (re-read from disk first)."""
    log_path = Path(log_file)
    with _SENT_ORDERS_LOCK:
        _, sent_orders = load_sent_orders(log_path)
        sent_orders.append(order_detail)
        save_sent_orders(log_path, sent_orders)


def update_sent_order(log_file, order_key: str, **fields) -> bool:
    """Update one saved order in place. Returns False when the key is not found."""
    log_path = Path(log_file)
    with _SENT_ORDERS_LOCK:
        _, sent_orders = load_sent_orders(log_path)
        for order in sent_orders:
            if order.get("key") == order_key:
                order.update(fields)
                save_sent_orders(log_path, sent_orders)
                return True
    return False


def record_signal_fill(log_file, order) -> None:
    """Save the fill result of an approved signal's order (a TrackedOrder) in sent_orders.json."""
    if not order.key:
        return
    fields = {
        "fill_status": order.status,
        "filled_qty": order.filled,
        "fill_price": order.avg_price,
    }
    if order.status in ("NOT_FILLED", "CANCELLED") and order.filled <= 0:
        fields["status"] = "NOT_FILLED"
    update_sent_order(log_file, order.key, **fields)


def expire_stale_pending(log_file) -> int:
    """Mark PENDING orders left by a previous run as EXPIRED (their buttons no longer work)."""
    log_path = Path(log_file)
    with _SENT_ORDERS_LOCK:
        _, sent_orders = load_sent_orders(log_path)
        count = 0
        for order in sent_orders:
            if order.get("status") == "PENDING":
                order["status"] = "EXPIRED"
                count += 1
        if count:
            save_sent_orders(log_path, sent_orders)
    return count


def update_order_status_from_csv(output_dir: str, log_file: str) -> int:
    """Update sent orders status from CSV files. Returns count of updated orders."""
    output_path = Path(output_dir)
    log_path = Path(log_file)
    
    sent_keys, sent_orders = load_sent_orders(log_path)
    
    if not sent_orders:
        return 0
    
    trades_from_csv = {}
    
    if output_path.exists():
        csv_files = list(output_path.glob("*.csv"))
        
        for csv_file in csv_files:
            try:
                df = pd.read_csv(csv_file)
                
                if 'Result' not in df.columns:
                    continue
                
                for _, row in df.iterrows():
                    symbol = str(row.get('Symbol', '')).strip()
                    trade_type = str(row.get('Type', '')).strip().upper()
                    entry_time = str(row.get('Entry_Time', '')).strip()
                    signal_timeframe = str(row.get('Signal_Timeframe', '')).strip()
                    result = str(row.get('Result', '')).strip().upper() if row.get('Result') else ''

                    if signal_timeframe:
                        trade_key = f"{symbol}|{trade_type}|{entry_time}|{signal_timeframe}"
                        legacy_trade_key = f"{symbol}|{trade_type}|{entry_time}"
                    else:
                        trade_key = f"{symbol}|{trade_type}|{entry_time}"
                        legacy_trade_key = trade_key
                    
                    if result in ('WIN', 'LOSS', 'OPEN'):
                        trades_from_csv[trade_key] = result
                        trades_from_csv[legacy_trade_key] = result
                    
            except Exception:
                continue
    
    updated_count = 0
    
    for order in sent_orders:
        key = order.get("key", "")
        current_status = order.get("status", "")
        
        if current_status in ('WIN', 'LOSS', 'CANCELLED') or current_status in NOT_PLACED_STATUSES:
            continue
        
        if key in trades_from_csv:
            new_status = trades_from_csv[key]
            
            if new_status != current_status:
                order["status"] = new_status
                updated_count += 1
        else:
            if current_status == 'OPEN':
                order["status"] = 'PASSED'
                updated_count += 1
    
    if updated_count > 0:
        save_sent_orders(log_path, sent_orders)
    
    return updated_count


def generate_order_key(row: pd.Series) -> str:
    symbol = str(row['Symbol']).strip()
    trade_type = str(row['Type']).strip().upper()
    entry_time = str(row['Entry_Time']).strip()
    signal_timeframe = str(row.get('Signal_Timeframe', '')).strip()
    if signal_timeframe:
        return f"{symbol}|{trade_type}|{entry_time}|{signal_timeframe}"
    return f"{symbol}|{trade_type}|{entry_time}"


def generate_legacy_order_key(row: pd.Series) -> str:
    symbol = str(row['Symbol']).strip()
    trade_type = str(row['Type']).strip().upper()
    entry_time = str(row['Entry_Time']).strip()
    return f"{symbol}|{trade_type}|{entry_time}"


def parse_optional_float(value: object) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_optional_bool(value: object, default: bool = False) -> bool:
    if value is None or pd.isna(value):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {'true', '1', 'yes', 'y'}:
        return True
    if text in {'false', '0', 'no', 'n', ''}:
        return False
    return default


def resolve_live_risk_fields(row: pd.Series) -> tuple[float, float, Optional[float]]:
    base_risk_per_trade = parse_optional_float(row.get('Base_Risk_Per_Trade'))
    effective_risk_per_trade = parse_optional_float(row.get('Risk_Per_Trade'))

    if effective_risk_per_trade is None or effective_risk_per_trade <= 0:
        if base_risk_per_trade is not None and base_risk_per_trade > 0:
            effective_risk_per_trade = base_risk_per_trade
        else:
            effective_risk_per_trade = float(default_risk_per_trade)

    risk_boost_multiplier = parse_optional_float(row.get('Risk_Boost_Multiplier'))
    if risk_boost_multiplier is None or risk_boost_multiplier <= 0:
        if base_risk_per_trade is not None and base_risk_per_trade > 0:
            risk_boost_multiplier = effective_risk_per_trade / base_risk_per_trade
        else:
            risk_boost_multiplier = 1.0

    return (effective_risk_per_trade, risk_boost_multiplier, base_risk_per_trade)


def is_order_too_old(entry_time_str: str, max_age_minutes: int = MAX_ORDER_AGE_MINUTES) -> bool:
    """Check if order is too old (entry_time is UTC+0, compare with current GMT+7 time)."""
    try:
        entry_time = datetime.strptime(entry_time_str.strip(), '%Y-%m-%d %H:%M:%S')
        entry_time_utc = entry_time.replace(tzinfo=timezone.utc)
        local_tz = timezone(timedelta(hours=7))
        entry_time_local = entry_time_utc.astimezone(local_tz)
        current_time_local = datetime.now(local_tz)
        
        age = current_time_local - entry_time_local
        return age > timedelta(minutes=max_age_minutes)
    except Exception:
        return False


def load_skipped_orders(log_file: Path) -> set:
    """Load skipped order keys from log file using JsonDB."""
    db = JsonDB(str(log_file))
    data = db.all()

    keys = set()

    if isinstance(data, dict):
        skipped_orders = data.get("skipped_orders", [])
        if isinstance(skipped_orders, list):
            for order in skipped_orders:
                if isinstance(order, dict):
                    key = order.get("key")
                    if isinstance(key, str) and key:
                        keys.add(key)

        skipped_keys = data.get("skipped_keys", [])
        if isinstance(skipped_keys, list):
            for key in skipped_keys:
                if isinstance(key, str) and key:
                    keys.add(key)

        return keys

    if isinstance(data, list):
        for order in data:
            if isinstance(order, dict):
                key = order.get("key")
                if isinstance(key, str) and key:
                    keys.add(key)

    return keys


def save_skipped_order(log_file: Path, order_key: str, reason: str, entry_time: str) -> None:
    """Save a skipped order to log file using JsonDB."""
    db = JsonDB(str(log_file))
    data = db.all()
    order_detail = {
        "key": order_key,
        "reason": reason,
        "entry_time": entry_time,
        "skipped_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }

    if isinstance(data, dict):
        skipped_orders = data.get("skipped_orders", [])
        if not isinstance(skipped_orders, list):
            skipped_orders = []

        existing_keys = set()
        for order in skipped_orders:
            if isinstance(order, dict):
                key = order.get("key")
                if isinstance(key, str) and key:
                    existing_keys.add(key)

        skipped_keys = data.get("skipped_keys", [])
        if isinstance(skipped_keys, list):
            for key in skipped_keys:
                if isinstance(key, str) and key:
                    existing_keys.add(key)

        if order_key in existing_keys:
            return

        skipped_orders.append(order_detail)
        data["skipped_orders"] = skipped_orders
        data["skipped_keys"] = [
            order.get("key")
            for order in skipped_orders
            if isinstance(order, dict) and isinstance(order.get("key"), str) and order.get("key")
        ]
        data["last_updated"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db._data = data
        db.save()
        return

    # Default / JsonDB-native format: list of order dicts
    if not isinstance(data, list):
        data = []

    existing_keys = set()
    for order in data:
        if isinstance(order, dict):
            key = order.get("key")
            if isinstance(key, str) and key:
                existing_keys.add(key)

    if order_key in existing_keys:
        return

    data.append(order_detail)
    db._data = data
    db.save()


def scan_open_orders(output_dir: Path) -> list:
    """Scan all CSV files in output_dir and filter OPEN orders."""
    if not output_dir.exists():
        return []

    csv_files = list(output_dir.glob("*.csv"))
    return scan_open_orders_from_files(csv_files)


def scan_open_orders_from_files(csv_files: list[Path]) -> list:
    """Scan specific CSV files and filter OPEN orders."""
    open_orders = []

    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file)

            if 'Result' not in df.columns:
                continue

            df['Result'] = df['Result'].fillna('').astype(str).str.strip().str.upper()
            open_df = df[df['Result'] == 'OPEN']

            for _, row in open_df.iterrows():
                open_orders.append(row)

        except Exception:
            continue

    return open_orders


def check_and_send_open_orders(
    output_dir: str,
    log_file: str,
    skipped_file: str = SKIPPED_ORDERS_FILE,
    changed_files: Optional[List[Path]] = None,
    update_status: bool = True,
) -> int:
    """Check for new OPEN orders and send notifications. Returns count of new orders sent.

    With an approval hub set, new signals are only sent for approval (status PENDING);
    the order is placed later, when the signal is approved on the web chart or Telegram.
    """
    with _SENT_ORDERS_LOCK:
        return _check_and_send_open_orders(output_dir, log_file, skipped_file, changed_files, update_status)


def _late_entry_problem(symbol: str, order_type: str, stop_loss: float, take_profit: Optional[float]) -> Optional[str]:
    """Approval comes minutes after the signal: refuse when price is already past SL or TP.

    A market order opened past SL would have no valid stop loss on the exchange.
    """
    try:
        price = get_exchange().last_price(symbol)
    except Exception:
        return None  # place_order reports exchange errors itself
    if price is None:
        return None
    price = float(price)
    if str(order_type).strip().upper() in ('LONG', 'BUY'):
        if price <= stop_loss:
            return f"Giá hiện tại {price} đã chạm SL {stop_loss}"
        if take_profit is not None and price >= take_profit:
            return f"Giá hiện tại {price} đã chạm TP {take_profit}"
    else:
        if price >= stop_loss:
            return f"Giá hiện tại {price} đã chạm SL {stop_loss}"
        if take_profit is not None and price <= take_profit:
            return f"Giá hiện tại {price} đã chạm TP {take_profit}"
    return None


def _follow_order(sig: Signal, result: dict, order_key: str) -> None:
    """Hand an approved signal's order to the order tracker (fill / no fill notifications)."""
    tracker = _order_tracker
    order_id = result.get('order_id')
    if tracker is None or not order_id:
        return

    position = result.get('position_info') or {}
    quantity = result.get('final_quantity') or result.get('contracts') or position.get('quantity') or 0
    is_limit = sig.order_type == "limit"
    tracked = tracker.track(
        source="signal",
        ref=sig.id,
        key=order_key,
        symbol=strip_timeframe(sig.symbol),
        side=sig.side,
        order_type=sig.order_type,
        order_id=order_id,
        price=sig.entry if is_limit else (result.get('entry_price') or sig.entry),
        stop_loss=result.get('stop_loss') or sig.stop_loss,
        take_profit=result.get('take_profit') or sig.take_profit,
        quantity=quantity,
        risk=sig.risk_per_trade,
        timeframe=sig.signal_timeframe,
        timeout_minutes=sig.fill_timeout_minutes,
        sl_tp_missing="" if is_limit else str(result.get('sl_tp_warning') or ""),
    )
    sig.tracked_id = tracked.id


def _short_reason(order_result: dict, text: str) -> str:
    """One line for sent_orders.json / the web history from a failed order result."""
    error = order_result.get('error')
    if error:
        return short_error(error)[:300]
    lines = [ln.strip(" └├─❌⚠️").strip() for ln in str(text or "").splitlines()]
    skip = {exchange_label().upper(), "THẤT BẠI", "KHÔNG ĐẶT LỆNH"}
    lines = [ln for ln in lines if ln and ln.upper() not in skip]
    reason = " · ".join(ln.split("Lỗi:", 1)[-1].strip() for ln in lines)
    return reason[:300]


def _make_approval_sender(hub, row: pd.Series, order_key: str, log_file: str, place_kwargs: dict):
    """Build the sender that puts the signal in the approval hub and posts the image to Discord."""
    symbol = str(row['Symbol']).strip()
    signal_timeframe = str(row.get('Signal_Timeframe', '')).strip()
    label = entry_title(str(row['Type']), symbol, signal_timeframe)
    now = lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    via_label = lambda sig: "Web" if sig.decided_via == "web" else f"Telegram: {sig.decided_by}"

    def on_approve(sig: Signal) -> tuple[bool, str]:
        # The signal holds the values to trade: the bot's, or the ones edited on the web
        is_limit = sig.order_type == "limit"
        kwargs = dict(
            place_kwargs,
            entry_price=sig.entry,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            risk_per_trade=sig.risk_per_trade,
        )
        if sig.leverage:
            kwargs['leverage'] = int(sig.leverage)
        approved = f"Đã duyệt ({via_label(sig)})" + (f", đã chỉnh: {sig.order_type.upper()}" if sig.edited else "")

        if not is_limit:
            problem = _late_entry_problem(kwargs['symbol'], kwargs['order_type'], kwargs['stop_loss'], kwargs['take_profit'])
            if problem:
                result_text = f"❌ KHÔNG ĐẶT LỆNH\n└─ {problem}"
                update_sent_order(log_file, order_key, status="FAILED", approved_at=now(), approved_via=sig.decided_via,
                                  reason=problem)
                send_custom_message(f"**{label}** ⚠️ {approved} — không đặt lệnh\n```\n{result_text}\n```")
                return False, result_text

        if is_limit:
            order_result = place_limit_order(**kwargs, signal_timestamp=time.time())
        else:
            order_result = place_order(**kwargs, signal_timestamp=time.time())
        result_text = f"{exchange_label().upper()}\n{format_order_message(order_result, sig.order_type)}"
        success = bool(order_result.get('success'))
        fields = dict(
            status="OPEN" if success else "FAILED",
            order_id=order_result.get('order_id'),
            approved_at=now(),
            approved_via=sig.decided_via,
            order_mode=sig.order_type,
        )
        if not success:
            fields["reason"] = _short_reason(order_result, result_text)
        if sig.edited:
            fields.update(
                edited=True,
                placed_entry=sig.entry,
                placed_stop_loss=sig.stop_loss,
                placed_take_profit=sig.take_profit,
                placed_risk_per_trade=sig.risk_per_trade,
            )
        update_sent_order(log_file, order_key, **fields)
        if success:
            try:
                _follow_order(sig, order_result, order_key)
            except Exception as e:  # the order is placed: never report it as failed
                print(f"Order tracking failed for {order_key}: {e}")
        verdict = "đã gửi lệnh, chờ sàn khớp" if success else "đặt lệnh thất bại"
        icon = "✅" if success else "⚠️"
        send_custom_message(f"**{label}** {icon} {approved} — {verdict}\n```\n{result_text}\n```")
        return success, result_text

    def on_reject(sig: Signal) -> None:
        update_sent_order(log_file, order_key, status="REJECTED", decided_at=now(), approved_via=sig.decided_via)
        send_custom_message(f"**{label}** ❌ Bỏ qua ({via_label(sig)}) — không đặt lệnh")

    def on_expire(sig: Signal) -> None:
        update_sent_order(log_file, order_key, status="EXPIRED", decided_at=now())
        send_custom_message(f"**{label}** ⌛ Hết hạn duyệt — không đặt lệnh")

    def sender(message_kwargs: dict, image: bytes | None) -> None:
        hub.submit(Signal(
            key=order_key,
            label=label,
            symbol=symbol,
            side=str(row['Type']).strip().upper(),
            signal_timeframe=signal_timeframe,
            entry_time=str(row['Entry_Time']).strip(),
            entry=float(place_kwargs['entry_price']),
            stop_loss=float(place_kwargs['stop_loss']),
            take_profit=place_kwargs['take_profit'],
            risk_per_trade=float(place_kwargs['risk_per_trade']),
            message_kwargs={k: v for k, v in message_kwargs.items() if k != 'result'},
            image=image,
            on_approve=on_approve,
            on_reject=on_reject,
            on_expire=on_expire,
        ))
        send_entry_discord(**message_kwargs, image=image)

    return sender


def _check_and_send_open_orders(
    output_dir: str,
    log_file: str,
    skipped_file: str,
    changed_files: Optional[List[Path]],
    update_status: bool,
) -> int:
    hub = _approval_hub
    output_path = Path(output_dir)
    log_path = Path(log_file)
    skipped_path = Path(skipped_file)

    if update_status:
        update_order_status_from_csv(output_dir, log_file)

    sent_keys, sent_orders = load_sent_orders(log_path)
    skipped_keys = load_skipped_orders(skipped_path)

    if changed_files:
        csv_files = [path for path in changed_files if path.exists() and path.suffix == ".csv"]
        open_orders = scan_open_orders_from_files(csv_files)
    else:
        open_orders = scan_open_orders(output_path)
    
    new_sent_count = 0
    
    for row in open_orders:
        order_key = generate_order_key(row)
        legacy_order_key = generate_legacy_order_key(row)

        if (
            order_key in sent_keys
            or legacy_order_key in sent_keys
            or order_key in skipped_keys
            or legacy_order_key in skipped_keys
        ):
            continue
        
        entry_time_str = str(row['Entry_Time']).strip()
        if is_order_too_old(entry_time_str):
            # (f"Skipping old order {row['Symbol']} - Entry time: {entry_time_str} (older than {MAX_ORDER_AGE_MINUTES} minutes)")
            save_skipped_order(skipped_path, order_key, f"Order too old (>{MAX_ORDER_AGE_MINUTES} min)", entry_time_str)
            continue
        
        try:
            # Ghi nhận thời điểm bắt đầu xử lý order (signal detected)
            signal_timestamp = time.time()
            effective_risk_per_trade, risk_boost_multiplier, base_risk_per_trade = resolve_live_risk_fields(row)
            take_profit = parse_optional_float(row.get('Take_Profit'))
            actual_rr_ratio = parse_optional_float(row.get('RR'))
            flex_rr_ratio = parse_optional_float(row.get('Flex_RR'))
            signal_timeframe = str(row.get('Signal_Timeframe', '')).strip()
            message_kwargs = dict(
                type=row['Type'],
                entry=float(row['Entry_Price']),
                stoploss=float(row['Stop_Loss']),
                takeprofit=float(take_profit) if take_profit is not None else float(row['Entry_Price']),
                time=row['Entry_Time'],
                name_coin=row['Symbol'],
                risk_per_trade=effective_risk_per_trade,
                signal_timeframe=signal_timeframe,
            )
            order_detail = {
                "key": order_key,
                "order_id": None,
                "symbol": row['Symbol'],
                "type": row['Type'],
                "signal_timeframe": signal_timeframe,
                "entry_price": float(row['Entry_Price']),
                "stop_loss": float(row['Stop_Loss']),
                "take_profit": take_profit,
                "rr": actual_rr_ratio,
                "flex_rr": flex_rr_ratio,
                "base_risk_per_trade": base_risk_per_trade,
                "risk_per_trade": effective_risk_per_trade,
                "risk_boost_multiplier": risk_boost_multiplier,
                "tp_capped": parse_optional_bool(row.get('TP_Capped')),
                "sent_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "status": "OPEN",
            }

            if hub is not None:
                # Manual approval: save as PENDING first, so the approval thread can update it.
                # Written straight to the file (not the in-memory list) so the final save below
                # cannot overwrite a status set meanwhile by the sender.
                order_detail["status"] = "PENDING"
                append_sent_order(log_path, order_detail)
                sent_keys.add(order_key)
                new_sent_count += 1
                place_kwargs = dict(
                    symbol=row['Symbol'],
                    order_type=row['Type'],
                    entry_price=float(row['Entry_Price']),
                    stop_loss=float(row['Stop_Loss']),
                    take_profit=take_profit,
                    entry_time=row['Entry_Time'],
                    risk_per_trade=effective_risk_per_trade,
                )
                sender = _make_approval_sender(hub, row, order_key, log_file, place_kwargs)
                pending_note = f"⏳ Chờ duyệt trên Web / Telegram (hết hạn sau {hub.timeout_minutes:g} phút)"
                notify_entry(row, dict(message_kwargs, result=pending_note), sender=sender)
                continue

            order_result = place_order(
                symbol=row['Symbol'],
                order_type=row['Type'],
                entry_price=float(row['Entry_Price']),
                stop_loss=float(row['Stop_Loss']),
                take_profit=take_profit,
                entry_time=row['Entry_Time'],
                risk_per_trade=effective_risk_per_trade,
                signal_timestamp=signal_timestamp,
            )
            result_text = f"**{exchange_label().upper()}**\n{format_order_message(order_result)}"
            notify_entry(row, dict(message_kwargs, result=result_text.strip()))

            order_detail["order_id"] = order_result.get('order_id')
            sent_orders.append(order_detail)
            sent_keys.add(order_key)
            new_sent_count += 1
            
        except Exception as e:
            print(f"Error processing order {order_key}: {e}")
            continue
    
    if new_sent_count > 0 and hub is None:
        save_sent_orders(log_path, sent_orders)
    
    return new_sent_count


if __name__ == "__main__":
    OUTPUT_FOLDER = "res/logs/output"
    LOG_FILE = "res/logs/sent_orders.json"
    check_and_send_open_orders(OUTPUT_FOLDER, LOG_FILE)
