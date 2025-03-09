from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import asyncio
from datetime import datetime, timedelta

my_token = '***REMOVED-TOKEN***'

# Lưu các chat_id đã từng tương tác vào một set
user_chat_ids = set()

# Hàm lưu chat_id khi người dùng tương tác với bot
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_chat_ids.add(chat_id)
    await context.bot.send_message(chat_id=chat_id, text='Bạn đã đăng ký nhận thông báo.')

# Hàm gửi tin nhắn đến tất cả mọi người đã từng tương tác
async def send_message_to_all(application, message: str):
    for chat_id in user_chat_ids:
        try:
            await application.bot.send_chat_action(chat_id=chat_id, action="typing")
            await application.bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            print(f"Lỗi khi gửi cho chat_id {chat_id}: {e}")

# Hàm để xuất ra và dùng ở chỗ khác
def export_entry_point():
    app = ApplicationBuilder().token(my_token).build()
    app.add_handler(CommandHandler("start", start))

    async def scheduled_entry_point():
        while True:
            type, entry, stoploss, takeprofit, time, name_coin = 'Long', '1.2345', 1.2345, 1.2345, datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), 'TestCoin'

            time_obj = datetime.strptime(time, '%Y-%m-%d %H:%M:%S')
            new_time = time_obj + timedelta(hours=7)

            message = (
                f'Coin: {name_coin}\n'
                f'GMT+0: {time_obj.strftime("%Y-%m-%d %H:%M:%S")}\n'
                f'GMT+7: {new_time.strftime("%Y-%m-%d %H:%M:%S")}\n'
                f'Type: {type}\n'
                f'Entry: {entry}\n'
                f'Stoploss: {stoploss}\n'
                f'Takeprofit: {takeprofit}'
            )

            await send_message_to_all(app, message)
            await asyncio.sleep(60)

    asyncio.ensure_future(scheduled_entry_point())
    app.run_polling()

if __name__ == '__main__':
    export_entry_point()