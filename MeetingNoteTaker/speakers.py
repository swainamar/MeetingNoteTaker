"""
Who said what (V1.5): turns "Others" into real names.

Upgrades in V1.5:
  - Uses full segment (start, end) spans from segments.json when available
    instead of a fixed 3-second window, improving speaker alignment accuracy.
  - Supports cycling through multiple voice clips ("Next clip") in NameDialog.
  - Exposes profile management helpers (list, rename, delete) for Settings UI.
"""

import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import tkinter as tk
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

import settings as cfg

HERE = Path(__file__).parent
MODELS, VOICES = HERE / "models", HERE / "voices"
REL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
SEG_URL = REL + "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
SEG_TAR_SHA = "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488"
SEG_MEMBER = "sherpa-onnx-pyannote-segmentation-3-0/model.onnx"
SEG_FILE, SEG_SHA = MODELS / "speaker-segmentation.onnx", \
    "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079"
EMB_URL = REL + "speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx"
EMB_FILE, EMB_SHA = MODELS / "speaker-embedding.onnx", \
    "e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012"

SR16 = 16000
THREADS = max(1, (os.cpu_count() or 4) // 2)
# V1.6.2: sherpa-onnx clustering threshold - LARGER means FEWER speakers.
# 0.33 split one person into 20-30 "speakers" on 30-minute Teams calls.
DEFAULT_THRESHOLD = 0.5
MERGE_SIM = 0.55            # two voices whose 30 s fingerprints are this alike = same person
MIN_SPEAKER_S = 8.0         # voices with less talk than this are folded into the closest voice
DEFAULT_MAX_SPEAKERS = 12   # hard cap when the number of speakers is not given
MAX_CLIPS = 5
SUGGEST_MIN = 0.30
SURE_MIN, SURE_MARGIN = 0.50, 0.25


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download(url: str, sha: str) -> bytes:
    with urllib.request.urlopen(url, timeout=180) as r:  # nosec B310 (fixed https URL, hash-checked)
        data = r.read()
    if _sha(data) != sha:
        raise RuntimeError(f"Integrity check failed for {url}; the file was not used.")
    return data


def ensure_models():
    MODELS.mkdir(exist_ok=True)
    if not SEG_FILE.exists() or _sha(SEG_FILE.read_bytes()) != SEG_SHA:
        print("Downloading speaker models (first time only, about 33 MB)...")
        with tarfile.open(fileobj=io.BytesIO(_download(SEG_URL, SEG_TAR_SHA)), mode="r:bz2") as t:
            member = t.extractfile(SEG_MEMBER)
            model = member.read() if member else b""
        if _sha(model) != SEG_SHA:
            raise RuntimeError("Segmentation model failed its integrity check.")
        SEG_FILE.write_bytes(model)
    if not EMB_FILE.exists() or _sha(EMB_FILE.read_bytes()) != EMB_SHA:
        EMB_FILE.write_bytes(_download(EMB_URL, EMB_SHA))


# --------------------------------------------------------------------------
# Audio + diarization
# --------------------------------------------------------------------------
def load_16k(path: Path) -> np.ndarray:
    """Read a recording as 16 kHz mono, a minute at a time (low memory)."""
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
    return np.concatenate(parts).astype("float32") if parts else np.zeros(0, "float32")


class SpeakerTool:
    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        ensure_models()
        import sherpa_onnx
        self.so, self.threshold = sherpa_onnx, threshold
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMB_FILE), num_threads=THREADS))

    def diarize(self, audio: np.ndarray, num_speakers: int | None = None):
        so = self.so
        cfg_d = so.OfflineSpeakerDiarizationConfig(
            segmentation=so.OfflineSpeakerSegmentationModelConfig(
                pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEG_FILE)),
                num_threads=THREADS),
            embedding=so.SpeakerEmbeddingExtractorConfig(model=str(EMB_FILE), num_threads=THREADS),
            clustering=so.FastClusteringConfig(num_clusters=num_speakers or -1,
                                               threshold=self.threshold),
            min_duration_on=0.3, min_duration_off=0.5)
        if not cfg_d.validate():
            raise RuntimeError("Speaker model configuration is invalid.")
        result = so.OfflineSpeakerDiarization(cfg_d).process(audio).sort_by_start_time()
        return [(r.start, r.end, r.speaker) for r in result]

    def embedding(self, audio: np.ndarray) -> np.ndarray:
        s = self.extractor.create_stream()
        s.accept_waveform(sample_rate=SR16, waveform=audio)
        s.input_finished()
        v = np.array(self.extractor.compute(s), dtype="float32")
        return v / (np.linalg.norm(v) or 1.0)


def summarize(tool: SpeakerTool, audio: np.ndarray, segs) -> dict:
    """Per voice: talk time, voice fingerprint, and up to 3 audio clips to play."""
    info = {}
    for spk in sorted({s for _, _, s in segs}):
        mine = sorted([(e - b, b, e) for b, e, s in segs if s == spk], reverse=True)
        clip_parts, total = [], 0.0
        for dur, b, e in mine:
            clip_parts.append(audio[int(b * SR16):int(e * SR16)])
            total += dur
            if total >= 30:
                break
        samples = [audio[int(b * SR16):int(min(e, b + 8) * SR16)] for _, b, e in mine[:3]]
        info[spk] = {"seconds": sum(d for d, _, _ in mine),
                     "emb": tool.embedding(np.concatenate(clip_parts)),
                     "sample": samples[0],
                     "samples": samples}
    return info


def merge_speakers(info: dict, segs, max_speakers: int | None = DEFAULT_MAX_SPEAKERS,
                   fixed: bool = False):
    """Fold voices that are really the same person (V1.6.2 fix for one voice
    showing up 20-30 times). Returns (info, segs) with merged labels.

    1. Repeatedly merge the two most similar voices while their averaged
       fingerprints are >= MERGE_SIM alike, or while there are more voices
       than max_speakers.
    2. Fold voices with under MIN_SPEAKER_S of talk (fragments, coughs,
       overlaps) into the most similar remaining voice.
    Skipped when the user gave an exact number of speakers (fixed=True).
    """
    if fixed or len(info) < 2:
        return info, list(segs)
    info = {k: dict(v) for k, v in info.items()}
    mapping = {s: s for s in info}

    def sim(a, b):
        return float(np.dot(info[a]["emb"], info[b]["emb"]))

    def fold(src, dst):
        wd, ws = max(info[dst]["seconds"], 1e-3), max(info[src]["seconds"], 1e-3)
        emb = info[dst]["emb"] * wd + info[src]["emb"] * ws
        info[dst]["emb"] = (emb / (np.linalg.norm(emb) or 1.0)).astype("float32")
        info[dst]["seconds"] += info[src]["seconds"]
        info[dst]["samples"] = (list(info[dst].get("samples") or [info[dst]["sample"]])
                                + list(info[src].get("samples") or [info[src]["sample"]]))[:MAX_CLIPS]
        for k, v in mapping.items():
            if v == src:
                mapping[k] = dst
        del info[src]

    while len(info) > 1:
        keys = list(info)
        best, a, b = max((sim(x, y), x, y) for i, x in enumerate(keys) for y in keys[i + 1:])
        too_many = bool(max_speakers) and len(info) > max_speakers
        if best < MERGE_SIM and not too_many:
            break
        src, dst = (a, b) if info[a]["seconds"] < info[b]["seconds"] else (b, a)
        fold(src, dst)

    big = [s for s in info if info[s]["seconds"] >= MIN_SPEAKER_S]
    if big:
        for s in [s for s in info if info[s]["seconds"] < MIN_SPEAKER_S]:
            fold(s, max(big, key=lambda d: sim(s, d)))
    return info, [(b, e, mapping[s]) for b, e, s in segs]


# --------------------------------------------------------------------------
# Voice profiles
# --------------------------------------------------------------------------
def _safe_name(name: str) -> str:
    return re.sub(r"[^\w .-]", "", name).strip(" .")[:60]


def list_profiles() -> list[str]:
    if not VOICES.exists():
        return []
    return sorted(f.stem for f in VOICES.glob("*.npy"))


def rename_profile(old_name: str, new_name: str) -> bool:
    safe_new = _safe_name(new_name)
    if not safe_new or safe_new == old_name:
        return False
    src = VOICES / f"{old_name}.npy"
    dst = VOICES / f"{safe_new}.npy"
    if not src.exists():
        return False
    if dst.exists():
        # Merge embeddings if target already exists
        emb = np.load(dst, allow_pickle=False) + np.load(src, allow_pickle=False)
        emb = emb / (np.linalg.norm(emb) or 1.0)
        np.save(dst, emb.astype("float32"), allow_pickle=False)
        src.unlink(missing_ok=True)
    else:
        src.rename(dst)
    return True


def delete_profile(name: str) -> bool:
    path = VOICES / f"{name}.npy"
    if path.exists():
        path.unlink(missing_ok=True)
        return True
    return False


def load_profiles() -> dict:
    out = {}
    if not VOICES.exists():
        return out
    for f in VOICES.glob("*.npy"):
        try:
            out[f.stem] = np.load(f, allow_pickle=False)
        except (OSError, ValueError) as e:
            print(f"Skipping voice profile {f.name}: {e}")
    return out


def save_profile(name: str, emb: np.ndarray):
    safe = _safe_name(name)
    if not safe:
        return
    VOICES.mkdir(exist_ok=True)
    path = VOICES / f"{safe}.npy"
    if path.exists():
        old = np.load(path, allow_pickle=False)
        emb = old + emb
        emb = emb / (np.linalg.norm(emb) or 1.0)
    np.save(path, emb.astype("float32"), allow_pickle=False)


def match_profiles(info: dict, profiles: dict) -> dict:
    """Suggest saved names for this meeting's voices: {spk: (name, score, sure)}."""
    out = {spk: (None, 0.0, False) for spk in info}
    if not profiles or not info:
        return out
    embs = {spk: d["emb"] for spk, d in info.items()}
    center = np.mean(list(embs.values()), axis=0) if len(embs) > 1 else 0.0
    unit = lambda v: v / (np.linalg.norm(v) or 1.0)
    score = {(spk, name): float(np.dot(unit(e - center), unit(p - center)))
             for spk, e in embs.items() for name, p in profiles.items()}
    used_spk, used_name = set(), set()
    for (spk, name), sc in sorted(score.items(), key=lambda x: -x[1]):
        if sc < SUGGEST_MIN or spk in used_spk or name in used_name:
            continue
        others = [v for (s2, n2), v in score.items() if s2 == spk and n2 != name]
        others += [v for (s2, n2), v in score.items() if n2 == name and s2 != spk]
        sure = sc >= SURE_MIN and sc - max(others, default=0.0) >= SURE_MARGIN
        out[spk] = (name, sc, sure)
        used_spk.add(spk)
        used_name.add(name)
    return out


# --------------------------------------------------------------------------
# Relabel the transcript (using full segment spans when available)
# --------------------------------------------------------------------------
LINE = re.compile(r"^\[(\d+):(\d\d):(\d\d)\] ([^:]+): (.*)$")


def _best_speaker_for_span(t0: float, t1: float, segs) -> int | None:
    """Find diarization speaker with largest overlap over [t0, t1], with small margin."""
    if t1 <= t0:
        t1 = t0 + 3.0
    best, most = None, 0.0
    for b, e, spk in segs:
        ov = min(e, t1) - max(b, t0)
        if ov > most:
            best, most = spk, ov
    if best is None and segs:
        # Fallback: nearest diarization segment within 2 seconds
        mid = (t0 + t1) / 2.0
        nearest = min(segs, key=lambda s: min(abs(s[0] - mid), abs(s[1] - mid)))
        if min(abs(nearest[0] - mid), abs(nearest[1] - mid)) <= 2.0:
            best = nearest[2]
    return best


def _stamp(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def relabel_from_segments(folder: Path, segs, names: dict, my_name: str = "Me") -> str | None:
    """Relabel using segments.json (full start/end spans) if present."""
    seg_file = folder / "segments.json"
    if not seg_file.exists():
        return None
    try:
        items = json.loads(seg_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    out = []
    for item in items:
        b = float(item.get("start", 0.0))
        e = float(item.get("end", b + 3.0))
        who = item.get("who", "Others")
        text = item.get("text", "")
        if who == "BOOKMARK":
            out.append(f"[{_stamp(b)}] {text}")
            continue
        if who == "Me" or who == my_name:
            who = my_name or "Me"
        else:
            best = _best_speaker_for_span(b, e, segs)
            who = names.get(best, "Others") if best is not None else "Others"
        out.append(f"[{_stamp(b)}] {who}: {text}")
    return "\n".join(out)


def relabel(transcript: str, segs, names: dict, my_name: str = "Me", folder: Path | None = None) -> str:
    if folder is not None:
        from_json = relabel_from_segments(folder, segs, names, my_name)
        if from_json is not None:
            return from_json

    out = []
    for line in transcript.splitlines():
        if "[BOOKMARK]" in line:
            out.append(line)
            continue
        m = LINE.match(line)
        if not m:
            out.append(line)
            continue
        h, mi, se, who, text = m.groups()
        if who == "Me" or who == my_name:
            who = my_name or "Me"
        else:
            t0 = int(h) * 3600 + int(mi) * 60 + int(se)
            best = _best_speaker_for_span(float(t0), float(t0 + 3), segs)
            who = names.get(best, "Others") if best is not None else "Others"
        out.append(f"[{h}:{mi}:{se}] {who}: {text}")
    return "\n".join(out)


def identify(folder: Path, transcript: str, my_name="Me", threshold=DEFAULT_THRESHOLD,
             num_speakers=None, ask=None, max_speakers=DEFAULT_MAX_SPEAKERS) -> str:
    """Return the transcript with names."""
    wav = folder / "audio_others.wav"
    if not wav.exists():
        return relabel(transcript, [], {}, my_name, folder=folder)
    print("Separating speakers...")
    tool = SpeakerTool(threshold)
    audio = load_16k(wav)
    segs = tool.diarize(audio, num_speakers)
    if not segs:
        return relabel(transcript, [], {}, my_name, folder=folder)
    info = summarize(tool, audio, segs)
    found = len(info)
    info, segs = merge_speakers(info, segs, max_speakers, fixed=bool(num_speakers))
    if len(info) != found:
        print(f"Merged {found} raw voices into {len(info)} speakers.")
    profiles = load_profiles()
    order = sorted(info, key=lambda s: -info[s]["seconds"])
    default = {spk: f"Speaker {i + 1}" for i, spk in enumerate(order)}
    suggest = match_profiles(info, profiles)
    names = {spk: suggest[spk][0] if suggest[spk][2] else default[spk] for spk in order}
    if ask:
        got = ask({s: info[s] for s in order}, suggest, default)
        if got:
            typed, remember = got
            names = {spk: (typed.get(spk) or "").strip() or default[spk] for spk in order}
            for spk in remember:
                if typed.get(spk, "").strip():
                    save_profile(typed[spk], info[spk]["emb"])
    print("Speakers: " + ", ".join(f"{names[s]} ({info[s]['seconds'] / 60:.1f} min)" for s in order))
    return relabel(transcript, segs, names, my_name, folder=folder)


# --------------------------------------------------------------------------
# Naming window
# --------------------------------------------------------------------------
class NameDialog:
    """Ask who each voice is. Calls on_done((names, remember)) or on_done(None)."""

    def __init__(self, root, info: dict, suggest: dict, default: dict, on_done, exclude_capture: bool = True,
                 names: list[str] | None = None):
        """names: suggestions for the name boxes (V2.0: Outlook attendees + saved voices)."""
        from tkinter import ttk
        self.on_done, self.done = on_done, False
        names = sorted({n for n in (names or []) if n} | set(list_profiles()), key=str.lower)
        self.tmp = Path(tempfile.mkdtemp(prefix="voices_"))
        self.clip_idx = {}
        self.clips = {}
        self.w = w = tk.Toplevel(root)
        w.title("Who was speaking?")
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.skip)
        cfg.set_capture_excluded(w, exclude_capture)

        # V1.6.2 fix: the buttons are packed FIRST at the bottom and the voice
        # list scrolls, so "Save names" is always visible however many voices.
        ttk.Label(w, text="Who was speaking?", style="H2.TLabel").pack(side="top", anchor="w",
                                                                       padx=16, pady=(14, 0))
        ttk.Label(w, text="Name each voice (blank keeps \"Speaker N\"). Same name twice = same person. "
                          "Press Play to listen.  Enter = Save, Esc = Skip.",
                  style="Muted.TLabel").pack(side="top", anchor="w", padx=16, pady=(2, 10))
        bottom = ttk.Frame(w)
        bottom.pack(side="bottom", fill="x", padx=16, pady=(6, 14))
        ttk.Label(bottom, text="Only tick Remember with that person's consent (voice data).",
                  style="Danger.TLabel").pack(side="left")
        ttk.Button(bottom, text="Skip", command=self.skip).pack(side="right")
        ttk.Button(bottom, text="Save names", style="Accent.TButton",
                   command=self.save).pack(side="right", padx=6)

        holder = ttk.Frame(w)
        holder.pack(side="top", fill="both", expand=True, padx=(8, 4))
        canvas = tk.Canvas(holder, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=body, anchor="nw")
        self.canvas = canvas

        self.entries, self.keep = {}, {}
        for r, (spk, data) in enumerate(info.items(), start=1):
            mins = data["seconds"] / 60
            ttk.Label(body, text=f"{default[spk]}  ({mins:.1f} min)", style="H3.TLabel"
                      ).grid(row=r, column=0, padx=8, sticky="w")
            raw_samples = data.get("samples") or [data["sample"]]
            self.clips[spk] = []
            for idx, arr in enumerate(raw_samples):
                clip = self.tmp / f"{spk}_{idx}.wav"
                sf.write(str(clip), arr, SR16)
                self.clips[spk].append(clip)
            self.clip_idx[spk] = 0

            ttk.Button(body, text="▶ Play", command=lambda s=spk: self.play_spk(s)
                       ).grid(row=r, column=1, padx=3, pady=3)
            btn_next = ttk.Button(body, text="Next clip", command=lambda s=spk: self.next_clip(s))
            if len(self.clips[spk]) <= 1:
                btn_next.state(["disabled"])
            btn_next.grid(row=r, column=2, padx=3, pady=3)

            e = ttk.Combobox(body, width=26, values=names)
            name, score, sure = suggest.get(spk, (None, 0.0, False))
            if name:
                e.insert(0, name)
            e.grid(row=r, column=3, padx=4)
            self.entries[spk] = e
            v = tk.BooleanVar(value=False)
            label = (f"Remember  ({'likely' if sure else 'a guess - please check'})"
                     if name else "Remember voice")
            ttk.Checkbutton(body, text=label, variable=v).grid(row=r, column=4, padx=(4, 12), sticky="w")
            self.keep[spk] = v

        # Size: as wide as the rows, at most 60% of the screen tall.
        try:
            import theme
            theme.scale_tree(w)
        except ImportError:
            pass
        body.update_idletasks()
        list_h = min(body.winfo_reqheight(), int(w.winfo_screenheight() * 0.6))
        canvas.config(width=body.winfo_reqwidth(), height=list_h,
                      scrollregion=(0, 0, body.winfo_reqwidth(), body.winfo_reqheight()))
        body.bind("<Configure>", lambda _e: canvas.config(scrollregion=canvas.bbox("all")))
        w.bind("<MouseWheel>", self._wheel)
        w.bind("<Return>", lambda _e: self.save())
        w.bind("<Escape>", lambda _e: self.skip())
        w.update_idletasks()
        x = max(0, (w.winfo_screenwidth() - w.winfo_reqwidth()) // 2)
        y = max(0, (w.winfo_screenheight() - w.winfo_reqheight()) // 3)
        w.geometry(f"+{x}+{y}")
        w.lift()
        w.bell()

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")

    def play_spk(self, spk):
        clip = self.clips[spk][self.clip_idx[spk]]
        self.play(clip)

    def next_clip(self, spk):
        self.clip_idx[spk] = (self.clip_idx[spk] + 1) % len(self.clips[spk])
        self.play_spk(spk)

    def play(self, clip: Path):
        try:
            import winsound
            winsound.PlaySound(str(clip), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except (ImportError, RuntimeError) as e:
            print(f"Could not play sample: {e}")

    def _close(self, result):
        if self.done:
            return
        self.done = True
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except (ImportError, RuntimeError) as e:
            print(f"Could not stop playback: {e}")
        self.w.destroy()
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.on_done(result)

    def save(self):
        names = {s: e.get() for s, e in self.entries.items()}
        remember = [s for s, v in self.keep.items() if v.get()]
        self._close((names, remember))

    def skip(self):
        self._close(None)
