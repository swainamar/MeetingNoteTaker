# Troubleshooting & FAQ

[← Back to README](../README.md)

To see detailed messages, start `MeetingNoteTaker\Debug Meeting Note-Taker.bat`, which opens a console window. Or open `MeetingNoteTaker\meetings\notetaker.log`.

## Starting the app

**"Windows protected your PC"**
The exe is not code-signed. Click **More info → Run anyway**; you only need to do this once. Your company may block unsigned programs; if so, ask IT to allow `MeetingNoteTaker.exe`.

**"The MeetingNoteTaker folder must stay next to MeetingNoteTaker.exe"**
You started the exe from inside the ZIP, or moved it on its own. Extract the whole ZIP (**Extract All**) and start the exe from the extracted folder. To have it elsewhere, pin it or make a shortcut instead of moving it.

**"The bundled Python runtime is missing"**
`MeetingNoteTaker\runtime` was not extracted, or an antivirus removed files. Extract the ZIP again.

**"Could not load the Python runtime"**
Very rare on Windows 10/11. Install the *Microsoft Visual C++ Redistributable (x64)* from Microsoft, then try again.

**Two taskbar icons after pinning**
Pin `MeetingNoteTaker.exe` itself, or a shortcut to it, not an old shortcut to `pythonw.exe` from version 2.0 or earlier. Unpin the old one.

## Recording

**The call audio meter stays flat**
Go to **Settings → Audio & AI** and choose the speaker or headset you actually hear the call on. Then click **Start Audio Test**. Bluetooth headsets often have two devices ("Hands-free" and "Stereo"), so try the other one.

**Auto-record does not start**
Make sure **Auto-record when a call starts** is ticked and the meeting app is ticked under **Watched meeting apps**. For calls in a browser, tick **Browser meeting**. The app detects calls through Windows' microphone privacy record, so **Settings → Privacy & security → Microphone** must be on for that app.

**A recording disappeared**
Recordings shorter than 15 seconds are discarded. Also check whether **Privacy & Compliance → retention** deleted older meetings. The **Audit log** shows every deletion.

## Transcription & languages

**Odia is written in Chinese, Korean or Hindi script**
Whisper has no Odia model. Install the Indic add-on (see [Installation → Odia](INSTALL.md#odia-and-other-indian-languages)), then set **Auto-detect only these languages** to include `or`, for example `en, hi, or`.

**"IndicConformer model is gated" / 401 error**
Accept the model terms on Hugging Face and set `HF_TOKEN`, or opt in to the pinned mirror with `INDIC_ASR_ALLOW_MIRROR=1`. Restart the app after `setx`.

**Transcription is slow**
On an NVIDIA PC, run `MeetingNoteTaker\Install GPU support (NVIDIA).bat` once. Without a GPU, choose a smaller Whisper model (**base** or **tiny**) in **Settings → Audio & AI**.

**Names or terms are misspelled**
Add them to **Settings → Glossary & Templates → Glossary**.

## Speakers

**The same person is listed many times**
Lower **Max speakers when auto** or enter the exact **Expected other speakers**. In "Who was speaking?", giving two voices the same name merges them.

**Everyone is merged into one speaker**
Enter the exact number of **Expected other speakers** for that meeting, then use **Meetings → Re-run speakers**.

## Screen recording

**"Screen recording needs FFmpeg"**
FFmpeg is part of the portable ZIP, so extract the ZIP again. Or install FFmpeg with:

```powershell
winget install --id Gyan.FFmpeg -e
```

**The wrong screen or a black video is recorded**
Use **Choose screen / area…** on the Dashboard and pick the screen from the previews. The previews come from the same capture method as the recording. A black video can happen with some remote desktop or virtual machine sessions, where Desktop Duplication isn't available.

**The video is out of sync**
The video starts when you choose the screen, and the offset is applied automatically. If it still drifts, lower the frame rate (5 fps) and check the CPU isn't at 100 %.

## Notes

**No notes were written**
- **No API key:** without `ANTHROPIC_API_KEY`, the prompt is copied to your clipboard and claude.ai opens. Press **Ctrl+V** and **Enter**. The prompt is also saved as `claude_prompt.md` in the meeting folder.
- **Action items tab is empty:** check the notes contain an *Action items* table. Then use **Regenerate notes** with the Executive Minutes template.

## FAQ

**Does a bot join my meeting?**
No. The app records your PC's sound and microphone, like a voice recorder next to your PC.

**Does it work with Google Meet, Webex or phone calls through the PC?**
Yes. Anything you hear on the PC can be recorded. For calls in a browser, tick **Browser meeting**.

**Can I use it on macOS or Linux?**
Not currently. It uses Windows-only APIs for call detection, audio loopback and screen capture.

**Where is my data?**
In `MeetingNoteTaker\meetings\`, with one folder per meeting. Voice profiles are in `MeetingNoteTaker\voices\`. See [SECURITY.md](../SECURITY.md).
