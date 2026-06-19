@echo off
REM Launch the Marana client via python.exe (no .exe to be blocked by Windows
REM Application Control). Pass args through, e.g.:  run_client.bat --host 192.168.1.7
cd /d "%~dp0"
".venv\Scripts\python.exe" -m marana_client %*
