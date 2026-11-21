# Installation guide

[← Back to README](../README.md)

## 1. Install Python 3.11

1. Download **Python 3.11 (64-bit)** from <https://www.python.org/downloads/windows/>.
2. In the installer, tick **"Add python.exe to PATH"**, then click **Install Now**.
3. Check it worked. Open PowerShell and run:
   ```powershell
   python --version
   ```
   It should print `Python 3.11.x`.

> Python 3.12 and later are not tested. `soundcard` needs `numpy 1.26`, which is pinned in `requirements.txt`.

## 2. Get the app

Put the app in a **private folder that only you can read**, such as your user folder, because it will hold your recordings.

- **ZIP:** on the repository page click **Code → Download ZIP**, then unzip it to `C:\Users\<you>\MeetingNoteTaker`.
- **Git:**
  ```powershell
  git clone https://github.com/swainamar/MeetingNoteTaker.git "$env:USERPROFILE\MeetingNoteTaker"
  ```

The folder looks like this:

```text
MeetingNoteTaker/              ← repository root
├─ Meeting Note-Taker.bat      ← the only file you start
├─ MeetingNoteTaker/           ← the app (don't move or rename)
│  ├─ app.py …
│  ├─ assets/  templates/
│  ├─ Install.bat                          (run automatically)
│  ├─ Install Indic Languages (Odia).bat   (optional)
│  └─ Debug Meeting Note-Taker.bat         (shows a console for troubleshooting)
└─ docs/
```

## 3. First start

Double-click **`Meeting Note-Taker.bat`**. On the first start it:

1. installs the Python packages from `MeetingNoteTaker/requirements.txt`. This takes a few minutes, one time only.
2. adds the **NVIDIA GPU libraries** automatically if `nvidia-smi` finds an NVIDIA card.
3. creates a **Meeting Note-Taker** shortcut with the logo on the Desktop and in the Start menu.

After that, use the shortcut. On first use the app downloads:
- the Whisper model, from Hugging Face;
- the speaker models, about 33 MB, from the sherpa-onnx GitHub releases. These are hash-checked.

## 4. Optional add-ons

### Odia and other Indian languages

Whisper has no Odia model, so the app uses AI4Bharat **IndicConformer** for Odia.

1. Double-click `MeetingNoteTaker\Install Indic Languages (Odia).bat`. This installs `transformers`, `torch` and `onnxruntime`.
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

Screen recording uses FFmpeg. The bundled `imageio-ffmpeg` works out of the box. If FFmpeg is already on your PATH, that copy is used instead, for example one installed with:

```powershell
winget install --id Gyan.FFmpeg -e
```

### Start with Windows

Turn on **Settings → Privacy & Compliance → Start with Windows**. The compact recorder then opens when you sign in, so calls are recorded even if you forget to open the app.

## Updating

- **Git:**
  ```powershell
  git -C "$env:USERPROFILE\MeetingNoteTaker" pull
  ```
- **ZIP:** download the new ZIP and copy it over the old folder. Your `meetings/` folder and voice profiles are not in the ZIP, so they are kept.

If `requirements.txt` changed, run `MeetingNoteTaker\Install.bat` once.

## Uninstalling

1. In the app, turn off **Start with Windows**.
2. Delete the Desktop and Start-menu shortcuts.
3. Delete the folder. This also deletes all recordings and voice profiles.

## Acknowledgements

| Component | Used for | License |
|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / OpenAI Whisper models | Transcription | MIT |
| [AI4Bharat IndicConformer](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) | Odia and Indian languages | See the model card |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), pyannote segmentation, WeSpeaker | Speaker separation | Apache-2.0 / MIT, see each project |
| [soundcard](https://github.com/bastibe/SoundCard), [soundfile](https://github.com/bastibe/python-soundfile) | Audio capture | BSD |
| [FFmpeg](https://ffmpeg.org) via [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | Screen recording | LGPL/GPL (FFmpeg), BSD (wrapper) |
| [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python) | Claude API notes | MIT |
