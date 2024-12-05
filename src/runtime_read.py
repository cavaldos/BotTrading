import os
import pandas as pd
from util.notify import entry_point

# Đọc file CSV vào DataFrame
def load_data(filename):
    return pd.read_csv(filename)

# Lọc các lệnh có trạng thái 'OnGoing' và in từng lệnh
def print_ongoing_trades_from_file(file_path):
    # Đọc dữ liệu từ file
    df = load_data(file_path)
    
    # Lọc các dòng có trạng thái là 'OnGoing'
    ongoing_trades = df[df['Status'] == 'OnGoing']
    
    if not ongoing_trades.empty:
        # Dùng iterrows() để lặp qua từng dòng và in ra
        for index, row in ongoing_trades.iterrows():
            print(f"File: {file_path}")  # In tên file trước từng lệnh
            print(f"Time: {row['Time']}")
            print(f"Type: {row['Type']}")
            print(f"Entry: {row['Entry']}")
            print(f"Stoploss: {row['Stoploss']}")
            print(f"Take_Profit: {row['Take_Profit']}")
            print(f"Status: {row['Status']}")
            print('-' * 30)  # In dấu gạch ngang để phân cách từng lệnh
            entry_point(row['Type'],
                            row['Entry'],
                            row['Stoploss'],
                            row['Take_Profit'],
                            str(row['Time']))
        print("\n")
    else:
        print(f"Không có lệnh 'OnGoing' trong file: {file_path}\n")

# Xử lý tất cả các file trong folder
def process_folder(folder_path):
    # Lặp qua tất cả các file trong folder
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        
        # Kiểm tra nếu là file CSV
        if os.path.isfile(file_path) and filename.endswith('.csv'):
            print_ongoing_trades_from_file(file_path)

if __name__ == '__main__':
    folder_path = './output' 

    process_folder(folder_path)
