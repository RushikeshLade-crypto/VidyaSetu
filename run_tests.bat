@echo off
REM Runs all 18 automated tests. Use after run_windows.bat has set things up once.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m unittest tests.test_smoke -v
python -m unittest tests.test_live_stub -v
python -m unittest tests.test_sarvam_stub -v
pause
