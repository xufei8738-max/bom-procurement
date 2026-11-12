"""成本清单 Excel 生成器（Skill 2 输出物，规范 §7）。

三区结构：
- 主表：一行一物料，三色匹配状态，底部分类小计 + 合计 + 覆盖率
- 未匹配清单：无价/未匹配/模糊候选，含候选与建议
- 口径异常：单位待确认 / 无历史价 / 价格偏旧 / 仅参考价

纯排版模块：数据全部来自 bom.compare_bom 的结果，本模块不查库、不补数据。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEAD_FILL = PatternFill("solid", fgColor="1F2937")
HEAD_FONT = Font(name="Microsoft YaHei", color="FFFFFF", bold=True, size=11)
BODY_FONT = Font(name="Microsoft YaHei", size=10)
THIN = Side(style="thin", color="D8DEE4")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
GREEN = PatternFill("solid", fgColor="DAFBE1")
YELLOW = PatternFill("solid", fgColor="FFF8C5")
RED = PatternFill("solid", fgColor="FFEBE9")

MAIN_HEADERS = [
    "BOM行号", "客户名称", "客户型号", "数量", "单位", "匹配状态",
    "内部型号", "品牌", "品类", "规格",
    "价格类型", "单价", "计价单位", "换算", "小计",
    "价格日期", "供应商", "税口径", "来源", "备注",
]
UNMATCHED_HEADERS = ["BOM行号", "客户描述", "客户型号", "候选1", "候选2", "候选3", "建议"]
ANOMALY_HEADERS = ["BOM行号", "类型", "说明"]

STATUS_CN = {"exact": "精确命中", "alias": "别名命中", "fuzzy": "模糊候选",
             "none": "未匹配"}
STATUS_FILL = {"exact": GREEN, "alias": GREEN, "fuzzy": YELLOW, "none": RED}
TYPE_CN = {"purchase": "采购成本", "quote": "报价参考", "contract": "合同价"}
TAX_CN = {"included": "含税", "excluded": "未税", "unknown": "未知"}


def _style_header(ws, ncols: int):
    for c in range(1, ncols + 1):
        cell = ws.cell(1, c)
        cell.fill, cell.font, cell.border = HEAD_FILL, HEAD_FONT, BORDER


def build(compared: dict, out_path: str | None = None) -> str:
    """compare_bom 结果 → 成本清单 xlsx。返回文件路径。"""
    results = compared.get("results", [])
    summary = compared.get("summary", {})
    wb = openpyxl.Workbook()

    # ---------- 主表 ----------
    ws = wb.active
    ws.title = "成本清单"
    ws.append(MAIN_HEADERS)
    _style_header(ws, len(MAIN_HEADERS))
    widths = {1: 8, 2: 20, 3: 22, 4: 7, 5: 6, 6: 10, 7: 22, 8: 10, 9: 13, 10: 22,
              11: 10, 12: 10, 13: 10, 14: 12, 15: 12, 16: 12, 17: 12, 18: 9, 19: 20, 20: 18}
    for c, w in widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    for r in results:
        item = r.get("item") or {}
        info = r.get("price_info") or {}
        notes = "; ".join(r.get("notes", []))
        anomalies_txt = "; ".join(f"{k}: {v}" for k, v in r.get("anomalies", []))
        remark = " | ".join(x for x in (notes, anomalies_txt) if x)
        ws.append([
            r.get("row_no"), r.get("customer_name"), r.get("customer_model"),
            r.get("qty"), r.get("unit"),
            STATUS_CN.get(r.get("status"), r.get("status")),
            item.get("model_raw"), item.get("brand"), item.get("category"), item.get("spec"),
            TYPE_CN.get(info.get("price_type"), info.get("price_type", "")),
            info.get("price"), info.get("price_unit"), r.get("convert_note", ""),
            r.get("subtotal"),
            info.get("price_date"), info.get("vendor"),
            TAX_CN.get(info.get("tax_mode"), info.get("tax_mode", "")),
            info.get("source"), remark,
        ])
        row_idx = ws.max_row
        fill = STATUS_FILL.get(r.get("status"))
        for c in range(1, len(MAIN_HEADERS) + 1):
            cell = ws.cell(row_idx, c)
            cell.font, cell.border = BODY_FONT, BORDER
            if fill:
                cell.fill = fill
            cell.alignment = Alignment(vertical="center", wrap_text=(c in (10, 20)))

    # 底部汇总：分类小计 + 合计 + 覆盖率
    ws.append([])
    for cat, amt in (summary.get("by_category") or {}).items():
        ws.append(["小计", cat, "", "", "", "", "", "", "", "", "", "", "", "", amt])
    ws.append(["合计", "", "", "", "", f"有价行 {summary.get('priced_lines', 0)}/"
               f"{summary.get('lines', 0)}", "", "", "", "", "", "", "", "",
               summary.get("total_cost", 0)])
    ws.append(["覆盖率", f"{summary.get('coverage_pct', 0)}%",
               summary.get("coverage_warning", "") or "（达标）",
               f"生成 {date.today().isoformat()}"])
    for r in range(ws.max_row - len(summary.get("by_category") or {}) - 2, ws.max_row + 1):
        for c in range(1, len(MAIN_HEADERS) + 1):
            ws.cell(r, c).font = Font(name="Microsoft YaHei", size=10, bold=True)

    # ---------- 未匹配清单 ----------
    ws2 = wb.create_sheet("未匹配清单")
    ws2.append(UNMATCHED_HEADERS)
    _style_header(ws2, len(UNMATCHED_HEADERS))
    unmatched = [r for r in results
                 if r.get("status") in ("none", "fuzzy")
                 or (r.get("status") in ("exact", "alias") and not r.get("price_info"))]
    for r in unmatched:
        cands = r.get("candidates") or []
        cells = [f"{c.get('model_raw')} ({c.get('brand', '') or '-'}) #{c.get('item_id')}"
                 for c in cands[:3]]
        while len(cells) < 3:
            cells.append("")
        suggestion = ("请人工挑选候选并登记别名" if r.get("status") == "fuzzy"
                      else ("需询价后补录" if not r.get("candidates") and r.get("price_info") is None
                            and r.get("status") in ("exact", "alias")
                            else "库内无此型号，需人工新建"))
        ws2.append([r.get("row_no"), r.get("customer_name"), r.get("customer_model"),
                    *cells, suggestion])
    if not unmatched:
        ws2.append(["—", "全部命中且有价", "", "", "", "", ""])
    for row in ws2.iter_rows(min_row=2):
        for cell in row:
            cell.font, cell.border = BODY_FONT, BORDER

    # ---------- 口径异常 ----------
    ws3 = wb.create_sheet("口径异常")
    ws3.append(ANOMALY_HEADERS)
    _style_header(ws3, len(ANOMALY_HEADERS))
    anomaly_rows = 0
    for r in results:
        for kind, desc in r.get("anomalies", []):
            ws3.append([r.get("row_no"), kind, desc])
            anomaly_rows += 1
    for line in compared.get("unparsed", []):
        ws3.append([line.get("source", ""), "BOM未解析行",
                    f"{line.get('raw_text', '')}（{line.get('reason', '')}）"])
        anomaly_rows += 1
    if not anomaly_rows:
        ws3.append(["—", "无口径异常", ""])
    for row in ws3.iter_rows(min_row=2):
        for cell in row:
            cell.font, cell.border = BODY_FONT, BORDER

    out = out_path or str(Path.home() / "Desktop" /
                          f"成本清单_{date.today().isoformat()}.xlsx")
    out = str(Path(out).expanduser())
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
