@echo off
cd /d %~dp0\..
python -m pip install -r requirements.txt
if not exist .env copy .env.example .env >nul
echo Setup complete. Add API keys to .env, then run scripts\run.bat
