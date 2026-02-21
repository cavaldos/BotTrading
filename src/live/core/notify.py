from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import asyncio
from datetime import datetime, timedelta
import os
import sys
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from .calculate import calculate_trade_size

load_dotenv()

my_token = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')



async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('hello')


async def send_custom_message(application, message: str) -> None:
    """Send custom message to Telegram with typing indicator."""
    await application.bot.send_chat_action(chat_id=CHAT_ID, action="typing")
    await application.bot.send_message(
        chat_id=CHAT_ID,
        text=message,
        parse_mode="Markdown"
    )

def send_message_to_chat(message: str, timeout: int = 5, retries: int = 3):
    """Send message to configured Telegram chat with retry."""
    async def send_message():
        app = ApplicationBuilder().token(my_token).read_timeout(timeout).write_timeout(timeout).connect_timeout(timeout).build()
        await send_custom_message(app, message)

    for attempt in range(retries):
        try:
            asyncio.run(send_message())
            return
        except Exception as e:
            if attempt < retries - 1:
                continue
            raise e


def send_entry_telegram(type, entry, stoploss, takeprofit, time, name_coin, risk_per_trade = 5):
    """Send trade entry notification to Telegram."""
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
    rr_ratio = reward / risk if risk > 0 else 0

    type_dot = "🟢" if type.lower() == "long" else "🔴"

    message = f"""{type_dot} *{name_coin}* | *{type.upper()}*
Date: {date_str}
├ GMT+0: *{time_gmt0}*
└ GMT+7: *{time_gmt7}*
Price Levels
├ Entry:      `{entry:.4f}`
├ SL:   `{stoploss:.4f}` (-{sl_diff_percent:.2f}%)
└ TP: `{takeprofit:.4f}` (+{tp_diff_percent:.2f}%)
Position
├ Volume:   `{required_margin:.2f}`
├ Leverage: `{leverage}x`
└ Risk:     `{risk_per_trade}$`
R:R = *1:{rr_ratio:.2f}*
"""

    send_message_to_chat(message)

def run_telegram_bot():
    """Start Telegram bot in a background thread."""
    async def start_bot():
        app = ApplicationBuilder().token(my_token).build()

        app.add_handler(CommandHandler("check", check_command))

        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await app.stop()
            await app.updater.stop()

    import threading
    bot_thread = threading.Thread(target=lambda: asyncio.run(start_bot()))
    bot_thread.daemon = True
    bot_thread.start()

# Test
if __name__ == "__main__":
    # Test sending a message
    send_entry_telegram('Short', 95916.4, 9760.34, 96228.52, '2021-10-10 10:10:10', 'TestCoin')

    # # Run the bot for testing
    # run_telegram_bot()

    # # Keep the main thread alive for testing
    # import time
    # while True:
    #     time.sleep(1)
