import html
import json
import requests
from datetime import datetime, timedelta
import os
import sys
from dotenv import load_dotenv

from src.live.core.calculate import calculate_trade_size

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


def send_message_with_image(
    message: str,
    image: bytes,
    filename: str = "chart.png",
    timeout: int = 15,
    retries: int = 3,
) -> bool:
    """Send a message with a PNG attachment to the Discord webhook. Returns True on success."""
    if not DISCORD_WEBHOOK_URL:
        return False
    payload = {"content": message}
    for attempt in range(retries):
        try:
            response = requests.post(
                DISCORD_WEBHOOK_URL,
                data={"payload_json": json.dumps(payload)},
                files={"files[0]": (filename, image, "image/png")},
                timeout=timeout,
            )
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException:
            if attempt < retries - 1:
                continue
    return False


def entry_title(type, name_coin, signal_timeframe=None) -> str:
    """Title like 'BTCUSDT_5m| LONG' shared by Discord and Telegram messages."""
    timeframe_display = str(signal_timeframe).strip() if signal_timeframe not in (None, "") else ""
    title_symbol = str(name_coin)
    if timeframe_display and not title_symbol.endswith(f"_{timeframe_display}"):
        title_symbol = f"{title_symbol}_{timeframe_display}"
    return f"{title_symbol}| {type.upper()}"


def entry_body(entry, stoploss, takeprofit, time, risk_per_trade=5) -> str:
    """Price levels / position block of the entry message (plain text, no RESULT part).

    RR ratio được tính từ Entry/SL/TP thực tế đặt trên sàn.
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
    
    # Tính RR từ Entry/SL/TP thực tế
    risk = abs(sl_diff)
    reward = abs(tp_diff)
    actual_rr_ratio = reward / risk if risk > 0 else 0
    rr_display = f"1:{actual_rr_ratio:.2f}"

    return f"""Date: {date_str}
Time: {time_gmt0} (GMT+0) / {time_gmt7} (GMT+7)

PRICE LEVELS
Entry:      {entry:.4f}
SL:         {stoploss:.4f}  (-{sl_diff_percent:.2f}%)
TP:         {takeprofit:.4f}  (+{tp_diff_percent:.2f}%)

POSITION
Volume:     {required_margin:.2f}
Leverage:   {leverage}x
Risk:       ${risk_per_trade}

R:R Ratio:  {rr_display}"""


def format_entry_message(
    type,
    entry,
    stoploss,
    takeprofit,
    time,
    name_coin,
    result=None,
    risk_per_trade=5,
    signal_timeframe=None,
) -> str:
    """Build the trade entry text sent to Discord."""
    title = entry_title(type, name_coin, signal_timeframe)
    body = entry_body(entry, stoploss, takeprofit, time, risk_per_trade)
    return f"""**{title}**
```
{body}
RESULT
{result if result else "N/A"}
```"""


def format_entry_message_telegram(
    type,
    entry,
    stoploss,
    takeprofit,
    time,
    name_coin,
    result=None,
    risk_per_trade=5,
    signal_timeframe=None,
) -> str:
    """Same entry text as Discord, as Telegram HTML (the RESULT part is left out)."""
    dot = "🟢" if str(type).strip().upper() in ("LONG", "BUY") else "🔴"
    title = entry_title(type, name_coin, signal_timeframe)
    body = entry_body(entry, stoploss, takeprofit, time, risk_per_trade)
    return f"{dot} <b>{html.escape(title)}</b>\n<pre>{html.escape(body)}</pre>"


def send_entry_discord(
    type,
    entry,
    stoploss,
    takeprofit,
    time,
    name_coin,
    result=None,
    risk_per_trade=5,
    signal_timeframe=None,
    image: bytes | None = None,
):
    """Send trade entry notification to Discord, with the chart image when given."""
    message = format_entry_message(
        type, entry, stoploss, takeprofit, time, name_coin,
        result=result, risk_per_trade=risk_per_trade, signal_timeframe=signal_timeframe,
    )
    if image and send_message_with_image(message, image):
        return
    send_custom_message(message)


def send_message_to_discord(message: str):
    """Send raw text message to Discord."""
    send_custom_message(message)


# Test
if __name__ == "__main__":
    # Test gửi embed message
    send_entry_discord('Long', 95916.4, 94500.34, 96228.52, '2021-10-10 10:10:10', 'BTC/USDT', signal_timeframe='5m')
    
    # Test gửi tin nhắn text
    # send_custom_message('Hello from Discord Bot!')
