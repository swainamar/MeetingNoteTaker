@echo off
rem Optional: faster transcription on PCs with an NVIDIA graphics card.
rem Installs the CUDA libraries into the bundled runtime (about 1.5 GB download).
set "NoDefaultCurrentDirectoryInExePath=1"
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"

where nvidia-smi >nul 2>nul
if errorlevel 1 (
  echo No NVIDIA graphics card was found on this PC - GPU support is not needed.
  pause
  exit /b 0
)
echo Installing NVIDIA GPU libraries...
"%PY%" -m pip install --disable-pip-version-check --no-warn-script-location nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
if errorlevel 1 (
  echo Install failed. Check your internet connection and try again.
  pause
  exit /b 1
)
echo.
echo Done! Restart Meeting Note-Taker.
pause
