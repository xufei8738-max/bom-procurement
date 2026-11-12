#!/usr/bin/env python3
"""采购系统安装器（跨平台：Windows / macOS / Linux）。

流程（对应方案 §13.3）：
1) 环境检查（Python 3.10+）
2) 布署代码到代码目录（数据目录独立，升级永不触碰）
3) 初始化数据库 + 生成接口口令
4) 自检（启动测试 / 健康检查）
5) 注册开机自启（Windows 计划任务 / macOS launchd）
6) 打印 OneAgent MCP 配置参数

用法：
  python3 scripts/install.py            # 安装
  python3 scripts/install.py --check    # 只做环境检查
  python3 scripts/install.py --no-autostart  # 跳过开机自启注册
  python3 scripts/install.py --uninstall    # 卸载（删自启 + 代码目录；数据目录保留）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.platform_utils import (  # noqa: E402
    IS_WINDOWS, OS_NAME, default_code_dir, default_data_dir,
    find_python, venv_pip, venv_python,
)

ROOT = Path(__file__).resolve().parent.parent          # 仓库根（skill 包内）
REQUIREMENTS = ROOT / "requirements.txt"
SERVER_SRC = ROOT / "server"
MIRRORS = ["https://pypi.tuna.tsinghua.edu.cn/simple", ""]


def log(msg: str):
    print(f"[install] {msg}")


# ---------------- 步骤 1：环境检查 ----------------

def check_python() -> str:
    exe = find_python()
    ver = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip()
    log(f"Python 解释器：{exe}（{ver}）")
    major_minor = sys.version_info[:2]
    if major_minor < (3, 10):
        # 安装脚本自身跑在旧解释器上时，尝试换一个
        alt = None
        for c in ("python3.13", "python3.12", "python3.11", "python3.10"):
            if shutil.which(c):
                alt = c
                break
        if alt:
            log(f"当前解释器版本过低，改用 {alt} 重新执行")
            os.execvp(alt, [alt, str(Path(__file__).resolve())] + sys.argv[1:])
        raise SystemExit(f"[错误] 需要 Python 3.10+，当前 {ver}")
    return exe


# ---------------- 步骤 2：布署代码 ----------------

def deploy_code(code_dir: Path) -> None:
    code_dir.mkdir(parents=True, exist_ok=True)
    for name in ("server", "scripts", "requirements.txt", "README.md", "LICENSE"):
        src = ROOT / name
        dst = code_dir / name
        if not src.exists():
            continue
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "tests", "*.pyc"))
        else:
            shutil.copy2(src, dst)
    log(f"代码已布署到 {code_dir}")


def create_venv_and_install(code_dir: Path, python_exe: str) -> None:
    if venv_python(code_dir).exists():
        log("虚拟环境已存在，跳过创建（更新依赖：pip install -U）")
    else:
        log("创建虚拟环境 ...")
        subprocess.run([python_exe, "-m", "venv", str(code_dir / ".venv")], check=True)
    pip = venv_pip(code_dir)
    last_err = None
    for mirror in MIRRORS:
        cmd = [str(pip), "install", "-q", "-r", str(code_dir / "requirements.txt")]
        if mirror:
            cmd += ["-i", mirror]
        try:
            subprocess.run(cmd, check=True)
            log(f"依赖安装完成（镜像：{mirror or '默认源'}）")
            return
        except subprocess.CalledProcessError as e:
            last_err = e
            log(f"镜像 {mirror or '默认源'} 失败，尝试下一个 ...")
    raise SystemExit(f"[错误] 依赖安装失败：{last_err}")


# ---------------- 步骤 3：初始化 ----------------

def init_db_and_config(data_dir: Path, code_dir: Path) -> dict:
    env = dict(os.environ, PROC_DATA_DIR=str(data_dir))
    subprocess.run([str(venv_python(code_dir)), "-c",
                    "import sys; sys.path.insert(0, r'%s'); "
                    "from server import db; db.get_config(); db.init_db()" % code_dir],
                   check=True, env=env)
    cfg = json.loads((data_dir / "config.json").read_text(encoding="utf-8"))
    token = cfg.get("token", "")
    log(f"数据库与口令就绪：{data_dir}")
    print()
    print("=" * 62)
    print(f"  接口口令（Web 界面与 MCP 共用，请妥善保存）：")
    print(f"  {token}")
    print(f"  明文副本：{data_dir / '接口口令.txt'}")
    print("=" * 62)
    return cfg


# ---------------- 步骤 4：自检 ----------------

def self_test(code_dir: Path, data_dir: Path, port: int) -> bool:
    env = dict(os.environ, PROC_DATA_DIR=str(data_dir), PROC_PORT=str(port))
    proc = subprocess.Popen(
        [str(venv_python(code_dir)), "-m", "uvicorn", "server.app:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(code_dir), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                    if r.status == 200:
                        log("健康检查通过：/api/health 200")
                        return True
            except Exception:
                continue
        log("[警告] 健康检查未通过，服务可能 30 秒内未启动（网络或防火墙原因）")
        return False
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


# ---------------- 步骤 5：开机自启 ----------------

WIN_TASK_NAME = "ProcurementSystemStart"


def register_autostart_windows(code_dir: Path, port: int) -> bool:
    bat = code_dir / "start.bat"
    if not bat.exists():
        log("[警告] start.bat 不存在，跳过自启注册")
        return False
    try:
        subprocess.run(["schtasks", "/Delete", "/TN", WIN_TASK_NAME, "/F"],
                       capture_output=True)
        subprocess.run(
            ["schtasks", "/Create", "/TN", WIN_TASK_NAME,
             "/TR", f'"{str(bat)}"', "/SC", "ONLOGON", "/F"],
            check=True, capture_output=True)
        log(f"已注册 Windows 计划任务（登录自启）：{WIN_TASK_NAME}")
        return True
    except Exception as e:
        log(f"[警告] 计划任务注册失败（可能需要管理员/家庭版限制）：{e}")
        return False


def register_autostart_macos(code_dir: Path, port: int) -> bool:
    label = "com.local.procurement-system"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    py = venv_python(code_dir)
    log_path = default_data_dir() / "autostart.log"
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{py}</string>
    <string>-m</string>
    <string>uvicorn</string>
    <string>server.app:app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>{port}</string>
  </array>
  <key>WorkingDirectory</key><string>{code_dir}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log_path}</string>
  <key>StandardErrorPath</key><string>{log_path}</string>
</dict>
</plist>
"""
    try:
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(plist_content, encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        subprocess.run(["launchctl", "load", str(plist)], check=True, capture_output=True)
        log(f"已注册 macOS launchd（登录自启 + 崩溃自动拉起）：{plist}")
        return True
    except Exception as e:
        log(f"[警告] launchd 注册失败：{e}")
        return False


def unregister_autostart() -> None:
    if IS_WINDOWS:
        subprocess.run(["schtasks", "/Delete", "/TN", WIN_TASK_NAME, "/F"], capture_output=True)
        log("已删除 Windows 计划任务")
    else:
        label = "com.local.procurement-system"
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        plist.unlink(missing_ok=True)
        log("已卸载 macOS launchd")


# ---------------- 步骤 6：MCP 配置说明 ----------------

def print_mcp_guide(cfg: dict, port: int) -> None:
    url = f"http://127.0.0.1:{port}/mcp"
    print()
    print("=" * 62)
    print("  OneAgent MCP 配置（在 OneAgent 设置 → MCP 管理中添加）：")
    print(f"    传输方式：http（streamable HTTP / SSE）")
    print(f"    URL：     {url}")
    print(f"    Header：  Authorization: Bearer {cfg.get('token', '<口令>')}")
    print("=" * 62)
    print(f"  Web 界面： http://127.0.0.1:{port}/  （口令同上）")
    print(f"  数据目录： {default_data_dir()}  （备份=复制整个目录）")
    print(f"  日常启动： 双击 start.bat (Win) / start.sh / start.command (macOS)")
    print()


# ---------------- 主流程 ----------------

def main() -> int:
    ap = argparse.ArgumentParser(description="采购系统安装器（跨平台）")
    ap.add_argument("--check", action="store_true", help="只做环境检查")
    ap.add_argument("--no-autostart", action="store_true", help="跳过开机自启")
    ap.add_argument("--no-selftest", action="store_true", help="跳过启动自检")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PROC_PORT", 8000)))
    ap.add_argument("--uninstall", action="store_true", help="卸载")
    args = ap.parse_args()

    code_dir = default_code_dir()
    data_dir = default_data_dir()

    if args.uninstall:
        unregister_autostart()
        if code_dir.exists():
            shutil.rmtree(code_dir)
            log(f"已删除代码目录 {code_dir}")
        log(f"数据目录保留（含历史价格数据）：{data_dir}")
        return 0

    print(f"采购系统安装器 · 目标平台 {OS_NAME}")
    print(f"代码目录：{code_dir}")
    print(f"数据目录：{data_dir}（升级永不触碰）")
    print()

    py = check_python()
    if args.check:
        log("环境检查通过，未做任何修改")
        return 0

    deploy_code(code_dir)
    create_venv_and_install(code_dir, py)
    cfg = init_db_and_config(data_dir, code_dir)

    if not args.no_selftest:
        self_test(code_dir, data_dir, args.port)

    if not args.no_autostart:
        if IS_WINDOWS:
            register_autostart_windows(code_dir, args.port)
        elif sys.platform == "darwin":
            register_autostart_macos(code_dir, args.port)
        else:
            log("[提示] Linux 自启请自行配置 systemd（本安装器暂不处理）")

    print_mcp_guide(cfg, args.port)
    log("安装完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
