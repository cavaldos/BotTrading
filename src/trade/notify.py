from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import asyncio
from datetime import datetime, timedelta
import os
import sys
from dotenv import load_dotenv

# Add the src directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from calculate import calculate_trade_size

# Tải biến môi trường từ file .env
load_dotenv()

# Đọc token và chat_id từ biến môi trường
my_token = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')


# Command handler for /check command
async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('hello')


# Hàm gửi tin nhắn từ string được truyền vào
async def send_custom_message(application, message: str) -> None:
    # Hiển thị trạng thái "typing"
    await application.bot.send_chat_action(chat_id=CHAT_ID, action="typing")

    # Gửi tin nhắn thực tế với hỗ trợ Markdown
    await application.bot.send_message(
        chat_id=CHAT_ID,
        text=message,
        parse_mode="Markdown"
    )

# Hàm để gửi tin nhắn tuỳ chỉnh cho CHAT_ID
def send_message_to_chat(message: str):
    async def send_message():
        app = ApplicationBuilder().token(my_token).build()
        await send_custom_message(app, message)

    asyncio.run(send_message())  # Gọi hàm gửi tin nhắn với message


def send_entry_telegram(type, entry, stoploss, takeprofit, time, name_coin):
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
    
    # Tính R:R ratio
    risk = abs(sl_diff)
    reward = abs(tp_diff)
    rr_ratio = reward / risk if risk > 0 else 0

    # Chấm màu theo loại lệnh: xanh cho LONG, đỏ cho SHORT
    type_dot = "🟢" if type.lower() == "long" else "🔴"

    # Format message
    message = f"""{type_dot} *{name_coin}* | *{type.upper()}*
Date: {date_str}
├ GMT+0: *{time_gmt0}*
└ GMT+7: *{time_gmt7}*
Price Levels
├ Entry:      `{entry:.2f}`
├ SL:   `{stoploss:.2f}` ({sl_diff:+.2f})
└ TP: `{takeprofit:.2f}` ({tp_diff:+.2f})
Position
├ Volume:   `{required_margin:.2f}`
├ Leverage: `{leverage}x`
└ Risk:     `{risk_per_trade}$`
R:R = *1:{rr_ratio:.2f}*
"""

    send_message_to_chat(message)

# Function to run the Telegram bot
def run_telegram_bot():
    async def start_bot():
        app = ApplicationBuilder().token(my_token).build()

        # Add command handlers
        app.add_handler(CommandHandler("check", check_command))

        # Start the bot
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        # Keep the bot running
        try:
            await asyncio.Future()  # Run forever
        except asyncio.CancelledError:
            # Handle shutdown
            await app.stop()
            await app.updater.stop()

    # Run the bot in a separate thread to not block the main program
    import threading
    bot_thread = threading.Thread(target=lambda: asyncio.run(start_bot()))
    bot_thread.daemon = True  # Allow the program to exit even if the bot is running
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
