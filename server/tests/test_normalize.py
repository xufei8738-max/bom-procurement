from server.normalize import clean_price, clean_qty, extract_unit, model_key


def test_model_key_basic():
    assert model_key("DS-2CD3T46WDV3-I3") == "DS2CD3T46WDV3I3"
    assert model_key("ds_2cd3t46 wdv3-i3") == "DS2CD3T46WDV3I3"


def test_model_key_fullwidth_and_noise():
    # 全角字母、噪声词、空格连字符都应清掉
    assert model_key("ＤＳ-２ＣＤ 全新 原装") == "DS2CD"
    assert model_key("  超5类网线-305米/箱 ") == "超5类网线305米/箱"


def test_model_key_empty():
    assert model_key("") == ""
    assert model_key(None) == ""
    assert model_key("—— 原装 ——") == ""


def test_clean_price_variants():
    assert clean_price("1,234.56")["price"] == 1234.56
    assert clean_price("¥99")["price"] == 99
    assert clean_price("99元")["price"] == 99
    assert clean_price("12~15")["status"] == "range"
    assert clean_price("12~15")["price"] == 15   # 保守取上限
    assert clean_price("15-12")["price_low"] == 12
    assert clean_price("面议")["status"] == "none"
    assert clean_price("/")["status"] == "none"
    assert clean_price("")["status"] == "none"
    assert clean_price(None)["status"] == "none"


def test_clean_qty():
    assert clean_qty("12")["qty"] == 12
    assert clean_qty(3.5)["qty"] == 3.5
    assert clean_qty("约30个")["qty"] == 30
    assert clean_qty("待定")["status"] == "none"


def test_extract_unit():
    assert extract_unit("超五类网线 305米/箱") == "箱"
    assert extract_unit("摄像机 10 台") == "台"
    assert extract_unit("PVC管 20根") == "根"
    assert extract_unit("") is None
