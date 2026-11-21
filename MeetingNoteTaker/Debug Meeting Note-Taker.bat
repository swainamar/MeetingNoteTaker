@echo off
rem Troubleshooting: runs Meeting Note-Taker with a visible console window.
set "NoDefaultCurrentDirectoryInExePath=1"
cd /d "%~dp0"
python "%~dp0app.py"
echo.
echo Exited with code %errorlevel%.
pause
