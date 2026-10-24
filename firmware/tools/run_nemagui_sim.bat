@echo off
rem nemagui をシミュレータ接続で起動する (python -m nemagui --sim)
rem 追加の引数はそのまま nemagui に渡される
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv が見つかりません: %~dp0.venv
    echo   python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements-gui.txt
    exit /b 1
)

".venv\Scripts\python.exe" -m nemagui --sim %*
exit /b %errorlevel%
