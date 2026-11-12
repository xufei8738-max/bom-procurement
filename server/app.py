"""FastAPI 应用：Web 界面 API + MCP 端点（同一进程，同一 SQLite）。

- /            单文件界面（webui/index.html）
- /api/health  免口令健康检查（供 OneAgent 探测"系统是否启动"）
- /api/*       界面用 JSON API（Bearer 口令校验）
- /mcp         FastMCP streamable HTTP（Bearer 口令校验）
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Windows 控制台编码兜底：日志里的中文不能把进程搞崩
for _s in ("stdout", "stderr"):
    _stream = getattr(sys, _s, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 重定向流无 reconfigure 时忽略
            pass

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from server import db, services
from server.mcp_tools import mcp

WEBUI_DIR = Path(__file__).parent / "webui"

# FastMCP http_app 需在父应用构造前创建（lifespan 引用它）
mcp_app = mcp.http_app(path="/mcp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.get_config()   # 确保数据目录与口令就绪
    db.init_db()
    # FastMCP streamable HTTP 需要 SessionManager 生命周期随父应用启动
    async with mcp_app.lifespan(mcp_app):
        yield


app = FastAPI(title="bom-procurement", lifespan=lifespan)


# ---------------- 口令校验 ----------------

def check_token(request: Request) -> None:
    """Authorization: Bearer <token>；Web UI 首次也可用 ?token= 引导。"""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = request.query_params.get("token", "")
    cfg = db.get_config()
    if token != cfg["token"]:
        raise HTTPException(status_code=401, detail="口令错误：请查看数据目录下 接口口令.txt")


# ---------------- 健康检查（免口令） ----------------

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "bom-procurement", "version": "0.1.0"}


# ---------------- Web API ----------------

@app.get("/api/stats")
def api_stats(_: None = Depends(check_token)):
    return services.stats()


@app.get("/api/items")
def api_items(keyword: str = "", status: str = "active", page: int = 1,
              _: None = Depends(check_token)):
    return services.list_items(keyword, status, page)


@app.get("/api/items/{item_id}")
def api_get_item(item_id: int, _: None = Depends(check_token)):
    return services.get_item(item_id)


@app.post("/api/items")
async def api_add_item(req: dict, _: None = Depends(check_token)):
    return services.upsert_item(
        req.get("model", ""), req.get("brand", ""), req.get("category", ""),
        req.get("spec", ""), req.get("unit", "个"),
    )


@app.post("/api/items/{item_id}")
async def api_update_item(item_id: int, req: dict, _: None = Depends(check_token)):
    fields = {k: v for k, v in req.items() if k in
              {"model_raw", "brand", "category", "spec", "unit", "status"}}
    return services.update_item(item_id, **fields)


@app.post("/api/items/{item_id}/deactivate")
def api_deactivate(item_id: int, _: None = Depends(check_token)):
    return services.deactivate_item(item_id)


@app.post("/api/prices")
async def api_add_price(req: dict, _: None = Depends(check_token)):
    return services.upsert_price(
        item_id=req.get("item_id"), model=req.get("model"), price=req.get("price"),
        price_type=req.get("price_type", "purchase"), price_date=req.get("price_date", ""),
        tax_mode=req.get("tax_mode", "unknown"), qty=req.get("qty"),
        unit=req.get("price_unit") or req.get("unit"), vendor=req.get("vendor", ""),
        source=req.get("source", ""),
    )


@app.get("/api/prices/{item_id}")
def api_prices(item_id: int, _: None = Depends(check_token)):
    return services.get_prices(item_id)


@app.post("/api/search")
async def api_search(req: dict, _: None = Depends(check_token)):
    return services.search_items(
        req.get("query", ""), req.get("brand", ""), req.get("category", ""),
        int(req.get("limit", 5)),
    )


@app.get("/api/aliases")
def api_aliases(keyword: str = "", _: None = Depends(check_token)):
    return services.list_aliases(keyword)


@app.post("/api/aliases")
async def api_add_alias(req: dict, _: None = Depends(check_token)):
    return services.add_alias(req.get("alias_text", ""), int(req.get("item_id", 0)),
                              req.get("source", ""))


@app.get("/api/pending")
def api_pending(limit: int = 50, _: None = Depends(check_token)):
    return services.list_pending(limit)


@app.post("/api/pending")
async def api_add_pending(req: dict, _: None = Depends(check_token)):
    return services.add_pending(
        req.get("raw_text", ""), req.get("model", ""), req.get("brand", ""),
        req.get("category", ""), req.get("spec", ""), req.get("unit", ""),
        req.get("price"), req.get("qty"), req.get("vendor", ""),
        req.get("price_date", ""), req.get("price_type", ""), req.get("source", ""),
    )


@app.post("/api/pending/{pending_id}/confirm")
async def api_confirm_pending(pending_id: int, req: dict, _: None = Depends(check_token)):
    return services.confirm_pending(
        pending_id, req.get("item_id"), req.get("model"), req.get("brand", ""),
        req.get("category", ""), req.get("spec", ""), req.get("unit", "个"),
        bool(req.get("add_price", True)), bool(req.get("add_alias", True)),
    )


@app.post("/api/pending/{pending_id}/dismiss")
def api_dismiss_pending(pending_id: int, _: None = Depends(check_token)):
    return services.dismiss_pending(pending_id)


@app.get("/api/conversions")
def api_conversions(_: None = Depends(check_token)):
    return services.list_conversions()


@app.post("/api/conversions")
async def api_add_conversion(req: dict, _: None = Depends(check_token)):
    return services.upsert_conversion(
        req.get("from_unit", ""), req.get("to_unit", ""), float(req.get("factor", 0)),
        req.get("category", ""),
    )


# ---------------- 资料导入（预览-确认两步） ----------------

@app.post("/api/parse")
async def api_parse_file(req: dict, _: None = Depends(check_token)):
    """Skill 1 第一步：解析预览（不落库）。req: {path, price_type?}"""
    return services.parse_import_file(req.get("path", ""), req.get("price_type") or None)


@app.post("/api/import")
async def api_import_parsed(req: dict, _: None = Depends(check_token)):
    """Skill 1 第二步：确认入库。req: {rows, pending?, default_source?}"""
    return services.import_parsed(
        req.get("rows", []), req.get("pending"), req.get("default_source", ""),
    )


# ---------------- MCP 挂载 ----------------

mcp_http_app = mcp_app  # 已在文件头创建


@app.middleware("http")
async def mcp_token_guard(request: Request, call_next):
    """/mcp* 请求先过口令，再交给 FastMCP。"""
    if request.url.path.startswith("/mcp"):
        try:
            check_token(request)
        except HTTPException:
            return JSONResponse({"detail": "口令错误：连接采购系统 MCP 需带 Bearer 口令"}, 401)
    return await call_next(request)


# 首页路由必须在 root mount 之前注册，否则会被 mount 吞掉
@app.get("/")
def index():
    return FileResponse(WEBUI_DIR / "index.html")


app.mount("/", mcp_app, name="mcp")
