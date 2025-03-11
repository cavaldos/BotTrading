import os
import shutil

def remove_all_files(folder_path):
    # Kiểm tra xem thư mục có tồn tại không
    if not os.path.exists(folder_path):
        print(f"Thư mục {folder_path} không tồn tại.")
        return

    # Lặp và xóa tất cả các file
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f'Lỗi khi xóa {file_path}. Lý do: {e}')

def clear_all_file_contents(folder_path):

    if not os.path.exists(folder_path):
        print(f"Thư mục {folder_path} không tồn tại.")
        return

    # Lặp qua tất cả các file trong thư mục
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        try:
            # Chỉ xử lý các file (không phải thư mục hoặc symlink)
            if os.path.isfile(file_path) and not os.path.islink(file_path):
                # Mở file ở chế độ 'w' để xóa nội dung
                with open(file_path, 'w') as f:
                    # Không ghi gì vào file, khiến file trống
                    pass
                print(f"Đã xóa nội dung của file {filename}")
        except Exception as e:
            print(f'Lỗi khi xóa nội dung của {file_path}. Lý do: {e}')


            
if __name__ == "__main__":
    # Ví dụ sử dụng
    folder_path = "src/data2"
    # remove_all_files(folder_path)
    # clear_all_file_contents(folder_path)