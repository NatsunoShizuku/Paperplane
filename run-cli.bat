@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv-win\Scripts\python.exe" (
    echo [Paperplane] .venv-win not found. Please run:
    echo   py -3.12 -m venv .venv-win
    echo   .\.venv-win\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
".venv-win\Scripts\python.exe" -m paperplane ui %*
pause
