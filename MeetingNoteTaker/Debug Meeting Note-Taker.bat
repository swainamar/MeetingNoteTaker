@echo off
rem Troubleshooting: runs Meeting Note-Taker with a visible console window.
set "NoDefaultCurrentDirectoryInExePath=1"
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0app.py"
echo.
echo Exited with code %errorlevel%.
pause
