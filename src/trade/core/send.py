import os
import sys
import json
import pandas as pd
from datetime import datetime
from pathlib import Path



from .notify import send_entry_telegram


def load_sent_orders(log_file: Path) -> tuple[set, list]:
    """
    Load danh sách các lệnh đã gửi từ file log
    Returns: (set of order keys, list of order details)
    """
    if not log_file.exists():
        return set(), []
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            orders = data.get("sent_orders", [])
            
            # Hỗ trợ cả format cũ (list of strings) và format mới (list of dicts)
            if orders and isinstance(orders[0], str):
                # Format cũ - chuyển sang format mới
                return set(orders), []
            else:
                # Format mới
                keys = set(order.get("key", "") for order in orders)
                return keys, orders
    except:
        return set(), []


def save_sent_orders(log_file: Path, sent_orders: list) -> None:
    """Lưu danh sách các lệnh đã gửi vào file log (format mới với chi tiết)"""
    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump({
                "sent_orders": sent_orders,
                "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }, f, indent=2, ensure_ascii=False)
    except:
        pass


def generate_order_key(row: pd.Series) -> str:
    """Tạo unique key cho mỗi lệnh để kiểm tra trùng (không bao gồm giá)"""
    return f"{row['Symbol']}|{row['Type']}|{row['Entry_Time']}"


def scan_open_orders(output_dir: Path) -> list:
    """Scan tất cả file CSV trong output_dir và lọc các lệnh Open"""
    open_orders = []
    
    if not output_dir.exists():
        return open_orders
    
    csv_files = list(output_dir.glob("*.csv"))
    
    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file)
            
            if 'Result' not in df.columns:
                continue
            
            open_df = df[df['Result'].str.upper() == 'OPEN']
            
            for _, row in open_df.iterrows():
                open_orders.append(row)
                
        except:
            continue
    
    return open_orders


def check_and_send_open_orders(output_dir: str, log_file: str) -> int:
    """
    Kiểm tra các lệnh Open và gửi Telegram nếu chưa gửi.
    Args:
        output_dir: Đường dẫn đến thư mục chứa các file CSV (vd: "src/output")
        log_file: Đường dẫn đến file log (vd: "trade/logs/sent_orders.json")
    Returns: Số lệnh đã gửi mới
    """
    output_path = Path(output_dir)
    log_path = Path(log_file)
    
    sent_keys, sent_orders = load_sent_orders(log_path)
    open_orders = scan_open_orders(output_path)
    
    new_sent_count = 0
    
    for row in open_orders:
        order_key = generate_order_key(row)
        
        if order_key in sent_keys:
            continue
        
        try:
            send_entry_telegram(
                type=row['Type'],
                entry=float(row['Entry_Price']),
                stoploss=float(row['Stop_Loss']),
                takeprofit=float(row['Take_Profit']),
                time=row['Entry_Time'],
                name_coin=row['Symbol']
            )
            
            # Thêm order với thông tin chi tiết
            order_detail = {
                "key": order_key,
                "entry_price": float(row['Entry_Price']),
                "stop_loss": float(row['Stop_Loss']),
                "take_profit": float(row['Take_Profit']),
                "sent_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            sent_orders.append(order_detail)
            sent_keys.add(order_key)
            new_sent_count += 1
            
        except:
            continue
    
    if new_sent_count > 0:
        save_sent_orders(log_path, sent_orders)
    
    return new_sent_count


if __name__ == "__main__":
    
    # Example usage
    OUTPUT_FOLDER = "res/output"    
    LOG_FILE = "res/logs/sent_orders.json"
    check_and_send_open_orders(OUTPUT_FOLDER, LOG_FILE)
    pass
