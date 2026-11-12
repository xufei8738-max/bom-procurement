"""MCP 工具层：13 个领域化工具（FastMCP 注册）。

契约：docs/procurement-system-spec.md §5
鉴权：app.py 在 http 层校验 Bearer 口令；工具本身不感知 token。
"""

from __future__ import annotations

from fastmcp import FastMCP

from server import services

import server.bom as _bom

mcp = FastMCP(
    name="bom-procurement",
    instructions=(
        "小企业器件采购库。三条红线："
        "1) 价格只能来自库内记录，库里没有就留空（未匹配清单），绝不允许估算或编造；"
        "2) 每条价格都应带 source（文件+sheet+行号）与 price_date；"
        "3) 同型号多行=多条价格记录并存，不覆盖。"
    ),
)


# ---------------- 查询 ----------------

@mcp.tool
def search_items(query: str, brand: str = "", category: str = "", limit: int = 5) -> dict:
    """按型号/关键字检索物料。返回三级匹配结果：
    alias=别名命中(可直接用)、exact=型号精确(可直接取价)、
    fuzzy=模糊候选(仅供人工挑选，不得直接取价)、none=无候选。"""
    return services.search_items(query, brand, category, limit)


@mcp.tool
def get_item(item_id: int) -> dict:
    """取物料详情：字段 + 最近 5 条价格 + 已登记别名。"""
    return services.get_item(item_id)


@mcp.tool
def get_prices(item_id: int) -> list:
    """取某物料全部价格流水（按日期倒序，含类型/供应商/税口径/来源）。"""
    return services.get_prices(item_id)


# ---------------- 录入 ----------------

@mcp.tool
def upsert_item(model: str, brand: str = "", category: str = "", spec: str = "",
                unit: str = "个") -> dict:
    """新增物料（型号归一后查重，重复则返回已有 item_id）。字段：
    model 型号；brand 品牌；category 品类(如 安防/摄像机)；spec 规格描述；unit 基本单位。"""
    return services.upsert_item(model, brand, category, spec, unit)


@mcp.tool
def upsert_price(item_id: int | None = None, model: str | None = None, price: float = None,
                 price_type: str = "purchase", price_date: str = "",
                 tax_mode: str = "unknown", qty: float | None = None, unit: str | None = None,
                 vendor: str = "", source: str = "",
                 brand: str = "", category: str = "", spec: str = "") -> dict:
    """追加一条价格记录（不覆盖历史）。price_type: purchase采购/quote报价/contract合同；
    tax_mode: included含税/excluded未税/unknown；source 形如 文件路径#sheet#行号。
    price<=0 默认拒绝（疑似赠品/测试价）。"""
    return services.upsert_price(
        item_id=item_id, model=model, price=price, price_type=price_type,
        price_date=price_date, tax_mode=tax_mode, qty=qty, unit=unit,
        vendor=vendor, source=source, brand=brand, category=category, spec=spec,
    )


@mcp.tool
def bulk_import(rows: list[dict], default_source: str = "") -> dict:
    """批量导入。rows 每行：model(必填), brand, category, spec, unit, price(可为原始字符串
    如"￥1,234"/"12~15"), price_type, price_date, tax_mode, qty, price_unit, vendor, source。
    价格无法解析时仅建物料不建价；单行失败不影响整批。"""
    return services.bulk_import(rows, default_source)


@mcp.tool
def add_alias(alias_text: str, item_id: int, source: str = "") -> dict:
    """登记别名：客户/资料的原始叫法 → 物料。人工确认匹配后必须写回，
    下次 BOM 同写法直接命中（回流机制）。"""
    return services.add_alias(alias_text, item_id, source)


@mcp.tool
def parse_import_file(path: str, price_type: str = "") -> dict:
    """解析采购合同/报价清单文件（xlsx/xls/csv/docx）→ 预览，不落库。
    返回统一行 rows（含型号/价格/供应商/来源 文件#sheet#行号）、无法解析的 pending 行、
    与库内已存在同型号的标注。**必须先给用户看预览，确认后才调 import_parsed。**
    price_type 可人工覆盖（purchase/quote/contract），默认按文件名关键词判定。"""
    return services.parse_import_file(path, price_type or None)


@mcp.tool
def import_parsed(rows: list[dict], pending: list[dict] | None = None,
                  default_source: str = "") -> dict:
    """把人工确认后的解析行批量入库（每行字段同 parse_import_file 返回的 rows）。
    价格无法解析的行只建物料不建价；pending 行写入待确认区待人工处理。
    红线：入库行必须带 source 与 price_date。"""
    return services.import_parsed(rows, pending, default_source)


@mcp.tool
def compare_bom(path: str) -> dict:
    """客户 BOM 比对（Skill 2 核心）：读 BOM（xlsx/xls/csv/docx/pdf/图片）→ 逐行三级匹配
    → 按规则取价（purchase>contract>quote，同类型取最新）→ 单位换算 → 汇总。

    返回 results（每行：status exact/alias/fuzzy/none、命中物料、价格、换算、异常、候选）
    与 summary（有价行覆盖率、合计、分类小计）。

    **红线**：fuzzy 候选不带价，必须人工确认后调 add_alias 回流再重新比对；
    none 行价格留空，绝不能估算或填 0。"""
    return _bom.compare_bom(path)


@mcp.tool
def export_cost_sheet(path: str, out_path: str = "") -> dict:
    """比对并导出成本清单 Excel（主表 + 未匹配清单 + 口径异常三区）。
    返回生成的文件路径与汇总；out_path 为空时保存到用户主目录。"""
    return _bom.export_cost_sheet(path, out_path or None)


@mcp.tool
def load_bom(path: str) -> dict:
    """只读预览 BOM（不比对）：返回解析出的行项目与无法解析的行，用于先确认列识别是否正确。"""
    return _bom.load_bom(path)


# ---------------- 待确认区 ----------------

@mcp.tool
def list_pending(limit: int = 50) -> list:
    """列出待人工确认条目（解析低把握/缺价/冲突项）。"""
    return services.list_pending(limit)


@mcp.tool
def confirm_pending(pending_id: int, item_id: int | None = None, model: str | None = None,
                    brand: str = "", category: str = "", spec: str = "", unit: str = "个",
                    add_price: bool = True, add_alias: bool = True) -> dict:
    """确认待确认条目：绑定/新建物料、按需补价格、原始叫法写回别名。"""
    return services.confirm_pending(
        pending_id, item_id, model, brand, category, spec, unit, add_price, add_alias
    )


@mcp.tool
def add_pending(raw_text: str, model: str = "", brand: str = "", category: str = "",
                spec: str = "", unit: str = "", price: float | None = None,
                qty: float | None = None, vendor: str = "", price_date: str = "",
                price_type: str = "", source: str = "") -> dict:
    """把无法可靠解析的条目放入待确认区（人工兜底入口）。"""
    return services.add_pending(
        raw_text, model, brand, category, spec, unit, price, qty, vendor, price_date,
        price_type, source,
    )


# ---------------- 维护 ----------------

@mcp.tool
def list_items(keyword: str = "", status: str = "active", page: int = 1) -> dict:
    """物料分页列表（可按关键字过滤）。"""
    return services.list_items(keyword, status, page)


@mcp.tool
def deactivate_item(item_id: int) -> dict:
    """停用物料（软删除，价格历史保留可追溯）。"""
    return services.deactivate_item(item_id)


@mcp.tool
def upsert_conversion(from_unit: str, to_unit: str, factor: float, category: str = "") -> dict:
    """登记/更新单位换算（如 箱→米 factor=305）。比对输出自动使用。"""
    return services.upsert_conversion(from_unit, to_unit, factor, category)


@mcp.tool
def stats() -> dict:
    """库统计：物料数/价格记录数/别名数/待确认数/最近录入。"""
    return services.stats()
