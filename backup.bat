@echo off
rem 备份数据目录（Windows）
chcp 65001 >nul
setlocal

set DATA_DIR=%USERPROFILE%\procurement-data
set DEST=%DATA_DIR%\backups
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set STAMP=%%i

if not exist "%DATA_DIR%\procurement.db" (
  echo [错误] 未找到数据库：%DATA_DIR%\procurement.db
  pause
  exit /b 1
)

mkdir "%DEST%" 2>nul
copy /y "%DATA_DIR%\procurement.db" "%DEST%\procurement-%STAMP%.db" >nul
if exist "%DATA_DIR%\config.json" copy /y "%DATA_DIR%\config.json" "%DEST%\config-%STAMP%.json" >nul

echo 已备份 → %DEST%\procurement-%STAMP%.db
pause
