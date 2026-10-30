@echo off
setlocal
cd /d "%~dp0"
title Wii U VC Image Studio Setup

where py >nul 2>nul
if errorlevel 1 (
    echo Python was not found.
    echo.
    echo Install Python 3.11 or newer from python.org and enable "Add Python to PATH",
    echo then run this file again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\pythonw.exe" (
    echo Creating local Python environment...
    py -3 -m venv .venv
    if errorlevel 1 goto :fail
)

echo Installing or updating required components...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail

start "" ".venv\Scripts\pythonw.exe" "%~dp0app.pyw"
exit /b 0

:fail
echo.
echo Setup failed. Review the messages above.
pause
exit /b 1
