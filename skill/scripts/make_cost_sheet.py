#!/usr/bin/env python3
"""成本清单 Excel 生成器（Skill 2「BOM 比对」输出物，规范 §7）。

输入：bom_lines（BOM 行，含客户描述/数量/单位/归一型号）+ match_results（逐行匹配结果）
输出：xlsx 文件路径。三区结构：主表 / 未匹配清单 / 口径异常。

纯本地生成：数据全部来自 MCP 查询结果；本脚本只做排版，不做任何数据补充。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEAD_COLOR = PatternFill("solid", fgColor="1F2937")
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
UNMATCHED_HEADERS = ["BOM行号", "客户描述", "原始型号", "候选1", "候选2", "候选3", "建议"]
ANOMALY_HEADERS = ["BOM行号", "类型", "说明"]


def build_cost_sheet(bom_lines: list[dict], match_results: list[dict],
                     out_path: str | None = None) -> str:
    """生成成本清单 Excel。

    bom_lines: [{row_no, name, model, qty, unit}]（来自 BOM 解析）
    match_results: 与 bom_lines 一一对应，
      [{status: exact|alias|fuzzy|none|confirmed, item, price_info, converted, notes:[...]}]
    返回生成的文件路径。
    """
    wb = openpyxl.Workbook()

    # ---------- 主表 ----------
    ws = wb.active
    ws.title = "成本清单"
    ws.append(MAIN_HEADERS)
    for c in range(1, len(MAIN_HEADERS) + 1):
        cell = ws.cell(1, c)
        cell.fill, cell.font, cell.border = HEAD_COLOR, HEAD_FONT, BORDER
        ws.column_dimensions[get_column_letter(c)].width = {
            1: 8, 2: 22, 3: 24, 4: 7, 5: 6, 6: 10, 7: 24, 8: 10, 9: 14, 10: 24,
            11: 10, 12: 10, 13: 10, 14: 8, 15: 12, 16: 12, 17: 12, 18: 9, 19: 22, 20: 20,
        }.get(c, 12)

    type_cn = {"purchase": "采购成本", "quote": "报价参考", "contract": "合同价"}
    tax_cn = {"included": "含税", "excluded": "未税", "unknown": "未知"}
    status_cn = {"exact": "精确命中", "alias": "别名命中", "fuzzy": "模糊候选",
                 "none": "未匹配", "confirmed": "已确认"}

    total = 0.0
    priced_lines = 0
    for line, m in zip(bom_lines, match_results):
        item = m.get("item") or {}
        pi = m.get("price_info") or {}
        unit_price = pi.get("effective_price")
        qty = line.get("qty")
        subtotal = round(unit_price * qty, 2) if unit_price is not None and qty is not None else None
        if subtotal is not None:
            total += subtotal
            priced_lines += 1
        ws.append([
            line.get("row_no"), line.get("name"), line.get("model"), qty, line.get("unit"),
            status_cn.get(m.get("status"), m.get("status")),
            item.get("model_raw"), item.get("brand"), item.get("category"), item.get("spec"),
            type_cn.get(pi.get("price_type"), pi.get("price_type", "")),
            pi.get("price"), pi.get("price_unit"),
            m.get("convert_note", ""), subtotal,
            pi.get("price_date"), pi.get("vendor"),
            tax_cn.get(pi.get("tax_mode"), pi.get("tax_mode", "")),
            pi.get("source"), "; ".join(m.get("notes", [])),
        ])
        r = ws.max_row
        fill = {"exact": GREEN, "alias": GREEN, "confirmed": YELLOW,
                "fuzzy": YELLOW, "none": RED}.get(m.get("status"))
        for c in range(1, len(MAIN_HEADERS) + 1):
            cell = ws.cell(r, c)
            cell.font, cell.border = BODY_FONT, BORDER
            if fill:
                cell.fill = fill
            cell.alignment = Alignment(vertical="center", wrap_text=(c in (10, 20)))

    coverage = f"{priced_lines}/{len(bom_lines)}" if bom_lines else "0/0"
    pct = (priced_lines / len(bom_lines) * 100) if bom_lines else 0
    ws.append([])
    ws.append(["合计", "", "", "", "", f"有价行 {coverage}", "", "", "", "", "", "", "", "",
               round(total, 2)])
    ws.append(["覆盖率", f"{pct:.0f}%" + ("（低于80%，不能作为决策依据）" if pct < 80 else ""),
               f"生成时间 {date.today().isoformat()}"])

    # ---------- 侧表：未匹配 ----------
    ws2 = wb.create_sheet("未匹配清单")
    ws2.append(UNMATCHED_HEADERS)
    unmatched = [(l, m) for l, m in zip(bom_lines, match_results)
                 if m.get("status") in ("none", "fuzzy")]
    for line, m in unmatched:
        cands = m.get("candidates") or []
        cand_cells = [f"{c.get('model_raw')} ({c.get('brand','')})" for c in cands[:3]]
        while len(cand_cells) < 3:
            cand_cells.append("")
        ws2.append([line.get("row_no"), line.get("name"), line.get("model"),
                    *cand_cells, "请人工挑选或新建物料"])
    if not unmatched:
        ws2.append(["—", "全部命中，无未匹配项", "", "", "", "", ""])

    # ---------- 侧表：口径异常 ----------
    ws3 = wb.create_sheet("口径异常")
    ws3.append(ANOMALY_HEADERS)
    anomalies = 0
    for line, m in zip(bom_lines, match_results):
        for kind, desc in (m.get("anomalies") or []):
            ws3.append([line.get("row_no"), kind, desc])
            anomalies += 1
        if not m.get("anomalies"):
            pass
    if anomalies == 0:
        ws3.append(["—", "无口径异常", ""])

    out = out_path or str(Path.home() / "Desktop" / f"成本清单_{date.today().isoformat()}.xlsx")
    wb.save(out)
    return out


def _demo():
    """开发自测用：两条命中 + 一条未匹配。"""
    bom_lines = [
        {"row_no": 3, "name": "400万网络筒机", "model": "DS-2CD3T46WDV3-I3", "qty": 10, "unit": "台"},
        {"row_no": 4, "name": "网线", "model": "超五类无氧铜", "qty": 500, "unit": "米"},
        {"row_no": 5, "name": "枪机支架", "model": "BKT-??", "qty": 10, "unit": "个"},
    ]
    match_results = [
        {"status": "exact",
         "item": {"model_raw": "DS-2CD3T46WDV3-I3", "brand": "海康", "category": "安防/摄像机",
                  "spec": "400万 4mm POE"},
         "price_info": {"price": 1234.5, "price_type": "purchase", "price_date": "2025-01-15",
                        "vendor": "代理商甲", "tax_mode": "unknown", "price_unit": "台",
                        "source": "采购清单.xlsx#Sheet1#2", "effective_price": 1234.5},
         "notes": []},
        {"status": "alias",
         "item": {"model_raw": "CAT5E-305", "brand": "", "category": "线材/网线",
                  "spec": "超五类 无氧铜 305米/箱"},
         "price_info": {"price": 380, "price_type": "purchase", "price_date": "2024-12-01",
                        "vendor": "线缆商", "tax_mode": "included", "price_unit": "箱",
                        "source": "采购合同.pdf#3", "effective_price": 1.246},
         "convert_note": "已换算 1箱=305米", "notes": []},
        {"status": "none", "item": None, "price_info": {}, "notes": [],
         "candidates": [
             {"model_raw": "BKT-100", "brand": "海康"},
             {"model_raw": "BKT-200", "brand": "大华"},
         ]},
    ]
    match_results[1]["anomalies"] = [("价格偏旧", "2024-12-01，超过12个月建议复核")]
    print(build_cost_sheet(bom_lines, match_results, "/tmp/成本清单_demo.xlsx"))


if __name__ == "__main__":
    if "--demo" in sys.argv:
        _demo()
