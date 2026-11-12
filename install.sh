#!/usr/bin/env bash
# 采购系统安装入口（macOS / Linux）
# 用法：bash install.sh [--check|--no-autostart|--no-selftest|--uninstall]
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

# 找 Python 3.10+
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "[错误] 需要 Python 3.10+。macOS 可执行：brew install python@3.12"
  exit 1
fi

exec "$PY" "$DIR/scripts/install.py" "$@"
