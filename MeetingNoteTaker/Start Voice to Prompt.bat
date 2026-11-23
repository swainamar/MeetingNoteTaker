@echo off
rem Starts Voice to Prompt without a console window (it is also in the app sidebar).
set "NoDefaultCurrentDirectoryInExePath=1"
cd /d "%~dp0"
if exist "%~dp0..\MeetingNoteTaker.exe" (
  start "" "%~dp0..\MeetingNoteTaker.exe" "%~dp0voice_prompt.py" --device auto --model auto
) else (
  start "" pythonw "%~dp0voice_prompt.py" --device auto --model auto
)
