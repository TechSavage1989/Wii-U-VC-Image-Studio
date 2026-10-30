@echo off
setlocal
cd /d "%~dp0"
if not exist ".buildvenv\Scripts\python.exe" py -3 -m venv .buildvenv
if errorlevel 1 exit /b 1
".buildvenv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-dev.txt
if errorlevel 1 exit /b 1
".buildvenv\Scripts\python.exe" scripts\build_windows.py
pause
