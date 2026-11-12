"""SQLite 数据层：建表、连接、配置（口令）、基础 CRUD。

数据目录约定（spec §10.3）：
- 环境变量 PROC_DATA_DIR，默认 ~/procurement-data
- 数据目录与代码目录彻底分离：升级只覆盖代码，数据永不触碰
- 首次初始化时生成随机口令写入 config.json（Web UI 与 MCP 共用校验）
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# 数据目录：PROC_DATA_DIR 覆盖；否则各平台均用用户主目录下 procurement-data
# （Windows: %USERPROFILE%\procurement-data；macOS/Linux: ~/procurement-data）
DATA_DIR = Path(os.environ.get("PROC_DATA_DIR", Path.home() / "procurement-data")).expanduser()
DB_PATH = DATA_DIR / "procurement.db"
CONFIG_PATH = DATA_DIR / "config.json"

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

PRICE_TYPES = ("purchase", "quote", "contract")
TAX_MODES = ("included", "excluded", "unknown")

DDL = """
CREATE TABLE IF NOT EXISTS items (
  item_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  model_raw  TEXT NOT NULL,
  model_norm TEXT NOT NULL,
  brand      TEXT DEFAULT '',
  category   TEXT DEFAULT '',
  spec       TEXT DEFAULT '',
  unit       TEXT DEFAULT '个',
  status     TEXT DEFAULT 'active' CHECK(status IN ('active','inactive')),
  created_at TEXT DEFAULT (datetime('now','localtime')),
  updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_items_norm ON items(model_norm);

CREATE TABLE IF NOT EXISTS prices (
  price_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id    INTEGER NOT NULL REFERENCES items(item_id),
  price      REAL NOT NULL,
  price_type TEXT NOT NULL CHECK(price_type IN ('purchase','quote','contract')),
  tax_mode   TEXT DEFAULT 'unknown' CHECK(tax_mode IN ('included','excluded','unknown')),
  qty        REAL,
  unit       TEXT,
  vendor     TEXT DEFAULT '',
  price_date TEXT NOT NULL,
  source     TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_prices_item ON prices(item_id, price_date DESC);

CREATE TABLE IF NOT EXISTS aliases (
  alias_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  alias_text   TEXT NOT NULL,
  alias_norm   TEXT NOT NULL,
  item_id      INTEGER NOT NULL REFERENCES items(item_id),
  source       TEXT DEFAULT '',
  confirmed_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_aliases_norm ON aliases(alias_norm);

CREATE TABLE IF NOT EXISTS unit_conversions (
  conv_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  from_unit TEXT NOT NULL,
  to_unit   TEXT NOT NULL,
  factor    REAL NOT NULL,
  category  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pending (
  pending_id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_text   TEXT NOT NULL,
  model      TEXT DEFAULT '',
  brand      TEXT DEFAULT '',
  category   TEXT DEFAULT '',
  spec       TEXT DEFAULT '',
  unit       TEXT DEFAULT '',
  price      REAL,
  qty        REAL,
  vendor     TEXT DEFAULT '',
  price_date TEXT DEFAULT '',
  price_type TEXT DEFAULT '',
  source     TEXT DEFAULT '',
  status     TEXT DEFAULT 'open' CHECK(status IN ('open','resolved','dismissed')),
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending(status);
"""


def get_config() -> dict:
    """读取（必要时初始化）数据目录与配置。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = {"token": "", "port": int(os.environ.get("PROC_PORT", "8000"))}
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(16)
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        # 同目录放一份人类可读说明
        hint = DATA_DIR / "接口口令.txt"
        hint.write_text(
            f"采购系统接口口令（Web 界面与 MCP 连接共用）：\n{cfg['token']}\n"
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"丢失可删除 config.json 后重启重新生成。\n",
            encoding="utf-8",
        )
    return cfg


@contextmanager
def db():
    """打开一个短事务连接。用法: with db() as con: ..."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    with db() as con:
        con.executescript(DDL)


# ---------------- 基础 CRUD ----------------

def insert_item(con, model_raw: str, brand="", category="", spec="", unit="个") -> tuple[int, bool]:
    """插入物料；model_norm 查重命中则返回已有 item_id（duplicate=True）。"""
    from server.normalize import model_key
    key = model_key(model_raw)
    if not key:
        raise ValueError("型号为空或清洗后为空")
    row = con.execute(
        "SELECT item_id FROM items WHERE model_norm=? AND status='active'", (key,)
    ).fetchone()
    if row:
        return int(row["item_id"]), True
    cur = con.execute(
        "INSERT INTO items(model_raw, model_norm, brand, category, spec, unit) VALUES(?,?,?,?,?,?)",
        (str(model_raw).strip(), key, brand or "", category or "", spec or "", unit or "个"),
    )
    return int(cur.lastrowid), False


def update_item(con, item_id: int, **fields) -> bool:
    allowed = {"model_raw", "brand", "category", "spec", "unit", "status"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=?")
            vals.append(v)
    if fields.get("model_raw"):
        from server.normalize import model_key
        sets.append("model_norm=?")
        vals.append(model_key(fields["model_raw"]))
    if not sets:
        return False
    sets.append("updated_at=datetime('now','localtime')")
    vals.append(item_id)
    con.execute(f"UPDATE items SET {', '.join(sets)} WHERE item_id=?", vals)
    return True


def insert_price(con, item_id: int, price: float, price_type: str, price_date: str,
                 tax_mode="unknown", qty=None, unit=None, vendor="", source="") -> int:
    if price_type not in PRICE_TYPES:
        raise ValueError(f"price_type 必须是 {PRICE_TYPES} 之一，收到: {price_type!r}")
    if tax_mode not in TAX_MODES:
        raise ValueError(f"tax_mode 必须是 {TAX_MODES} 之一，收到: {tax_mode!r}")
    price = float(price)
    if price < 0:
        raise ValueError("价格不能为负")
    cur = con.execute(
        "INSERT INTO prices(item_id, price, price_type, tax_mode, qty, unit, vendor, price_date, source) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (item_id, price, price_type, tax_mode, qty, unit, vendor or "", price_date, source or ""),
    )
    return int(cur.lastrowid)


def item_row_to_dict(r) -> dict:
    return {
        "item_id": r["item_id"], "model_raw": r["model_raw"], "model_norm": r["model_norm"],
        "brand": r["brand"], "category": r["category"], "spec": r["spec"],
        "unit": r["unit"], "status": r["status"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def price_row_to_dict(r) -> dict:
    return {
        "price_id": r["price_id"], "item_id": r["item_id"], "price": r["price"],
        "price_type": r["price_type"], "tax_mode": r["tax_mode"], "qty": r["qty"],
        "unit": r["unit"], "vendor": r["vendor"], "price_date": r["price_date"],
        "source": r["source"], "created_at": r["created_at"],
    }


def stats() -> dict:
    with db() as con:
        items = con.execute("SELECT COUNT(*) c FROM items WHERE status='active'").fetchone()["c"]
        prices = con.execute("SELECT COUNT(*) c FROM prices").fetchone()["c"]
        aliases = con.execute("SELECT COUNT(*) c FROM aliases").fetchone()["c"]
        pending = con.execute("SELECT COUNT(*) c FROM pending WHERE status='open'").fetchone()["c"]
        last = con.execute(
            "SELECT MAX(created_at) m FROM (SELECT MAX(created_at) created_at FROM items UNION ALL "
            "SELECT MAX(created_at) FROM prices)"
        ).fetchone()["m"]
        return {
            "items": items, "prices": prices, "aliases": aliases, "pending": pending,
            "last_entry": last, "db_path": str(DB_PATH),
        }
