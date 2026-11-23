@echo off
rem Optional add-on for Odia and all 22 Indian languages (AI4Bharat IndicConformer).
rem Installs into the bundled runtime, so nothing is installed on Windows itself.
set "NoDefaultCurrentDirectoryInExePath=1"
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"

echo Installing Odia / Indian-language speech recognition (transformers, torch, onnxruntime)...
echo This downloads about 1 GB and takes a few minutes.
echo.
"%PY%" -m pip install --disable-pip-version-check --no-warn-script-location "transformers>=4.44,<5" "onnxruntime>=1.19" torch torchaudio
if errorlevel 1 (
  echo.
  echo Install failed. Check your internet connection and try again.
  pause
  exit /b 1
)
echo.
echo Done! Restart Meeting Note-Taker, open Settings - Audio and AI, and add "or" to the languages.
echo The first Odia recording downloads the IndicConformer model (about 2.5 GB) once, then works offline.
pause
