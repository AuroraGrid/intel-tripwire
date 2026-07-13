@echo off
cd /d %~dp0
start "" http://127.0.0.1:8080
python app.py --offline
pause
