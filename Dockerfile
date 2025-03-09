# Use official Python image with Python 3.9
FROM python:3.9-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy only the necessary application files
COPY src/ /app/src/
COPY run.sh /app/

# Create necessary directories
RUN mkdir -p /app/src/data /app/src/results /app/src/sum

# Make the run script executable
RUN chmod +x /app/run.sh

# Command to run the application
CMD ["python", "src/main.py"]
