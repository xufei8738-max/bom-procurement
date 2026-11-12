#!/usr/bin/env bash
# macOS 双击启动（Finder 中双击即可，等价于 start.sh）
cd "$(dirname "$0")" || exit 1
exec ./start.sh
