"""三级匹配：别名 → 型号归一键精确 → 模糊候选。

规则来源：docs/procurement-system-spec.md §9.4
匹配优先级：aliases.alias_norm 命中 > items.model_norm 精确 > 模糊（包含/被包含）。
模糊候选最多 3 条，交人工确认，不自动取价（红线）。
"""

from __future__ import annotations

from server import db
from server.normalize import model_key


def _row_to_candidate(r, match_type: str, score: float) -> dict:
    return {
        "item_id": r["item_id"],
        "model_raw": r["model_raw"],
        "brand": r["brand"],
        "category": r["category"],
        "spec": r["spec"],
        "unit": r["unit"],
        "match_type": match_type,
        "score": score,
    }


def find_candidates(query: str, brand: str = "", category: str = "", limit: int = 5) -> dict:
    """查找候选物料。

    返回 {"status": "none|alias|exact|fuzzy", "results": [...], "count": n}
    - alias: 别名命中（人工确认过的对应关系，直接可用）
    - exact: 型号归一键精确命中（可直接取价）
    - fuzzy: 模糊候选（仅供人工挑选，不得直接取价）
    - none: 无候选 → 该行进未匹配清单
    """
    key = model_key(query)
    if not key:
        return {"status": "none", "results": [], "count": 0}

    with db.db() as con:
        # 1) 别名命中
        rows = con.execute(
            "SELECT i.*, a.alias_text, a.alias_norm FROM aliases a JOIN items i ON i.item_id=a.item_id "
            "WHERE a.alias_norm=? AND i.status='active'",
            (key,),
        ).fetchall()
        if rows:
            results = []
            for r in rows:
                c = _row_to_candidate(r, "alias", 1.0)
                c["matched_alias"] = r["alias_text"]
                results.append(c)
            return {"status": "alias", "results": results, "count": len(results)}

        # 2) 型号精确
        rows = con.execute(
            "SELECT * FROM items WHERE model_norm=? AND status='active'", (key,)
        ).fetchall()
        if rows:
            return {
                "status": "exact",
                "results": [_row_to_candidate(r, "exact", 0.98) for r in rows],
                "count": len(rows),
            }

        # 3) 模糊：双向包含（query 包含库内键，或反之）
        con_args = []
        extra = ""
        if brand:
            extra += " AND (brand LIKE ? OR ?='')"
            con_args += [f"%{brand}%", brand]
        if category:
            extra += " AND (category LIKE ? OR ?='')"
            con_args += [f"%{category}%", category]
        rows = con.execute(
            f"SELECT * FROM items WHERE status='active'{extra} "
            f"AND (model_norm LIKE ? OR ? LIKE '%'||model_norm||'%') "
            f"ORDER BY length(model_norm) LIMIT 30",
            [f"%{key}%", key] + con_args,
        ).fetchall()

        # 前缀/长键优先，按重合度排序
        def overlap_score(rk: str) -> float:
            common = sum(1 for a, b in zip(key, rk) if a == b)
            return round(0.5 + 0.5 * common / max(len(key), len(rk), 1), 3)

        rows = sorted(rows, key=lambda r: -overlap_score(r["model_norm"]))
        results = [
            _row_to_candidate(r, "fuzzy", overlap_score(r["model_norm"])) for r in rows[:limit]
        ]
        return {"status": "fuzzy" if results else "none", "results": results, "count": len(results)}
