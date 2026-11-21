# Contributing

Thanks for helping improve Meeting Note-Taker!

## Reporting bugs and ideas

- **Bug:** open an issue with the **Bug report** template. Include the version (window title), your Windows version, and the relevant lines from `MeetingNoteTaker\meetings\notetaker.log`.
- **Idea:** use the **Feature request** template.
- **Security problem:** follow [SECURITY.md](SECURITY.md) instead of opening an issue.

**Never attach recordings, transcripts, voice profiles or the audit log** to an issue. They contain other people's personal data.

## Development setup

```powershell
git clone https://github.com/swainamar/MeetingNoteTaker.git
```
```powershell
cd MeetingNoteTaker\MeetingNoteTaker
```
```powershell
python -m pip install -r requirements.txt
```
```powershell
python app.py
```

`Debug Meeting Note-Taker.bat` runs the app with a console window so you can see messages.

## Project layout

| File | Purpose |
|---|---|
| `app.py` | Main window: sidebar pages, recording control, screen-recording flow |
| `teams_autorecorder.py` | Call detection, audio capture, post-call processing |
| `meeting_notes.py` / `live_notes.py` | Transcription and notes (batch / live) |
| `lang_guard.py` / `indic_asr.py` | Language guard and IndicConformer (Odia) |
| `speakers.py` | Speaker separation, merging, voice profiles, "Who was speaking?" |
| `screen_rec.py` / `screen_picker.py` | FFmpeg screen recording, screen/area picker |
| `enterprise.py` | Meeting metadata, retention, action items, export, audit log, Outlook |
| `ui_dialogs.py` / `theme.py` | Settings and Meetings pages, styling and high-DPI |
| `settings.py` | Defaults, settings file, Windows helpers |

## Pull requests

1. Create a branch from `main`.
2. Keep changes focused. Match the existing style: plain Tk/ttk and no new UI frameworks.
3. Pin any new dependency to an exact version in `requirements.txt` and check it:
   ```powershell
   pip-audit -r MeetingNoteTaker/requirements.txt
   ```
4. Test on Windows 10 or 11, ideally at 100 % and 200 % display scaling.
5. Update `CHANGELOG.md` and the docs if behaviour changes. Use demo data only in screenshots.
