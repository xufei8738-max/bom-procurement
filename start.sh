#!/usr/bin/env bash
# 采购系统日常启动（macOS / Linux，安装后使用）
# 数据目录与代码目录见 scripts/platform_utils.py（默认 ~/procurement-data 与 ~/procurement-system）
set -e

CODE_DIR="${PROC_CODE_DIR:-$HOME/procurement-system}"
DATA_DIR="${PROC_DATA_DIR:-$HOME/procurement-data}"
PORT="${PROC_PORT:-8000}"

if [ ! -x "$CODE_DIR/.venv/bin/python" ]; then
  echo "[错误] 未检测到安装（$CODE_DIR/.venv 不存在）。请先运行："
  echo "  python3 scripts/install.py"
  exit 1
fi

echo "采购系统启动中：http://127.0.0.1:$PORT  （Ctrl+C 停止）"
( sleep 1.5
  if command -v open >/dev/null 2>&1; then open "http://127.0.0.1:$PORT"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:$PORT"
  fi ) >/dev/null 2>&1 || true

cd "$CODE_DIR"
PROC_DATA_DIR="$DATA_DIR" PROC_PORT="$PORT" exec "$CODE_DIR/.venv/bin/python" -m uvicorn \
  server.app:app --host 127.0.0.1 --port "$PORT"
