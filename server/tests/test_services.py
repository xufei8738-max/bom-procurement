"""服务层核心流程 + 三条红线验证。"""
from server import services


def _reset():
    from server import db
    with db.db() as con:
        for t in ("prices", "aliases", "pending", "unit_conversions", "items"):
            con.execute(f"DELETE FROM {t}")
            con.execute("DELETE FROM sqlite_sequence WHERE name=?", (t,))


def test_upsert_item_dedup():
    _reset()
    r1 = services.upsert_item("DS-2CD3T46WDV3-I3", "海康", "安防/摄像机", "400万 4mm POE", "台")
    r2 = services.upsert_item("ds_2cd3t46 wdv3-i3")  # 归一后同键
    assert r1["item_id"] == r2["item_id"]
    assert r2["duplicate"] is True


def test_price_priority_purchase_over_quote():
    _reset()
    iid = services.upsert_item("HK-X1")["item_id"]
    services.upsert_price(item_id=iid, price=100.0, price_type="quote", price_date="2026-01-01")
    services.upsert_price(item_id=iid, price=80.0, price_type="purchase", price_date="2026-01-02")
    services.upsert_price(item_id=iid, price=90.0, price_type="purchase", price_date="2025-12-01")
    from server import pricing
    p = pricing.pick_price(iid)
    assert p["price"] == 80.0            # purchase 优先 + 日期最新
    assert p["price_type"] == "purchase"


def test_quote_latest_when_only_quote():
    _reset()
    iid = services.upsert_item("HK-X2")["item_id"]
    services.upsert_price(item_id=iid, price=50.0, price_type="quote", price_date="2026-03-01")
    services.upsert_price(item_id=iid, price=45.0, price_type="quote", price_date="2026-05-01")
    from server import pricing
    p = pricing.pick_price(iid)
    assert p["price"] == 45.0
    assert p["is_reference_only"] is True  # 仅报价参考要标注


def test_redline_no_price_returns_none():
    _reset()
    iid = services.upsert_item("NO-PRICE")["item_id"]
    from server import pricing
    assert pricing.pick_price(iid) is None  # 无记录 → None，绝不猜测


def test_redline_zero_price_rejected():
    _reset()
    r = services.upsert_price(model="ZP-1", price=0)
    assert "error" in r  # 0/负价默认拒绝（疑似赠品/测试价）


def test_price_append_not_overwrite():
    _reset()
    iid = services.upsert_item("MP-1")["item_id"]
    services.upsert_price(item_id=iid, price=10, price_type="purchase", price_date="2026-01-01")
    services.upsert_price(item_id=iid, price=12, price_type="purchase", price_date="2026-02-01")
    assert len(services.get_prices(iid)) == 2  # 两条并存


def test_match_three_levels():
    _reset()
    iid = services.upsert_item("DS-2CD3T46WDV3-I3", "海康", "安防/摄像机")["item_id"]

    r = services.search_items("DS-2CD3T46WDV3-I3")
    assert r["status"] == "exact"

    services.add_alias("海康400万筒机", iid)
    r = services.search_items("海康400万筒机")
    assert r["status"] == "alias"   # 别名回流后直接命中

    r = services.search_items("2CD3T46")
    assert r["status"] == "fuzzy"   # 部分型号 → 模糊候选
    assert r["results"][0]["item_id"] == iid

    r = services.search_items("完全不存在的型号XYZ")
    assert r["status"] == "none"


def test_search_exact_attaches_price_fuzzy_does_not():
    _reset()
    iid = services.upsert_item("CAM-A1")["item_id"]
    services.upsert_price(item_id=iid, price=66.0, price_type="purchase", price_date="2026-01-01")

    r = services.search_items("CAM-A1")
    assert r["results"][0]["latest_price"]["price"] == 66.0

    services.upsert_item("CAM-A12")  # 制造模糊场景
    r = services.search_items("CAM-A1X")  # 无精确命中 → 模糊
    for c in r["results"]:
        assert "latest_price" not in c  # 红线：模糊候选不带价，人工挑选


def test_bulk_import_with_messy_prices():
    _reset()
    rows = [
        {"model": "B1", "price": "¥1,234", "price_type": "purchase", "price_date": "2026-01-05",
         "vendor": "供应商甲", "source": "test.xlsx#Sheet1#2"},
        {"model": "B2", "price": "12~15", "price_type": "purchase", "price_date": "2026-01-06"},
        {"model": "B3", "price": "面议"},          # 仅建物料
        {"model": "", "price": 5},                 # 失败行
    ]
    out = services.bulk_import(rows)
    s = out["summary"]
    assert s["total"] == 4 and s["fail"] == 1
    b2 = [r for r in out["results"] if r.get("model") == "B2"][0]
    assert b2["price_id"] and "区间价" in b2["price_note"]
    b3 = [r for r in out["results"] if r.get("model") == "B3"][0]
    assert b3["status"] == "item_only"


def test_pending_confirm_writes_alias():
    _reset()
    iid = services.upsert_item("PEN-1", "海康")["item_id"]
    services.add_pending(raw_text="客户叫法 HK-400W", model="HK-400W", price=88.0,
                         price_date="2026-02-02", price_type="purchase", source="bom.xlsx#3")
    p = services.list_pending()[0]
    r = services.confirm_pending(p["pending_id"], item_id=iid)
    assert r["item_id"] == iid and r["price_id"]

    # 原始叫法已回流成别名
    r2 = services.search_items("HK-400W")
    assert r2["status"] == "alias"
    # 待确认区清空
    assert services.list_pending() == []


def test_unit_conversion_category_fuzzy(tmp_path):
    """换算表品类「线材」应命中无品类物料与前缀品类物料（双向宽容）。"""
    _reset()
    from server import pricing
    services.upsert_conversion("箱", "米", 305, "线材")
    # 物料无品类（资料没提供品类列的常见情况）
    assert pricing.convert_unit(305.0, "箱", "米", "")["price"] == 1.0
    # 物料品类为前缀展开
    assert pricing.convert_unit(305.0, "箱", "米", "线材/网线")["price"] == 1.0
    # 无关品类不硬算（红线）
    r = pricing.convert_unit(10.0, "箱", "米", "安防/摄像机")
    assert r["price"] is None


def test_unit_conversion():
    _reset()
    services.upsert_conversion("箱", "米", 305, "网线")
    from server import pricing
    r = pricing.convert_unit(305.0, "箱", "米", "网线")
    assert r["converted"] and abs(r["price"] - 1.0) < 1e-6

    # 计数单位互认：台↔个 等单件单位 1:1 直通
    r = pricing.convert_unit(10.0, "台", "个")
    assert r["price"] == 10.0 and "互认" in r["note"]

    # 组合/包装单位缺换算记录 → 留空待人工，不硬算（红线）
    r = pricing.convert_unit(10.0, "套", "个")
    assert r["price"] is None and "需人工确认" in r["note"]
    r = pricing.convert_unit(10.0, "卷", "米")
    assert r["price"] is None and "需人工确认" in r["note"]


def test_stats_counts():
    _reset()
    services.upsert_item("ST-1")
    iid = services.upsert_item("ST-2")["item_id"]
    services.upsert_price(item_id=iid, price=9.9, price_type="purchase")
    s = services.stats()
    assert s["items"] == 2 and s["prices"] == 1
