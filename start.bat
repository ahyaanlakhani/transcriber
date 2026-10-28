@echo off
setlocal
set "APP_DIR=%~dp0"
set "VENV=%LOCALAPPDATA%\meeting-transcriber\venv"

where python >nul 2>&1 || (
  echo Python was not found. Install Python 3.11 or newer from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup, then run start.bat again.
  pause
  exit /b 1
)

if not exist "%VENV%\Scripts\python.exe" (
  echo First run: creating the Python environment in %VENV% ...
  python -m venv "%VENV%" || goto :error
)

fc /b "%APP_DIR%requirements.txt" "%VENV%\requirements.installed" >nul 2>&1
if errorlevel 1 (
  echo Installing dependencies...
  "%VENV%\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r "%APP_DIR%requirements.txt" || goto :error
  copy /y "%APP_DIR%requirements.txt" "%VENV%\requirements.installed" >nul
)

if not exist "%APP_DIR%.env" (
  copy "%APP_DIR%.env.example" "%APP_DIR%.env" >nul
  echo.
  echo Paste your Groq API key into the file that just opened, save it, and close Notepad.
  echo Get a free key at https://console.groq.com/keys
  notepad "%APP_DIR%.env"
)

cd /d "%APP_DIR%"
"%VENV%\Scripts\python.exe" -m transcriber || goto :error
goto :eof

:error
echo.
echo Something went wrong - see the messages above.
echo If it says the address or port is already in use, add TRANSCRIBER_PORT=8001 to .env and try again.
pause
exit /b 1
