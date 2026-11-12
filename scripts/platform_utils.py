#!/usr/bin/env python3
"""跨平台辅助模块：路径、启动器生成、系统判定。

Windows：%USERPROFILE%\\procurement-system 与 %USERPROFILE%\\procurement-data
macOS/Linux：~/procurement-system 与 ~/procurement-data

设计目标：同一套 server 代码在三个平台行为一致，差异只收敛在本文件。
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

OS_NAME = "Windows" if IS_WINDOWS else ("macOS" if IS_MACOS else "Linux")

DEFAULT_PORT = 8000


def default_data_dir() -> Path:
    """数据目录（可用 PROC_DATA_DIR 覆盖）。"""
    env = os.environ.get("PROC_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / "procurement-data"


def default_code_dir() -> Path:
    """代码安装目录（可用 PROC_CODE_DIR 覆盖）。"""
    env = os.environ.get("PROC_CODE_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / "procurement-system"


def venv_python(code_dir: Path) -> Path:
    """虚拟环境中的 python 可执行文件路径。"""
    if IS_WINDOWS:
        return code_dir / ".venv" / "Scripts" / "python.exe"
    return code_dir / ".venv" / "bin" / "python"


def venv_pip(code_dir: Path) -> Path:
    if IS_WINDOWS:
        return code_dir / ".venv" / "Scripts" / "pip.exe"
    return code_dir / ".venv" / "bin" / "pip"


def find_python() -> str:
    """找一个 Python 3.10+ 解释器。"""
    candidates = []
    if IS_WINDOWS:
        candidates = ["python", "py"]
    else:
        candidates = ["python3.13", "python3.12", "python3.11", "python3.10", "python3"]
    for c in candidates:
        exe = shutil.which(c)
        if not exe:
            continue
        if sys.executable and Path(exe).resolve() == Path(sys.executable).resolve():
            return sys.executable
        return exe
    return sys.executable or "python3"


def open_command() -> list[str] | None:
    """打开浏览器的命令（跨平台）。"""
    if IS_WINDOWS:
        return ["cmd", "/c", "start", ""]
    if IS_MACOS:
        return ["open"]
    return ["xdg-open"]


def path_join_source(*parts: str) -> str:
    """生成来源字符串（spec 约定：文件路径#sheet#行号）。"""
    return "#".join(str(p) for p in parts if p not in (None, ""))


def summary() -> dict:
    return {
        "os": OS_NAME,
        "platform": platform.platform(),
        "data_dir": str(default_data_dir()),
        "code_dir": str(default_code_dir()),
        "python": sys.executable,
        "port": int(os.environ.get("PROC_PORT", DEFAULT_PORT)),
    }
