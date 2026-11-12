"""PDF / 图片 OCR 识别测试。

不依赖外部 OCR 服务的部分（表格重建算法、TSV 解析、降级文本）必须无条件通过；
依赖 macOS Vision 的部分按后端可用性跳过。
"""
import pymupdf
import pytest

from server import ocr, parser


# ---------------- 表格重建算法（核心，无外部依赖） ----------------

def _w(text, x0, y0, x1, y1):
    return {"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def test_words_to_rows_columns():
    """按 x 间隙切列：三列（型号/数量/单价）。"""
    words = [
        _w("型号", 10, 10, 40, 22), _w("数量", 300, 10, 330, 22), _w("单价", 600, 10, 630, 22),
        _w("CAM-1", 10, 40, 60, 52), _w("10", 300, 40, 315, 52), _w("800", 600, 40, 625, 52),
        _w("CAM-2", 10, 70, 60, 82), _w("5", 300, 70, 310, 82), _w("450", 600, 70, 625, 82),
    ]
    rows = ocr.words_to_rows(words, width=800)
    assert len(rows) == 3
    assert rows[0] == ["型号", "数量", "单价"]
    assert rows[1] == ["CAM-1", "10", "800"]
    assert rows[2] == ["CAM-2", "5", "450"]


def test_words_to_rows_merges_same_cell_words():
    """同一单元格内被空格断开的词要合并（不误切列）。"""
    words = [
        _w("400万", 10, 10, 50, 22), _w("网络筒机", 52, 10, 100, 22),  # 间隙小 → 同列
        _w("海康", 400, 10, 430, 22),
    ]
    rows = ocr.words_to_rows(words, width=800)
    assert rows[0][0] == "400万 网络筒机"
    assert rows[0][1] == "海康"


def test_words_to_rows_multiline():
    """y 相差一行高 → 分成两行。"""
    words = [
        _w("A", 10, 10, 20, 22), _w("1", 200, 10, 210, 22),
        _w("B", 10, 60, 20, 72), _w("2", 200, 60, 210, 72),
    ]
    rows = ocr.words_to_rows(words, width=400)
    assert rows == [["A", "1"], ["B", "2"]]


def test_words_to_rows_empty():
    assert ocr.words_to_rows([], 800) == []
    assert ocr.words_to_rows([], None) == []


def test_parse_tesseract_tsv():
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t10\t40\t12\t92.5\t型号\n"
        "5\t1\t1\t1\t1\t2\t100\t10\t30\t12\t88.0\t单价\n"
        "4\t1\t1\t1\t1\t0\t10\t10\t120\t12\t-1\t\n"
    )
    words = ocr._parse_tesseract_tsv(tsv)
    assert len(words) == 2
    assert words[0]["text"] == "型号" and words[1]["x0"] == 100.0


def test_text_to_rows_fallback():
    rows = ocr.text_to_rows("型号    数量   单价\nCAM-1    10    800\n单列行")
    assert rows[0] == ["型号", "数量", "单价"]
    assert rows[1] == ["CAM-1", "10", "800"]
    assert rows[2] == ["单列行"]          # 无分隔时按整行处理


# ---------------- PDF 文本层 ----------------

def _make_pdf(tmp_path, with_text=True, name="资料.pdf"):
    """测试用 PDF。注意：pymupdf 内置字体无 CJK 字形，中文会渲染成 ··，
    因此测试文本用 ASCII（真实中文 PDF 由客户提供，不影响解析逻辑）。"""
    p = tmp_path / name
    doc = pymupdf.open()
    page = doc.new_page()
    if with_text:
        page.insert_text((50, 60), "MODEL   DS-2CD3T46WDV3-I3", fontsize=11)
        page.insert_text((50, 100), "PRICE   1180", fontsize=11)
        page.insert_text((50, 140), "QTY     20", fontsize=11)
    else:
        page.draw_rect(pymupdf.Rect(50, 50, 300, 150), fill=(0.9, 0.9, 0.9))
    doc.save(str(p))
    doc.close()
    return p


def test_pdf_text_layer_detection(tmp_path):
    assert ocr.pdf_has_text_layer(_make_pdf(tmp_path, True)) is True
    assert ocr.pdf_has_text_layer(_make_pdf(tmp_path, False, "扫描.pdf")) is False


def test_pdf_words_to_tables_and_parse(tmp_path):
    p = _make_pdf(tmp_path, True)
    tables = ocr.pdf_words_to_tables(p)
    assert tables, "文本层 PDF 应能提取表格行"
    label, rows = tables[0]
    assert label.startswith("第")
    # 用解析器走全流程：文本层 PDF 不触发 OCR
    r = parser.parse_file(p)
    assert r.get("kind") == "pdf_text", r
    assert "ocr" not in r or r["ocr"].get("mode") == "text_layer"
    assert r["summary"]["total"] >= 1


def test_pdf_scan_falls_back_to_ocr_or_reports(tmp_path):
    """无文本层 PDF → 走 OCR；无 OCR 后端时必须明确报错而不是返回假数据。"""
    p = _make_pdf(tmp_path, False, "扫描件.pdf")
    r = parser.parse_file(p)
    if ocr.available_backends():
        # 有后端：允许识别为空（纯色块）但不得是「不支持格式」
        assert "error" not in r or "不支持" not in r.get("error", "")
    else:
        assert "error" in r
        assert "OCR" in r["error"]


# ---------------- 图片 ----------------

def test_image_unsupported_without_backend(tmp_path, monkeypatch):
    """无任何 OCR 后端时，必须返回可操作的安装指引。"""
    monkeypatch.delenv("PROC_OCR_CMD", raising=False)
    monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
    monkeypatch.setattr(ocr, "_vision_binary", lambda: None)
    monkeypatch.setattr(ocr, "available_backends", lambda: [])
    p = tmp_path / "清单.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")  # 假图片，后端探测在先
    res = ocr.ocr_words([p])
    assert res["ok"] is False
    assert "OCR" in res["error"]
    assert "tesseract" in res["error"] or "Vision" in res["error"]


def test_image_missing_file():
    res = ocr.ocr_words(["/nonexistent/xx.jpg"])
    assert res["ok"] is False and "不存在" in res["error"]


@pytest.mark.skipif(not ocr.available_backends(), reason="本机无 OCR 后端")
def test_real_image_ocr_smoke(tmp_path):
    """真有后端时做一次冒烟：画一张带文字的图片并识别。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        pytest.skip("无 Pillow，跳过真实图片冒烟")
    img = Image.new("RGB", (600, 200), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 40), "MODEL  CAM-100", fill="black")
    d.text((20, 90), "PRICE  880", fill="black")
    p = tmp_path / "smoke.png"
    img.save(p)
    res = ocr.ocr_words([p])
    assert res["ok"] is True
    rows = ocr.words_to_rows(res["pages"][0]["words"], res["pages"][0].get("width"))
    joined = " ".join(" ".join(r) for r in rows).upper()
    assert "CAM-100" in joined.replace(" ", "") or "CAM" in joined
