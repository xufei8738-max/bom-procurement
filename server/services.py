"""业务服务层：MCP 工具与 Web API 共用的单一逻辑来源。

红线（spec §2.2）：
- 价格 <=0 视为异常（赠品/测试价），拒绝入库，提示人工确认
- 缺价留空（None），绝不猜测
- 价格只追加不覆盖；别名人工确认后回流
"""

from __future__ import annotations

from datetime import date
from typing import Any

from server import db, match, parser, pricing
from server.db import db as db_ctx
from server.normalize import clean_price, clean_qty, model_key


# ---------------- 物料 ----------------

def upsert_item(model: str, brand: str = "", category: str = "", spec: str = "",
                unit: str = "个") -> dict:
    """新增/查重物料。model_norm 查重命中则返回已有 item_id。"""
    with db_ctx() as con:
        item_id, duplicate = db.insert_item(con, model, brand, category, spec, unit)
    return {
        "item_id": item_id,
        "duplicate": duplicate,
        "message": "已存在同型号物料，未重复创建" if duplicate else "新物料已创建",
    }


def get_item(item_id: int) -> dict:
    with db_ctx() as con:
        row = con.execute("SELECT * FROM items WHERE item_id=?", (item_id,)).fetchone()
        if not row:
            return {"error": f"物料 {item_id} 不存在"}
        item = db.item_row_to_dict(row)
        prices = con.execute(
            "SELECT * FROM prices WHERE item_id=? ORDER BY price_date DESC LIMIT 5", (item_id,)
        ).fetchall()
        item["recent_prices"] = [db.price_row_to_dict(r) for r in prices]
        aliases = con.execute(
            "SELECT alias_text FROM aliases WHERE item_id=?", (item_id,)
        ).fetchall()
        item["aliases"] = [r["alias_text"] for r in aliases]
    return item


def update_item(item_id: int, **fields) -> dict:
    with db_ctx() as con:
        ok = db.update_item(con, item_id, **fields)
    return {"item_id": item_id, "updated": ok}


def list_items(keyword: str = "", status: str = "active", page: int = 1,
               page_size: int = 50) -> dict:
    key = model_key(keyword)
    where, args = "status=?", [status]
    if keyword:
        where += " AND (model_raw LIKE ? OR model_norm LIKE ? OR brand LIKE ? OR category LIKE ? OR spec LIKE ?)"
        args += [f"%{keyword}%"] * 5
    with db_ctx() as con:
        total = con.execute(f"SELECT COUNT(*) c FROM items WHERE {where}", args).fetchone()["c"]
        offset = max(page - 1, 0) * page_size
        rows = con.execute(
            f"SELECT * FROM items WHERE {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            args + [page_size, offset],
        ).fetchall()
    return {"total": total, "page": page, "items": [db.item_row_to_dict(r) for r in rows]}


def deactivate_item(item_id: int) -> dict:
    with db_ctx() as con:
        con.execute(
            "UPDATE items SET status='inactive', updated_at=datetime('now','localtime') WHERE item_id=?",
            (item_id,),
        )
    return {"item_id": item_id, "status": "inactive"}


# ---------------- 价格 ----------------

def _resolve_item_id(con, item_id: int | None, model: str | None,
                     brand="", category="", spec="", unit="个") -> tuple[int | None, bool]:
    """解析目标物料：item_id 优先；model 查库；查不到则自动新建（返回 created=True）。"""
    if item_id:
        row = con.execute("SELECT item_id FROM items WHERE item_id=?", (item_id,)).fetchone()
        if not row:
            return None, False
        return int(row["item_id"]), False
    if model:
        iid, dup = db.insert_item(con, model, brand, category, spec, unit)
        return iid, not dup
    return None, False


def upsert_price(item_id: int | None = None, model: str | None = None, price: float | None = None,
                 price_type: str = "purchase", price_date: str = "",
                 tax_mode: str = "unknown", qty: float | None = None, unit: str | None = None,
                 vendor: str = "", source: str = "",
                 brand: str = "", category: str = "", spec: str = "",
                 allow_zero: bool = False) -> dict:
    """追加一条价格记录（不覆盖历史）。

    price<=0 视为异常价（赠品/测试价），默认拒绝。
    """
    if price is None:
        return {"error": "缺少价格数值（price）"}
    price = float(price)
    if price <= 0 and not allow_zero:
        return {"error": f"价格 {price} 疑似异常（0/负值，常见为赠品或测试价），如确认无误请带 allow_zero=true"}
    if price_type not in db.PRICE_TYPES:
        return {"error": f"price_type 必须是 {'/'.join(db.PRICE_TYPES)} 之一，收到 {price_type!r}"}
    price_date = (price_date or date.today().isoformat())[:10]

    with db_ctx() as con:
        iid, created = _resolve_item_id(con, item_id, model, brand, category, spec)
        if iid is None:
            return {"error": "需要 item_id 或 model 之一"}
        pid = db.insert_price(
            con, iid, price, price_type, price_date, tax_mode=tax_mode, qty=qty,
            unit=unit, vendor=vendor, source=source,
        )
    return {"price_id": pid, "item_id": iid, "created_item": created}


def get_prices(item_id: int) -> list:
    with db_ctx() as con:
        rows = con.execute(
            "SELECT * FROM prices WHERE item_id=? ORDER BY price_date DESC, price_id DESC",
            (item_id,),
        ).fetchall()
    return [db.price_row_to_dict(r) for r in rows]


# ---------------- 批量导入 ----------------

def bulk_import(rows: list[dict], default_source: str = "") -> dict:
    """批量导入。每行字段：
    model(必填), brand, category, spec, unit, price, price_type, price_date,
    tax_mode, qty, price_unit, vendor, source

    价格字段可以是原始字符串（如 "￥1,234"、"12~15"），内部清洗；
    区间价取上限（保守），解析失败则跳过价格只建物料。
    """
    results = []
    ok = fail = price_skipped = 0
    with db_ctx() as con:
        for idx, row in enumerate(rows):
            r = dict(row or {})
            model = str(r.get("model") or r.get("model_raw") or "").strip()
            if not model:
                results.append({"row": idx, "status": "fail", "reason": "型号为空"})
                fail += 1
                continue
            try:
                item_id, dup = db.insert_item(
                    con, model, r.get("brand", ""), r.get("category", ""), r.get("spec", ""),
                    r.get("unit", "个"),
                )
                entry = {
                    "row": idx, "model": model, "item_id": item_id,
                    "status": "ok" if not dup else "duplicate_item",
                    "price_id": None,
                }
                raw_price = r.get("price")
                cp = clean_price(raw_price) if raw_price is not None else {"status": "none"}
                ptype = r.get("price_type") or "purchase"
                if cp["status"] in ("ok", "range"):
                    pid = db.insert_price(
                        con, item_id, cp["price"], ptype,
                        (r.get("price_date") or date.today().isoformat())[:10],
                        tax_mode=r.get("tax_mode", "unknown"),
                        qty=clean_qty(r.get("qty")).get("qty"),
                        unit=r.get("price_unit") or r.get("unit"),
                        vendor=r.get("vendor", ""),
                        source=r.get("source") or default_source,
                    )
                    entry["price_id"] = pid
                    if cp["status"] == "range":
                        entry["price_note"] = f"区间价 {cp['price_low']}~{cp['price_high']}，保守取上限"
                    ok += 1
                else:
                    price_skipped += 1
                    entry["status"] = "item_only"
                    entry["reason"] = "价格缺失或无法解析（面议/待定），仅建物料"
                results.append(entry)
            except Exception as e:  # noqa: BLE001 - 单行失败不影响整批
                results.append({"row": idx, "model": model, "status": "fail", "reason": str(e)})
                fail += 1
    return {
        "results": results,
        "summary": {"total": len(rows), "ok": ok, "item_only": price_skipped, "fail": fail},
    }


# ---------------- 资料导入（Skill 1：两步式，先预览后确认） ----------------

def parse_import_file(path: str, price_type: str | None = None) -> dict:
    """解析资料文件 → 预览结构（不落库）。

    返回 {file, price_type, price_type_reason, rows(前若干条预览), pending, summary,
          dup_hits: 与库内已存在的同型号行}
    FR-17：必须先预览，人工确认后再调 import_parsed 落库。
    """
    result = parser.parse_file(path)
    if "error" in result:
        return result
    if price_type:
        result["price_type"] = price_type
        result["price_type_reason"] = "人工指定"

    # 预览时标注库内是否已有同型号（帮助判断重复录入）
    dup_hits = 0
    with db_ctx() as con:
        for row in result["rows"]:
            key = model_key(row.get("model", ""))
            if not key:
                continue
            hit = con.execute(
                "SELECT item_id FROM items WHERE model_norm=? AND status='active'", (key,)
            ).fetchone()
            if hit:
                row["existing_item_id"] = int(hit["item_id"])
                dup_hits += 1
    result["summary"]["dup_in_library"] = dup_hits
    return result


def import_parsed(rows: list[dict], pending: list[dict] | None = None,
                  default_source: str = "") -> dict:
    """把人工确认后的解析行落库（bulk_import 的有来源版）。

    rows 每行来自 parse_import_file 的 rows；pending 为无法解析的行，写入待确认区（FR-19）。
    """
    for r in rows:
        if not r.get("source") and default_source:
            r["source"] = default_source
    out = bulk_import(rows, default_source)
    pending_added = 0
    for p in pending or []:
        services_add = add_pending(
            raw_text=p.get("raw_text", ""), source=p.get("source", ""),
        )
        pending_added += 1 if "pending_id" in services_add else 0
    out["summary"]["pending_added"] = pending_added
    return out


# ---------------- 别名 ----------------

def add_alias(alias_text: str, item_id: int, source: str = "") -> dict:
    if not alias_text or not item_id:
        return {"error": "需要 alias_text 与 item_id"}
    key = model_key(alias_text)
    if not key:
        return {"error": "别名清洗后为空"}
    with db_ctx() as con:
        row = con.execute("SELECT item_id FROM items WHERE item_id=?", (item_id,)).fetchone()
        if not row:
            return {"error": f"物料 {item_id} 不存在"}
        cur = con.execute(
            "INSERT INTO aliases(alias_text, alias_norm, item_id, source) VALUES(?,?,?,?)",
            (alias_text, key, item_id, source),
        )
        alias_id = int(cur.lastrowid)
    return {"alias_id": alias_id, "item_id": item_id, "alias_text": alias_text}


def list_aliases(keyword: str = "") -> list:
    where, args = "1=1", []
    if keyword:
        where += " AND (a.alias_text LIKE ? OR i.model_raw LIKE ?)"
        args = [f"%{keyword}%"] * 2
    with db_ctx() as con:
        rows = con.execute(
            f"SELECT a.*, i.model_raw, i.brand FROM aliases a JOIN items i ON i.item_id=a.item_id "
            f"WHERE {where} ORDER BY a.alias_id DESC LIMIT 200",
            args,
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- 待确认区 ----------------

def add_pending(raw_text: str, model: str = "", brand: str = "", category: str = "",
                spec: str = "", unit: str = "", price: float | None = None,
                qty: float | None = None, vendor: str = "", price_date: str = "",
                price_type: str = "", source: str = "") -> dict:
    """把低把握条目放入待确认区（FR-19：不丢弃、不静默入库）。"""
    if not raw_text and not model:
        return {"error": "需要 raw_text 或 model"}
    with db_ctx() as con:
        cur = con.execute(
            "INSERT INTO pending(raw_text, model, brand, category, spec, unit, price, qty, vendor, "
            "price_date, price_type, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (raw_text or model, model, brand, category, spec, unit, price, qty, vendor,
             price_date, price_type, source),
        )
        pid = int(cur.lastrowid)
    return {"pending_id": pid}


def list_pending(limit: int = 50) -> list:
    with db_ctx() as con:
        rows = con.execute(
            "SELECT * FROM pending WHERE status='open' ORDER BY pending_id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def confirm_pending(pending_id: int, item_id: int | None = None, model: str | None = None,
                    brand: str = "", category: str = "", spec: str = "", unit: str = "个",
                    add_price: bool = True, add_alias: bool = True) -> dict:
    """确认待确认条目：绑定/新建物料，按需补价格，并把原始叫法写回别名（回流）。"""
    with db_ctx() as con:
        row = con.execute(
            "SELECT * FROM pending WHERE pending_id=? AND status='open'", (pending_id,)
        ).fetchone()
        if not row:
            return {"error": f"待确认条目 {pending_id} 不存在或已处理"}
        p = dict(row)

        if item_id:
            exists = con.execute(
                "SELECT item_id FROM items WHERE item_id=?", (item_id,)
            ).fetchone()
            if not exists:
                return {"error": f"物料 {item_id} 不存在"}
            iid, created = int(item_id), False
        elif model or p["model"]:
            iid, created = db.insert_item(
                con, model or p["model"],
                brand or p["brand"], category or p["category"], spec or p["spec"],
                unit or p["unit"] or "个",
            )
        else:
            return {"error": "需要 item_id 或 model 以完成确认"}

        price_id = None
        if add_price and p["price"] is not None:
            ptype = p["price_type"] or "purchase"
            if ptype not in db.PRICE_TYPES:
                ptype = "purchase"
            price_id = db.insert_price(
                con, iid, p["price"], ptype, p["price_date"] or date.today().isoformat(),
                qty=p["qty"], vendor=p["vendor"], source=p["source"],
            )

        if add_alias:
            alias_text = p["model"] or p["raw_text"]
            key = model_key(alias_text)
            if key and key != con.execute(
                "SELECT model_norm FROM items WHERE item_id=?", (iid,)
            ).fetchone()["model_norm"]:
                con.execute(
                    "INSERT INTO aliases(alias_text, alias_norm, item_id, source) VALUES(?,?,?,?)",
                    (alias_text, key, iid, p["source"] or "pending确认"),
                )

        con.execute(
            "UPDATE pending SET status='resolved' WHERE pending_id=?", (pending_id,)
        )
    return {"pending_id": pending_id, "item_id": iid, "created_item": created, "price_id": price_id}


def dismiss_pending(pending_id: int) -> dict:
    with db_ctx() as con:
        con.execute("UPDATE pending SET status='dismissed' WHERE pending_id=?", (pending_id,))
    return {"pending_id": pending_id, "status": "dismissed"}


# ---------------- 单位换算 ----------------

def upsert_conversion(from_unit: str, to_unit: str, factor: float, category: str = "") -> dict:
    if factor <= 0:
        return {"error": "换算系数必须为正"}
    with db_ctx() as con:
        row = con.execute(
            "SELECT conv_id FROM unit_conversions WHERE from_unit=? AND to_unit=? AND category=?",
            (from_unit, to_unit, category),
        ).fetchone()
        if row:
            con.execute(
                "UPDATE unit_conversions SET factor=? WHERE conv_id=?", (factor, row["conv_id"])
            )
            return {"conv_id": row["conv_id"], "updated": True}
        cur = con.execute(
            "INSERT INTO unit_conversions(from_unit, to_unit, factor, category) VALUES(?,?,?,?)",
            (from_unit, to_unit, factor, category),
        )
        return {"conv_id": int(cur.lastrowid), "updated": False}


def list_conversions() -> list:
    with db_ctx() as con:
        rows = con.execute("SELECT * FROM unit_conversions ORDER BY conv_id").fetchall()
    return [dict(r) for r in rows]


# ---------------- 检索与统计 ----------------

def search_items(query: str, brand: str = "", category: str = "", limit: int = 5) -> dict:
    """三级匹配检索：别名 → 精确 → 模糊候选（fuzzy 仅供人工挑选）。"""
    out = match.find_candidates(query, brand, category, limit)
    # 精确/别名命中直接附最新适用价，fuzzy 不附（红线：不自动取价）
    if out["status"] in ("alias", "exact"):
        for c in out["results"]:
            p = pricing.pick_price(c["item_id"])
            if p:
                c["latest_price"] = {
                    "price": p["price"], "price_type": p["price_type"],
                    "price_date": p["price_date"], "vendor": p["vendor"],
                    "is_reference_only": p.get("is_reference_only", False),
                }
    return out


def stats() -> dict:
    return db.stats()
