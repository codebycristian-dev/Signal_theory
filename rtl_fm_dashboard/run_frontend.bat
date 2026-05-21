@echo off
cd /d %~dp0\frontend
python -m http.server 5173
pause
