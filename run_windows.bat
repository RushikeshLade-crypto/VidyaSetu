@echo off
REM VidyaSetu: double-click this file to set up and start the app on Windows.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python is not installed. Install Python 3.10+ from python.org and tick "Add to PATH". & pause & exit /b 1)
if not exist .venv (
  echo Creating a virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
echo Installing requirements (first time takes a few minutes)...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || (echo Install failed. Check your internet connection. & pause & exit /b 1)
pip install -r requirements-optional.txt >nul 2>nul || echo (Optional extras skipped - the app still works.)
if not exist .env copy .env.example .env >nul
echo.
echo Starting VidyaSetu. Open http://localhost:5000 in Chrome or Edge.
echo Press Ctrl+C in this window to stop it.
start "" http://localhost:5000
python app.py
pause
