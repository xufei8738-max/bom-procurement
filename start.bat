@echo off
rem 采购系统日常启动（Windows，安装后使用）
chcp 65001 >nul
setlocal

set CODE_DIR=%USERPROFILE%\procurement-system
set DATA_DIR=%USERPROFILE%\procurement-data
set PORT=8000
if defined PROC_PORT set PORT=%PROC_PORT%

if not exist "%CODE_DIR%\.venv\Scripts\python.exe" (
  echo [错误] 未检测到安装（%CODE_DIR%）。请先运行：
  echo   python scripts\install.py
  pause
  exit /b 1
)

echo 采购系统启动中：http://127.0.0.1:%PORT%  （关闭本窗口即停止服务）
start "" http://127.0.0.1:%PORT%

cd /d "%CODE_DIR%"
set PROC_DATA_DIR=%DATA_DIR%
set PROC_PORT=%PORT%
"%CODE_DIR%\.venv\Scripts\python.exe" -m uvicorn server.app:app --host 127.0.0.1 --port %PORT%
pause
