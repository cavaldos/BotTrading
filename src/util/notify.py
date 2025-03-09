from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import asyncio
from datetime import datetime, timedelta
from caculate import calculate_trade_size
my_token = '***REMOVED-TOKEN***'
CHAT_ID = '***REMOVED-CHATID***'


# Hàm gửi tin nhắn từ string được truyền vào
async def send_custom_message(application, message: str) -> None:
    # Hiển thị trạng thái "typing"
    await application.bot.send_chat_action(chat_id=CHAT_ID, action="typing")
    
    # Gửi tin nhắn thực tế
    await application.bot.send_message(
        chat_id=CHAT_ID, 
        text=message
    )

# Hàm để gửi tin nhắn tuỳ chỉnh cho CHAT_ID
def send_message_to_chat(message: str):
    async def send_message():
        app = ApplicationBuilder().token(my_token).build()
        await send_custom_message(app, message)
    
    asyncio.run(send_message())  # Gọi hàm gửi tin nhắn với message


def entry_point(type, entry, stoploss, takeprofit, time,name_coin):
    # Chuyển chuỗi time thành đối tượng datetime
    time_obj = datetime.strptime(time, '%Y-%m-%d %H:%M:%S')
    
    # Cộng thêm 5 giờ vào thời gian
    new_time = time_obj + timedelta(hours=7)
    
    # Chuyển đổi lại thời gian sang chuỗi theo định dạng mong muốn
    new_time_str = new_time.strftime('%Y-%m-%d %H:%M:%S')
    time_obj_str = time_obj.strftime('%Y-%m-%d %H:%M:%S')
    # Gửi tin nhắn
    send_message_to_chat(
        'Coin: ' + name_coin + '\n' +
        'GMT+0: ' + time_obj_str + '\n' +
        'GMT+7: ' + new_time_str + '\n' +
        'Type: ' + type + '\n' +
        'Entry: ' + str(entry) + '\n' +
        'Stoploss: ' + str(stoploss) + '\n' +
        'Takeprofit: ' + str(takeprofit)+ '\n' +
        'Trade size: ' + str(calculate_trade_size(entry, stoploss, takeprofit)) + '\n' 
    )
 
# Test
entry_point('Long', 86397, 85765, 87573, '2021-10-10 10:10:10', 'TestCoin')
