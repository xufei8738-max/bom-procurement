"""BOM 比对（Skill 2）测试：六种匹配场景 + 取价规则 + 单位换算 + 覆盖率 + Excel 输出。"""
from pathlib import Path

import openpyxl
import pytest

from server import bom, cost_sheet, db, services


def _clean():
    with db.db() as con:
        for t in ("prices", "aliases", "pending", "unit_conversions", "items"):
            con.execute(f"DELETE FROM {t}")
            con.execute("DELETE FROM sqlite_sequence WHERE name=?", (t,))


def _xlsx(tmp_path, rows, name="客户BOM.xlsx"):
    p = tmp_path / name
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(p)
    return p


@pytest.fixture()
def seeded():
    """准备：精确定价、别名、无价、单位换算场景。"""
    _clean()
    i1 = services.upsert_item("DS-2CD3T46WDV3-I3", "海康", "安防/摄像机", "400万筒机", "台")["item_id"]
    services.upsert_price(item_id=i1, price=1180.0, price_type="purchase",
                          price_date="2025-06-01", vendor="代理商甲", source="采购.xlsx#2")

    i2 = services.upsert_item("CAT5E-305", "安普", "线材/网线", "超五类 305米/箱", "箱")["item_id"]
    services.upsert_price(item_id=i2, price=395.0, price_type="purchase",
                          price_date="2025-05-01", unit="箱", source="采购.xlsx#5")
    services.upsert_conversion("箱", "米", 305, "线材")

    i3 = services.upsert_item("IPC-HDBW2231R", "大华", "安防/摄像机", "200万半球", "台")["item_id"]
    services.add_alias("大华200万半球机", i3)   # 别名命中，但该物料无价
    return {"i1": i1, "i2": i2, "i3": i3}


# ---------------- 读取 BOM ----------------

def test_load_bom_keeps_row_numbers(tmp_path):
    p = _xlsx(tmp_path, [
        ["序号", "名称", "型号", "品牌", "数量", "单位"],
        [1, "400万筒机", "DS-2CD3T46WDV3-I3", "海康", 10, "台"],
        [2, "网线", "CAT5E-305", "安普", 500, "米"],
    ])
    loaded = bom.load_bom(p)
    assert "error" not in loaded
    assert len(loaded["rows"]) == 2
    assert loaded["rows"][0]["row_no"] == 2       # 保留 Excel 原始行号
    assert loaded["rows"][0]["qty"] == 10
    assert loaded["rows"][1]["unit"] == "米"


def test_load_bom_missing_file():
    assert "error" in bom.load_bom("/nope/xx.xlsx")


# ---------------- 六种匹配场景 ----------------

def test_compare_exact_hit_with_price(tmp_path, seeded):
    p = _xlsx(tmp_path, [
        ["型号", "数量", "单位"],
        ["DS-2CD3T46WDV3-I3", 10, "台"],
    ])
    r = bom.compare_bom(p)
    line = r["results"][0]
    assert line["status"] == "exact"
    assert line["price_info"]["price"] == 1180.0
    assert line["subtotal"] == 11800.0
    assert r["summary"]["total_cost"] == 11800.0
    assert r["summary"]["coverage_pct"] == 100.0


def test_compare_alias_hit_but_no_price(tmp_path, seeded):
    """别名命中但库内无价 → 留空 + 无历史价异常（红线：不估算）。"""
    p = _xlsx(tmp_path, [["型号", "数量"], ["大华200万半球机", 5]])
    r = bom.compare_bom(p)
    line = r["results"][0]
    assert line["status"] == "alias"
    assert line["price_info"] is None
    assert line["subtotal"] is None
    kinds = [k for k, _ in line["anomalies"]]
    assert "无历史价" in kinds
    assert r["summary"]["coverage_pct"] == 0.0
    assert "不能作为决策依据" in r["summary"]["coverage_warning"]


def test_compare_fuzzy_never_prices(tmp_path, seeded):
    """模糊候选不得带价（红线），只给候选。"""
    p = _xlsx(tmp_path, [["型号", "数量"], ["2CD3T46WDV3", 1]])
    r = bom.compare_bom(p)
    line = r["results"][0]
    assert line["status"] == "fuzzy"
    assert line["price_info"] is None
    assert line["subtotal"] is None
    assert line["candidates"], "应给出候选供人工挑选"
    assert all("price" not in c for c in line["candidates"])


def test_compare_none(tmp_path, seeded):
    p = _xlsx(tmp_path, [["型号", "数量"], ["完全不存在的型号XYZ-9", 1]])
    r = bom.compare_bom(p)
    line = r["results"][0]
    assert line["status"] == "none"
    assert ("未匹配", "库内无此型号，需人工指定或新建物料") in line["anomalies"]
    assert line["subtotal"] is None


def test_compare_unit_conversion_applied(tmp_path, seeded):
    """BOM 用「米」，库内按「箱」计价 → 自动换算（1箱=305米）。"""
    p = _xlsx(tmp_path, [["型号", "数量", "单位"], ["CAT5E-305", 305, "米"]])
    r = bom.compare_bom(p)
    line = r["results"][0]
    assert line["status"] == "exact"
    assert "已换算" in line["convert_note"]
    assert abs(line["price_info"]["effective_price"] - 395 / 305) < 1e-3   # 换算保留 4 位小数
    assert abs(line["subtotal"] - 395.0) < 0.02     # 305 米正好一箱（含四舍五入误差）


def test_compare_unit_unconvertible(tmp_path, seeded):
    """无换算记录的单位 → 挂「单位待确认」，不硬算。"""
    p = _xlsx(tmp_path, [["型号", "数量", "单位"], ["CAT5E-305", 2, "卷"]])
    r = bom.compare_bom(p)
    line = r["results"][0]
    kinds = [k for k, _ in line["anomalies"]]
    assert "单位待确认" in kinds
    assert line["price_info"]["effective_price"] is None
    assert line["subtotal"] is None


def test_compare_stale_price_flagged(tmp_path, seeded):
    p = _xlsx(tmp_path, [["型号", "数量"], ["DS-2CD3T46WDV3-I3", 1]])
    r = bom.compare_bom(p)
    kinds = [k for k, _ in r["results"][0]["anomalies"]]
    assert "价格偏旧" in kinds       # 2025-06-01 > 365 天


def test_compare_quote_only_marked_reference(tmp_path, seeded):
    _clean()
    iid = services.upsert_item("Q-ONLY")["item_id"]
    services.upsert_price(item_id=iid, price=88.0, price_type="quote", price_date="2026-10-01")
    p = _xlsx(tmp_path, [["型号", "数量"], ["Q-ONLY", 2]])
    r = bom.compare_bom(p)
    line = r["results"][0]
    assert line["price_info"]["is_reference_only"] is True
    assert any("仅报价参考" in n for n in line["notes"])
    assert line["subtotal"] == 176.0


def test_compare_mixed_coverage(tmp_path, seeded):
    """混合场景：覆盖率与分类小计。"""
    p = _xlsx(tmp_path, [
        ["型号", "数量", "单位"],
        ["DS-2CD3T46WDV3-I3", 10, "台"],
        ["CAT5E-305", 305, "米"],
        ["不存在XYZ", 1, "个"],
    ])
    r = bom.compare_bom(p)
    s = r["summary"]
    assert s["lines"] == 3 and s["priced_lines"] == 2
    assert abs(s["coverage_pct"] - 66.7) < 0.1
    assert abs(s["total_cost"] - (11800.0 + 395.0)) < 0.05   # 含单位换算的 4 位小数舍入
    assert s["by_category"].get("安防/摄像机") == 11800.0


def test_compare_empty_bom(tmp_path):
    _clean()
    p = _xlsx(tmp_path, [["随便", "写点"]])
    r = bom.compare_bom(p)
    # 无表头 → parser 直接报未识别（error 或 0 行）
    assert "error" in r or r["summary"]["lines"] == 0


# ---------------- Excel 输出 ----------------

def test_export_cost_sheet_three_sections(tmp_path, seeded):
    p = _xlsx(tmp_path, [
        ["型号", "数量", "单位"],
        ["DS-2CD3T46WDV3-I3", 10, "台"],
        ["2CD3T46WDV3", 1, "台"],
        ["不存在XYZ", 2, "个"],
    ])
    out = tmp_path / "成本清单.xlsx"
    result = bom.export_cost_sheet(p, str(out))
    assert "error" not in result
    assert out.exists()

    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["成本清单", "未匹配清单", "口径异常"]

    ws = wb["成本清单"]
    assert ws.cell(1, 1).value == "BOM行号"
    # 三行数据 + 空行 + 分类小计 + 合计 + 覆盖率
    assert ws.max_row >= 7
    texts = [ws.cell(r, 6).value for r in range(2, 5)]
    assert texts == ["精确命中", "模糊候选", "未匹配"]

    ws2 = wb["未匹配清单"]
    assert ws2.cell(1, 1).value == "BOM行号"
    assert ws2.max_row >= 3        # 模糊 + 未匹配两行

    ws3 = wb["口径异常"]
    kinds = [ws3.cell(r, 2).value for r in range(2, ws3.max_row + 1)]
    assert any("未匹配" in str(k) for k in kinds)


def test_cost_sheet_default_path(tmp_path, seeded, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = _xlsx(tmp_path, [["型号", "数量"], ["DS-2CD3T46WDV3-I3", 1]])
    result = bom.export_cost_sheet(p)
    assert "error" not in result
    assert Path(result["out"]).exists()
