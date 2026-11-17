@echo off
cd /d "%~dp0"
echo Iniciando GTA 6 Shorts Studio em http://127.0.0.1:8765 ...
start "" http://127.0.0.1:8765
"%~dp0.venv\Scripts\python.exe" -m uvicorn app.web_server:app --host 127.0.0.1 --port 8765
pause
