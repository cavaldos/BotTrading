import psutil
import time
import threading
from send import main_send
from get_data import getdata
from static import mainstatic
import os
import signal

def limit_cpu_usage(max_percent=50):
    """Giới hạn CPU sử dụng tối đa theo phần trăm"""
    process = psutil.Process()
    while True:
        # Measure across all threads
        cpu_usage = process.cpu_percent(interval=0.5)
        if cpu_usage > max_percent:
            time.sleep(0.5)  # Increased sleep time for better throttling

def main():
    # Chạy giới hạn CPU trong một thread riêng
    cpu_limiter = threading.Thread(target=limit_cpu_usage, args=(70,), daemon=True)
    cpu_limiter.start()

    # Chạy các thread chính với khoảng cách để tránh cạnh tranh
    t1 = threading.Thread(target=getdata, daemon=True)
    t1.start()
    time.sleep(2)  # Wait before starting the next thread
    
    t2 = threading.Thread(target=mainstatic, daemon=True)
    t2.start()
    time.sleep(2)  # Wait before starting the next thread
    
    t3 = threading.Thread(target=main_send, daemon=True)
    t3.start()

    print("Chương trình đang chạy. Nhấn Ctrl+C để dừng.")
    try:
        # Replace busy waiting with efficient sleep
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Dừng chương trình.")

if __name__ == '__main__':
    main()
