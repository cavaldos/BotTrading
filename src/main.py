import psutil
import time
import threading
from send import main_send
from get_data import getdata
from static import mainstatic

def limit_cpu_usage(max_percent=50):
    """Giới hạn CPU sử dụng tối đa theo phần trăm"""
    process = psutil.Process()
    while True:
        cpu_usage = process.cpu_percent(interval=1)
        if cpu_usage > max_percent:
            time.sleep(0.1)  # Giảm tải CPU nếu vượt ngưỡng

def main():
    # Chạy giới hạn CPU trong một thread riêng
    cpu_limiter = threading.Thread(target=limit_cpu_usage, daemon=True)
    cpu_limiter.start()

    # Chạy các thread chính
    t1 = threading.Thread(target=getdata, daemon=True)
    t2 = threading.Thread(target=mainstatic, daemon=True)
    t3 = threading.Thread(target=main_send, daemon=True)

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
