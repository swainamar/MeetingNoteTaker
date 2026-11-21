"""
Language guard (V1.6.2)
=======================
Whisper has no Odia model. When it hears Odia on "Auto-detect" it guesses a
random language and writes the words in Chinese, Korean, Hindi, Bengali...

This module fixes that for Auto-detect mode:
  1. Auto-detect is limited to the languages you actually speak
     (Settings -> "Auto-detect only these languages", default: en, hi, or).
  2. Each ~30 s window is checked: if Whisper is not sure it is one of the
     allowed Whisper languages, the window goes to IndicConformer (Odia).
  3. Any line still written in a script none of the allowed languages use
     (e.g. Chinese/Korean characters) is re-done with IndicConformer, or
     marked "(unclear in recording)" if Odia support is not installed.
  4. Well-known Whisper "hallucinations" on silence (e.g. a 30-second
     "Thank you.") are dropped.
"""

import re

import numpy as np

SR16 = 16000
WINDOW_S = 30          # one Whisper window; language is chosen per window
SURE_PROB = 0.60       # below this, a non-Indic guess is not trusted
UNCLEAR = "(unclear in recording)"

# Languages Whisper cannot transcribe at all: must use IndicConformer.
INDIC_ONLY = {"or", "mai", "kok", "ks", "doi", "brx", "mni", "sat"}

# Script used by each language (anything not listed is assumed Latin).
LANG_SCRIPT = {
    "hi": "deva", "mr": "deva", "ne": "deva", "sa": "deva", "mai": "deva",
    "kok": "deva", "doi": "deva", "brx": "deva",
    "bn": "beng", "as": "beng", "mni": "beng",
    "or": "orya", "pa": "guru", "gu": "gujr", "ta": "taml", "te": "telu",
    "kn": "knda", "ml": "mlym", "si": "sinh", "sat": "olck",
    "ur": "arab", "sd": "arab", "ks": "arab", "ar": "arab", "fa": "arab", "ps": "arab",
    "zh": "cjk", "yue": "cjk", "ja": "cjk", "ko": "hang",
    "ru": "cyrl", "uk": "cyrl", "bg": "cyrl", "sr": "cyrl", "kk": "cyrl", "mk": "cyrl",
    "be": "cyrl", "mn": "cyrl", "tg": "cyrl", "ba": "cyrl", "tt": "cyrl",
    "el": "grek", "he": "hebr", "yi": "hebr", "th": "thai", "lo": "laoo",
    "my": "mymr", "km": "khmr", "ka": "geor", "hy": "armn", "am": "ethi", "bo": "tibt",
}

_RANGES = [
    ("latn", 0x0041, 0x024F), ("grek", 0x0370, 0x03FF), ("cyrl", 0x0400, 0x04FF),
    ("armn", 0x0530, 0x058F), ("hebr", 0x0590, 0x05FF), ("arab", 0x0600, 0x06FF),
    ("arab", 0x0750, 0x077F), ("deva", 0x0900, 0x097F), ("beng", 0x0980, 0x09FF),
    ("guru", 0x0A00, 0x0A7F), ("gujr", 0x0A80, 0x0AFF), ("orya", 0x0B00, 0x0B7F),
    ("taml", 0x0B80, 0x0BFF), ("telu", 0x0C00, 0x0C7F), ("knda", 0x0C80, 0x0CFF),
    ("mlym", 0x0D00, 0x0D7F), ("sinh", 0x0D80, 0x0DFF), ("thai", 0x0E00, 0x0E7F),
    ("laoo", 0x0E80, 0x0EFF), ("tibt", 0x0F00, 0x0FFF), ("mymr", 0x1000, 0x109F),
    ("geor", 0x10A0, 0x10FF), ("hang", 0x1100, 0x11FF), ("ethi", 0x1200, 0x137F),
    ("khmr", 0x1780, 0x17FF), ("olck", 0x1C50, 0x1C7F), ("cjk", 0x3040, 0x30FF),
    ("hang", 0x3130, 0x318F), ("cjk", 0x3400, 0x4DBF), ("cjk", 0x4E00, 0x9FFF),
    ("hang", 0xAC00, 0xD7AF), ("cjk", 0xF900, 0xFAFF),
]

# Phrases Whisper invents over silence or music.
HALLUCINATIONS = {
    "thank you", "thank you very much", "thanks for watching", "thank you for watching",
    "please subscribe", "subscribe to my channel", "bye", "you", "so", "oh",
    "subtitles by the amara.org community", "thanks", "okay",
}


def _script(ch: str) -> str | None:
    if not ch.isalpha():
        return None
    cp = ord(ch)
    for name, lo, hi in _RANGES:
        if lo <= cp <= hi:
            return name
    return "other"


def allowed_scripts(allowed: list[str]) -> set[str]:
    return {"latn"} | {LANG_SCRIPT.get(code, "latn") for code in allowed}


def unexpected_script(text: str, allowed: list[str], max_share: float = 0.3) -> bool:
    """True if more than max_share of the letters are in a script that none of
    the allowed languages use (e.g. Chinese characters in an Odia/English call)."""
    ok = allowed_scripts(allowed)
    letters = [s for s in (_script(c) for c in text) if s]
    if not letters:
        return False
    bad = sum(1 for s in letters if s not in ok)
    return bad / len(letters) > max_share


def is_hallucination(text: str, start: float, end: float) -> bool:
    """A stock phrase stretched over a long stretch of (near) silence."""
    norm = re.sub(r"[^\w\s]", "", text).strip().lower()
    return norm in HALLUCINATIONS and (end - start) >= 6.0


def parse_allowed(value) -> list[str]:
    """'en, hi, or' or ['en','hi','or'] -> ['en','hi','or'] (empty = no limit)."""
    if isinstance(value, str):
        value = re.split(r"[\s,;]+", value)
    out = []
    for v in value or []:
        v = str(v).strip().lower()
        if re.fullmatch(r"[a-z]{2,3}", v) and v not in out:
            out.append(v)
    return out


def choose_language(model, audio16k: np.ndarray, allowed: list[str], indic_ok: bool):
    """Return ('whisper', code_or_None) or ('indic', code) for this window."""
    whisper_allowed = [a for a in allowed if a not in INDIC_ONLY]
    indic_target = next((a for a in allowed if a in INDIC_ONLY), None)
    try:
        lang, prob, probs = model.detect_language(audio16k, vad_filter=True)
    except Exception as e:              # e.g. no speech found by VAD
        print(f"Language detection skipped: {e}")
        lang, prob, probs = None, 0.0, []
    if lang in whisper_allowed and prob >= SURE_PROB:
        return "whisper", lang
    if indic_target and indic_ok:
        return "indic", indic_target
    ranked = [(p, code) for code, p in probs if code in whisper_allowed]
    if ranked:
        return "whisper", max(ranked)[1]
    return "whisper", (whisper_allowed[0] if whisper_allowed else lang)


def transcribe_guarded(model, audio16k: np.ndarray, allowed: list[str],
                       initial_prompt: str | None = None, offset: float = 0.0,
                       window_s: float = WINDOW_S) -> list[tuple[float, float, str]]:
    """Auto-detect transcription that never leaves the allowed languages.
    Returns [(start_sec, end_sec, text)] with `offset` added to every time."""
    import indic_asr
    indic_target = next((a for a in allowed if a in INDIC_ONLY), None)
    indic_ok = bool(indic_target) and indic_asr.is_available()
    out = []
    for wb, _we, chunk in indic_asr.split_windows(audio16k, window_s):
        if len(chunk) < int(0.3 * SR16) or float(np.max(np.abs(chunk), initial=0.0)) < 1e-4:
            continue
        base = offset + wb
        engine, lang = choose_language(model, chunk, allowed, indic_ok)
        if engine == "indic":
            out += indic_asr.transcribe_array(chunk, lang=lang, start_offset=base)
            continue
        segs, _ = model.transcribe(chunk, language=lang, vad_filter=True, beam_size=5,
                                   condition_on_previous_text=False,
                                   initial_prompt=initial_prompt or None)
        for s in segs:
            text = s.text.strip()
            b, e = float(s.start), float(s.end)
            if not text or is_hallucination(text, b, e):
                continue
            if unexpected_script(text, allowed):
                if indic_ok:
                    piece = chunk[int(b * SR16):int(e * SR16)]
                    out += indic_asr.transcribe_array(piece, lang=indic_target, start_offset=base + b)
                else:
                    out.append((base + b, base + e, UNCLEAR))
                continue
            out.append((base + b, base + e, text))
    return out
