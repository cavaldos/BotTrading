import pandas as pd

def remove_duplicate_signal_times(input_file, output_file):
    # Đọc file CSV vào DataFrame
    df = pd.read_csv(input_file)
    
    # In thông tin về số dòng ban đầu
    print(f"Số dòng ban đầu: {len(df)}")
    
    # Loại bỏ các dòng có Signal_Time trùng nhau, giữ lại dòng đầu tiên
    df_no_duplicates = df.drop_duplicates(subset=['Signal_Time'], keep='first')
    
    # In thông tin về số dòng sau khi loại bỏ trùng lặp
    print(f"Số dòng sau khi loại bỏ trùng lặp: {len(df_no_duplicates)}")
    
    # Lưu DataFrame mới vào file CSV mới
    df_no_duplicates.to_csv(output_file, index=False)
    
    print(f"Đã tạo file mới: {output_file}")

# Tên file đầu vào và đầu ra
input_file = "src/signals_log.csv"  # Thay đổi thành tên file của bạn
output_file = "signals_no_duplicates.csv"

# Thực thi hàm
remove_duplicate_signal_times(input_file, output_file)