"""
Teams Meeting Note-Taker (Windows) - Core Engine (V1.5)
=======================================================
Records your meeting audio (system loopback + microphone), transcribes locally
with Whisper, and produces structured meeting notes using customizable templates.

  - System audio (what you HEAR: other participants)  -> labelled "Others"
  - Microphone   (what you SAY)                        -> labelled "Me" / --my-name
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
import soundcard as sc
import soundfile as sf

import settings as cfg

SAMPLE_RATE = 48000          # capture rate for WASAPI compatibility
SAVE_RATE_16K = 16000        # optional 3:1 downsampled save rate (~67% smaller WAVs)
BLOCK = 4800                 # 0.1 s per read
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL") or cfg.load_settings().get("claude_model", "claude-sonnet-4-5")

NOTES_PROMPT = cfg.DEFAULT_TEMPLATES["executive_minutes"][1]


def new_meeting_folder(out: Path, title: str, now: dt.datetime) -> Path:
    """Create a brand-new folder for one meeting. Never reuses an existing one."""
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-")[:60] or "meeting"
    out.mkdir(parents=True, exist_ok=True)
    base = f"{now:%Y-%m-%d_%H%M%S}_{safe}"
    for n in range(1, 1000):
        folder = out / (base if n == 1 else f"{base}-{n}")
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise RuntimeError("Could not create a unique meeting folder")


def list_audio_devices() -> tuple[list[str], list[str]]:
    """Return (speaker_names, mic_names) currently visible to Windows."""
    try:
        speakers = [d.name for d in sc.all_speakers()]
    except Exception:
        speakers = []
    try:
        mics = [d.name for d in sc.all_microphones()]
    except Exception:
        mics = []
    return speakers, mics


# --------------------------------------------------------------------------
# 1. Recording
# --------------------------------------------------------------------------
def _candidates(kind: str, preferred_name: str = ""):
    """Devices to try, best first. kind = 'others' (loopback) or 'me' (mic)."""
    if kind == "others":
        default, every = sc.default_speaker(), sc.all_speakers()
    else:
        default, every = sc.default_microphone(), sc.all_microphones()
    preferred = [d for d in every if preferred_name and d.name == preferred_name]
    call_dev = [d for d in every if re.search(r"hands-?free|headset", d.name, re.I)]
    seen, out = set(), []
    for d in preferred + [default] + call_dev + list(every):
        if d is None or d.id in seen:
            continue
        seen.add(d.id)
        if kind == "others":
            try:
                d = sc.get_microphone(id=d.id, include_loopback=True)
            except Exception as e:
                print(f"[others] skipping {d.name}: {e}")
                continue
        out.append(d)
    return out


def _open_stream(kind: str, preferred_name: str = ""):
    """Open the first device that works. Returns (device, ctx, recorder, error)."""
    last = None
    for d in _candidates(kind, preferred_name):
        try:
            ctx = d.recorder(samplerate=SAMPLE_RATE)
            return d, ctx, ctx.__enter__(), None
        except Exception as e:
            last = e
    return None, None, None, last


def _downsample_3x(x: np.ndarray) -> np.ndarray:
    """Fast 48 kHz -> 16 kHz mono downsample (average every 3 samples)."""
    n = len(x) - len(x) % 3
    if n <= 0:
        return np.zeros(0, dtype="float32")
    return x[:n].reshape(-1, 3).mean(axis=1).astype("float32")


def record_stream(kind: str, path: Path, stop: threading.Event, errors: list,
                  paused: threading.Event | None = None, on_frames=None,
                  muted: threading.Event | None = None, on_audio=None,
                  on_level=None, preferred_device: str = "", save_16k: bool = False):
    """Record one stream to a mono WAV until `stop` is set.

    `on_level(rms)` receives the 0.0..1.0 RMS amplitude of each captured block
    for driving the live audio level meter.
    `on_audio(samples)` receives 48 kHz float32 samples for live transcription.
    """
    file_rate = SAVE_RATE_16K if save_16k else SAMPLE_RATE
    with sf.SoundFile(path, "w", file_rate, 1, "PCM_16") as f:
        def emit(samples: np.ndarray, silent: bool = False):
            if save_16k:
                ds = _downsample_3x(samples)
                if len(ds):
                    f.write(ds)
            else:
                f.write(samples)
            if on_frames:
                on_frames(len(samples))
            if on_audio:
                on_audio(samples)
            if on_level:
                rms = 0.0 if silent or len(samples) == 0 else float(np.sqrt(np.mean(samples ** 2)))
                on_level(min(1.0, rms))

        fails, lost_since = 0, None
        while not stop.is_set():
            if muted is not None and muted.is_set():
                if on_level:
                    on_level(0.0)
                last = time.monotonic()
                while muted.is_set() and not stop.is_set():
                    stop.wait(0.1)
                    now = time.monotonic()
                    n, last = int((now - last) * SAMPLE_RATE), now
                    n -= n % 3
                    if n > 0 and not (paused and paused.is_set()):
                        emit(np.zeros(n, dtype="float32"), silent=True)
                lost_since = None
                continue
            dev, ctx, rec, err = _open_stream(kind, preferred_device)
            if dev is None:
                fails += 1
                lost_since = lost_since or time.monotonic()
                if on_level:
                    on_level(0.0)
                if fails >= 20:                      # ~10 s with nothing working
                    errors.append(f"{kind}: no working audio device ({err})")
                    return
                stop.wait(0.5)
                continue
            print(f"[{kind}] recording from: {dev.name}")
            if lost_since is not None:
                gap = int((time.monotonic() - lost_since) * SAMPLE_RATE)
                gap -= gap % 3
                if gap > 0 and not (paused and paused.is_set()):
                    emit(np.zeros(gap, dtype="float32"), silent=True)
                lost_since = None
            fails = 0
            try:
                while not stop.is_set():
                    if muted is not None and muted.is_set():
                        break                                # close the mic
                    data = rec.record(numframes=BLOCK)       # keep draining
                    if paused is not None and paused.is_set():
                        if on_level:
                            on_level(0.0)
                        continue                             # ...but don't save
                    emit(data.mean(axis=1).astype("float32"))
            except Exception as e:
                print(f"[{kind}] device stopped ({e}); switching device...")
                lost_since = time.monotonic()
            finally:
                try:
                    ctx.__exit__(None, None, None)
                except Exception as e:
                    print(f"[{kind}] could not close device cleanly: {e}")


def record(folder: Path, mic: bool = True, save_16k: bool = True):
    kinds = ("others", "me") if mic else ("others",)
    print("\n● Recording" + ("" if mic else " call audio only (your mic is OFF)")
          + "... press ENTER to stop.\n")
    stop, errors = threading.Event(), []
    threads = [threading.Thread(target=record_stream, daemon=True,
                                args=(k, folder / f"audio_{k}.wav", stop, errors),
                                kwargs={"save_16k": save_16k})
               for k in kinds]
    start = dt.datetime.now()
    for t in threads:
        t.start()
    try:
        input()
    except (KeyboardInterrupt, EOFError):
        pass
    stop.set()
    for t in threads:
        t.join(timeout=5)

    for e in errors:
        print(f"Warning: {e}")
    if len(errors) == len(kinds):
        sys.exit("Recording failed: no audio device could be recorded.")
    mins = (dt.datetime.now() - start).total_seconds() / 60
    print(f"■ Stopped. Recorded {mins:.1f} minutes.\n")


# --------------------------------------------------------------------------
# 2. Transcription (local, nothing leaves your computer)
# --------------------------------------------------------------------------
_MODELS = {}
_MODELS_LOCK = threading.Lock()


def _load_model(model_size: str, device: str):
    device, model_size = cfg.resolve_device_and_model(device, model_size)
    with _MODELS_LOCK:
        if (model_size, device) in _MODELS:
            return _MODELS[(model_size, device)]
        if device == "cuda":
            cfg.add_nvidia_dlls()
        from faster_whisper import WhisperModel
        compute = "int8" if device == "cpu" else "float16"
        print(f"Loading Whisper model '{model_size}' on {device.upper()} "
              f"(first run downloads it)...")
        try:
            model = WhisperModel(model_size, device=device, compute_type=compute)
        except (RuntimeError, ValueError) as e:
            if device == "cpu":
                raise
            print(f"  Could not start on GPU ({e}). Using CPU instead.")
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            _MODELS[(model_size, "cpu")] = model
            return model
        _MODELS[(model_size, device)] = model
        return model


def _run(model, path: Path, language: str | None, label: str, initial_prompt: str | None = None,
         auto_languages: list | None = None):
    import lang_guard
    if not language and auto_languages:
        # V1.6.2: Auto-detect limited to the allowed languages, window by window,
        # so Odia is never written out as Chinese / Korean / Hindi.
        import indic_asr
        audio = indic_asr.load_wav_16k(path)
        return [(b, e, label, t) for b, e, t in
                lang_guard.transcribe_guarded(model, audio, auto_languages, initial_prompt)]
    segments, _ = model.transcribe(str(path), language=language or None,
                                   vad_filter=True, beam_size=5,
                                   initial_prompt=initial_prompt or None)
    # Keep (start, end, label, text) so speaker diarization can align full spans
    return [(float(s.start), float(s.end), label, s.text.strip())
            for s in segments if s.text.strip()
            and not lang_guard.is_hallucination(s.text, float(s.start), float(s.end))]


def save_segments(folder: Path, segments: list):
    """Persist structured segment spans for accurate speaker relabeling."""
    data = [{"start": round(b, 3), "end": round(e, 3), "who": w, "text": t}
            for b, e, w, t in segments]
    try:
        (folder / "segments.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"Could not save segments.json: {e}")


def format_transcript(segments: list) -> str:
    stamp = lambda sec: f"{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{int(sec % 60):02d}"
    return "\n".join(f"[{stamp(b)}] {who}: {txt}" for b, _e, who, txt in segments)


def transcribe(folder: Path, model_size: str, language: str | None,
               device: str = "auto", my_name: str = "Me", asr_engine: str = "auto",
               auto_languages: list | None = None) -> str:
    use_indic = cfg.should_use_indic(asr_engine, language)
    if auto_languages is None:
        auto_languages = cfg.load_settings().get("auto_languages") or []
    if not cfg.use_language_guard(asr_engine, language, auto_languages):
        auto_languages = []
    segs = []
    if use_indic:
        import indic_asr
        lang = (language or "or").strip().lower()
        for label, file in (("Others", "audio_others.wav"), (my_name or "Me", "audio_me.wav")):
            path = folder / file
            if not path.exists():
                continue
            print(f"Transcribing {label.lower()} audio with IndicConformer ({lang})...")
            for b, e, txt in indic_asr.transcribe_file(path, lang=lang):
                segs.append((b, e, label, txt))
    else:
        device, model_size = cfg.resolve_device_and_model(device, model_size)
        model = _load_model(model_size, device)
        glossary = cfg.load_glossary_prompt() or None
        for label, file in (("Others", "audio_others.wav"), (my_name or "Me", "audio_me.wav")):
            path = folder / file
            if not path.exists():
                continue
            print(f"Transcribing {label.lower()} audio...")
            try:
                segs += _run(model, path, language, label, glossary, auto_languages)
            except RuntimeError as e:
                if device == "cpu" or not re.search(r"cublas|cudnn|cuda", str(e), re.I):
                    raise
                print(f"  GPU libraries not available ({e}).\n  Switching to CPU...")
                device = "cpu"
                model = _load_model(model_size, device)
                segs += _run(model, path, language, label, glossary, auto_languages)

    segs.sort(key=lambda x: x[0])
    save_segments(folder, segs)
    transcript = format_transcript(segs)
    (folder / "transcript.md").write_text(transcript or "(no speech detected)",
                                          encoding="utf-8")
    print(f"Transcript saved: {folder / 'transcript.md'}\n")
    return transcript


# --------------------------------------------------------------------------
# 3. Notes with Claude
# --------------------------------------------------------------------------
def _copy_to_clipboard(text: str) -> bool:
    """Put text on the Windows clipboard without starting any other program.
    Marks it so it is NOT saved in clipboard history (Win+V) and NOT synced
    to the cloud clipboard, because transcripts are confidential."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    GMEM_MOVEABLE, CF_UNICODETEXT = 0x0002, 13

    def put(fmt, data: bytes):
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            raise OSError("GlobalAlloc failed")
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(fmt, h):
            kernel32.GlobalFree(h)
            raise OSError("SetClipboardData failed")

    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.1)
    else:
        return False
    try:
        user32.EmptyClipboard()
        put(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
        zero = (0).to_bytes(4, "little")
        for name in ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
            fmt = user32.RegisterClipboardFormatW(name)
            if fmt:
                put(fmt, zero)
        return True
    except OSError:
        return False
    finally:
        user32.CloseClipboard()


def send_to_claude_app(folder: Path, prompt: str):
    """No API key: save the full prompt, copy it to the clipboard and open
    Claude in the browser, so you only need to press Ctrl+V and Enter."""
    import webbrowser

    prompt_file = folder / "claude_prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    copied = _copy_to_clipboard(prompt)

    webbrowser.open("https://claude.ai/new")
    print("\n" + "=" * 60)
    if copied:
        print("The notes prompt + transcript is on your clipboard.")
        print("Claude is opening in your browser: press Ctrl+V, then Enter.")
        print("(It is kept out of clipboard history and cloud clipboard sync.)")
    else:
        print("Could not copy to the clipboard automatically.")
        print(f"Open {prompt_file} , copy everything, and paste it into Claude.")
    print("=" * 60)


def build_prompt(transcript: str, title: str, date: str, template_key: str = "executive_minutes") -> str:
    """Fill the selected notes template, neutralizing tag-like text in the transcript."""
    clean = lambda s: s.replace("<", "\u2039").replace(">", "\u203a")
    template = cfg.load_template(template_key)
    try:
        return template.format(title=clean(title), date=date, transcript=clean(transcript))
    except KeyError:
        # Fallback if a user-edited template has stray braces
        return (template
                .replace("{title}", clean(title))
                .replace("{date}", date)
                .replace("{transcript}", clean(transcript)))


def make_notes(folder: Path, transcript: str, title: str, date: str,
               template_key: str = "executive_minutes", claude_model: str | None = None):
    if not transcript.strip():
        print("No speech detected, so no notes were written.")
        return
    prompt = build_prompt(transcript, title, date, template_key)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        send_to_claude_app(folder, prompt)
        return

    import anthropic
    model_name = claude_model or os.environ.get("CLAUDE_MODEL") or cfg.load_settings().get("claude_model", CLAUDE_MODEL)
    print(f"Writing notes with {model_name} (template: {template_key})...")
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model_name,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    notes = "".join(b.text for b in msg.content if b.type == "text")
    out = folder / "notes.md"
    out.write_text(f"# {title}\n_{date}_\n\n{notes}\n", encoding="utf-8")
    print(f"Notes saved: {out}\n")
    print(notes)


# --------------------------------------------------------------------------
def main():
    saved = cfg.load_settings()
    p = argparse.ArgumentParser(description="Record a meeting and take notes.")
    p.add_argument("--title", default="Teams meeting", help="Meeting title")
    p.add_argument("--model", default=saved["model"],
                   help="Whisper size: auto, tiny, base, small, medium, large-v3, large-v3-turbo")
    p.add_argument("--language", default=saved["language"] or None,
                   help="Force a language code, e.g. en, de, tr (default: auto-detect)")
    p.add_argument("--device", default=saved["device"], choices=["auto", "cpu", "cuda"],
                   help="Run Whisper on auto (default), cpu, or cuda (NVIDIA GPU)")
    p.add_argument("--no-ai", action="store_true", help="Transcript only: no API call, no clipboard/browser hand-off")
    p.add_argument("--no-mic", action="store_true",
                   help="Record only the call audio (other people), not your microphone")
    p.add_argument("--speakers", action="store_true",
                   help="Name the other speakers using saved voice profiles")
    p.add_argument("--my-name", default=saved["my_name"], help="Your name in the transcript")
    p.add_argument("--template", default=saved["template"], help="Note template name in templates/")
    p.add_argument("--from-folder", type=Path,
                   help="Re-process an existing recording folder instead of recording")
    p.add_argument("--out", type=Path, default=Path("meetings"), help="Output root folder")
    a = p.parse_args()

    now = dt.datetime.now()
    if a.from_folder:
        folder = a.from_folder
        if not folder.is_dir():
            sys.exit(f"Folder not found: {folder}")
    else:
        folder = new_meeting_folder(a.out, a.title, now)
        print("Reminder: tell participants you are recording before you start.\n")
        record(folder, mic=not a.no_mic, save_16k=saved.get("save_16k", True))

    transcript = transcribe(folder, a.model, a.language, a.device, my_name=a.my_name)
    if a.speakers:
        import speakers
        transcript = speakers.identify(folder, transcript, a.my_name)
        (folder / "transcript.md").write_text(transcript, encoding="utf-8")
    if not a.no_ai:
        make_notes(folder, transcript, a.title, f"{now:%d %B %Y, %H:%M}", template_key=a.template)
    print(f"\nDone. Everything is in: {folder.resolve()}")


if __name__ == "__main__":
    main()
