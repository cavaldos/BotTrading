# ---- Stage 1: build the chart frontend (used for trade snapshots) ----
FROM node:20-slim AS chart-build

WORKDIR /chart
COPY chart/package.json ./
RUN npm install --no-audit --no-fund
COPY chart/ ./
RUN npm run build


# ---- Stage 2: trading bot ----
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

# Headless Chromium (+ system libs and fonts) for chart snapshots
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY src/ /app/src/

# Built frontend served locally to Chromium by src/live/core/snapshot.py
COPY --from=chart-build /chart/dist /app/chart/dist

# Install project as editable package to create src module structure
RUN pip install -e .

# Create necessary directories for trading app
RUN mkdir -p /app/res/logs/data /app/res/logs/output

# Set environment variable for unbuffered Python output
ENV PYTHONUNBUFFERED=1

# Command to run the trading application
CMD ["python", "-u", "-m", "src.live.run"]
