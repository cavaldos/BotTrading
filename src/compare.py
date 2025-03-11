import os
import pandas as pd

def compare_signals_with_results(signals_file, results_folder):
    # Đọc file CSV chứa các signal không trùng lặp
    signals_df = pd.read_csv(signals_file)
    
    # Khởi tạo biến đếm
    win_count = 0
    loss_count = 0
    total_signals = len(signals_df)
    matched_signals = 0
    
    # Lưu trữ kết quả chi tiết
    detailed_results = []
    
    # Duyệt qua tất cả các file trong thư mục kết quả
    for filename in os.listdir(results_folder):
        if filename.endswith('.csv'):
            file_path = os.path.join(results_folder, filename)
            
            # Đọc file kết quả
            results_df = pd.read_csv(file_path)
            
            # Đảm bảo cột Time tồn tại trong file kết quả
            if 'Time' in results_df.columns and 'Status' in results_df.columns:
                # Đổi tên cột cho dễ so sánh
                results_df = results_df.rename(columns={'Time': 'Signal_Time'})
                
                # So sánh từng signal với các kết quả
                for _, signal_row in signals_df.iterrows():
                    signal_time = signal_row['Signal_Time']
                    signal_type = signal_row['Type']
                    
                    # Tìm kiếm kết quả tương ứng
                    matching_results = results_df[(results_df['Signal_Time'] == signal_time) & 
                                                (results_df['Type'] == signal_type)]
                    
                    if not matching_results.empty:
                        matched_signals += 1
                        # Lấy trạng thái win/loss của signal
                        status = matching_results.iloc[0]['Status'].lower()
                        
                        if status == 'win':
                            win_count += 1
                        elif status == 'loss':
                            loss_count += 1
                            
                        # Thêm vào kết quả chi tiết
                        detailed_results.append({
                            'Signal_Time': signal_time,
                            'Type': signal_type,
                            'File': filename,
                            'Status': status
                        })
    
    # Tính tỷ lệ thắng
    win_rate = (win_count / matched_signals * 100) if matched_signals > 0 else 0
    
    # In kết quả
    print(f"Tổng số tín hiệu: {total_signals}")
    print(f"Số tín hiệu được tìm thấy: {matched_signals}")
    print(f"Số lệnh thắng (win): {win_count}")
    print(f"Số lệnh thua (loss): {loss_count}")
    print(f"Tỷ lệ thắng: {win_rate:.2f}%")
    
    # Tạo DataFrame từ kết quả chi tiết và lưu vào file
    if detailed_results:
        details_df = pd.DataFrame(detailed_results)
        details_df.to_csv('signal_results_detailed.csv', index=False)
        print("Đã lưu kết quả chi tiết vào file 'signal_results_detailed.csv'")

# Sử dụng hàm với đường dẫn phù hợp
signals_file = "signals_no_duplicates.csv"  # File chứa tín hiệu không trùng lặp
results_folder = "src/res"  # Thư mục chứa các file kết quả

compare_signals_with_results(signals_file, results_folder)