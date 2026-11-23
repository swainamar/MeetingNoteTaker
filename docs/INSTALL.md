# Installation guide

[← Back to README](../README.md)

Meeting Note-Taker is **portable**:
- **Nothing to install:** no installer, no admin rights, no Python, and no NVIDIA card needed.
- **Leaves Windows unchanged:** nothing is written to the registry, Program Files or system folders.

## 1. Download and extract

1. Download `MeetingNoteTaker-2.2-win64.zip` from **[Releases](https://github.com/swainamar/MeetingNoteTaker/releases/latest)**.
2. Right-click the ZIP and choose **Extract All**. Pick a folder that only you can read, because it will hold your recordings. For example `C:\Users\<you>\Documents\Meeting Note-Taker`, or a USB drive.

The extracted folder:

```text
Meeting Note-Taker/
├─ MeetingNoteTaker.exe        ← start this (pin it to the taskbar / Start)
├─ README.txt
└─ MeetingNoteTaker/           ← supporting files (keep next to the exe)
   ├─ runtime/                 bundled Python, FFmpeg and libraries
   ├─ app.py …  assets/  templates/
   ├─ Install GPU support (NVIDIA).bat      (optional)
   ├─ Install Indic Languages (Odia).bat    (optional)
   ├─ Debug Meeting Note-Taker.bat          (shows a console for troubleshooting)
   └─ meetings/                created on first use: your recordings
```

## 2. Start and pin it

- Double-click **`MeetingNoteTaker.exe`**.
- If Windows shows **"Windows protected your PC"**, click **More info → Run anyway**. This appears because the exe is not code-signed, and only the first time.
- To keep it handy, right-click `MeetingNoteTaker.exe` and use:
  - **Pin to taskbar** or **Pin to Start**;
  - **Show more options → Send to → Desktop (create shortcut)** for a desktop icon.

  The pinned icon and the running window are the same taskbar button, as with Word or Excel.

On first use the app downloads once, then works offline:
- the Whisper speech model from Hugging Face: about 480 MB on a normal PC, or 1.6 GB for the large model used on NVIDIA GPUs. It is stored in your user profile.
- the speaker models, about 33 MB, from the sherpa-onnx GitHub releases. These are hash-checked.

To use a smaller and faster model on a slow PC, go to **Settings → Audio & AI → Model** and choose **base** (~145 MB) or **tiny** (~75 MB). Accuracy is lower.

## 3. Requirements

- **Windows:** Windows 10 or 11, 64-bit. The launcher uses the .NET Framework 4 that is part of Windows.
- **Memory:** 8 GB RAM, or 16 GB recommended.
- **No GPU needed:** transcription runs on the CPU. On an NVIDIA PC, run `MeetingNoteTaker\Install GPU support (NVIDIA).bat` once for much faster transcription. It downloads about 1.5 GB into the app folder and needs no admin rights.

## 4. Optional add-ons

All add-ons install into the app's own `runtime` folder, so no admin rights are needed.

### Odia and other Indian languages

Whisper has no Odia model, so the app uses AI4Bharat **IndicConformer** for Odia.

1. Double-click `MeetingNoteTaker\Install Indic Languages (Odia).bat`. This installs `transformers`, `torch` and `onnxruntime`, about 1 GB.
2. In the app, go to **Settings → Audio & AI** and set **Auto-detect only these languages** to, for example, `en, hi, or`. Or choose **Odia** as the fixed language.
3. Get access to the model, using one of these two options:
   - **Official model (gated):** accept the terms at <https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual>. Create a *read* token and set it once:
     ```powershell
     setx HF_TOKEN "hf_your_read_token"
     ```
   - **Community mirror:** opt in to the mirror, pinned to a known version:
     ```powershell
     setx INDIC_ASR_ALLOW_MIRROR 1
     ```
     ```powershell
     setx INDIC_ASR_REVISION 29ffe9553e78231c2e6e1aff8760620ee15d75ac
     ```
     The model runs code from its repository (`trust_remote_code`), so only use a mirror you trust. Keep it pinned to a known revision.
4. Restart the app. The first Odia recording downloads the model (about 2.5 GB) once. After that it works offline.

### AI meeting notes

The app picks the first notes engine that is available:

| Engine | Setup | Where the transcript goes |
|---|---|---|
| **Claude API** | `setx ANTHROPIC_API_KEY "sk-ant-…"`, then restart the app. Pick the model in Settings. | Anthropic API |
| **Ollama (local)** | Install [Ollama](https://ollama.com), then run `ollama pull llama3.1`. Used for live key points. | Stays on your PC |
| **claude.ai (no key)** | Nothing to set up. The prompt and transcript are copied to your clipboard and claude.ai opens; press **Ctrl+V** then **Enter**. | Only what you paste |

### Screen recording

FFmpeg is included in the portable ZIP, so screen recording works out of the box. If FFmpeg is on your PATH, that copy is used instead.

### Start with Windows

Turn on **Settings → Privacy & Compliance → Start with Windows**. The compact recorder then opens when you sign in, so calls are recorded even if you forget to open the app. This adds a shortcut to your own Startup folder; no admin rights are needed.

## Updating

1. Close the app.
2. Download the new ZIP and extract it over the old folder, replacing files.

Your `meetings/` folder, settings and voice profiles are not in the ZIP, so they are kept. Add-ons (Odia, GPU) live in `runtime/`: install them again if a new version replaces `runtime/`.

## Uninstalling

1. In the app, turn off **Start with Windows**.
2. Unpin the app and delete any shortcuts you made.
3. Delete the folder. This also deletes all recordings and voice profiles.

Nothing else was installed. Only the downloaded speech models remain in `%USERPROFILE%\.cache\huggingface`, and you can delete that folder too.

## Building from source (developers)

You need Python 3.11 (64-bit).

```powershell
git clone https://github.com/swainamar/MeetingNoteTaker.git
```
```powershell
powershell -ExecutionPolicy Bypass -File MeetingNoteTaker\build\build.ps1
```

`build.ps1` does the following:
- copies the PC's Python 3.11 into `MeetingNoteTaker\runtime`;
- installs `requirements.txt` there;
- compiles `MeetingNoteTaker.exe` (logo included) with the C# compiler built into Windows.

Add `-Gpu` for the NVIDIA libraries, and `-Package` to create the release ZIP in `dist\`. The ZIP never includes recordings, settings or voice profiles.

## Acknowledgements

| Component | Used for | License |
|---|---|---|
| [Python](https://www.python.org) 3.11 (bundled runtime) | Runs the app | PSF License |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / OpenAI Whisper models | Transcription | MIT |
| [AI4Bharat IndicConformer](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) | Odia and Indian languages | See the model card |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), pyannote segmentation, WeSpeaker | Speaker separation | Apache-2.0 / MIT, see each project |
| [soundcard](https://github.com/bastibe/SoundCard), [soundfile](https://github.com/bastibe/python-soundfile) | Audio capture | BSD |
| [FFmpeg](https://ffmpeg.org) via [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | Screen recording | LGPL/GPL (FFmpeg), BSD (wrapper) |
| [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python) | Claude API notes | MIT |
