import os
import sys
import json
import time
import asyncio
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path

MAX_ORDER_AGE_MINUTES = 10
SKIPPED_ORDERS_FILE = "res/logs/skipped_orders.json"


from src.trade.core.discord_notify import send_entry_discord

# from .place_order import risk_per_trade, place_limit_order as place_order, bybit_format_limit_message as bybit_format_order_message
from .place_order import risk_per_trade, place_market_order as place_order, bybit_format_market_message as bybit_format_order_message


def load_sent_orders(log_file: Path) -> tuple[set, list]:
    """Load sent orders from log file. Returns (order keys set, order details list)."""
    if not log_file.exists():
        return set(), []
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            orders = data.get("sent_orders", [])
            
            # Support both old format (list of strings) and new format (list of dicts)
            if orders and isinstance(orders[0], str):
                return set(orders), []
            else:
                keys = set(order.get("key", "") for order in orders)
                return keys, orders
    except:
        return set(), []


def save_sent_orders(log_file: Path, sent_orders: list) -> None:
    """Save sent orders to log file in JSON format with timestamp."""
    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump({
                "sent_orders": sent_orders,
                "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }, f, indent=2, ensure_ascii=False)
    except:
        pass


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
                    result = str(row.get('Result', '')).strip().upper() if row.get('Result') else ''
                    
                    trade_key = f"{symbol}|{trade_type}|{entry_time}"
                    
                    if result in ('WIN', 'LOSS', 'OPEN'):
                        trades_from_csv[trade_key] = result
                    
            except Exception:
                continue
    
    updated_count = 0
    
    for order in sent_orders:
        key = order.get("key", "")
        current_status = order.get("status", "")
        
        if current_status in ('WIN', 'LOSS'):
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
    return f"{symbol}|{trade_type}|{entry_time}"


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
    """Load skipped order keys from log file."""
    if not log_file.exists():
        return set()
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return set(data.get("skipped_keys", []))
    except:
        return set()


def save_skipped_order(log_file: Path, order_key: str, reason: str, entry_time: str) -> None:
    """Save a skipped order to log file."""
    try:
        if log_file.exists():
            with open(log_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            data = {"skipped_keys": [], "skipped_orders": []}
        
        if order_key not in data.get("skipped_keys", []):
            data.setdefault("skipped_keys", []).append(order_key)
            data.setdefault("skipped_orders", []).append({
                "key": order_key,
                "reason": reason,
                "entry_time": entry_time,
                "skipped_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            data["last_updated"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            with open(log_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
    except:
        pass


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


async def send_entry_notifications_async(
    row,
    risk_per_trade: float,
    timeout: float = 60,
    combined_result: str | None = None,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
):
    """Send entry notifications to Discord asynchronously."""
    try:
        entry_value = float(entry_price) if entry_price is not None else float(row['Entry_Price'])
        stop_loss_value = float(stop_loss) if stop_loss is not None else float(row['Stop_Loss'])
        take_profit_value = float(take_profit) if take_profit is not None else float(row['Take_Profit'])
        params_dis = {
            "type": row['Type'],
            "entry": entry_value,
            "stoploss": stop_loss_value,
            "takeprofit": take_profit_value,
            "time": row['Entry_Time'],
            "name_coin": row['Symbol'],
            "result": combined_result,
            "risk_per_trade": risk_per_trade
        }
        
        await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(send_entry_discord, **params_dis),
                return_exceptions=True
            ),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        print(f"Timeout sending notifications for {row['Symbol']}, but continuing...")
    except Exception as e:
        print(f"Error sending notifications for {row['Symbol']}: {e}")
        raise


def check_and_send_open_orders(
    output_dir: str,
    log_file: str,
    skipped_file: str = SKIPPED_ORDERS_FILE,
    changed_files: list[Path] | None = None,
    update_status: bool = True,
) -> int:
    """Check for new OPEN orders and send notifications. Returns count of new orders sent."""
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
        
        if order_key in sent_keys or order_key in skipped_keys:
            continue
        
        entry_time_str = str(row['Entry_Time']).strip()
        if is_order_too_old(entry_time_str):
            # (f"Skipping old order {row['Symbol']} - Entry time: {entry_time_str} (older than {MAX_ORDER_AGE_MINUTES} minutes)")
            save_skipped_order(skipped_path, order_key, f"Order too old (>{MAX_ORDER_AGE_MINUTES} min)", entry_time_str)
            continue
        
        try:
            # Ghi nhận thời điểm bắt đầu xử lý order (signal detected)
            signal_timestamp = time.time()
            
            order_result = place_order(
                symbol=row['Symbol'],
                order_type=row['Type'],
                entry_price=float(row['Entry_Price']),
                stop_loss=float(row['Stop_Loss']),
                take_profit=None,
                entry_time=row['Entry_Time'],
                risk_per_trade=risk_per_trade,
                signal_timestamp=signal_timestamp,
            )
            bybit_msg = ""
            
            if order_result.get('bybit'):
                bybit_msg = f"**BYBIT**\n{bybit_format_order_message(order_result['bybit'])}"

            notify_entry = float(row['Entry_Price'])
            notify_stop_loss = float(row['Stop_Loss'])
            notify_take_profit = float(row['Take_Profit'])
            if order_result.get('bybit', {}).get('success'):
                bybit_order = order_result['bybit']
                notify_entry = (
                    bybit_order.get('entry_price')
                    or bybit_order.get('slippage_info', {}).get('actual_entry')
                    or notify_entry
                )
                notify_stop_loss = bybit_order.get('stop_loss') or notify_stop_loss
                notify_take_profit = bybit_order.get('take_profit') or notify_take_profit

            bybit_order_id = None
            if order_result.get('bybit'):
                bybit_order = order_result['bybit']
                bybit_order_id = (
                    bybit_order.get('order_id')
                    or bybit_order.get('order', {}).get('id')
                    or bybit_order.get('order', {}).get('info', {}).get('orderId')
                )
            
            combined_result = bybit_msg.strip()
            asyncio.run(
                send_entry_notifications_async(
                    row,
                    risk_per_trade,
                    combined_result=combined_result,
                    entry_price=notify_entry,
                    stop_loss=notify_stop_loss,
                    take_profit=notify_take_profit,
                )
            )
            
            order_detail = {
                "key": order_key,
                "order_id": bybit_order_id,
                "entry_price": float(row['Entry_Price']),
                "stop_loss": float(row['Stop_Loss']),
                "take_profit": float(row['Take_Profit']),
                "sent_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "status": "OPEN",
            }
            sent_orders.append(order_detail)
            sent_keys.add(order_key)
            new_sent_count += 1
            
        except Exception as e:
            print(f"Error processing order {order_key}: {e}")
            continue
    
    if new_sent_count > 0:
        save_sent_orders(log_path, sent_orders)
    
    return new_sent_count


if __name__ == "__main__":
    OUTPUT_FOLDER = "res/logs/output"
    LOG_FILE = "res/logs/sent_orders.json"
    check_and_send_open_orders(OUTPUT_FOLDER, LOG_FILE)
