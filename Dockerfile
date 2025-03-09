FROM python:3.9-slim

WORKDIR /app

# Cài đặt virtualenv và tạo môi trường ảo
RUN python -m pip install --upgrade pip && \
    pip install virtualenv && \
    python -m virtualenv venv

# Kích hoạt môi trường ảo và cài đặt thư viện
COPY requirements.txt .
RUN . /app/venv/bin/activate && pip install -r requirements.txt

# Copy thư mục src vào trong container
COPY ./src /app/src

# Kích hoạt môi trường ảo và chạy main.py trong src
CMD ["/bin/bash", "-c", "source /app/bin/activate && python src/main.py"]
