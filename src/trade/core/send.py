import os
import sys
import json
import asyncio
import pandas as pd
from datetime import datetime
from pathlib import Path


from src.trade.core.notify import send_entry_telegram
from src.trade.core.discord_notify import send_entry_discord

from .place_order import place_order, bybit_format_order_message, okx_format_order_message, risk_per_trade


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
                      
                      # Create key: {SYMBOL}|{TYPE}|{ENTRY_TIME}
                      trade_key = f"{symbol}|{trade_type}|{entry_time}"
                      
                      # Save only valid results
                      if result in ('WIN', 'LOSS', 'OPEN'):
                          trades_from_csv[trade_key] = result
                     
             except Exception:
                 continue
     
     # Update order statuses
     updated_count = 0
     
     for order in sent_orders:
         key = order.get("key", "")
         current_status = order.get("status", "")
         
         # Skip only final results (WIN/LOSS)
         if current_status in ('WIN', 'LOSS'):
             continue
         
         if key in trades_from_csv:
             new_status = trades_from_csv[key]
             
             if new_status != current_status:
                 order["status"] = new_status
                 updated_count += 1
         else:
             # Order not found in CSV -> mark as PASSED
             if current_status == 'OPEN':
                 order["status"] = 'PASSED'
                 updated_count += 1
     
     # Save if any updates
     if updated_count > 0:
         save_sent_orders(log_path, sent_orders)
     
     return updated_count


def generate_order_key(row: pd.Series) -> str:
    symbol = str(row['Symbol']).strip()
    trade_type = str(row['Type']).strip().upper()
    entry_time = str(row['Entry_Time']).strip()
    return f"{symbol}|{trade_type}|{entry_time}"


def scan_open_orders(output_dir: Path) -> list:
     """Scan all CSV files in output_dir and filter OPEN orders."""
     open_orders = []
     
     if not output_dir.exists():
         return open_orders
     
     csv_files = list(output_dir.glob("*.csv"))
     
     for csv_file in csv_files:
         try:
             df = pd.read_csv(csv_file)
             
             if 'Result' not in df.columns:
                 continue
             
             # Convert Result column to string, remove NaN, handle case-insensitive
             df['Result'] = df['Result'].fillna('').astype(str).str.strip().str.upper()
             open_df = df[df['Result'] == 'OPEN']
             
             for _, row in open_df.iterrows():
                 open_orders.append(row)
                 
         except Exception as e:
             print(f"Error scanning {csv_file}: {e}")
             continue
     
     return open_orders


async def send_entry_notifications_async(row, risk_per_trade: float, timeout: float = 60, combined_result: str = None):
     """Send entry notifications to Telegram and Discord asynchronously."""
     try:
          params_tele = {
              "type": row['Type'],
              "entry": float(row['Entry_Price']),
              "stoploss": float(row['Stop_Loss']),
              "takeprofit": float(row['Take_Profit']),
              "time": row['Entry_Time'],
              "name_coin": row['Symbol'],
              "risk_per_trade": risk_per_trade
          }
          params_dis = {
              "type": row['Type'],
              "entry": float(row['Entry_Price']),
              "stoploss": float(row['Stop_Loss']),
              "takeprofit": float(row['Take_Profit']),
              "time": row['Entry_Time'],
              "name_coin": row['Symbol'],
              "result": combined_result,
              "risk_per_trade": risk_per_trade
          }

          
          await asyncio.wait_for(
              asyncio.gather(
                  asyncio.to_thread(send_entry_telegram, **params_tele),
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


def check_and_send_open_orders(output_dir: str, log_file: str) -> int:
      """Check for new OPEN orders and send notifications. Returns count of new orders sent."""
      output_path = Path(output_dir)
      log_path = Path(log_file)
      
      update_order_status_from_csv(output_dir, log_file)
      
      sent_keys, sent_orders = load_sent_orders(log_path)
      open_orders = scan_open_orders(output_path)
      
      new_sent_count = 0
      
      for row in open_orders:
          order_key = generate_order_key(row)
          
          if order_key in sent_keys:
              continue
          
          try:
              
              order_result = place_order(
                 symbol=row['Symbol'],
                 order_type=row['Type'],
                 entry_price=float(row['Entry_Price']),
                 stop_loss=float(row['Stop_Loss']),
                 take_profit=None,
                 entry_time=row['Entry_Time'],
                 risk_per_trade=risk_per_trade,
              )
              bybit_msg = ""
              okx_msg = ""
             
              if order_result.get('bybit'):
                  bybit_msg = f"**BYBIT**\n{bybit_format_order_message(order_result['bybit'])}"
             
              if order_result.get('okx'):
                  okx_msg = f"**OKX**\n{okx_format_order_message(order_result['okx'])}"
             
              combined_result = f"{bybit_msg}\n\n{okx_msg}".strip()
              # Send notifications asynchronously
              asyncio.run(send_entry_notifications_async(row, risk_per_trade, combined_result=combined_result))
              # Add to sent_orders
              order_detail = {
                  "key": order_key,
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
     
     # Example usage
     OUTPUT_FOLDER = "res/logs/output"    
     LOG_FILE = "res/logs/sent_orders.json"
     check_and_send_open_orders(OUTPUT_FOLDER, LOG_FILE)
     pass
