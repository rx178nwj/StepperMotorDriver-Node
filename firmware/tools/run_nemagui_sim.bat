@echo off
rem Launch nemagui against the simulator (python -m nemagui --sim).
rem Extra arguments are passed through to nemagui.
rem NOTE: keep this file ASCII-only; cmd.exe misreads UTF-8 under cp932.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found: %~dp0.venv
    echo   python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements-gui.txt
    exit /b 1
)

".venv\Scripts\python.exe" -m nemagui --sim %*
exit /b %errorlevel%
