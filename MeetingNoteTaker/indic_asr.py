"""
Offline Indian-language ASR backend (Odia + all 22 scheduled Indian languages)
powered by AI4Bharat IndicConformer 600M (MIT license).

Used automatically when language is set to Odia ('or') or when ASR Engine is
set to 'indic' in Settings. Runs 100% locally after one-time setup via
'Install Indic Languages (Odia).bat'.
"""

import os
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

SR16 = 16000
CHUNK_S = 15       # IndicConformer performs best on <=15s windows
LOOK_S = 2         # cut at quietest moment in last 2s

PRIMARY_REPO = "ai4bharat/indic-conformer-600m-multilingual"
MIRROR_REPO = "DictionLabs/indic-conformer-600m-multilingual"

INDIC_LANGUAGES = {
    "or": "Odia",
    "hi": "Hindi",
    "te": "Telugu",
    "ta": "Tamil",
    "bn": "Bengali",
    "mr": "Marathi",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
    "pa": "Punjabi",
    "ur": "Urdu",
    "as": "Assamese",
    "sa": "Sanskrit",
    "ne": "Nepali",
    "sd": "Sindhi",
    "mai": "Maithili",
    "kok": "Konkani",
    "ks": "Kashmiri",
    "doi": "Dogri",
    "brx": "Bodo",
    "mni": "Manipuri",
    "sat": "Santali",
}

_MODEL = None
_LOCK = threading.Lock()


def is_available() -> bool:
    """Return True if optional IndicConformer runtime packages are installed.
    Uses find_spec so the check is instant (importing torch takes seconds)."""
    import importlib.util
    return all(importlib.util.find_spec(m) is not None
               for m in ("torch", "transformers", "onnxruntime"))


def _load_model():
    global _MODEL
    with _LOCK:
        if _MODEL is not None:
            return _MODEL
        try:
            import torch  # noqa: F401
            import onnxruntime  # noqa: F401
            from transformers import AutoModel
        except ImportError as e:
            raise RuntimeError(
                "Odia / IndicConformer support is not installed yet.\n"
                "Double-click 'Install Indic Languages (Odia).bat' once, then try again."
            ) from e

        repo = os.environ.get("INDIC_ASR_MODEL") or PRIMARY_REPO
        token = os.environ.get("HF_TOKEN") or None
        # Security: this model runs Python code downloaded from Hugging Face
        # (trust_remote_code). Pin it to a reviewed commit with INDIC_ASR_REVISION.
        revision = os.environ.get("INDIC_ASR_REVISION") or None
        print(f"Loading IndicConformer model ({repo}"
              f"{'@' + revision if revision else ''})... (first run downloads about 2.5 GB)")
        try:
            _MODEL = AutoModel.from_pretrained(repo, trust_remote_code=True,
                                               token=token, revision=revision)
        except Exception as e:
            err_s = str(e).lower()
            gated = any(k in err_s for k in ("401", "403", "gated", "unauthorized", "access"))
            if repo != PRIMARY_REPO or not gated:
                raise
            # Security fix (V1.6.2): never silently run code from a third-party
            # mirror. The user must opt in explicitly.
            if os.environ.get("INDIC_ASR_ALLOW_MIRROR") != "1":
                raise RuntimeError(
                    "The AI4Bharat Odia model needs a free Hugging Face login.\n"
                    "1) Accept the terms at huggingface.co/" + PRIMARY_REPO + "\n"
                    "2) Set the environment variable HF_TOKEN to your read token, then retry.\n"
                    "(Or, at your own risk, set INDIC_ASR_ALLOW_MIRROR=1 to use the "
                    "unofficial mirror " + MIRROR_REPO + ", which runs its own code.)"
                ) from e
            print(f"Using unofficial mirror ({MIRROR_REPO}) because INDIC_ASR_ALLOW_MIRROR=1...")
            _MODEL = AutoModel.from_pretrained(MIRROR_REPO, trust_remote_code=True, revision=revision)
        return _MODEL


def _load_wav_16k(path: Path) -> np.ndarray:
    """Read any mono/stereo WAV as 16 kHz float32 mono without ffmpeg."""
    rate = sf.info(str(path)).samplerate
    step = max(1, rate // SR16)
    parts = []
    for block in sf.blocks(str(path), blocksize=rate * 60, dtype="float32", always_2d=True):
        mono = block.mean(axis=1)
        if step == 1:
            parts.append(mono)
        else:
            n = len(mono) - len(mono) % step
            if n > 0:
                parts.append(mono[:n].reshape(-1, step).mean(axis=1))
    return np.concatenate(parts).astype("float32") if parts else np.zeros(0, dtype="float32")


def _split_windows(audio_16k: np.ndarray, chunk_s: float = CHUNK_S) -> list[tuple[float, float, np.ndarray]]:
    """Slice 16 kHz mono audio into <=chunk_s windows, cutting at quiet moments."""
    total = len(audio_16k)
    if total == 0:
        return []
    win_max = int(chunk_s * SR16)
    look = LOOK_S * SR16
    frame = int(0.2 * SR16)
    out = []
    pos = 0
    while pos < total:
        end = min(total, pos + win_max)
        if end < total and (end - pos) > look + frame:
            tail = audio_16k[end - look:end]
            energy = [float(np.mean(tail[i:i + frame] ** 2))
                      for i in range(0, len(tail) - frame + 1, frame)]
            cut = (end - look) + int(np.argmin(energy)) * frame + frame // 2
        else:
            cut = end
        chunk = audio_16k[pos:cut]
        out.append((pos / SR16, cut / SR16, chunk))
        pos = cut
    return out


def transcribe_array(audio_16k: np.ndarray, lang: str = "or",
                     decoder: str = "ctc", start_offset: float = 0.0) -> list[tuple[float, float, str]]:
    """Transcribe a 16 kHz float32 mono numpy array.
    Returns list of (start_sec, end_sec, text).
    """
    if lang not in INDIC_LANGUAGES:
        lang = "or"
    model = _load_model()
    import torch

    results = []
    for b, e, chunk in _split_windows(audio_16k):
        if len(chunk) < int(0.3 * SR16):
            continue
        if float(np.max(np.abs(chunk), initial=0.0)) < 1e-4:
            continue
        wav = torch.from_numpy(np.ascontiguousarray(chunk, dtype="float32")).unsqueeze(0)
        raw = model(wav, lang, decoder)
        text = (raw[0] if isinstance(raw, (list, tuple)) else str(raw)).strip()
        if text:
            results.append((start_offset + b, start_offset + e, text))
    return results


load_wav_16k = _load_wav_16k
split_windows = _split_windows


def transcribe_file(path: Path, lang: str = "or", decoder: str = "ctc") -> list[tuple[float, float, str]]:
    """Transcribe a WAV file using IndicConformer."""
    audio = _load_wav_16k(path)
    return transcribe_array(audio, lang=lang, decoder=decoder, start_offset=0.0)
