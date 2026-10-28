@echo off
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo Set up first: see README.md in this folder.
  pause
  exit /b 1
)
venv\Scripts\python.exe transcribe_server.py
pause
