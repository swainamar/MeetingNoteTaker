@echo off
rem ===================================================================
rem  Meeting Note-Taker 2.1 - the ONLY file you need to start.
rem  First run: installs what is needed (one time) and creates a
rem  "Meeting Note-Taker" shortcut with the logo on your Desktop and in
rem  the Start menu. After that you can use either this file or the icon.
rem ===================================================================
setlocal
title Meeting Note-Taker
rem Security: never run a program that happens to sit in the current folder.
set "NoDefaultCurrentDirectoryInExePath=1"
set "APP=%~dp0MeetingNoteTaker"

if not exist "%APP%\app.py" (
  echo ERROR: The "MeetingNoteTaker" folder must stay next to this file.
  pause
  exit /b 1
)
cd /d "%APP%"

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 is not installed or not on the PATH.
  echo Install it from python.org and tick "Add Python to PATH", then run this file again.
  start "" "https://www.python.org/downloads/windows/"
  pause
  exit /b 1
)

if not exist "%APP%\.installed" (
  echo First start: checking the required components...
  python -c "import soundcard, soundfile, numpy, faster_whisper, sherpa_onnx" >nul 2>nul
  if errorlevel 1 (
    echo Installing components - one time only, this can take a few minutes.
    call "%APP%\Install.bat" /quiet
    if errorlevel 1 (
      echo.
      echo Setup did not finish. Please check your internet connection and try again.
      pause
      exit /b 1
    )
  )
  type nul > "%APP%\.installed"
)

if not exist "%APP%\.shortcuts_created" (
  python "%APP%\shortcuts.py" install && type nul > "%APP%\.shortcuts_created"
)

start "" pythonw "%APP%\app.py" %*
exit /b 0
