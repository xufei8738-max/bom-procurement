"""HTTP 层测试：口令校验、Web API、/mcp MCP 端点握手。"""
import pytest
from fastapi.testclient import TestClient

from server import db, services
from server.app import app

TOKEN = db.get_config()["token"]
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="session")
def client():
    """必须用上下文管理器进入，才会触发 lifespan（FastMCP SessionManager 在其中初始化）。"""
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean():
    with db.db() as con:
        for t in ("prices", "aliases", "pending", "unit_conversions", "items"):
            con.execute(f"DELETE FROM {t}")
            con.execute("DELETE FROM sqlite_sequence WHERE name=?", (t,))
    yield


def test_health_no_token(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_api_requires_token(client):
    assert client.get("/api/stats").status_code == 401
    assert client.get("/api/stats", headers=AUTH).status_code == 200


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "器件采购库" in r.text


def test_item_crud_flow(client):
    r = client.post("/api/items", json={"model": "AP-100", "brand": "TP", "category": "网络/AP",
                                        "spec": "WiFi6", "unit": "台"}, headers=AUTH)
    iid = r.json()["item_id"]

    r = client.get(f"/api/items/{iid}", headers=AUTH)
    assert r.json()["model_raw"] == "AP-100"

    r = client.post(f"/api/items/{iid}", json={"spec": "WiFi6 AX3000"}, headers=AUTH)
    assert r.json()["updated"]

    r = client.get("/api/items?keyword=AP-100", headers=AUTH)
    assert r.json()["total"] == 1

    client.post(f"/api/items/{iid}/deactivate", headers=AUTH)
    r = client.get("/api/items?status=inactive", headers=AUTH)
    assert r.json()["total"] == 1


def test_price_flow_and_search(client):
    iid = client.post("/api/items", json={"model": "CAM-77"}, headers=AUTH).json()["item_id"]
    r = client.post("/api/prices", json={
        "item_id": iid, "price": 399.0, "price_type": "purchase",
        "price_date": "2026-01-10", "vendor": "代理商A", "source": "合同.pdf#5",
    }, headers=AUTH)
    assert r.json()["price_id"]

    rows = client.get(f"/api/prices/{iid}", headers=AUTH).json()
    assert rows[0]["price"] == 399.0 and rows[0]["source"] == "合同.pdf#5"

    r = client.post("/api/search", json={"query": "CAM-77"}, headers=AUTH).json()
    assert r["status"] == "exact"
    assert r["results"][0]["latest_price"]["price"] == 399.0


def test_pending_flow_via_api(client):
    client.post("/api/pending", json={"raw_text": "未知型号 XX-?", "model": "XX-?"}, headers=AUTH)
    pend = client.get("/api/pending", headers=AUTH).json()
    assert len(pend) == 1
    r = client.post(f"/api/pending/{pend[0]['pending_id']}/confirm",
                    json={"model": "XX-?"}, headers=AUTH).json()
    assert r["item_id"]
    assert client.get("/api/pending", headers=AUTH).json() == []


def test_zero_price_rejected_at_api(client):
    r = client.post("/api/prices", json={"model": "ZP-9", "price": 0}, headers=AUTH)
    assert "疑似异常" in r.json()["error"]


def test_mcp_requires_token(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2025-03-26",
                                             "capabilities": {},
                                             "clientInfo": {"name": "t", "version": "0"}}})
    assert r.status_code == 401


def test_mcp_initialize_handshake(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2025-03-26",
                                             "capabilities": {},
                                             "clientInfo": {"name": "pytest", "version": "0"}}},
                    headers={**AUTH, "Accept": "application/json, text/event-stream"})
    assert r.status_code == 200
    body = r.text
    assert "jsonrpc" in body or "serverInfo" in body or "event" in body
    # 会话头应存在（streamable HTTP 模式）
    assert r.headers.get("mcp-session-id") or "endpoint" in body


def test_mcp_tools_list_via_inmemory_client():
    """用 FastMCP 内存客户端验证工具注册数量与红线说明。"""
    import asyncio

    from fastmcp import Client

    from server.mcp_tools import mcp

    async def run():
        async with Client(mcp) as c:
            tools = await c.list_tools()
            names = [t.name for t in tools]
            assert len(tools) >= 13
            for expected in ("search_items", "get_prices", "upsert_price", "bulk_import",
                             "add_alias", "list_pending", "confirm_pending", "stats"):
                assert expected in names
            # 真正调一次工具，确保端到端可执行
            res = await c.call_tool("upsert_item", {"model": "MCP-T1"})
            data = res[0].data if isinstance(res, list) else res.data
            assert data["item_id"] > 0

    asyncio.run(run())
