@echo off
rem One-time optional setup for Odia and all 22 Indian languages (AI4Bharat IndicConformer).
set "NoDefaultCurrentDirectoryInExePath=1"
cd /d "%~dp0"

echo Installing offline Odia / Indian-language ASR packages (transformers, torch, onnxruntime)...
echo This may take a few minutes on the first install.
echo.
python -m pip install "transformers>=4.44,<5" "onnxruntime>=1.19" torch torchaudio
if errorlevel 1 (
  echo.
  echo Install failed. Check that Python 3.11 is installed and on your PATH.
  pause
  exit /b 1
)

echo.
echo Done! Open Meeting Note-Taker - Settings - Audio and AI, and choose "Odia (or)" as Language.
echo The first Odia recording will download the ~600 MB IndicConformer model once, then work offline.
pause
