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
            
            