"""历史资料解析器（Skill 1 核心）：Excel / CSV / Word → 统一行。

规则来源：docs/procurement-system-spec.md §6.1
- 表头同义字典：型号/规格型号/Model/P.N → model 等（内置可增补）
- 价格类型判定：文件名关键词（采购/进货/入库→purchase；报价/询价→quote；合同/协议→contract）
- 来源：文件路径#sheet名#行号（FR-18 可追溯）
- 解析失败行不丢弃，返回 pending 列表交人工（FR-19）

纯函数模块：不落库、不改库——预览与入库由 services 层两步完成（FR-17）。
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from server.normalize import clean_price, clean_qty, extract_unit

# ---------------- 表头同义字典（可增补） ----------------

HEADER_MAP: dict[str, list[str]] = {
    "model": ["型号", "规格型号", "产品型号", "物料型号", "model", "modelno", "modelnumber",
              "pn", "p/n", "partno", "partnumber", "料号", "货号", "编号", "item"],
    "brand": ["品牌", "厂商", "厂家", "制造商", "brand", "manufacturer", "make"],
    "category": ["品类", "类别", "分类", "类型", "category", "type", "种类"],
    "spec": ["规格", "规格描述", "参数", "描述", "说明", "spec", "specification", "desc",
             "description", "remark", "备注", "名称", "品名", "产品名称", "货物名称"],
    "unit": ["单位", "计量单位", "units", "unit", "uom"],
    "price": ["单价", "含税单价", "未税单价", "价格", "成本价", "采购价", "采购单价", "合同单价",
              "报价", "单价(元)", "单价（元）", "price", "unitprice", "unit_price", "cost",
              "costprice", "amount",
              # 总价类字段也映射到 price，但会触发「疑似总价」人工核对提示
              "总价", "合计", "金额", "小计", "total", "sum"],
    "qty": ["数量", "采购数量", "订量", "qty", "quantity", "amount2", "pcs", "数量(个)"],
    "vendor": ["供应商", "供货商", "厂商名称", "vendor", "supplier", "供应商名称"],
    "price_date": ["日期", "采购日期", "报价日期", "合同日期", "成交日期", "date", "price_date"],
}

# 数量字段的兜底：若表头没有数量列，但价格列名暗示"总价"，则提示换算风险
PRICE_IS_TOTAL_HINTS = ["总价", "合计", "金额", "小计", "total", "sum", "amount"]

NOISE_HEADER = ["序号", "no", "sn", "行号", "id", "备注2", "税率", "taxrate", "税率(%)"]

# 占位型型号（不是真实型号，应进待确认而不是污染主数据）
PLACEHOLDER_MODEL = re.compile(
    r"^[（(【\[]?\s*(见附件|见备注|待定|待补充|待询|待确认|TBD|无|同上|同前|/|-|—)\s*[)）】\]]?$"
)


def _is_placeholder_model(model: str) -> bool:
    text = (model or "").strip()
    if not text:
        return True
    return bool(PLACEHOLDER_MODEL.match(text))


def _header_key(raw: Any) -> str | None:
    """把一个表头单元格映射到标准字段名；映射不上返回 None。

    三级匹配：
    1. 精确匹配（去空格后全等）
    2. 粘连子串匹配（OCR 把「序号 型号」认成一个词时，取命中的最长别名）
    """
    if raw is None:
        return None
    text = str(raw).strip().lower().replace(" ", "").replace("　", "")
    if not text or text in {str(x).lower() for x in NOISE_HEADER}:
        return None
    for key, aliases in HEADER_MAP.items():
        for alias in aliases:
            if text == alias.lower() or text == alias.replace(" ", "").lower():
                return key
    # 粘连/复合表头：取包含的最长别名（'含税单价' 优先于 '单价'）
    best_key, best_len = None, 0
    for key, aliases in HEADER_MAP.items():
        for alias in aliases:
            a = alias.replace(" ", "").lower()
            if len(a) > best_len and a in text:
                best_key, best_len = key, len(a)
    return best_key


def guess_price_type(filename: str) -> tuple[str, str]:
    """按文件名关键词判定价格类型。返回 (price_type, 判定依据说明)。"""
    name = filename or ""
    rules = [
        (("采购", "进货", "入库", "购买"), "purchase"),
        (("合同", "协议", "订单"), "contract"),
        (("报价", "询价", "quotation", "quote"), "quote"),
    ]
    for keywords, ptype in rules:
        for kw in keywords:
            if kw in name:
                return ptype, f"文件名含「{kw}」→ {ptype}"
    return "quote", "文件名无关键词，默认 quote（报价参考），可人工改"


# ---------------- 行解析 ----------------

def _cell_text(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def _clean_date(raw: str, fallback: str) -> str:
    """日期归一为 YYYY-MM-DD；认不出就用 fallback（今天）。"""
    text = _cell_text(raw).replace("年", "-").replace("月", "-").replace("日", "").replace(".", "-").replace("/", "-")
    m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", text)  # 20250115
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"(\d{1,2})-(\d{1,2})-(20\d{2})", text)  # 15-01-2025
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return fallback


def parse_table_rows(rows: list[list[Any]], sheet_name: str, source_file: str,
                     default_price_type: str = "quote") -> dict:
    """解析一个二维表（已含表头行）。

    返回 {rows: [...可入库行], pending: [...需人工行], summary: {...}}
    每个可入库行含：model, brand, category, spec, unit, price, price_type,
    price_date, qty, price_unit, vendor, source, price_note
    """
    # 找表头行：前 5 行里映射字段最多的行；支持一格多词（OCR 列粘连
    # 「序号 型号」在同一格时按词位对齐：噪声词也占位，数据行同位取词）
    header_idx, header_map, header_multi = -1, {}, {}
    for i, row in enumerate(rows[: min(5, len(rows))]):
        mapping, multi = {}, {}
        for col, cell in enumerate(row):
            parts = str(cell or "").split()
            if len(parts) >= 2:
                keys = [_header_key(p) for p in parts]  # None 占位
                hit = [k for k in keys if k]
                if len(hit) >= 1 and all(k not in mapping for k in hit):
                    for k in hit:
                        mapping[k] = col
                    multi[col] = keys
                    continue
            key = _header_key(cell)
            if key and key not in mapping:
                mapping[key] = col
                multi[col] = [key]
        if "model" in mapping and len(mapping) > len(header_map):
            header_idx, header_map, header_multi = i, mapping, multi
    if header_idx < 0:
        return {"rows": [], "pending": [
            {"raw_text": f"[{sheet_name}] 未识别到表头行（前5行无 型号/价格 等列）", "source": f"{source_file}#{sheet_name}"}],
            "summary": {"sheets": 1, "parsed": 0, "pending": 1, "header": None}}

    # 数据行取值：一格多字段时按词位对齐（表头第 i 词 ↔ 数据第 i 词）
    def make_get(row_cells: list):
        def get(key: str) -> str:
            if key not in header_map:
                return ""
            col = header_map[key]
            if col >= len(row_cells):
                return ""
            text = _cell_text(row_cells[col])
            keys_in_col = header_multi.get(col) or [key]
            if len(keys_in_col) > 1:
                try:
                    idx = keys_in_col.index(key)
                except ValueError:
                    return text
                parts = text.split()
                if len(parts) == len(keys_in_col):
                    return parts[idx]
                if len(parts) > len(keys_in_col) and idx == len(keys_in_col) - 1:
                    return " ".join(parts[idx:])
                # 词数对不上：整格给首个键，其余留空（交人工复核）
                return text if idx == 0 else ""
            return text
        return get

    out_rows, pending = [], []
    for r_idx in range(header_idx + 1, len(rows)):
        row = rows[r_idx]
        get = make_get(row)

        # 型号必须来自型号列（或 spec 兼做型号列的场景）；spec 绝不做兜底——
        # 描述文字当型号会污染主数据（一物多码）
        model = get("model")
        if "model" not in header_map:
            model = get("spec")  # 表头完全没有型号列时，才允许描述列做型号
        if not model or _is_placeholder_model(model):
            # 空行/占位型号跳过入库；有内容但无有效型号 → pending
            if any(_cell_text(c) for c in row):
                pending.append({
                    "raw_text": " | ".join(_cell_text(c) for c in row if _cell_text(c))[:200],
                    "source": f"{source_file}#{sheet_name}#{r_idx + 1}",
                    "reason": "无有效型号（占位符或空）",
                })
            continue

        cp = clean_price(get("price"))
        note = ""
        price = cp["price"] if cp["status"] in ("ok", "range") else None
        if cp["status"] == "range":
            note = f"区间价 {cp['price_low']}~{cp['price_high']}，保守取上限"
        elif cp["status"] == "none" and get("price"):
            note = f"价格无法解析（原文：{get('price')}）"

        # 表头无法识别数量时从型号串尾部抽取
        qty_raw = clean_qty(get("qty")).get("qty")
        unit = get("unit") or extract_unit(get("qty") or get("spec")) or "个"

        # 总价伪装成单价的提示
        header_price_text = _cell_text(rows[header_idx][header_map.get("price", -1)]) if "price" in header_map else ""
        if header_price_text and any(h in header_price_text.lower() for h in PRICE_IS_TOTAL_HINTS):
            note = (note + "；" if note else "") + f"价格列表头疑似总价（{header_price_text}），请人工核对"

        out_rows.append({
            "model": model,
            "brand": get("brand"),
            "category": get("category"),
            "spec": get("spec") if ("spec" in header_map and get("spec") != model) else "",
            "unit": unit,
            "price": price,
            "price_note": note,
            "price_type": default_price_type,
            "price_date": _clean_date(get("price_date"), _today()),
            "qty": qty_raw,
            "price_unit": unit if price is not None else None,
            "vendor": get("vendor"),
            "source": f"{source_file}#{sheet_name}#{r_idx + 1}",
        })

    return {"rows": out_rows, "pending": pending,
            "summary": {"sheets": 1, "parsed": len(out_rows), "pending": len(pending),
                        "header": {k: v for k, v in sorted(header_map.items())}}}


# ---------------- 文件级入口 ----------------

def parse_file(path: str | Path, ocr_dpi: int = 200) -> dict:
    """解析一个资料文件（.xlsx/.xls/.csv/.docx/.pdf/.png/.jpg…）。

    - Excel/CSV/Word：直读表格
    - PDF：优先取文本层（快、准）；无文本层判定为扫描件 → 渲染成图走 OCR
    - 图片（png/jpg/jpeg/bmp/tif/tiff/webp）：走 OCR

    返回 {file, kind, price_type, price_type_reason, rows, pending, summary, ocr: {...}}
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"文件不存在：{p}"}
    suffix = p.suffix.lower()
    ptype, reason = guess_price_type(p.name)
    ocr_info: dict = {}

    if suffix in (".xlsx", ".xls"):
        tables, kind, ocr_info = _read_excel(p), "excel", {}
    elif suffix == ".csv":
        tables, kind, ocr_info = [(p.stem, _read_csv(p))], "csv", {}
    elif suffix == ".docx":
        tables, kind, ocr_info = _read_docx(p), "docx", {}
    elif suffix == ".pdf":
        tables, kind, ocr_info = _read_pdf(p, ocr_dpi)
        if tables is None:
            return {"error": ocr_info.get("error", "PDF 解析失败"), "kind": kind, "ocr": ocr_info}
    elif suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".heic"):
        tables, ocr_info = _read_images([p])
        kind = "image"
        if tables is None:
            return {"error": ocr_info.get("error", "图片识别失败"), "kind": kind, "ocr": ocr_info}
    else:
        return {"error": f"不支持的格式 {suffix}（支持 xlsx/xls/csv/docx/pdf/png/jpg/jpeg/bmp/tif/tiff/webp）"}

    all_rows, all_pending = [], []
    for sheet_name, rows in tables:
        if not rows:
            continue
        r = parse_table_rows(rows, sheet_name, p.name, ptype)
        all_rows.extend(r["rows"])
        all_pending.extend(r["pending"])

    out = {
        "file": str(p),
        "kind": kind,
        "price_type": ptype,
        "price_type_reason": reason,
        "rows": all_rows,
        "pending": all_pending,
        "summary": {"sheets": len(tables), "parsed": len(all_rows),
                    "pending": len(all_pending), "total": len(all_rows) + len(all_pending)},
    }
    if ocr_info:
        out["ocr"] = ocr_info
    # OCR/PDF 来源要提醒人工复核（识别可能有误差）
    if kind in ("pdf_scan", "image") or ocr_info:
        out["review_hint"] = "该文件经 OCR 识别，请务必核对预览中的型号与单价后再入库"
    return out


def _read_excel(p: Path) -> list[tuple[str, list[list]]]:
    import openpyxl
    wb = openpyxl.load_workbook(p, data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            if row is None:
                continue
            vals = [v for v in row]
            # 去掉整行 None 的尾部
            while vals and vals[-1] is None:
                vals.pop()
            rows.append(vals)
        if rows:
            out.append((ws.title, rows))
    wb.close()
    return out


def _read_csv(p: Path) -> list[list]:
    for enc in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            with open(p, newline="", encoding=enc) as f:
                return [[c for c in r] for r in csv.reader(f)]
        except UnicodeDecodeError:
            continue
    with open(p, newline="", encoding="utf-8", errors="replace") as f:
        return [[c for c in r] for r in csv.reader(f)]


def _read_docx(p: Path) -> list[tuple[str, list[list]]]:
    import docx
    doc = docx.Document(str(p))
    out = []
    for t_idx, table in enumerate(doc.tables, 1):
        rows = []
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                rows.append(cells)
        if rows:
            out.append((f"表格{t_idx}", rows))
    return out


# ---------------- PDF 与图片（P1.5 扩充，接 server/ocr.py） ----------------

def _read_images(paths: list[Path]) -> tuple[list[tuple[str, list[list]]] | None, dict]:
    """多张图片 → 表格。失败返回 (None, info)；info 含 backend/confidence/错误。"""
    from server import ocr

    res = ocr.ocr_words(paths)
    info = {"backend": res.get("backend"), "confidence": res.get("confidence"),
            "pages": len(paths), "backend_available": ocr.available_backends()}
    if not res["ok"]:
        info["error"] = res.get("error")
        return None, info

    tables = []
    low_conf = []
    for i, page in enumerate(res["pages"], 1):
        if page.get("words"):
            rows = ocr.words_to_rows(page["words"], page.get("width"))
        else:
            rows = ocr.text_to_rows(page.get("raw_text", ""))
        if rows:
            tables.append((f"第{i}页" if len(paths) > 1 else "图片", rows))
        if res.get("confidence") is not None and res["confidence"] < 0.6:
            low_conf.append(i)
    if low_conf:
        info["low_confidence_pages"] = low_conf
        info["warning"] = f"第 {low_conf} 页识别置信度偏低，建议人工核对"
    return tables, info


def _read_pdf(p: Path, ocr_dpi: int = 200) -> tuple[list[tuple[str, list[list]]] | None, str, dict]:
    """PDF：有文本层走文本层；否则渲染为图走 OCR。返回 (tables, kind, ocr_info)。"""
    from server import ocr

    try:
        has_text = ocr.pdf_has_text_layer(p)
    except Exception as e:  # noqa: BLE001 - 缺 pymupdf 等情况
        return None, "pdf", {"error": f"无法读取 PDF（{e}）。请安装 pymupdf：pip install pymupdf"}

    if has_text:
        tables = ocr.pdf_words_to_tables(p)
        if tables:
            return tables, "pdf_text", {"mode": "text_layer", "pages": len(tables)}

    # 扫描件路径
    pages = ocr.render_pdf_pages(p, dpi=ocr_dpi)
    if not pages:
        return None, "pdf_scan", {"error": "PDF 无文本层且渲染失败"}
    tables, info = _read_images(pages)
    info["mode"] = "ocr"
    info["dpi"] = ocr_dpi
    return tables, "pdf_scan", info
