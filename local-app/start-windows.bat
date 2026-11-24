@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo Python not found. Install it from https://www.python.org/downloads/ and tick "Add Python to PATH". & pause & exit /b)
if not exist .venv (python -m venv .venv)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
python app.py
pause
