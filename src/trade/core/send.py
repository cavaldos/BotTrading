import os
import sys
import json
import yaml
import pandas as pd
from datetime import datetime
from pathlib import Path


from src.trade.bybit.order_executor import validate_and_place_order as bybit_place_order
from src.trade.okx.order_executor import validate_and_place_order as okx_place_order

from .notify import send_entry_telegram
from .discord_notify import send_entry_discord


# Load config
def load_config() -> dict:
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, "r") as f:
        return yaml.safe_load(f) or {}


CONFIG = load_config()
RISK_USD = CONFIG.get("strategy", {}).get("risk_usd", 5)


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


def update_order_status_from_csv(output_dir: str, log_file: str) -> int:
    """
    Cập nhật status các orders trong sent_orders.json từ kết quả trong CSV.
    Status có thể là: WIN, LOSS, OPEN
    
    Args:
        output_dir: Đường dẫn đến thư mục chứa các file CSV
        log_file: Đường dẫn đến file sent_orders.json
    
    Returns: Số orders được cập nhật
    """
    output_path = Path(output_dir)
    log_path = Path(log_file)
    
    # Load sent_orders từ file log
    sent_keys, sent_orders = load_sent_orders(log_path)
    
    if not sent_orders:
        return 0
    
    # Load tất cả trades từ CSV
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
                    result = str(row.get('Result', '')).strip().upper()
                    
                    # Create key: {SYMBOL}|{TYPE}|{ENTRY_TIME}
                    trade_key = f"{symbol}|{trade_type}|{entry_time}"
                    
                    # Lưu kết quả vào dict
                    if result in ('WIN', 'LOSS', 'OPEN'):
                        trades_from_csv[trade_key] = result
                    
            except Exception:
                continue
    
    # Cập nhật status cho các orders
    updated_count = 0
    
    for order in sent_orders:
        key = order.get("key", "")
        if key in trades_from_csv:
            new_status = trades_from_csv[key]
            current_status = order.get("status", "")
            
            if new_status != current_status:
                order["status"] = new_status
                updated_count += 1
    
    # Lưu lại nếu có cập nhật
    if updated_count > 0:
        save_sent_orders(log_path, sent_orders)
    
    return updated_count


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
def place_order(
    symbol: str,
    order_type: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    entry_time: str,
    leverage: int = 20,
    risk_usd: float = None,
    exchanges: list = None,
) -> dict:
    """
    Đặt lệnh trên một hoặc nhiều sàn giao dịch.
    
    Args:
        symbol: Symbol giao dịch
        order_type: 'LONG' hoặc 'SHORT'
        entry_price: Giá entry
        stop_loss: Giá stop loss
        take_profit: Giá take profit
        entry_time: Thời gian entry
        leverage: Đòn bẩy (default 20)
        risk_usd: Rủi ro USD (default từ config)
        exchanges: Danh sách sàn ['bybit', 'okx'] (default cả 2)
    
    Returns:
        dict: Kết quả đặt lệnh cho từng sàn
    """
    if risk_usd is None:
        risk_usd = RISK_USD
    
    if exchanges is None:
        exchanges = ['bybit', 'okx']  # Mặc định đặt cả 2 sàn
    
    order_type = order_type.strip().upper()
    side = 'buy' if order_type == 'LONG' else 'sell'
    
    results = {
        'success': False,
        'bybit': None,
        'okx': None,
        'any_success': False,
    }
    
    # Đặt lệnh trên Bybit
    if 'bybit' in exchanges:
        try:
            bybit_result = bybit_place_order(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                leverage=leverage,
                risk_usd=risk_usd,
            )
            results['bybit'] = bybit_result
            if bybit_result.get('success'):
                results['any_success'] = True
        except Exception as e:
            results['bybit'] = {
                'success': False,
                'error': str(e),
                'exchange': 'Bybit',
            }
    
    # Đặt lệnh trên OKX
    if 'okx' in exchanges:
        try:
            okx_result = okx_place_order(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                leverage=leverage,
                risk_usd=risk_usd,
            )
            results['okx'] = okx_result
            if okx_result.get('success'):
                results['any_success'] = True
        except Exception as e:
            results['okx'] = {
                'success': False,
                'error': str(e),
                'exchange': 'OKX',
            }
    
    # Đánh dấu success nếu ít nhất 1 sàn thành công
    results['success'] = results['any_success']
    
    return results


def check_and_send_open_orders(output_dir: str, log_file: str, exchanges: list = None) -> int:
     """
     Kiểm tra các lệnh Open, đặt lệnh trên Bybit và OKX, sau đó gửi Telegram/Discord.
     
     Args:
         output_dir: Đường dẫn đến thư mục chứa các file CSV (vd: "src/output")
         log_file: Đường dẫn đến file log (vd: "trade/logs/sent_orders.json")
         exchanges: Danh sách sàn giao dịch ['bybit', 'okx'] (default cả 2)
     
     Returns: Số lệnh đã gửi mới
     """
     if exchanges is None:
         exchanges = ['bybit', 'okx']  # Mặc định đặt cả 2 sàn
     
     output_path = Path(output_dir)
     log_path = Path(log_file)
     
     # Cập nhật status từ CSV trước khi xử lý
     updated_status_count = update_order_status_from_csv(output_dir, log_file)
     
     sent_keys, sent_orders = load_sent_orders(log_path)
     open_orders = scan_open_orders(output_path)
     
     new_sent_count = 0
     
     for row in open_orders:
         order_key = generate_order_key(row)
         
         if order_key in sent_keys:
             continue
         
         try:
             # Bước 1: Đặt lệnh trên cả Bybit và OKX
             order_result = place_order(
                 symbol=row['Symbol'],
                 order_type=row['Type'],
                 entry_price=float(row['Entry_Price']),
                 stop_loss=float(row['Stop_Loss']),
                 take_profit=float(row['Take_Profit']),
                 entry_time=row['Entry_Time'],
                 exchanges=exchanges,
             )
             
             # Bước 2: Gửi thông báo Telegram
             send_entry_telegram(
                 type=row['Type'],
                 entry=float(row['Entry_Price']),
                 stoploss=float(row['Stop_Loss']),
                 takeprofit=float(row['Take_Profit']),
                 time=row['Entry_Time'],
                 name_coin=row['Symbol']
             )
             
             # Bước 3: Gửi thông báo Discord với kết quả từ cả 2 sàn
             send_entry_discord(
                 type=row['Type'],
                 entry=float(row['Entry_Price']),
                 stoploss=float(row['Stop_Loss']),
                 takeprofit=float(row['Take_Profit']),
                 time=row['Entry_Time'],
                 name_coin=row['Symbol'],
                 result=order_result
             )
             
             # Thêm order với thông tin chi tiết
             order_detail = {
                 "key": order_key,
                 "entry_price": float(row['Entry_Price']),
                 "stop_loss": float(row['Stop_Loss']),
                 "take_profit": float(row['Take_Profit']),
                 "sent_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                 "status": "OPEN",
                 "exchanges": {
                     "bybit": order_result.get('bybit', {}).get('success', False) if order_result.get('bybit') else False,
                     "okx": order_result.get('okx', {}).get('success', False) if order_result.get('okx') else False,
                 }
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
