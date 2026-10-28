@echo off
rem Runs Transcriber in the background and restarts it if it stops. Started at login by Transcriber.vbs
rem in your Startup folder. Output goes to %LOCALAPPDATA%\meeting-transcriber\app.log.
cd /d "%~dp0.."
set PYTHONUNBUFFERED=1
set "LOG=%LOCALAPPDATA%\meeting-transcriber\app.log"
:loop
for %%A in ("%LOG%") do if %%~zA GTR 10485760 move /y "%LOG%" "%LOG%.old" >nul
echo [%date% %time%] starting >> "%LOG%"
"%LOCALAPPDATA%\meeting-transcriber\venv\Scripts\python.exe" -m transcriber --no-browser >> "%LOG%" 2>&1
rem Stopped, or could not start (e.g. already running): try again in 15 seconds.
ping -n 16 127.0.0.1 >nul
goto loop
