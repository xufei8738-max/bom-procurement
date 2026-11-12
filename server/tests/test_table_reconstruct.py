"""OCR 表格还原回归测试：防止列带聚类/表头拆分逻辑被改坏。

真实 OCR 冒烟测试见 test_ocr.py（按后端可用性跳过）。
这里全部用合成词坐标（无外部依赖，必跑）。
"""
from server.ocr import words_to_rows, text_to_rows
from server.parser import parse_table_rows, _header_key


def _w(text, x0, y0, x1, y1):
    return {"text": text, "conf": 1.0, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


# ---- 归一化坐标（Vision 风格，0~1） ----

def test_words_to_rows_basic_columns_normalized():
    words = [
        _w("序号", 0.04, 0.10, 0.10, 0.16), _w("型号", 0.21, 0.10, 0.27, 0.16),
        _w("单价", 0.85, 0.10, 0.91, 0.16),
        _w("1", 0.04, 0.30, 0.06, 0.34), _w("CAM-1", 0.21, 0.30, 0.29, 0.34),
        _w("800", 0.85, 0.30, 0.90, 0.34),
    ]
    rows = words_to_rows(words)
    assert rows[0] == ["序号", "型号", "单价"]
    assert rows[1] == ["1", "CAM-1", "800"]


def test_words_to_rows_sticky_header_separates():
    """OCR 把「序号 型号」粘连成一个词 → 列带聚类 + 跨列拆分要能拆开。"""
    words = [
        _w("序号 型号", 0.04, 0.10, 0.27, 0.16), _w("品牌", 0.40, 0.10, 0.46, 0.16),
        _w("1", 0.04, 0.30, 0.06, 0.34), _w("CAM-1", 0.21, 0.30, 0.29, 0.34),
        _w("海康", 0.40, 0.30, 0.45, 0.34),
    ]
    rows = words_to_rows(words)
    # 至少 3 列，且 CAM-1 与 1 不同列
    assert len(rows[1]) >= 3
    assert rows[1][0] == "1" or "1" in rows[1][0]
    assert any("CAM-1" in c for c in rows[1][1:])


def test_words_to_rows_prefix_number_stays_in_own_cell():
    """行首序号与型号分离（各自独立词）→ 不合并成一格。"""
    words = [
        _w("序号", 0.04, 0.10, 0.09, 0.16), _w("型号", 0.21, 0.10, 0.26, 0.16),
        _w("2", 0.04, 0.30, 0.06, 0.34), _w("IPC-100", 0.21, 0.30, 0.30, 0.34),
    ]
    rows = words_to_rows(words)
    assert rows[1][0] == "2"
    assert rows[1][1] == "IPC-100"


def test_words_to_rows_sticky_data_row_number_and_model():
    """数据行「2 IPC-HDBW2231R」被 OCR 认成一个词 → 拆分后序号、型号各归列。"""
    words = [
        _w("序号", 0.04, 0.10, 0.09, 0.16), _w("型号", 0.21, 0.10, 0.26, 0.16),
        _w("2 IPC-HDBW2231R", 0.04, 0.30, 0.46, 0.34),
    ]
    rows = words_to_rows(words)
    assert "2" in rows[1][0]
    assert any("IPC-HDBW2231R" in c for c in rows[1][1:])


def test_words_to_rows_pixel_coordinates():
    """tesseract/RapidOCR 风格的像素坐标同样工作。"""
    words = [
        _w("MODEL", 40, 10, 90, 24), _w("PRICE", 600, 10, 660, 24),
        _w("A-1", 40, 60, 70, 74), _w("120", 600, 60, 630, 74),
    ]
    rows = words_to_rows(words, width=800)
    assert rows[0] == ["MODEL", "PRICE"]
    assert rows[1] == ["A-1", "120"]


def test_words_to_rows_empty():
    assert words_to_rows([]) == []


# ---- 表头识别 ----

def test_header_key_sticky_match():
    """粘连表头子串匹配：'含税单价' 应映射 price（最长别名优先）。"""
    assert _header_key("含税单价") == "price"
    assert _header_key("型号数量") == "model"  # 两个别名都命中时取长的
    assert _header_key("序号") is None          # 噪声列


def test_parse_table_rows_multiword_header_cells():
    """一格多词表头（OCR 粘连）：数据格内拆词分配。"""
    rows = [
        ["序号 型号", "品牌", "数量", "单位", "含税单价"],
        ["1 CAM-100", "海康", "20", "台", "880"],
    ]
    r = parse_table_rows(rows, "图片", "scan.png", "purchase")
    assert r["rows"], r
    row = r["rows"][0]
    assert row["model"] == "CAM-100"
    assert row["brand"] == "海康"
    assert row["qty"] == 20.0
    assert row["price"] == 880.0


# ---- 降级文本切列 ----

def test_text_to_rows_multi_space():
    rows = text_to_rows("MODEL  PRICE\nA-1  880")
    assert rows[0] == ["MODEL", "PRICE"]
    assert rows[1] == ["A-1", "880"]


def test_text_to_rows_single_word_line():
    assert text_to_rows("面议") == [["面议"]]
