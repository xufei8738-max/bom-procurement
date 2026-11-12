"""取价与单位换算。

规则来源：docs/procurement-system-spec.md §6.2 取价规则（只出成本，不做报价）
优先级：purchase > contract > quote；同类型取 price_date 最新。
红线：无记录 → 单价 None（留空），绝不返回猜测价格。
"""

from __future__ import annotations

from server import db

TYPE_PRIORITY = {"purchase": 1, "contract": 2, "quote": 3}

# 单件计数单位互认集合（1:1 直通，不做幅度换算）
# 套/组/对（组合单位）、箱/卷/盘/包（包装单位）、米（长度单位）不在其中
PIECE_UNITS = {"台", "个", "只", "件", "PCS", "PC", "根", "支", "条", "块", "片", "张"}


def pick_price(item_id: int, qty: float | None = None) -> dict | None:
    """取该物料的适用价格。

    返回价格记录 dict + selected_reason；无记录返回 None。
    qty 用于阶梯价筛选（qty_tier 满足时优先）——当前实现取最新，不做复杂阶梯。
    """
    with db.db() as con:
        rows = con.execute(
            "SELECT * FROM prices WHERE item_id=? ORDER BY price_date DESC, price_id DESC",
            (item_id,),
        ).fetchall()
    if not rows:
        return None

    rows = [dict(r) for r in rows]
    # 按类型优先级分组，取每组最新一条，再选优先级最高的组
    best_of_type: dict[str, dict] = {}
    for r in rows:
        t = r["price_type"]
        if t not in best_of_type:
            best_of_type[t] = r
    for t in sorted(TYPE_PRIORITY, key=TYPE_PRIORITY.get):
        if t in best_of_type:
            out = best_of_type[t].copy()
            out["selected_reason"] = (
                f"{ {'purchase':'采购成本','contract':'合同价','quote':'报价参考'}[t] }，日期最新"
                if t != "quote"
                else "仅报价参考（无采购/合同价），日期最新"
            )
            out["is_reference_only"] = t == "quote"
            return out
    return None


def convert_unit(price: float, price_unit: str | None, target_unit: str | None,
                 category: str = "") -> dict:
    """把按 price_unit 计的价格换算到 target_unit。

    返回 {converted: bool, price, factor, note}
    - 命中换算表：price/factor（如 305米/箱 → 每米）
    - 未命中且单位不同：converted=False，note 提示人工确认（红线：不硬算）
    - 单位相同或缺失：原价直通
    """
    if not price_unit or not target_unit or price_unit == target_unit:
        return {"converted": False, "price": price, "factor": None, "note": ""}

    # 计数单位互认：台/个/只/件… 均为「单件」计量，1:1 直通
    # （避免常见换算噪音；组合/包装单位仍走人工确认，不硬算）
    if price_unit.upper() in PIECE_UNITS and target_unit.upper() in PIECE_UNITS:
        return {"converted": False, "price": price, "factor": None,
                "note": f"计数单位互认（{price_unit}↔{target_unit}，1:1）"}

    with db.db() as con:
        row = con.execute(
            "SELECT factor FROM unit_conversions WHERE from_unit=? AND to_unit=? "
            "AND (category=? OR category='') ORDER BY category='' DESC LIMIT 1",
            (price_unit, target_unit, category),
        ).fetchone()
        if row:
            f = float(row["factor"])
            return {
                "converted": True, "price": round(price / f, 4), "factor": f,
                "note": f"已换算 1{price_unit}={f}{target_unit}",
            }
    return {
        "converted": False, "price": None, "factor": None,
        "note": f"单位不一致（{price_unit} vs {target_unit}）且无换算记录，需人工确认",
    }


def line_cost(unit_price: float | None, qty: float | None) -> float | None:
    """行小计。任一为空则 None（未匹配行留空，不硬算）。"""
    if unit_price is None or qty is None:
        return None
    return round(unit_price * qty, 2)
