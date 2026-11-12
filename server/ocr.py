"""OCR 后端抽象 + PDF 文本层抽取 + 表格结构还原（P1.5）。

后端优先级（自动选择，缺则明确报错并给安装指引，绝不静默返回空）：
1. macOS Vision（系统自带，中文质量高，零额外依赖）→ 走 scripts/ocr_vision.swift
2. RapidOCR（纯 pip：pip install rapidocr-onnxruntime，Windows/Linux/macOS 通用）
3. tesseract（命令行引擎，需单独安装；用 TSV 模式拿词坐标）

统一输出：
- ocr_words(paths) → {"ok", "pages": [{"path","width","height","words":[{text,x0,y0,x1,y1,conf}],
                                       "raw_text"}], "backend", "confidence", "error"?}
- words_to_rows(words, width) → 二维表（按 y 聚行、按 x 间隙切列）
- text_to_rows(text) → 二维表（无坐标时的降级：按多空格/制表符切列）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SWIFT_SCRIPT = HERE.parent / "scripts" / "ocr_vision.swift"

IS_MACOS = sys.platform == "darwin"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".heic"}


# ---------------- 后端探测 ----------------

def available_backends() -> list[str]:
    """返回可用后端名列表（供诊断与测试跳过判断）。"""
    out = []
    if _vision_binary():
        out.append("vision")
    if _rapidocr_available():
        out.append("rapidocr")
    if shutil.which("tesseract"):
        out.append("tesseract")
    return out


def _vision_binary() -> str | None:
    """macOS Vision 可用性 → 返回 swift 可执行路径。"""
    if not (IS_MACOS and SWIFT_SCRIPT.exists()):
        return None
    return shutil.which("swift")


def _rapidocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _no_backend_error() -> str:
    return (
        "没有可用的 OCR 后端，无法识别图片/扫描件。安装任一即可：\n"
        "  macOS：系统自带 Vision（需 swift，一般已具备）\n"
        "  通用：pip install rapidocr-onnxruntime\n"
        "  通用：安装 tesseract 引擎（含中文包 chi_sim）"
    )


# ---------------- 主入口 ----------------

def ocr_words(paths: list[str | Path]) -> dict:
    """对一批图片做 OCR。返回结构化结果（见模块 docstring）。"""
    paths = [Path(p) for p in paths]
    for p in paths:
        if not p.exists():
            return {"ok": False, "error": f"文件不存在：{p}", "pages": []}
    if not paths:
        return {"ok": True, "pages": [], "backend": None, "confidence": None}

    backends = available_backends()
    if not backends:
        return {"ok": False, "error": _no_backend_error(), "pages": []}

    errors: list[str] = []
    for backend in backends:
        try:
            if backend == "vision":
                pages = _ocr_vision(paths)
            elif backend == "rapidocr":
                pages = _ocr_rapidocr(paths)
            else:
                pages = _ocr_tesseract(paths)
        except Exception as e:  # noqa: BLE001 - 单个后端失败则换下一个
            errors.append(f"{backend}: {e}")
            continue
        confs = [w["conf"] for pg in pages for w in pg["words"] if w.get("conf") is not None]
        return {
            "ok": True,
            "pages": pages,
            "backend": backend,
            "confidence": round(sum(confs) / len(confs), 3) if confs else None,
            "errors": errors,
        }
    return {"ok": False, "error": "所有 OCR 后端均失败：\n" + "\n".join(errors), "pages": []}


# ---------------- 后端实现 ----------------

def _ocr_vision(paths: list[Path]) -> list[dict]:
    """macOS Vision：swift 脚本输出 {path: [{text, conf, box:[x0,y0,x1,y1]}]}（坐标已归一化）。"""
    swift = _vision_binary()
    proc = subprocess.run(
        [swift, str(SWIFT_SCRIPT), *[str(p) for p in paths]],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:300] or "swift 执行失败")
    raw = json.loads(proc.stdout or "{}")

    pages = []
    for p in paths:
        words = []
        for item in raw.get(str(p), []):
            b = item.get("box") or [0, 0, 0, 0]
            words.append({
                "text": item.get("text", ""), "conf": item.get("conf"),
                "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3],
            })
        pages.append({
            "path": str(p), "width": 1.0, "height": 1.0,
            "words": words, "raw_text": "\n".join(w["text"] for w in words),
        })
    return pages


def _ocr_rapidocr(paths: list[Path]) -> list[dict]:
    """RapidOCR：四点坐标 → 轴对齐 box（像素），width/height 为图片像素尺寸。"""
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    pages = []
    for p in paths:
        result, _ = engine(str(p))
        with Image.open(p) as im:
            w, h = im.size
        words = []
        for item in result or []:
            pts, text, score = item[0], item[1], item[2]
            xs = [pt[0] for pt in pts]
            ys = [pt[1] for pt in pts]
            words.append({"text": text, "conf": float(score),
                          "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)})
        pages.append({"path": str(p), "width": w, "height": h, "words": words,
                      "raw_text": "\n".join(x["text"] for x in words)})
    return pages


def _parse_tesseract_tsv(tsv: str) -> list[dict]:
    """解析 tesseract tsv 输出为词坐标列表（level=5 是词）。"""
    words = []
    lines = tsv.splitlines()
    if not lines:
        return words
    header = lines[0].split("\t")
    try:
        idx = {k: header.index(k) for k in ("level", "left", "top", "width", "height", "conf", "text")}
    except ValueError:
        return words
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < len(header):
            continue
        if parts[idx["level"]] != "5":
            continue
        text = parts[idx["text"]].strip()
        if not text:
            continue
        try:
            conf = float(parts[idx["conf"]])
        except ValueError:
            conf = None
        left, top = float(parts[idx["left"]]), float(parts[idx["top"]])
        w, h = float(parts[idx["width"]]), float(parts[idx["height"]])
        words.append({"text": text, "conf": (conf / 100 if conf and conf >= 0 else None),
                      "x0": left, "y0": top, "x1": left + w, "y1": top + h})
    return words


def _ocr_tesseract(paths: list[Path]) -> list[dict]:
    from PIL import Image

    pages = []
    for p in paths:
        proc = subprocess.run(
            ["tesseract", str(p), "stdout", "-l", "chi_sim+eng", "--psm", "6", "tsv"],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip()[:200] or "tesseract 执行失败")
        words = _parse_tesseract_tsv(proc.stdout)
        with Image.open(p) as im:
            w, h = im.size
        pages.append({"path": str(p), "width": w, "height": h, "words": words,
                      "raw_text": "\n".join(x["text"] for x in words)})
    return pages


# ---------------- 表格还原 ----------------

def words_to_rows(words: list[dict], width: float | None = None,
                  row_tol: float = 0.60, col_tol: float = 1.0) -> list[list[str]]:
    """词坐标 → 二维表（列带聚类算法，跨行对齐列位置）。

    步骤：
    1) 坐标统一（Vision 归一化 0~1 / tesseract 像素 → 统一放大 1000）
    2) 按 y 中心聚行（容差 = 行高 × row_tol）
    3) 行内预合并：间隙 < 字高×0.6 的相邻词视为同格碎片（如「400万」+「网络筒机」）
    4) 全页按词起点 x0 聚类成「列带」（容差 = 中位字高 × col_tol）
    5) 每个词归入覆盖的列带；跨多列的词（如表头「序号 型号」被 OCR 合并成一个观察）
       按空格拆分文本分 配到各列——实现跨行列对齐
    """
    items = [w for w in words if str(w.get("text", "")).strip()]
    if not items:
        return []

    normalized = all(float(w.get("x1", 0)) <= 1.01 and float(w.get("y1", 0)) <= 1.01
                     for w in items)
    scale = 1000.0 if normalized else 1.0

    norm = []
    for w in items:
        x0, x1 = float(w["x0"]) * scale, float(w["x1"]) * scale
        y0, y1 = float(w["y0"]) * scale, float(w["y1"]) * scale
        norm.append({"text": str(w["text"]).strip(), "x0": x0, "x1": x1,
                     "yc": (y0 + y1) / 2, "h": max(y1 - y0, 1.0)})

    # 2) 聚行
    norm.sort(key=lambda i: i["yc"])
    rows: list[list[dict]] = []
    for it in norm:
        if rows:
            ref = rows[-1]
            ref_yc = sum(x["yc"] for x in ref) / len(ref)
            ref_h = max(x["h"] for x in ref)
            if abs(it["yc"] - ref_yc) <= ref_h * row_tol:
                ref.append(it)
                continue
        rows.append([it])

    # 3) 行内预合并同格碎片
    merged_rows: list[list[dict]] = []
    for row in rows:
        row.sort(key=lambda i: i["x0"])
        merged: list[dict] = []
        for it in row:
            if merged:
                prev = merged[-1]
                gap = it["x0"] - prev["x1"]
                if gap < 0.6 * max(prev["h"], it["h"]):
                    prev["text"] = f"{prev['text']} {it['text']}"
                    prev["x1"] = max(prev["x1"], it["x1"])
                    prev["h"] = max(prev["h"], it["h"])
                    continue
            merged.append(dict(it))
        merged_rows.append(merged)

    # 4) 列带：按起点 x0 聚类（全页统一，跨行对齐）
    starts = sorted(c["x0"] for row in merged_rows for c in row)
    heights = sorted(c["h"] for row in merged_rows for c in row)
    median_h = heights[len(heights) // 2] if heights else 20.0
    tol = col_tol * median_h
    bands: list[list[float]] = []
    for s in starts:
        if bands and s - bands[-1][-1] <= tol:
            bands[-1].append(s)
        else:
            bands.append([s])
    centers = [sum(b) / len(b) for b in bands]
    if not centers:
        return []

    # 5) 归带 + 跨列文本拆分
    table: list[list[str]] = []
    for row in merged_rows:
        cells: dict[int, list[str]] = {}
        for cand in row:
            covered = [i for i, c in enumerate(centers)
                       if cand["x0"] - tol * 0.5 <= c <= cand["x1"] + tol * 0.5]
            if not covered:
                # 落在列带之外（页边噪声等）→ 就近列
                nearest = min(range(len(centers)),
                              key=lambda i: abs(centers[i] - (cand["x0"] + cand["x1"]) / 2))
                covered = [nearest]
            parts = cand["text"].split()
            if len(covered) >= 2 and len(parts) >= len(covered):
                # 跨列词且文本可按列数拆（如「序号 型号」）：
                # 前 k-1 列各取一词，最后一列收尾剩余词
                k = len(covered)
                for i, band_i in enumerate(covered):
                    chunk = parts[i] if i < k - 1 else " ".join(parts[k - 1:])
                    cells.setdefault(band_i, []).append(chunk)
            else:
                cells.setdefault(covered[0], []).append(cand["text"])
        table.append([" ".join(cells.get(i, [])) for i in range(len(centers))])

    return table


def text_to_rows(text: str) -> list[list[str]]:
    """无坐标时的降级：按 2+ 空格/制表符切列。"""
    out = []
    for line in (text or "").splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        cells = [c.strip() for c in re.split(r"[\t\u3000]{1,}|\s{2,}", line.strip()) if c.strip()]
        out.append(cells or [line.strip()])
    return out


# ---------------- PDF ----------------

def pdf_has_text_layer(pdf_path: str | Path, min_chars_per_page: int = 40) -> bool:
    """判断 PDF 是否有可用文本层（平均每页字符数达标）。"""
    import pymupdf

    with pymupdf.open(str(pdf_path)) as doc:
        n = len(doc)
        if n == 0:
            return False
        total = sum(len((page.get_text() or "").strip()) for page in doc)
        return (total / n) >= min_chars_per_page


def pdf_words_to_tables(pdf_path: str | Path) -> list[tuple[str, list[list]]]:
    """文本层 PDF → 表格列表 [(页标签, rows)]；优先用表格识别，退化为词坐标。"""
    import pymupdf

    out: list[tuple[str, list[list]]] = []
    with pymupdf.open(str(pdf_path)) as doc:
        for pno, page in enumerate(doc, 1):
            extracted = False
            try:
                finder = page.find_tables()
                for t_idx, t in enumerate(getattr(finder, "tables", []) or [], 1):
                    data = t.extract()
                    if data:
                        rows = [[("" if c is None else str(c).strip()) for c in row] for row in data]
                        out.append((f"第{pno}页-表{t_idx}", rows))
                        extracted = True
            except Exception:
                extracted = False

            if extracted:
                continue
            words = page.get_text("words")  # (x0,y0,x1,y1,word,...)
            if words:
                rect = page.rect
                ws = [{"text": w[4], "conf": 1.0,
                       "x0": w[0] / rect.width, "y0": w[1] / rect.height,
                       "x1": w[2] / rect.width, "y1": w[3] / rect.height} for w in words]
                rows = words_to_rows(ws, 1.0)
                if rows:
                    out.append((f"第{pno}页", rows))
    return out


def render_pdf_pages(pdf_path: str | Path, dpi: int = 200, max_pages: int = 20) -> list[Path]:
    """PDF 每页渲染为临时 PNG（扫描件 OCR 用）。"""
    import pymupdf

    out_dir = Path(tempfile.mkdtemp(prefix="proc-pdf-"))
    paths: list[Path] = []
    with pymupdf.open(str(pdf_path)) as doc:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(dpi=dpi)
            p = out_dir / f"page{i + 1}.png"
            pix.save(str(p))
            paths.append(p)
    return paths
