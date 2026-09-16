@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3.11 -m venv .venv
    if errorlevel 1 goto fail
)
.venv\Scripts\python.exe -c "import cadquery" >nul 2>&1
if errorlevel 1 (
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 goto fail
)
.venv\Scripts\python.exe run.py
pause
exit /b
:fail
echo Setup failed. Install Python 3.11 x64 and try again.
pause
exit /b 1
