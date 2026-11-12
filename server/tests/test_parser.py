"""解析器测试：表头同义、价格清洗、来源行号、价格类型判定、边界情况。"""
import openpyxl
import pytest

from server import parser, services


def _make_xlsx(tmp_path, name="采购清单20250115.xlsx", rows=None):
    """构造一个 xlsx 文件用于测试。"""
    rows = rows or [
        ["序号", "型号", "品牌", "规格描述", "数量", "单位", "单价", "供应商", "日期"],
        [1, "DS-2CD3T46WDV3-I3", "海康", "400万 POE 筒机", 10, "台", "￥1,234.50", "代理商甲", "2025-01-15"],
        [2, "ds_2cd3t46wdv3-i3", "海康", "同型号不同写法", 2, "台", "12~15", "代理商乙", "2025-01-16"],
        [3, "XYZ-??", "未知", "无法识别价格", 1, "个", "面议", "", ""],
        [4, "", "", "空型号行", 1, "个", "99", "", ""],
        [5, "AP-WIFI6", "TP", "AX3000 吸顶AP", 3, "台", "880", "代理商甲", "2025.01.20"],
    ]
    p = tmp_path / name
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for r in rows:
        ws.append(r)
    wb.save(p)
    return p


def test_guess_price_type():
    assert parser.guess_price_type("采购清单2025.xlsx")[0] == "purchase"
    assert parser.guess_price_type("进货单.xlsx")[0] == "purchase"
    assert parser.guess_price_type("报价单-海康.xlsx")[0] == "quote"
    assert parser.guess_price_type("采购框架合同.pdf")[0] == "purchase"  # 采购优先
    assert parser.guess_price_type("合同页.docx")[0] == "contract"
    assert parser.guess_price_type("随便.xlsx")[0] == "quote"


def test_header_synonym_mapping():
    assert parser._header_key("型号") == "model"
    assert parser._header_key("规格型号") == "model"
    assert parser._header_key("Model") == "model"
    assert parser._header_key("P/N") == "model"
    assert parser._header_key("含税单价") == "price"
    assert parser._header_key("单价（元）") == "price"
    assert parser._header_key("数量") == "qty"
    assert parser._header_key("供应商") == "vendor"
    assert parser._header_key("序号") is None
    assert parser._header_key("税率") is None


def test_parse_file_end_to_end(tmp_path):
    p = _make_xlsx(tmp_path)
    r = parser.parse_file(p)
    assert "error" not in r
    assert r["price_type"] == "purchase"          # 文件名含「采购」
    assert len(r["rows"]) == 4                     # 4 条可入库（空型号行跳过）
    assert len(r["pending"]) == 1                  # 空型号但有内容 → pending

    first = r["rows"][0]
    assert first["model"] == "DS-2CD3T46WDV3-I3"
    assert first["price"] == 1234.50               # 去 ￥ 与千分位
    assert first["qty"] == 10 and first["unit"] == "台"
    assert first["vendor"] == "代理商甲"
    assert first["price_date"] == "2025-01-15"
    assert first["source"].endswith("#Sheet1#2")   # 来源精确到行号

    # 区间价取上限并留提示
    second = r["rows"][1]
    assert second["price"] == 15.0
    assert "区间价" in second["price_note"]

    # 面议价 → price=None 且带说明
    third = r["rows"][2]
    assert third["price"] is None
    assert "无法解析" in third["price_note"]

    # 日期格式 2025.01.20 归一
    assert r["rows"][3]["price_date"] == "2025-01-20"


def test_parse_csv_with_gbk_fallback(tmp_path):
    p = tmp_path / "报价单.csv"
    p.write_bytes("型号,单价,单位\nCAM-1,¥300,台\n".encode("gb18030"))
    r = parser.parse_file(p)
    assert r["price_type"] == "quote"
    assert len(r["rows"]) == 1 and r["rows"][0]["price"] == 300.0


def test_parse_no_header(tmp_path):
    p = tmp_path / "无表头.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["随便", "写点", "东西"])
    ws.append(["a", "b", "c"])
    wb.save(p)
    r = parser.parse_file(p)
    assert r["rows"] == [] and len(r["pending"]) == 1
    assert "未识别到表头" in r["pending"][0]["raw_text"]


def test_parse_unsupported_format(tmp_path):
    p = tmp_path / "随便.txt"
    p.write_text("型号,单价\nA,1\n", encoding="utf-8")
    r = parser.parse_file(p)
    assert "error" in r and "不支持" in r["error"]
    # 支持列表里应出现 pdf 与图片格式
    assert "pdf" in r["error"] and "png" in r["error"]


def test_total_price_header_warning(tmp_path):
    """价格列表头是「总价」时要给出提示，避免把总价当成单价。"""
    p = _make_xlsx(tmp_path, "报价单.xlsx", rows=[
        ["型号", "总价"], ["A-1", "1000"],
    ])
    r = parser.parse_file(p)
    assert "总价" in r["rows"][0]["price_note"]


def test_parse_then_import_pipeline(tmp_path):
    """预览 → 入库 两步式闭环，并验证去重与待确认落库。"""
    from server import db
    with db.db() as con:
        for t in ("prices", "aliases", "pending", "unit_conversions", "items"):
            con.execute(f"DELETE FROM {t}")
            con.execute("DELETE FROM sqlite_sequence WHERE name=?", (t,))

    p = _make_xlsx(tmp_path)
    preview = services.parse_import_file(str(p))
    assert preview["summary"]["parsed"] == 4
    assert preview["summary"]["dup_in_library"] == 0   # 首次导入，库内无重复

    out = services.import_parsed(preview["rows"], preview["pending"])
    assert out["summary"]["fail"] == 0
    assert out["summary"]["pending_added"] == 1        # 失败行进待确认区

    # 库内检索命中 + 价格可取（取价规则：purchase 同类型取日期最新 → 15.0 是 2025-01-16 的行）
    r = services.search_items("DS-2CD3T46WDV3-I3")
    assert r["status"] == "exact"
    assert r["results"][0]["latest_price"]["price"] == 15.0
    assert r["results"][0]["latest_price"]["price_type"] == "purchase"

    # 价格流水两条并存（1234.50 与 15.0），且都带来源
    iid = r["results"][0]["item_id"]
    prices = services.get_prices(iid)
    assert len(prices) == 2
    assert {p["price"] for p in prices} == {1234.50, 15.0}
    assert all(p["source"].endswith(("#Sheet1#2", "#Sheet1#3")) for p in prices)

    # 同一写法（不同大小写/分隔符）归一到同一物料，不重复建
    assert len([x for x in r["results"] if x["model_raw"].lower().startswith("ds")]) == 1

    # 待确认区有 1 条
    assert len(services.list_pending()) == 1

    # 二次预览应标注「库内已有」
    preview2 = services.parse_import_file(str(p))
    assert preview2["summary"]["dup_in_library"] >= 2
