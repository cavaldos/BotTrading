import requests
from datetime import datetime, timedelta
import os
import sys
from dotenv import load_dotenv

from src.trade.core.calculate import calculate_trade_size
from src.trade.bybit.formatter import format_result as format_bybit_result
from src.trade.okx.formatter import format_result as format_okx_result

# Tải biến môi trường từ file .env
load_dotenv()

# Discord Webhook URL
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL')


def format_multi_exchange_result(result: dict) -> str:
    """
    Format kết quả từ nhiều sàn giao dịch thành chuỗi dễ đọc.
    
    Args:
        result: Dict chứa kết quả từ các sàn {'bybit': {...}, 'okx': {...}}
    
    Returns:
        str: Chuỗi format cho Discord
    """
    if result is None:
        return ""
    
    # Nếu là kết quả từ 1 sàn (format cũ)
    if 'bybit' not in result and 'okx' not in result:
        return format_bybit_result(result)
    
    lines = []
    
    # Format kết quả Bybit
    if result.get('bybit'):
        bybit_result = result['bybit']
        if bybit_result.get('success'):
            order = bybit_result.get('order', {})
            position_info = bybit_result.get('position_info', {})
            order_id = order.get('info', {}).get('orderId', 'N/A')
            quantity = position_info.get('quantity', 'N/A')
            lines.append(f"BYBIT: ✓ OK (ID: {order_id}, Qty: {quantity})")
        else:
            error = bybit_result.get('error', 'Unknown')
            # Truncate long error messages
            if len(error) > 50:
                error = error[:50] + "..."
            lines.append(f"BYBIT: ✗ {error}")
    
    # Format kết quả OKX
    if result.get('okx'):
        okx_result = result['okx']
        if okx_result.get('success'):
            order = okx_result.get('order', {})
            order_id = order.get('id', 'N/A') or order.get('info', {}).get('ordId', 'N/A')
            contracts = okx_result.get('contracts', 'N/A')
            lines.append(f"OKX:   ✓ OK (ID: {order_id}, Contracts: {contracts})")
        else:
            error = okx_result.get('error', 'Unknown')
            # Truncate long error messages
            if len(error) > 50:
                error = error[:50] + "..."
            lines.append(f"OKX:   ✗ {error}")
    
    if lines:
        return "\n" + "\n".join(lines)
    
    return ""

# Hàm gửi tin nhắn tùy chỉnh qua Discord webhook
def send_custom_message(message: str) -> None:
    """
    Gửi tin nhắn tùy chỉnh đến Discord webhook
    """
    payload = {
        "content": message
    }
    
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        response.raise_for_status()
        print(f"✓ Tin nhắn đã gửi đến Discord")
    except requests.exceptions.RequestException as e:
        print(f"✗ Lỗi gửi tin nhắn: {e}")


def send_entry_discord(type, entry, stoploss, takeprofit, time, name_coin, result = None):
    """
    Gửi thông tin entry lệnh giao dịch đến Discord
    """
    # Chuyển chuỗi time thành đối tượng datetime
    time_obj = datetime.strptime(time, '%Y-%m-%d %H:%M:%S')

    # Cộng thêm 7 giờ vào thời gian
    new_time = time_obj + timedelta(hours=7)

    # Tách ngày và giờ (bỏ giây)
    date_str = time_obj.strftime('%Y-%m-%d')
    time_gmt0 = time_obj.strftime('%H:%M')
    time_gmt7 = new_time.strftime('%H:%M')

    # Tính toán position size
    required_margin, leverage, risk_per_trade = calculate_trade_size(entry, stoploss, takeprofit)
    
    # Tính chênh lệch giá
    sl_diff = entry - stoploss
    tp_diff = takeprofit - entry
    
    # Tính chênh lệch theo phần trăm
    sl_diff_percent = (abs(sl_diff) / entry) * 100
    tp_diff_percent = (abs(tp_diff) / entry) * 100
    
    # Tính R:R ratio
    risk = abs(sl_diff)
    reward = abs(tp_diff)
    rr_ratio = reward / risk if risk > 0 else 0

    # Xác định loại lệnh
    type_upper = type.upper()

    # Format message dạng table
    message = f"""**{name_coin} | {type_upper}**
```
Date: {date_str}
Time: {time_gmt0} (GMT+0) / {time_gmt7} (GMT+7)

PRICE LEVELS
Entry:      {entry:.4f}
SL:         {stoploss:.4f}  (-{sl_diff_percent:.2f}%)
TP:         {takeprofit:.4f}  (+{tp_diff_percent:.2f}%)

POSITION
Volume:     {required_margin:.2f}
Leverage:   {leverage}x
Risk:       ${risk_per_trade}

R:R Ratio:  1:{rr_ratio:.2f}
{format_multi_exchange_result(result) if result else ''}
```"""

    send_custom_message(message)


def send_message_to_discord(message: str):
    """
    Hàm wrapper để gửi tin nhắn text thô đến Discord
    """
    send_custom_message(message)


# Test
if __name__ == "__main__":
    # Test gửi embed message
    send_entry_discord('Long', 95916.4, 94500.34, 96228.52, '2021-10-10 10:10:10', 'BTC/USDT')
    
    # Test gửi tin nhắn text
    # send_custom_message('Hello from Discord Bot!')
