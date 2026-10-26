#!/usr/bin/env bash
# Start the whole live setup with one command:  ./start.sh
#
#   1. Chart (web UI)                 -> background, log: res/logs/chart.log
#   2. Live candle export for chart   -> background, log: res/logs/export_live.log
#   3. Trading bot + trading API      -> foreground (dashboard)
#
# Ctrl+C stops the bot; the two background processes are stopped with it.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT/res/logs"
CHART_PORT=5173

cd "$ROOT"
mkdir -p "$LOG_DIR"

# ---- Python virtualenv -------------------------------------------------------
if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
else
  echo "Không tìm thấy virtualenv (.venv hoặc venv). Chạy: python -m venv .venv && pip install -r requirements.txt"
  exit 1
fi
export PYTHONUNBUFFERED=1

# ---- Cleanup on exit ---------------------------------------------------------
PIDS=()
cleanup() {
  echo ""
  echo "Đang tắt chart và export..."
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo "Đã tắt hết."
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# ---- 1. Chart (Vite dev server, proxies /api to the bot) ---------------------
if [ ! -d "chart/node_modules" ]; then
  echo "Cài thư viện cho chart (lần đầu)..."
  (cd chart && npm install --no-audit --no-fund)
fi
(cd chart && exec ./node_modules/.bin/vite --port "$CHART_PORT" --strictPort) > "$LOG_DIR/chart.log" 2>&1 &
PIDS+=($!)

# ---- 2. Live candle export for the chart (every 30 s) ------------------------
python -m src.evaluate.export_live_data > "$LOG_DIR/export_live.log" 2>&1 &
PIDS+=($!)

# Give the chart a few seconds to start, then report
sleep 3
if kill -0 "${PIDS[0]}" 2>/dev/null; then
  echo "Chart:  http://localhost:$CHART_PORT/#/?source=live   (bấm biểu tượng Trade ở cột phải)"
else
  echo "Chart KHÔNG chạy được (cổng $CHART_PORT đang bận?). Xem log: $LOG_DIR/chart.log"
fi
echo "Logs:   $LOG_DIR/chart.log, $LOG_DIR/export_live.log"
echo "Bot đang khởi động..."
sleep 2

# ---- 3. Trading bot (foreground) ---------------------------------------------
python -m src.live.run
