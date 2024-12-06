from send import main_send
from get_data import getdata
from static import main_static
import threading
import os

def main():
    # Kiểm tra và tạo thư mục nếu chưa tồn tại
    os.makedirs('./data', exist_ok=True)
    os.makedirs('./output', exist_ok=True)

    # Tạo các thread
    t1 = threading.Thread(target=getdata, daemon=True)
    t2 = threading.Thread(target=main_static, daemon=True)
    t3 = threading.Thread(target=main_send, daemon=True)

    # Bắt đầu các thread
    t1.start()
    t2.start()
    t3.start()

    print("Chương trình đang chạy. Nhấn Ctrl+C để dừng.")
    try:
        while True:
            pass
    except KeyboardInterrupt:
        print("Dừng chương trình.")

if __name__ == '__main__':
    main()
