@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "runtime\python.exe" (
    set "PYTHONNOUSERSITE=1"
    echo [Paperplane] Starting GUI ^(offline runtime^) at http://127.0.0.1:8765 ...
    "runtime\python.exe" -m paperplane gui %*
    pause
    exit /b %errorlevel%
)
if not exist ".venv-win\Scripts\python.exe" (
    echo [Paperplane] First run: creating .venv-win and installing dependencies...
    where py >nul 2>nul && (py -3.12 -m venv .venv-win 2>nul || py -m venv .venv-win) || python -m venv .venv-win
    if not exist ".venv-win\Scripts\python.exe" (
        echo [Paperplane] Failed to create .venv-win. Please install Python 3.11+ first.
        pause
        exit /b 1
    )
    ".venv-win\Scripts\python.exe" -m pip install --no-cache-dir -r requirements.txt
    if errorlevel 1 (
        echo [Paperplane] Dependency installation failed.
        pause
        exit /b 1
    )
)
echo [Paperplane] Starting GUI at http://127.0.0.1:8765 ...
".venv-win\Scripts\python.exe" -m paperplane gui %*
pause
