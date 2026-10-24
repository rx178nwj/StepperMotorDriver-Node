@echo off
rem Launch nemagui against the simulator from the project root.
rem Delegates to firmware\tools\run_nemagui_sim.bat (arguments are passed through).
rem NOTE: keep this file ASCII-only; cmd.exe misreads UTF-8 under cp932.
call "%~dp0firmware\tools\run_nemagui_sim.bat" %*
exit /b %errorlevel%
