"""BOM 比对核心（Skill 2）：客户 BOM → 逐行匹配 + 取价 → 成本清单数据。

规则来源：docs/procurement-system-spec.md §6.2 / §7
红线：
- 价格只来自库内记录；无价留空（None），绝不估算
- 模糊候选只作为待确认候选，**不自动取价**
- 单位不一致且无换算记录 → 挂「单位待确认」，不硬算
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from server import db, match, parser, pricing
from server.db import db as db_ctx
from server.normalize import clean_qty, model_key

# BOM 表头同义字典（客户 BOM 的列名千奇百怪，独立于采购资料的表头映射）
BOM_HEADER_ALIASES = {
    "model": ["型号", "规格型号", "产品型号", "物料型号", "设备型号", "model", "pn", "p/n",
              "料号", "编号", "产品编号", "设备名称", "货物名称", "品名", "名称", "材料名称"],
    "name": ["名称", "品名", "货物名称", "设备名称", "描述", "内容", "item", "description"],
    "brand": ["品牌", "厂商", "厂家", "brand"],
    "qty": ["数量", "需求数量", "用量", "qty", "quantity", "num"],
    "unit": ["单位", "计量单位", "unit", "uom"],
    "category": ["品类", "类别", "分类", "category", "系统", "子系统"],
}

_STALE_DAYS = 365   # 价格超过一年 → 标「价格偏旧」


def _header_key(raw) -> str | None:
    text = str(raw or "").strip().lower().replace(" ", "").replace("　", "")
    if not text:
        return None
    for key, aliases in BOM_HEADER_ALIASES.items():
        for a in aliases:
            if text == a.lower():
                return key
    # 粘连/复合表头回退：最长别名命中
    best, best_len = None, 0
    for key, aliases in BOM_HEADER_ALIASES.items():
        for a in aliases:
            if len(a) > best_len and a.lower() in text:
                best, best_len = key, len(a)
    return best


# ---------------- 1) 读取 BOM ----------------

def load_bom(path: str | Path) -> dict:
    """读取客户 BOM（复用资料解析器的多格式能力），保留原始行号。

    返回 {file, rows: [{row_no, model, name, brand, qty, unit, category}], unparsed: [...], summary}
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"文件不存在：{p}"}

    parsed = parser.parse_file(p)
    if "error" in parsed:
        return parsed

    rows = []
    for r in parsed.get("rows", []):
        src = r.get("source", "")
        row_no = None
        parts = src.split("#")
        if parts and parts[-1].isdigit():
            row_no = int(parts[-1])
        rows.append({
            "row_no": row_no or (len(rows) + 1),
            "model": r.get("model", ""),
            "name": r.get("spec", "") or r.get("model", ""),
            "brand": r.get("brand", ""),
            "qty": r.get("qty"),
            "unit": r.get("unit") or "个",
            "category": r.get("category", ""),
        })

    unparsed = [{"raw_text": x.get("raw_text", ""), "source": x.get("source", ""),
                 "reason": x.get("reason", "")} for x in parsed.get("pending", [])]

    return {
        "file": str(p),
        "kind": parsed.get("kind"),
        "rows": rows,
        "unparsed": unparsed,
        "ocr": parsed.get("ocr") if parsed.get("kind") in ("image", "pdf_scan") else None,
        "review_hint": parsed.get("review_hint"),
        "summary": {"total": len(rows) + len(unparsed), "rows": len(rows),
                    "unparsed": len(unparsed)},
    }


# ---------------- 2) 逐行匹配 + 取价 ----------------

def _days_old(price_date: str) -> int | None:
    try:
        d = datetime.strptime((price_date or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (date.today() - d).days


def match_line(line: dict) -> dict:
    """匹配单行 BOM → 结果 dict（含候选、命中、取价、换算、异常）。"""
    model = (line.get("model") or "").strip()
    qty = line.get("qty")
    bom_unit = (line.get("unit") or "").strip() or "个"

    out = {
        "row_no": line.get("row_no"),
        "customer_name": line.get("name", ""),
        "customer_model": model,
        "qty": qty,
        "unit": bom_unit,
        "status": "none",
        "item": None,
        "price_info": None,
        "convert_note": "",
        "candidates": [],
        "anomalies": [],
        "notes": [],
    }
    if not model:
        out["anomalies"].append(("空型号", "BOM 行没有型号，无法匹配"))
        return out

    res = match.find_candidates(model, line.get("brand", ""), line.get("category", ""), limit=3)
    out["status"] = res["status"]

    if res["status"] in ("alias", "exact"):
        hit = res["results"][0]
        out["item"] = {k: hit[k] for k in
                       ("item_id", "model_raw", "brand", "category", "spec", "unit")}
        out["match_type"] = hit["match_type"]
        _attach_price(out, hit["item_id"], bom_unit, hit.get("category", ""))
        if len(res["results"]) > 1:
            out["notes"].append(f"库内另有 {len(res['results']) - 1} 个同型号记录")
    elif res["status"] == "fuzzy":
        # 红线：模糊候选不取价，只列出来给人挑
        out["candidates"] = res["results"]
        out["notes"].append("模糊候选，需人工确认后再取价")
    else:
        out["anomalies"].append(("未匹配", "库内无此型号，需人工指定或新建物料"))

    return out


def _attach_price(out: dict, item_id: int, bom_unit: str, category: str = "") -> None:
    """取价 + 单位换算 + 异常标注。无价则留空并记异常。"""
    p = pricing.pick_price(item_id)
    if not p:
        out["anomalies"].append(("无历史价", "库内无价格记录，需询价后补录"))
        return

    info = {
        "price": p["price"],
        "price_type": p["price_type"],
        "tax_mode": p["tax_mode"],
        "price_date": p["price_date"],
        "created_at": p.get("created_at", ""),   # 录入时间（何时进的库，区别于资料上的价格日期）
        "vendor": p["vendor"],
        "source": p["source"],
        "price_unit": p.get("unit") or "",
        "effective_price": p["price"],
        "is_reference_only": p.get("is_reference_only", False),
    }

    conv = pricing.convert_unit(p["price"], p.get("unit"), bom_unit, category)
    if conv.get("price") is None:
        out["anomalies"].append(("单位待确认", conv["note"]))
        info["effective_price"] = None
    else:
        info["effective_price"] = conv["price"]
        if conv.get("converted"):
            out["convert_note"] = conv["note"]

    if info["is_reference_only"]:
        out["notes"].append("仅报价参考（无采购/合同价）")

    days = _days_old(info["price_date"])
    if days is not None and days > _STALE_DAYS:
        out["anomalies"].append(("价格偏旧", f"{info['price_date']}，距今 {days} 天，建议复核"))

    out["price_info"] = info


def match_bom(lines: list[dict]) -> list[dict]:
    return [match_line(line) for line in lines]


# ---------------- 3) 汇总 ----------------

def summarize(results: list[dict]) -> dict:
    """分类小计 + 合计 + 有价行覆盖率。"""
    total = 0.0
    priced = 0
    by_category: dict[str, float] = {}
    by_status: dict[str, int] = {}

    for r in results:
        st = r["status"]
        by_status[st] = by_status.get(st, 0) + 1
        info = r.get("price_info") or {}
        eff = info.get("effective_price")
        qty = r.get("qty")
        if eff is not None and qty is not None:
            sub = round(eff * qty, 2)
            r["subtotal"] = sub
            total += sub
            priced += 1
            cat = (r.get("item") or {}).get("category") or "未分类"
            by_category[cat] = round(by_category.get(cat, 0.0) + sub, 2)
        else:
            r["subtotal"] = None

    n = len(results)
    pct = round(priced / n * 100, 1) if n else 0.0
    return {
        "lines": n,
        "priced_lines": priced,
        "unpriced_lines": n - priced,
        "coverage_pct": pct,
        "coverage_warning": "有价行覆盖率低于 80%，该清单不能作为决策依据" if n and pct < 80 else "",
        "total_cost": round(total, 2),
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        "by_status": by_status,
    }


# ---------------- 4) 一站式入口 ----------------

def compare_bom(path: str | Path, model_column: str = "", qty_column: str = "") -> dict:
    """完整比对：读 BOM → 逐行匹配取价 → 汇总。返回结构化结果（不生成文件）。"""
    loaded = load_bom(path)
    if "error" in loaded:
        return loaded
    if not loaded["rows"]:
        return {"error": "BOM 中没有解析出任何物料行（请检查表头是否为 型号/数量 类列名）",
                "file": loaded["file"], "unparsed": loaded.get("unparsed", [])}

    results = match_bom(loaded["rows"])
    summary = summarize(results)

    return {
        "file": loaded["file"],
        "kind": loaded.get("kind"),
        "review_hint": loaded.get("review_hint"),
        "results": results,
        "unparsed": loaded.get("unparsed", []),
        "summary": summary,
    }


def export_cost_sheet(path: str | Path, out_path: str | None = None) -> dict:
    """比对并生成成本清单 Excel，返回 {file, out, summary}。"""
    from server import cost_sheet

    compared = compare_bom(path)
    if "error" in compared:
        return compared

    if not out_path:
        base = Path(path).stem
        out_path = str(Path.home() / f"成本清单_{base}_{date.today().isoformat()}.xlsx")

    cost_sheet.build(compared, out_path)
    return {
        "file": compared["file"],
        "out": out_path,
        "summary": compared["summary"],
        "unparsed": compared.get("unparsed", []),
    }
