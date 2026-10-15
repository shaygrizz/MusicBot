@echo off
cd /d %~dp0
if not exist .venv (
    "C:\Users\petro\AppData\Local\Python\pythoncore-3.14-64\python.exe" -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python bot.py
pause
