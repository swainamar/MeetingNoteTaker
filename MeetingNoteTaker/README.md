# Meeting Note-Taker 2.1 — Enterprise Edition

Privacy-first Windows meeting recorder and note-taker. Transcription (Whisper, IndicConformer for Odia) and
speaker separation (Sherpa-ONNX) run locally on your PC. Nothing is uploaded unless you send notes to an AI model.

## How to start (2.1)

Double-click **`Meeting Note-Taker.bat`** (the only file next to this folder). On the first start it:
1. installs the required components (one time, a few minutes; NVIDIA GPU libraries are added automatically if you have an NVIDIA card),
2. creates a **Meeting Note-Taker** shortcut with the logo on your Desktop and in the Start menu.

After that, use the desktop icon. It starts without a console window, and the window and taskbar show the logo.
Everything else lives in the `MeetingNoteTaker` folder (`Install.bat`, `Debug Meeting Note-Taker.bat`,
`Install Indic Languages (Odia).bat` and `Start Voice to Prompt.bat` are optional tools; Voice to Prompt
is also in the app sidebar).

## What's new in 2.1
- **Record mode, chosen per meeting**: the top bar (next to Start) and the Dashboard both have **Audio only / Audio + screen**.
  - With *Audio + screen*, a **"What should be recorded?"** window shows a live preview of every screen. Click a screen
    to record all of it, or **Select area…** and drag a rectangle (snip-style) to record just that part.
    Choose **Audio only this time** to skip the video for one call.
  - Audio starts at once; the video joins when you pick, and stays in sync. For auto-recorded calls the picker
    counts down 20 s and then uses your last choice.
  - Tick **Remember my choice** (or untick *Ask each time*) to always record the same screen or area.
    **Choose screen / area…** on the Dashboard changes it at any time.
  - The output is meeting_video.mp4 (screen + call audio, max 1920 px wide, 5 fps), opened from Meetings → **▶ Play video**.
    It works correctly on multi-monitor setups with mixed scaling. *Delete recordings* and data retention cover the video.
    The video captures everything in the chosen screen or area, including notifications.
- **Start with Windows** (Settings → Privacy & Compliance): opens the compact recorder when you sign in, so
  calls are auto-recorded without you remembering to start the app.
- **One launcher + logo shortcuts**, as described above.
- Fewer log lines: the audio driver's repeated "data discontinuity" warning is now logged once.

## What's new in 2.0

**One professional main window** (`app.py`) replaces the small control box and pop-ups:

| Page | What it does |
|---|---|
| Dashboard | KPIs (meetings and time recorded in the last 30 days, open action items, storage), current session with meters, recent meetings, open actions |
| Live meeting | Live transcript and rolling AI key points, pin moments, send to Claude |
| Meetings | Search library, formatted notes and transcript preview, **Export (HTML → PDF)**, regenerate notes, re-run speakers, delete audio or meeting |
| Action items | Every action item from every meeting in one tracker: filter, tick off, open the source meeting, **Export CSV** |
| Voices | Saved voice profiles (biometric data): rename, merge, delete, or delete all |
| Settings | Everything from 1.x, plus a **Privacy & Compliance** tab |
| Audit log | Who recorded, exported or deleted what, and when |

**Enterprise features**
- **Outlook integration**: names the recording after the Outlook meeting happening now and offers the
  attendee names as suggestions in "Who was speaking?". Reads classic Outlook on this PC only.
- **Data retention**: automatically delete recordings after N days and/or whole meetings after M days
  (runs at start-up and every 6 hours). You confirm before a new limit is applied.
- **Audit log**: `meetings/audit.jsonl` (append-only) logs recordings, exports, deletions, setting changes,
  voice-profile changes and retention purges, with the Windows user name.
- **Action-item tracker**: parsed from each meeting's notes; done/open status is saved in `meeting.json`.
- **Print-ready export**: branded HTML notes (Ctrl+P → Save as PDF), escaped and script-free.
- **Compact recorder**: a small always-on-top bar (status, timer, level, pause, stop, pin). It can open
  automatically when a call starts.
- **Light and dark themes**, crisp text on high-DPI (150–200%) screens, and Fluent-style controls.
  No new packages: the UI is built on Tk/ttk only.

All V1.6.2 fixes are included: Odia language guard, merging of duplicate speakers, scrollable speaker
dialog, and the security fixes. V1.x settings are not shared: this folder has its own `meetings/` and
settings file.

## Setup (once)

1. Install Python 3.11 for Windows and tick **Add Python to PATH**.
2. Unzip this folder to a private path (e.g., `C:\Users\<you>\MeetingNoteTaker`).
3. Double-click `Install.bat`. Answer **Y** to NVIDIA GPU libraries only if you have an NVIDIA card.
4. Double-click `Start Teams Note-Taker.bat`, open **Settings**, set your name, add key terms to **Glossary**, and run **Start Audio Test** to verify your speaker and mic meters.

## Privacy & Security

- Always inform participants before recording.
- Recordings, transcripts, and voice profiles (`voices/*.npy`) are stored locally unencrypted—keep this folder on an encrypted drive (BitLocker) and use **Delete WAVs** in **Past meetings** when audio is no longer needed.
- Clipboard copies disable Windows clipboard history and cloud clipboard sync automatically.

## V1.6.2 fixes
- **Odia written as Chinese/Korean/Hindi**: Whisper has no Odia model. Auto-detect is now limited to
  *Settings → Audio & AI → Auto-detect only these languages* (default `en, hi, or`). Uncertain windows go to
  IndicConformer (Odia); lines in a script no allowed language uses are re-done in Odia or marked
  "(unclear in recording)". **Run `Install Indic Languages (Odia).bat` once** – without it Odia cannot be transcribed.
  Choosing Odia with the engine set to "whisper" no longer crashes.
- **Same voice listed 20–30 times**: the clustering threshold went from 0.33 to 0.5 (larger = fewer speakers), and
  similar voices are merged afterwards. Voices with under 8 s of speech are folded into the closest voice, and
  *Max speakers when auto* (default 12) caps the list. An exact *Expected other speakers* number still wins.
- **Save button off-screen**: the "Who was speaking?" list now scrolls (mouse wheel), and Save/Skip are always visible.
  Enter = Save, Esc = Skip.
- Whisper "Thank you." hallucinations over silence are dropped.
- A recording is no longer deleted as "<15 s" when only the call-audio device failed.
- Security: the unofficial IndicConformer mirror (runs remote code) is used only with `INDIC_ASR_ALLOW_MIRROR=1`.
  Pin the model with `INDIC_ASR_REVISION=<commit>`. The single-instance socket no longer hangs on an idle connection.
