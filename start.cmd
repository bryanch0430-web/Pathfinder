@echo off
rem Pathfinder v5 - start the API and the built UI on http://127.0.0.1:8101
setlocal
set ROOT=%~dp0
set PYTHON=%ROOT%backend\.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python
set PYTHONPATH=%ROOT%backend
set PATHFINDER_SANDBOX=0
if /I "%1"=="sandbox" set PATHFINDER_SANDBOX=1
echo.
echo   Pathfinder v5
echo   UI + API : http://127.0.0.1:8101
echo   API docs : http://127.0.0.1:8101/docs
echo.
"%PYTHON%" -m uvicorn app.main:app --app-dir "%ROOT%backend" --host 127.0.0.1 --port 8101
endlocal
