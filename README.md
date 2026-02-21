# Bot Trade

Trading bot with real-time dashboard.

![Dashboard](docs/screenshots/image.png)
![Exchange](docs/screenshots/image2.png)

## Quick Start

### Local Development

```bash
# 1. Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 2. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .

# 3. Configure
cp src/config/live.ex.yaml src/config/live.yaml
cp src/config/backtest.ex.yaml src/config/backtest.yaml
cp src/config/strategy.ex.yaml src/config/strategy.yaml
# Edit config files as needed

# 4. Setup environment variables
# Create .env file with:
TELEGRAM_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# 5. Run
python -m src.live.run

# Run backtest
python -m src.backtest.main
```

### Docker

```bash
# 1. Prepare config files
cp src/config/live.ex.yaml src/config/live.yaml
cp src/config/backtest.ex.yaml src/config/backtest.yaml
cp src/config/strategy.ex.yaml src/config/strategy.yaml
# Create .env file with TELEGRAM_TOKEN and TELEGRAM_CHAT_ID

# 2. Build and run
docker-compose up --build

# Run in background
docker-compose up -d --build

# View logs
docker-compose logs -f bot-trade

# Stop
docker-compose down
```

## Configuration

Edit `src/config/live.yaml`, `src/config/backtest.yaml`, and `src/config/strategy.yaml`:

```yaml
risk_reward_ratio: 2.0
```

## File Structure

```
Bot_Trade/
├── .env                      # Telegram credentials
├── src/config/live.yaml      # Live runtime configuration
├── src/config/backtest.yaml  # Backtest runtime configuration
├── src/config/strategy.yaml  # Shared strategy configuration
└── res/logs/                 # Data and output logs
```
