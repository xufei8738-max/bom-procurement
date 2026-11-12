#!/usr/bin/env bash
# 备份数据目录（macOS / Linux）
# 用法：./backup.sh [目标目录]   （默认 数据目录/backups/）
set -e

DATA_DIR="${PROC_DATA_DIR:-$HOME/procurement-data}"
DEST="${1:-$DATA_DIR/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [ ! -f "$DATA_DIR/procurement.db" ]; then
  echo "[错误] 未找到数据库：$DATA_DIR/procurement.db"
  exit 1
fi

mkdir -p "$DEST"
cp "$DATA_DIR/procurement.db" "$DEST/procurement-$STAMP.db"
[ -f "$DATA_DIR/config.json" ] && cp "$DATA_DIR/config.json" "$DEST/config-$STAMP.json"

echo "已备份 → $DEST/procurement-$STAMP.db"
echo "最近 5 个备份："
ls -t "$DEST" | head -5
