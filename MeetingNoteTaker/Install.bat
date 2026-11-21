@echo off
rem One-time setup: installs the Python packages Meeting Note-Taker needs.
rem Usually run automatically by "Meeting Note-Taker.bat". Use /quiet for no prompts.
setlocal
set "NoDefaultCurrentDirectoryInExePath=1"
cd /d "%~dp0"
set "QUIET="
if /i "%~1"=="/quiet" set "QUIET=1"

echo Installing required packages...
python -m pip install --disable-pip-version-check -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo.
  echo Install failed. Check that Python 3.11 is installed and on your PATH.
  if not defined QUIET pause
  exit /b 1
)

rem NVIDIA GPU libraries: automatic when an NVIDIA card is present.
where nvidia-smi >nul 2>nul
if errorlevel 1 goto done
echo NVIDIA graphics card found: installing GPU libraries for faster transcription...
python -m pip install --disable-pip-version-check nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"

:done
echo.
echo Setup finished.
if not defined QUIET pause
exit /b 0
