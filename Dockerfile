# Use official Python image with Python 3.11
FROM python:3.11-slim

# Install system dependencies (git required for tvdatafeed from GitHub)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY pyproject.toml .
COPY src/ /app/src/

# Install project as editable package to create src module structure
RUN pip install -e .

# Create necessary directories for trading app
RUN mkdir -p /app/res/logs/data /app/res/logs/output

# Set environment variable for unbuffered Python output
ENV PYTHONUNBUFFERED=1

# Command to run the trading application
CMD ["python", "-u", "-m", "src.live.run"]
