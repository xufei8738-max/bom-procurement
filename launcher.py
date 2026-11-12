"""PyInstaller 打包入口：双击 exe / 命令行运行，自动起服务并打开界面。

用法（源码）：  python launcher.py
用法（打包后）：双击 bom-procurement.exe（Windows）或 ./bom-procurement（macOS）

数据目录与源码运行一致：~/procurement-data（升级不触碰）。
"""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _prepare_path() -> None:
    """源码模式：让 server 包可导入。frozen（PyInstaller）模式无需处理。"""
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parent))


def _force_utf8_console() -> None:
    """Windows 控制台默认 cp1252/cp936，print 中文会崩；强制 UTF-8 + 不可编码字符替换。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - 重定向到无 reconfigure 的流时忽略
                pass


def main() -> None:
    port = int(os.environ.get("PROC_PORT", "8000"))

    _force_utf8_console()
    _prepare_path()
    from server import db

    db.get_config()   # 确保数据目录与口令就绪
    db.init_db()

    def open_browser():
        time.sleep(1.5)
        try:
            webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception:  # noqa: BLE001 - 打不开浏览器不影响服务
            pass

    threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn

    from server.app import app as asgi_app

    token_hint = db.get_config().get("token", "")
    print("=" * 60)
    print(f"  采购系统已启动: http://127.0.0.1:{port}")
    print(f"  接口口令: {token_hint}")
    print(f"  数据目录: {db.DATA_DIR}")
    print("  MCP 端点: http://127.0.0.1:%d/mcp (Bearer 口令)" % port)
    print("  关闭本窗口即停止服务（开机自启请用 install 脚本）")
    print("=" * 60)

    # 传 app 对象（frozen 模式下字符串导入路径不可用）
    uvicorn.run(asgi_app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
