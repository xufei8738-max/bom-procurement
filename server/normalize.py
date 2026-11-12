"""型号归一与数值清洗。

规则来源：docs/procurement-system-spec.md §9.4
- model_key(): 全角→半角、大写、去空格/连字符/下划线、去噪声词 —— 仅用于召回
- clean_price(): 货币符号/千分位/区间价/文本价
- clean_qty(): 数量清洗
"""

from __future__ import annotations

import re
import unicodedata

# 归一后要剔除的噪声词（中文/英文常见营销与口径词）
NOISE_WORDS = [
    "全新", "原装", "正品", "行货", "国产", "进口", "含税", "不含税", "未税",
    "国标", "免运费", "包邮", "现货", "热销", "优质",
]
# 允许型保留的字符类（字母数字 + 中文 + 少量技术符号）
_TECH_KEEP = re.compile(r"[A-Z0-9\u4e00-\u9fff.+/×Ωμ#]")


def _to_halfwidth(s: str) -> str:
    """全角转半角。"""
    out = []
    for ch in s or "":
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def strip_noise(s: str) -> str:
    """去掉噪声词。"""
    text = s or ""
    for w in NOISE_WORDS:
        if w:
            text = text.replace(w, "")
    return text


def model_key(model: str) -> str:
    """型号归一键：全角→半角 → 大写 → 去 [-_ 空格] → 去噪声词 → 过滤杂字符。

    仅用于索引/召回，不用于最终判定（见红线：宁可多召回，判定靠人）。
    OCR 场景补充：剥离行首粘连的序号（如「2 IPC-HDBW2231R」→「IPC-HDBW2231R」；
    仅当序号后跟空格+拉丁字母，避免误伤「305米」这类以数字开头的真实型号）。
    """
    if not model:
        return ""
    s = _to_halfwidth(str(model))
    s = unicodedata.normalize("NFKC", s)
    s = s.upper()
    s = re.sub(r"^\d{1,3}\s+(?=[A-Za-z])", "", s)   # OCR 行首序号残留
    # 去 separators
    s = re.sub(r"[-_—–\s]+", "", s)
    s = strip_noise(s)
    # 只保留字母数字、中文与技术符号，去掉广告括注之类
    s = "".join(ch for ch in s if _TECH_KEEP.match(ch))
    return s


PRICE_PATTERNS = [
    # 区间价：12~15 / 12-15 / 12—15（择低与高；注意先于单值匹配）
    (re.compile(r"(\d+(?:\.\d+)?)\s*[~～\-—–]\s*(\d+(?:\.\d+)?)"), "range"),
]


def clean_price(raw) -> dict:
    """清洗价格字段。

    返回 {status: ok|range|none, price, price_low, price_high, raw}
    - ok: 单值
    - range: 区间价，保守取上限（成本口径），low/high 都保留
    - none: 面议/待定/空/无法解析
    """
    out = {"status": "none", "price": None, "price_low": None, "price_high": None, "raw": raw}
    if raw is None:
        return out
    text = _to_halfwidth(str(raw)).strip()
    if not text or text in {"/", "-", "—", "面议", "待定", "待询", "询价", "TBD", "N/A", "NA"}:
        return out

    # 去货币符号/单位/千分位
    text = re.sub(r"[¥￥$€£元]", "", text)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)  # 千分位
    text = text.replace("，", ",").replace(" ", "")

    for pat, kind in PRICE_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                lo = float(m.group(1))
                hi = float(m.group(2))
                if lo > hi:
                    lo, hi = hi, lo
                out.update(status="range", price=hi, price_low=lo, price_high=hi)
                return out
            except ValueError:
                pass

    m = re.search(r"\d+(?:\.\d+)?", text)
    if m:
        out.update(status="ok", price=float(m.group()))
        return out
    return out


def clean_qty(raw) -> dict:
    """清洗数量字段。返回 {status, qty}"""
    if raw is None:
        return {"status": "none", "qty": None}
    if isinstance(raw, (int, float)):
        try:
            return {"status": "ok", "qty": float(raw)}
        except (TypeError, ValueError):
            return {"status": "none", "qty": None}
    text = _to_halfwidth(str(raw)).strip()
    m = re.search(r"\d+(?:\.\d+)?", text)
    if m:
        return {"status": "ok", "qty": float(m.group())}
    return {"status": "none", "qty": None}


COMMON_UNITS = [
    "台", "个", "只", "套", "件", "箱", "卷", "盘", "包", "米", "米/M", "根",
    "对", "条", "块", "片", "张", "组", "副", "M", "PCS", "PC", "SET", "KIT",
]


def extract_unit(raw) -> str | None:
    """从字符串尾部抽取计量单位（如 "超五类网线 305米/箱" → 箱）。"""
    if not raw:
        return None
    text = _to_halfwidth(str(raw)).strip()
    # 优先匹配 305米/箱 这类包装形式
    m = re.search(r"(\d+)\s*([米M])\s*/\s*(箱|卷|盘|包|根|条)", text)
    if m:
        return m.group(3)
    # 末尾单位
    tail = text[-4:]
    for u in sorted(COMMON_UNITS, key=len, reverse=True):
        if tail.endswith(u):
            return u
    return None


def price_type_cn(t: str) -> str:
    return {"purchase": "采购成本", "quote": "报价参考", "contract": "合同价"}.get(t, t or "")


def tax_mode_cn(t: str) -> str:
    return {"included": "含税", "excluded": "未税", "unknown": "税口径未知"}.get(t, t or "")
