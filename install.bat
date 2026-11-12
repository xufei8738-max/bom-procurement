@echo off
rem 采购系统安装入口（Windows）
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Python，请先安装 Python 3.10+ 并勾选 "Add to PATH"
  echo 下载: https://www.python.org/downloads/
  pause
  exit /b 1
)

python scripts\install.py %*
pause
