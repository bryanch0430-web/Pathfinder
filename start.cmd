@echo off
REM Start Pathfinder locally (http://127.0.0.1:8100).
REM Double-click this file, or run it from a terminal:
REM   start.cmd            run as installed
REM   start.cmd -Rebuild   rebuild the frontend first
REM   start.cmd -Port 8110 listen on another port
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
echo.
echo Pathfinder stopped. Press any key to close this window.
pause >nul
