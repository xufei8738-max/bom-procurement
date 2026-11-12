"""Skill 1 端到端：MCP 工具层 parse_import_file → import_parsed 闭环。"""
import asyncio
import json

import openpyxl
from fastmcp import Client

from server import db
from server.mcp_tools import mcp


def unwrap(res):
    """FastMCP 4.x：dict 返回 → res.data；list 返回 → data 为 None，JSON 在 content[0].text。"""
    if isinstance(res, list):
        res = res[0]
    data = getattr(res, "data", None)
    if data is not None:
        return data
    return json.loads(res.content[0].text)


def _xlsx(tmp_path, rows, name="采购清单A.xlsx"):
    p = tmp_path / name
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(p)
    return p


def _clean_db():
    with db.db() as con:
        for t in ("prices", "aliases", "pending", "unit_conversions", "items"):
            con.execute(f"DELETE FROM {t}")
            con.execute("DELETE FROM sqlite_sequence WHERE name=?", (t,))


def test_mcp_parse_and_import_roundtrip(tmp_path):
    _clean_db()
    p = _xlsx(tmp_path, [
        ["型号", "品牌", "规格", "数量", "单位", "含税单价", "供应商", "日期"],
        ["CAM-100", "海康", "400万筒机", 5, "台", "800", "甲", "2025-02-01"],
        ["CAM-200", "大华", "200万半球", 8, "台", "450", "乙", "2025-02-02"],
    ])

    async def run():
        async with Client(mcp) as c:
            # 1) 解析预览（不落库）
            res = await c.call_tool("parse_import_file", {"path": str(p)})
            preview = unwrap(res)
            assert preview["price_type"] == "purchase"
            assert preview["summary"]["parsed"] == 2
            assert len(preview["rows"]) == 2
            assert preview["rows"][0]["price"] == 800.0
            assert preview["rows"][0]["source"].endswith("#Sheet#2")

            # 预览阶段库内不应有物料（红线：不预览不入库）
            st = await c.call_tool("stats", {})
            stats = unwrap(st)
            assert stats["items"] == 0

            # 2) 确认入库
            res2 = await c.call_tool("import_parsed", {
                "rows": preview["rows"], "pending": preview["pending"],
            })
            out = unwrap(res2)
            assert out["summary"]["ok"] == 2 and out["summary"]["fail"] == 0

            # 3) 检索命中并带价
            sr = await c.call_tool("search_items", {"query": "cam-100"})
            s = unwrap(sr)
            assert s["status"] == "exact"       # 大小写差异已归一
            assert s["results"][0]["latest_price"]["price"] == 800.0

    asyncio.run(run())


def test_mcp_import_reports_pending(tmp_path):
    _clean_db()
    p = _xlsx(tmp_path, [
        ["型号", "单价"],
        ["OK-1", "100"],
        ["", "500"],          # 空型号 → pending
    ])

    async def run():
        async with Client(mcp) as c:
            res = await c.call_tool("parse_import_file", {"path": str(p)})
            preview = unwrap(res)
            assert preview["summary"]["parsed"] == 1
            assert preview["summary"]["pending"] == 1

            res2 = await c.call_tool("import_parsed",
                                     {"rows": preview["rows"], "pending": preview["pending"]})
            out = unwrap(res2)
            assert out["summary"]["pending_added"] == 1

            lp = await c.call_tool("list_pending", {})
            pend = unwrap(lp)
            assert len(pend) == 1

    asyncio.run(run())


def test_mcp_tool_count_includes_new_tools():
    async def run():
        async with Client(mcp) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}
            assert {"parse_import_file", "import_parsed"} <= names
            assert {"compare_bom", "export_cost_sheet", "load_bom"} <= names
            assert len(tools) == 19
    asyncio.run(run())
