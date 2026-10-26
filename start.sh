#!/usr/bin/env bash
# Start the whole live setup with one command:  ./start.sh
#
#   1. Chart (web UI)                    -> background, log: res/logs/chart.log
#   2. Live chart export (candles, SMC   -> background, log: res/logs/export_live.log
#      indicators, bot trades + status)     re-exports within ~2 s of each bot update
#   3. Trading bot + trading API         -> foreground (dashboard)
#
# The browser opens on the Live chart. New signals pop up there (and as a desktop
# notification); approve or reject them on the chart. Press "Connect bot" once and
# enter WEB_API_TOKEN from .env (kept in that browser).
#
# Ctrl+C stops the bot; the two background processes are stopped with it.
# NO_BROWSER=1 ./start.sh  starts everything without opening the browser.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT/res/logs"
CHART_PORT=5173
CHART_URL="http://localhost:$CHART_PORT/#/?source=live"

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

# The chart reaches the bot only through its web API (live.yaml `web`)
WEB_INFO=$(python -c "from src.config import load_live_config; from src.live.web.orders import load_web_config; c = load_web_config(load_live_config(include_strategy=False)); print('on' if c.get('enabled') else 'off', c.get('host'), c.get('port'))" 2>/dev/null || echo "unknown")
case "$WEB_INFO" in
  on*) echo "Bot web API: http://$(echo "$WEB_INFO" | cut -d' ' -f2):$(echo "$WEB_INFO" | cut -d' ' -f3) (chart proxies /api to it)" ;;
  off*) echo "Cảnh báo: web.enabled đang là false trong src/config/live.yaml -> chart KHÔNG kết nối được bot (Bot is not reachable)." ;;
  *) echo "Cảnh báo: không đọc được src/config/live.yaml (mục web)." ;;
esac

if ! grep -qE '^WEB_API_TOKEN=.{16,}' .env 2>/dev/null; then
  echo "Cảnh báo: .env chưa có WEB_API_TOKEN (16+ ký tự) -> web không duyệt lệnh được."
  echo "  Tạo token: python -c \"import secrets; print(secrets.token_urlsafe(24))\""
fi

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
CHART_PID=$!
PIDS+=("$CHART_PID")

# ---- 2. Live chart export (loop: only files whose inputs changed) ------------
python -m src.evaluate.export_live_data > "$LOG_DIR/export_live.log" 2>&1 &
PIDS+=($!)

# Wait until the chart answers (up to 20 s)
chart_ok=0
for _ in $(seq 1 40); do
  if ! kill -0 "$CHART_PID" 2>/dev/null; then
    break
  fi
  if curl -fs -o /dev/null "http://localhost:$CHART_PORT/"; then
    chart_ok=1
    break
  fi
  sleep 0.5
done

if [ "$chart_ok" = 1 ]; then
  echo "Chart:  $CHART_URL"
  if [ "${NO_BROWSER:-0}" != 1 ]; then
    if command -v open >/dev/null 2>&1; then
      open "$CHART_URL" >/dev/null 2>&1 || true
    elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$CHART_URL" >/dev/null 2>&1 || true
    fi
  fi
else
  echo "Chart KHÔNG chạy được (cổng $CHART_PORT đang bận?). Xem log: $LOG_DIR/chart.log"
fi
echo "Logs:   $LOG_DIR/chart.log, $LOG_DIR/export_live.log"
echo "Bot đang khởi động..."
sleep 1

# ---- 3. Trading bot (foreground) ---------------------------------------------
set +e
python -m src.live.run
code=$?
set -e
echo ""
if [ "$code" = 0 ] || [ "$code" = 130 ]; then
  echo "Bot đã dừng."
else
  echo "Bot dừng do lỗi (exit code $code). Xem thông báo lỗi ở trên."
fi
exit "$code"
