@echo off
rem Starts Voice to Prompt V1.5 without a console window.
rem Messages and errors go to voice_prompt.log (rotating) next to this file.

rem Security: only run programs found on the PATH, never one planted in this folder.
set "NoDefaultCurrentDirectoryInExePath=1"
cd /d "%~dp0"

if not exist "%~dp0meeting_notes.py" (
  echo ERROR: meeting_notes.py must be in the same folder as this file.
  pause
  exit /b 1
)
start "" pythonw "%~dp0voice_prompt.py" --device auto --model auto
