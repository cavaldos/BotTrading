# Bắt đầu từ một image Python chính thức
FROM python:3.9-slim

# Cài đặt thư viện và công cụ cần thiết (ví dụ: để cài đặt môi trường ảo, git, v.v.)
RUN apt-get update && apt-get install -y \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Thiết lập thư mục làm việc
WORKDIR /app

# Sao chép các file vào thư mục làm việc
COPY requirements.txt /app/
COPY main.py /app/

# Tạo môi trường ảo
RUN python -m venv venv

# Cập nhật pip
RUN venv/bin/pip install --upgrade pip

# Cài đặt các thư viện từ requirements.txt
RUN venv/bin/pip install -r requirements.txt

# Lệnh chạy main.py trong môi trường ảo
CMD ["venv/bin/python", "main.py"]

# # Bắt đầu từ một image Python chính thức
# FROM python:3.9-slim

# # Cài đặt thư viện và công cụ cần thiết (ví dụ: để cài đặt môi trường ảo)
# RUN apt-get update && apt-get install -y \
#     python3-venv \
#     && rm -rf /var/lib/apt/lists/*

# # Thiết lập thư mục làm việc
# WORKDIR /app

# # Sao chép requirements.txt vào container
# COPY requirements.txt /app/

# # Sao chép toàn bộ thư mục src vào container
# COPY src /app/src/

# # Tạo môi trường ảo
# RUN python -m venv venv

# # Cập nhật pip
# RUN venv/bin/pip install --upgrade pip

# # Cài đặt các thư viện từ requirements.txt
# RUN venv/bin/pip install -r requirements.txt

# # Lệnh chạy main.py trong môi trường ảo
# CMD ["venv/bin/python", "src/main.py"]
