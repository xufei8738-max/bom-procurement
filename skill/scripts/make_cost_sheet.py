#!/usr/bin/env python3
"""成本清单生成（Skill 2 命令行入口，薄壳）。

实际逻辑在 server/bom.py + server/cost_sheet.py，与 MCP 工具 compare_bom 同源。

用法：
  python scripts/make_cost_sheet.py <BOM文件> [输出xlsx路径]
  支持 xlsx/xls/csv/docx/pdf/图片（与资料录入共用解析器）

需要能 import server 包：请在仓库根目录运行，或设置 PYTHONPATH。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent   # 仓库根（skill/scripts 的上两级）
sys.path.insert(0, str(ROOT))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if sys.argv[1] in ("--demo", "-h", "--help"):
        print(__doc__)
        return 0

    from server import bom, db

    db.init_db()
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None

    result = bom.export_cost_sheet(src, out)
    if "error" in result:
        print(f"[错误] {result['error']}")
        return 1

    print(f"✅ 成本清单已生成：{result['out']}")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
