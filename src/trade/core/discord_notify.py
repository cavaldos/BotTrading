import requests
from datetime import datetime, timedelta
import os
import sys
from dotenv import load_dotenv

from src.trade.core.calculate import calculate_trade_size

load_dotenv()

DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL')


def send_custom_message(message: str, timeout: int = 5, retries: int = 3) -> None:
    """Send custom message to Discord webhook with timeout and retry."""
    payload = {
        "content": message
    }
    
    for attempt in range(retries):
        try:
            response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=timeout)
            response.raise_for_status()
            return
        except requests.exceptions.Timeout:
            if attempt < retries - 1:
                continue
        except requests.exceptions.RequestException:
            pass
    # Silent mode - avoid dashboard flicker


def send_entry_discord(type, entry, stoploss, takeprofit, time, name_coin, result = None, risk_per_trade = 5, configured_rr = None):
    """Send trade entry notification to Discord.
    """
    time_obj = datetime.strptime(time, '%Y-%m-%d %H:%M:%S')
    new_time = time_obj + timedelta(hours=7)

    date_str = time_obj.strftime('%Y-%m-%d')
    time_gmt0 = time_obj.strftime('%H:%M')
    time_gmt7 = new_time.strftime('%H:%M')

    required_margin, leverage = calculate_trade_size(entry, stoploss, takeprofit, risk_per_trade)
    
    sl_diff = entry - stoploss
    tp_diff = takeprofit - entry
    sl_diff_percent = (abs(sl_diff) / entry) * 100
    tp_diff_percent = (abs(tp_diff) / entry) * 100
    
    risk = abs(sl_diff)
    reward = abs(tp_diff)
    actual_rr_ratio = reward / risk if risk > 0 else 0
    
    # Hiển thị RR: nếu có configured_rr thì hiển thị cả 2
    rr_display = f"1:{actual_rr_ratio:.2f}"
    if configured_rr is not None and abs(configured_rr - actual_rr_ratio) > 0.01:
        rr_display = f"1:{actual_rr_ratio:.2f} (cấu hình: 1:{configured_rr:.2f})"

    type_upper = type.upper()

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

R:R Ratio:  {rr_display}
RESULT
{result if result else "N/A"}
```"""

    send_custom_message(message)


def send_message_to_discord(message: str):
    """Send raw text message to Discord."""
    send_custom_message(message)


# Test
if __name__ == "__main__":
    # Test gửi embed message
    send_entry_discord('Long', 95916.4, 94500.34, 96228.52, '2021-10-10 10:10:10', 'BTC/USDT')
    
    # Test gửi tin nhắn text
    # send_custom_message('Hello from Discord Bot!')
