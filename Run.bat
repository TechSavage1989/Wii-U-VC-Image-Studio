@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    call Setup_and_Run.bat
    exit /b
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0app.pyw"
