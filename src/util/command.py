from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue
import asyncio

my_token = '***REMOVED-TOKEN***'
CHAT_ID = '***REMOVED-CHATID***'


# Hàm "hello"
async def hello(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f'Hello {update.effective_user.first_name}')

# Hàm "test"
async def test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    print(f"Chat ID: {chat_id}")
    await context.bot.send_message(chat_id, text=f"Your Chat ID is: {chat_id}")

# Hàm periodic_task được gọi mỗi 5 giây
async def periodic_task(context: ContextTypes.DEFAULT_TYPE) -> None:
    # Gọi hàm test mà không cần `update`
    chat_id = context.job.context  # Lấy chat_id từ context
    await context.bot.send_message(chat_id, text="This is a periodic message!")

def main2():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = ApplicationBuilder().token(my_token).build()

    # Thêm handler
    app.add_handler(CommandHandler("hello", hello))
    app.add_handler(CommandHandler("test", test))

    app.run_polling()
    
main2()