"""
UI components and dialogs for Meeting Note-Taker V1.5:
  - AudioMeter: live RMS volume bar with peak hold (for main window, settings test, voice_prompt)
  - SettingsDialog: tabbed settings (General, Audio & AI, Glossary & Templates, Voice Profiles)
  - HistoryWindow: Past Meetings browser with full-text search, preview, template regeneration,
    speaker re-run, and one-click WAV cleanup.
"""

import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

import enterprise as ent
import meeting_notes as mn
import settings as cfg
import speakers
import theme


def _open_path(path: Path):
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        messagebox.showerror("Open folder", f"Could not open {path}:\n{e}")


# --------------------------------------------------------------------------
# Reusable live audio level meter
# --------------------------------------------------------------------------
class AudioMeter(tk.Canvas):
    """Smooth horizontal VU meter (green -> amber -> red) with peak hold."""

    def __init__(self, parent, width: int = 190, height: int = 12, **kwargs):
        width, height = theme.px(width), theme.px(height)
        super().__init__(parent, width=width, height=height, bg=theme.C["meter_track"],
                         highlightthickness=0, bd=0, **kwargs)
        self._meter_w = width
        self._meter_h = height
        self._level = 0.0
        self._peak = 0.0
        self._peak_t = 0.0
        self._bar = self.create_rectangle(0, 0, 0, height, fill="#16A34A", width=0)
        self._peak_line = self.create_rectangle(0, 0, 0, height, fill=theme.C["text"], width=0)

    def set_level(self, rms: float):
        """Update meter from raw 0..1 RMS using a perceptual dB curve."""
        if rms <= 1e-5:
            norm = 0.0
        else:
            db = 20.0 * math.log10(max(rms, 1e-5))   # -100 .. 0 dB
            norm = max(0.0, min(1.0, (db + 55.0) / 50.0))
        # Smooth decay so bars don't flicker harshly
        self._level = max(norm, self._level * 0.72)
        now = time.monotonic()
        if self._level >= self._peak or (now - self._peak_t) > 1.2:
            self._peak = self._level
            self._peak_t = now

        fill = "#16A34A" if self._level < 0.65 else ("#D97706" if self._level < 0.88 else "#DC2626")
        x = int(self._level * self._meter_w)
        self.coords(self._bar, 0, 0, x, self._meter_h)
        self.itemconfig(self._bar, fill=fill)

        px = int(self._peak * self._meter_w)
        if px > 2:
            self.coords(self._peak_line, max(0, px - 2), 0, px, self._meter_h)
        else:
            self.coords(self._peak_line, 0, 0, 0, 0)


# --------------------------------------------------------------------------
# Settings Dialog
# --------------------------------------------------------------------------
class SettingsDialog:
    def __init__(self, root: tk.Tk, current: dict, meeting_apps: dict, on_save,
                 container=None, out_dir: Path | None = None):
        """container=None opens a window; a Frame embeds the settings (V2.0 main window)."""
        self.root = root
        self.current = dict(current)
        self.meeting_apps = meeting_apps
        self.on_save = on_save
        self.out_dir = out_dir
        self.container = container
        self.embedded = container is not None
        self._test_stop = None
        self._test_levels = {"others": 0.0, "me": 0.0}

        if self.embedded:
            self.w = w = container
        else:
            self.w = w = tk.Toplevel(root)
            w.title("Meeting Note-Taker Settings")
            w.geometry("680x640")
            w.transient(root)
            w.protocol("WM_DELETE_WINDOW", self._close)
            cfg.set_capture_excluded(w, current.get("exclude_capture", False))

        bf = ttk.Frame(w)
        bf.pack(side="bottom", fill="x", padx=2, pady=(8, 2))
        ttk.Button(bf, text="Save & Apply", style="Accent.TButton",
                   command=self._save).pack(side="right", padx=(6, 0))
        ttk.Button(bf, text="Revert" if self.embedded else "Cancel",
                   command=self._close).pack(side="right")
        self.lbl_saved = ttk.Label(bf, text="", style="Success.TLabel")
        self.lbl_saved.pack(side="left")

        nb = ttk.Notebook(w)
        nb.pack(fill="both", expand=True)

        self._build_general_tab(nb)
        self._build_audio_tab(nb)
        self._build_compliance_tab(nb)
        self._build_glossary_tab(nb)
        if not self.embedded:
            self._build_profiles_tab(nb)

        self._pump_test_meters()

    def _build_compliance_tab(self, nb):
        f = ttk.Frame(nb, padding=16, style="TFrame")
        nb.add(f, text="Privacy & Compliance")
        r = 0
        ttk.Label(f, text="Data retention", style="H3.TLabel").grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        ttk.Label(f, text="Delete recordings (WAV) after", style="TLabel").grid(row=r, column=0, sticky="w", pady=4)
        self.v_ret_audio = tk.StringVar(value=str(self.current.get("audio_retention_days", 0)))
        ttk.Spinbox(f, from_=0, to=3650, textvariable=self.v_ret_audio, width=7).grid(row=r, column=1, sticky="w", padx=6)
        ttk.Label(f, text="days  (0 = keep)", style="Muted.TLabel").grid(row=r, column=2, sticky="w")
        r += 1
        ttk.Label(f, text="Delete whole meetings after", style="TLabel").grid(row=r, column=0, sticky="w", pady=4)
        self.v_ret_meet = tk.StringVar(value=str(self.current.get("meeting_retention_days", 0)))
        ttk.Spinbox(f, from_=0, to=3650, textvariable=self.v_ret_meet, width=7).grid(row=r, column=1, sticky="w", padx=6)
        ttk.Label(f, text="days  (0 = keep)", style="Muted.TLabel").grid(row=r, column=2, sticky="w")

        r += 1
        ttk.Label(f, text="Audit & integrations", style="H3.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(16, 2))
        r += 1
        self.v_audit = tk.BooleanVar(value=self.current.get("audit_enabled", True))
        ttk.Checkbutton(f, text="Keep an audit log (recordings, exports, deletions)", style="TCheckbutton",
                        variable=self.v_audit).grid(row=r, column=0, columnspan=2, sticky="w", pady=3)
        if self.out_dir is not None:
            ttk.Button(f, text="Open audit log", style="Ghost.TButton",
                       command=lambda: _open_path(self.out_dir / "audit.jsonl")
                       if (self.out_dir / "audit.jsonl").exists() else None).grid(row=r, column=2, sticky="w")
        r += 1
        self.v_outlook = tk.BooleanVar(value=self.current.get("outlook_integration", True))
        ttk.Checkbutton(f, text="Name the meeting from the Outlook event happening now", style="TCheckbutton",
                        variable=self.v_outlook).grid(row=r, column=0, columnspan=3, sticky="w", pady=3)
        r += 1
        self.v_outlook_att = tk.BooleanVar(value=self.current.get("outlook_attendees", True))
        ttk.Checkbutton(f, text="Suggest attendee names when naming speakers", style="TCheckbutton",
                        variable=self.v_outlook_att).grid(row=r, column=0, columnspan=3, sticky="w", pady=3)

        r += 1
        ttk.Label(f, text="Appearance", style="H3.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(16, 2))
        r += 1
        self.v_theme = tk.StringVar(value=self.current.get("theme", "light"))
        tf = ttk.Frame(f, style="TFrame")
        tf.grid(row=r, column=0, columnspan=3, sticky="w")
        for val, label in (("light", "Light"), ("dark", "Dark")):
            ttk.Radiobutton(tf, text=label, value=val, variable=self.v_theme,
                            style="TRadiobutton").pack(side="left", padx=(0, 14))
        ttk.Label(tf, text="(applies after restart)", style="Muted.TLabel").pack(side="left")
        r += 1
        self.v_compact = tk.BooleanVar(value=self.current.get("compact_on_record", False))
        ttk.Checkbutton(f, text="Switch to the compact recorder when a call starts", style="TCheckbutton",
                        variable=self.v_compact).grid(row=r, column=0, columnspan=3, sticky="w", pady=3)
        r += 1
        self.v_startup = tk.BooleanVar(value=self.current.get("start_with_windows", False))
        ttk.Checkbutton(f, text="Start automatically when I sign in to Windows (compact recorder)",
                        style="TCheckbutton", variable=self.v_startup
                        ).grid(row=r, column=0, columnspan=3, sticky="w", pady=3)

    def _build_general_tab(self, nb):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text="General")

        r = 0
        ttk.Label(f, text="Your name in transcripts:").grid(row=r, column=0, sticky="w", pady=4)
        self.v_name = tk.StringVar(value=self.current.get("my_name", "Me"))
        ttk.Entry(f, textvariable=self.v_name, width=28).grid(row=r, column=1, sticky="w", pady=4)

        r += 1
        self.v_auto = tk.BooleanVar(value=self.current.get("auto_record", True))
        ttk.Checkbutton(f, text="Auto-record when a watched meeting app opens the mic",
                        variable=self.v_auto).grid(row=r, column=0, columnspan=2, sticky="w", pady=3)

        r += 1
        self.v_exclude = tk.BooleanVar(value=self.current.get("exclude_capture", False))
        ttk.Checkbutton(f, text="Hide Note-Taker windows during screen sharing (Windows 10/11)",
                        variable=self.v_exclude).grid(row=r, column=0, columnspan=2, sticky="w", pady=3)

        r += 1
        self.v_live = tk.BooleanVar(value=self.current.get("live_enabled", True))
        ttk.Checkbutton(f, text="Enable live transcription & key points during meetings",
                        variable=self.v_live).grid(row=r, column=0, columnspan=2, sticky="w", pady=3)

        r += 1
        ttk.Label(f, text="Live key-points interval (minutes):").grid(row=r, column=0, sticky="w", pady=4)
        self.v_every = tk.StringVar(value=str(self.current.get("live_every", 5.0)))
        ttk.Entry(f, textvariable=self.v_every, width=10).grid(row=r, column=1, sticky="w", pady=4)

        r += 1
        self.v_final = tk.BooleanVar(value=self.current.get("final_pass", False))
        ttk.Checkbutton(f, text="Run full-recording accuracy pass after meeting ends (slower)",
                        variable=self.v_final).grid(row=r, column=0, columnspan=2, sticky="w", pady=3)

        r += 1
        self.v_spk = tk.BooleanVar(value=self.current.get("speakers_enabled", True))
        ttk.Checkbutton(f, text="Separate and name other speakers after call",
                        variable=self.v_spk).grid(row=r, column=0, columnspan=2, sticky="w", pady=3)

        r += 1
        ttk.Label(f, text="Expected other speakers (blank = auto):").grid(row=r, column=0, sticky="w", pady=4)
        ns = self.current.get("num_speakers")
        self.v_num_spk = tk.StringVar(value="" if ns is None else str(ns))
        ttk.Entry(f, textvariable=self.v_num_spk, width=10).grid(row=r, column=1, sticky="w", pady=4)

        r += 1
        ttk.Label(f, text="Max speakers when auto:").grid(row=r, column=0, sticky="w", pady=4)
        self.v_max_spk = tk.StringVar(value=str(self.current.get("max_speakers") or 12))
        ttk.Entry(f, textvariable=self.v_max_spk, width=10).grid(row=r, column=1, sticky="w", pady=4)

        r += 1
        ttk.Label(f, text="Watched meeting apps:", font=("Segoe UI", 9, "bold")).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(10, 4))
        r += 1
        app_frame = ttk.Frame(f)
        app_frame.grid(row=r, column=0, columnspan=2, sticky="w")
        active_apps = set(self.current.get("apps", []))
        self.v_apps = {}
        for idx, (key, (label, _)) in enumerate(self.meeting_apps.items()):
            var = tk.BooleanVar(value=key in active_apps)
            self.v_apps[key] = var
            ttk.Checkbutton(app_frame, text=label, variable=var).grid(
                row=idx // 3, column=idx % 3, sticky="w", padx=(0, 14), pady=2)

    def _build_audio_tab(self, nb):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text="Audio & AI")

        speakers_list, mics_list = mn.list_audio_devices()
        spk_opts = ["System Default"] + speakers_list
        mic_opts = ["System Default"] + mics_list

        r = 0
        ttk.Label(f, text="Call audio (Others / speaker):").grid(row=r, column=0, sticky="w", pady=4)
        self.v_pref_spk = tk.StringVar(value=self.current.get("preferred_speaker") or "System Default")
        ttk.Combobox(f, textvariable=self.v_pref_spk, values=spk_opts,
                     state="readonly", width=34).grid(row=r, column=1, sticky="w", pady=4)

        r += 1
        ttk.Label(f, text="Microphone (Me):").grid(row=r, column=0, sticky="w", pady=4)
        self.v_pref_mic = tk.StringVar(value=self.current.get("preferred_mic") or "System Default")
        ttk.Combobox(f, textvariable=self.v_pref_mic, values=mic_opts,
                     state="readonly", width=34).grid(row=r, column=1, sticky="w", pady=4)

        r += 1
        test_box = ttk.LabelFrame(f, text="Live Audio Test", padding=8)
        test_box.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(6, 10))
        ttk.Label(test_box, text="Others:").grid(row=0, column=0, sticky="w")
        self.test_m_others = AudioMeter(test_box, width=260, height=12)
        self.test_m_others.grid(row=0, column=1, padx=8, pady=3)
        ttk.Label(test_box, text="Me:").grid(row=1, column=0, sticky="w")
        self.test_m_me = AudioMeter(test_box, width=260, height=12)
        self.test_m_me.grid(row=1, column=1, padx=8, pady=3)
        self.btn_test = ttk.Button(test_box, text="Start Audio Test", command=self._toggle_test)
        self.btn_test.grid(row=0, column=2, rowspan=2, padx=(6, 0))

        r += 1
        ttk.Label(f, text="Compute device:").grid(row=r, column=0, sticky="w", pady=4)
        self.v_dev = tk.StringVar(value=self.current.get("device", "auto"))
        ttk.Combobox(f, textvariable=self.v_dev, values=["auto", "cuda", "cpu"],
                     state="readonly", width=16).grid(row=r, column=1, sticky="w", pady=4)

        r += 1
        ttk.Label(f, text="Whisper model:").grid(row=r, column=0, sticky="w", pady=4)
        self.v_model = tk.StringVar(value=self.current.get("model", "auto"))
        ttk.Combobox(f, textvariable=self.v_model,
                     values=["auto", "tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"],
                     state="readonly", width=16).grid(row=r, column=1, sticky="w", pady=4)

        r += 1
        ttk.Label(f, text="Language (supports Odia, Hindi, etc.):").grid(row=r, column=0, sticky="w", pady=4)
        self.v_lang = tk.StringVar(value=cfg.format_language_choice(self.current.get("language", "")))
        ttk.Combobox(f, textvariable=self.v_lang,
                     values=[label for _k, label in cfg.LANGUAGE_OPTIONS],
                     width=30).grid(row=r, column=1, sticky="w", pady=4)

        r += 1
        ttk.Label(f, text="Auto-detect only these languages:").grid(row=r, column=0, sticky="w", pady=4)
        self.v_autolangs = tk.StringVar(value=", ".join(self.current.get("auto_languages") or []))
        ttk.Entry(f, textvariable=self.v_autolangs, width=30).grid(row=r, column=1, sticky="w", pady=4)
        r += 1
        ttk.Label(f, text="(codes, e.g. en, hi, or - stops Chinese/Korean output; blank = any)",
                  foreground="gray40", font=("Segoe UI", 8)).grid(row=r, column=1, sticky="w")

        r += 1
        ttk.Label(f, text="Speech engine:").grid(row=r, column=0, sticky="w", pady=4)
        self.v_asr = tk.StringVar(value=self.current.get("asr_engine", "auto"))
        ttk.Combobox(f, textvariable=self.v_asr, values=["auto", "whisper", "indic"],
                     state="readonly", width=16).grid(row=r, column=1, sticky="w", pady=4)

        r += 1
        self.v_16k = tk.BooleanVar(value=self.current.get("save_16k", True))
        ttk.Checkbutton(f, text="Save compact 16 kHz WAVs (~67% smaller, same Whisper accuracy)",
                        variable=self.v_16k).grid(row=r, column=0, columnspan=2, sticky="w", pady=4)

        r += 1
        import screen_rec
        has_ff = bool(screen_rec.find_ffmpeg())
        self.v_screen = tk.BooleanVar(value=self.current.get("screen_record", False))
        cb = ttk.Checkbutton(f, text="Record mode: audio + screen (meeting_video.mp4 with the call audio)",
                             variable=self.v_screen)
        cb.grid(row=r, column=0, columnspan=2, sticky="w", pady=(10, 2))
        if not has_ff:
            cb.state(["disabled"])
        r += 1
        sf_ = ttk.Frame(f)
        sf_.grid(row=r, column=0, columnspan=2, sticky="w")
        n_mon = screen_rec.monitor_count()
        self.mon_labels = [f"Screen {i + 1}" + (" (main)" if i == 0 else "") for i in range(n_mon)]
        cur_m = min(int(self.current.get("screen_monitor", 0) or 0), n_mon - 1)
        self.v_screen_mon = tk.StringVar(value=self.mon_labels[cur_m])
        ttk.Label(sf_, text="Screen:").pack(side="left")
        ttk.Combobox(sf_, textvariable=self.v_screen_mon, values=self.mon_labels, state="readonly",
                     width=14).pack(side="left", padx=(4, 14))
        self.v_screen_ask = tk.BooleanVar(value=self.current.get("screen_ask", True))
        ttk.Checkbutton(sf_, text="Ask each time", variable=self.v_screen_ask).pack(side="left", padx=(0, 14))
        ttk.Label(sf_, text="Frames/sec:").pack(side="left")
        self.v_screen_fps = tk.StringVar(value=str(self.current.get("screen_fps", 5)))
        ttk.Combobox(sf_, textvariable=self.v_screen_fps, values=["2", "5", "10", "15"], state="readonly",
                     width=5).pack(side="left", padx=4)
        r += 1
        ttk.Label(f, text=("Captures everything on that screen, including notifications. "
                           "About 0.2-0.7 GB per hour." if has_ff else
                           "Needs FFmpeg: run Install.bat (or 'winget install ffmpeg')."),
                  style="Muted.TLabel").grid(row=r, column=0, columnspan=2, sticky="w")

        r += 1
        ttk.Label(f, text="Claude API model:").grid(row=r, column=0, sticky="w", pady=4)
        self.v_claude = tk.StringVar(value=self.current.get("claude_model", "claude-sonnet-4-5"))
        ttk.Entry(f, textvariable=self.v_claude, width=28).grid(row=r, column=1, sticky="w", pady=4)

        r += 1
        ttk.Label(f, text="Preferred Ollama model (blank = auto):").grid(row=r, column=0, sticky="w", pady=4)
        self.v_ollama = tk.StringVar(value=self.current.get("ollama_model", ""))
        ttk.Entry(f, textvariable=self.v_ollama, width=28).grid(row=r, column=1, sticky="w", pady=4)

    def _build_glossary_tab(self, nb):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text="Glossary & Templates")

        ttk.Label(f, text="Custom Glossary (names, acronyms, product terms for Whisper):",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.txt_glossary = tk.Text(f, height=11, wrap="word")
        theme.style_text(self.txt_glossary)
        self.txt_glossary.configure(font=("Consolas", 10))
        self.txt_glossary.pack(fill="both", expand=True, pady=(4, 10))
        self.txt_glossary.insert("1.0", cfg.load_glossary_raw())

        tpl_row = ttk.Frame(f)
        tpl_row.pack(fill="x")
        ttk.Label(tpl_row, text="Note templates live as .md files in templates/").pack(side="left")
        ttk.Button(tpl_row, text="Open templates folder",
                   command=lambda: _open_path(cfg.ensure_templates())).pack(side="right")

    def _build_profiles_tab(self, nb):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text="Voice Profiles")

        ttk.Label(f, text="Saved colleague voice profiles (biometric embeddings in voices/):",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
        self.lb_profiles = tk.Listbox(f, height=13, font=("Segoe UI", 10))
        self.lb_profiles.pack(fill="both", expand=True)
        self._reload_profiles()

        row = ttk.Frame(f)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Rename / Merge", command=self._rename_profile).pack(side="left")
        ttk.Button(row, text="Delete selected", command=self._delete_profile).pack(side="left", padx=6)
        ttk.Button(row, text="Refresh", command=self._reload_profiles).pack(side="right")

    def _reload_profiles(self):
        self.lb_profiles.delete(0, "end")
        for name in speakers.list_profiles():
            self.lb_profiles.insert("end", name)

    def _rename_profile(self):
        sel = self.lb_profiles.curselection()
        if not sel:
            return
        old = self.lb_profiles.get(sel[0])
        new = simpledialog.askstring("Rename voice profile",
                                     f"New name for '{old}' (existing name will merge):",
                                     initialvalue=old, parent=self.w)
        if new and speakers.rename_profile(old, new):
            self._reload_profiles()

    def _delete_profile(self):
        sel = self.lb_profiles.curselection()
        if not sel:
            return
        name = self.lb_profiles.get(sel[0])
        if messagebox.askyesno("Delete voice profile",
                               f"Permanently delete saved voice profile for '{name}'?",
                               parent=self.w):
            speakers.delete_profile(name)
            self._reload_profiles()

    def _toggle_test(self):
        if self._test_stop is not None:
            self._stop_test()
            return
        self._test_stop = threading.Event()
        self.btn_test.config(text="Stop Audio Test")
        pref_spk = "" if self.v_pref_spk.get() == "System Default" else self.v_pref_spk.get()
        pref_mic = "" if self.v_pref_mic.get() == "System Default" else self.v_pref_mic.get()

        def probe(kind, pref):
            dev, ctx, rec, _err = mn._open_stream(kind, pref)
            if dev is None:
                return
            try:
                while not self._test_stop.is_set():
                    data = rec.record(numframes=mn.BLOCK)
                    mono = data.mean(axis=1).astype("float32")
                    self._test_levels[kind] = min(1.0, float((mono ** 2).mean() ** 0.5))
            except Exception:
                pass
            finally:
                self._test_levels[kind] = 0.0
                try:
                    ctx.__exit__(None, None, None)
                except Exception:
                    pass

        for k, pref in (("others", pref_spk), ("me", pref_mic)):
            threading.Thread(target=probe, args=(k, pref), daemon=True).start()

    def _stop_test(self):
        if self._test_stop is not None:
            self._test_stop.set()
            self._test_stop = None
        self._test_levels = {"others": 0.0, "me": 0.0}
        try:
            self.btn_test.config(text="Start Audio Test")
        except tk.TclError:
            pass

    def _pump_test_meters(self):
        if not self.w.winfo_exists():
            return
        self.test_m_others.set_level(self._test_levels["others"])
        self.test_m_me.set_level(self._test_levels["me"])
        self.w.after(80, self._pump_test_meters)

    def _save(self):
        apps = [k for k, v in self.v_apps.items() if v.get()]
        if not apps:
            messagebox.showwarning("Settings", "Select at least one meeting app.", parent=self.w)
            return
        try:
            every = max(1.0, float(self.v_every.get()))
        except ValueError:
            every = 5.0
        ns_raw = self.v_num_spk.get().strip()
        num_spk = int(ns_raw) if ns_raw.isdigit() and int(ns_raw) > 0 else None
        ms_raw = self.v_max_spk.get().strip()
        max_spk = min(50, int(ms_raw)) if ms_raw.isdigit() and int(ms_raw) > 0 else 12
        import lang_guard
        auto_langs = lang_guard.parse_allowed(self.v_autolangs.get())

        updated = dict(self.current)
        updated.update({
            "my_name": self.v_name.get().strip() or "Me",
            "auto_record": self.v_auto.get(),
            "exclude_capture": self.v_exclude.get(),
            "live_enabled": self.v_live.get(),
            "live_every": every,
            "final_pass": self.v_final.get(),
            "speakers_enabled": self.v_spk.get(),
            "num_speakers": num_spk,
            "max_speakers": max_spk,
            "auto_languages": auto_langs,
            "apps": apps,
            "preferred_speaker": "" if self.v_pref_spk.get() == "System Default" else self.v_pref_spk.get(),
            "preferred_mic": "" if self.v_pref_mic.get() == "System Default" else self.v_pref_mic.get(),
            "device": self.v_dev.get(),
            "model": self.v_model.get(),
            "language": cfg.parse_language_code(self.v_lang.get()),
            "asr_engine": self.v_asr.get(),
            "save_16k": self.v_16k.get(),
            "claude_model": self.v_claude.get().strip() or "claude-sonnet-4-5",
            "ollama_model": self.v_ollama.get().strip(),
            "audio_retention_days": _int(self.v_ret_audio.get()),
            "meeting_retention_days": _int(self.v_ret_meet.get()),
            "audit_enabled": self.v_audit.get(),
            "outlook_integration": self.v_outlook.get(),
            "outlook_attendees": self.v_outlook_att.get(),
            "theme": self.v_theme.get(),
            "compact_on_record": self.v_compact.get(),
            "screen_record": self.v_screen.get(),
            "screen_monitor": self.mon_labels.index(self.v_screen_mon.get())
                              if self.v_screen_mon.get() in self.mon_labels else 0,
            "screen_fps": _int(self.v_screen_fps.get(), 5) or 5,
            "screen_ask": self.v_screen_ask.get(),
            "screen_area": self.current.get("screen_area") if (
                self.mon_labels.index(self.v_screen_mon.get()) if self.v_screen_mon.get() in self.mon_labels
                else 0) == int(self.current.get("screen_monitor", 0) or 0) else None,
            "start_with_windows": self.v_startup.get(),
        })
        r_meet, r_audio = updated["meeting_retention_days"], updated["audio_retention_days"]
        if (r_meet and r_meet != self.current.get("meeting_retention_days")) or \
                (r_audio and r_audio != self.current.get("audio_retention_days")):
            if not messagebox.askyesno(
                    "Data retention",
                    "Recordings / meetings older than the new limits will be deleted "
                    "permanently the next time retention runs (at start-up and daily).\n\nContinue?",
                    parent=self.w):
                return
        cfg.save_settings(updated)
        cfg.save_glossary_raw(self.txt_glossary.get("1.0", "end-1c"))
        if cfg.indic_needed(updated):
            import indic_asr
            if not indic_asr.is_available():
                messagebox.showinfo(
                    "Odia / Indic Setup Required",
                    "You selected Odia / IndicConformer.\n\n"
                    "Please double-click \"Install Indic Languages (Odia).bat\" once "
                    "to install the offline IndicConformer engine.",
                    parent=self.w,
                )
        self._stop_test()
        if self.embedded:
            self.current = dict(updated)
            self.lbl_saved.config(text="✓ Saved " + time.strftime("%H:%M"))
        else:
            self.w.destroy()
        self.on_save(updated)

    def _close(self):
        self._stop_test()
        if self.embedded:                      # Revert: rebuild from the saved values
            for child in self.w.winfo_children():
                child.destroy()
            self.__init__(self.root, cfg.load_settings(), self.meeting_apps, self.on_save,
                          container=self.w, out_dir=self.out_dir)
            theme.scale_tree(self.w)
            return
        self.w.destroy()


def _int(value, default: int = 0) -> int:
    try:
        return max(0, int(str(value).strip()))
    except ValueError:
        return default


# --------------------------------------------------------------------------
# Past Meetings Browser & Search
# --------------------------------------------------------------------------
class HistoryWindow:
    def __init__(self, root: tk.Tk, out_dir: Path, settings_getter, on_rerun_speakers=None,
                 container=None, on_change=None):
        """container=None opens a window; a Frame embeds the browser (V2.0 main window)."""
        self.root = root
        self.out_dir = out_dir
        self.settings_getter = settings_getter
        self.on_rerun_speakers = on_rerun_speakers
        self.on_change = on_change or (lambda: None)
        self.meetings = []          # list of dicts
        self.selected: Path | None = None

        if container is not None:
            self.w = w = container
        else:
            self.w = w = tk.Toplevel(root)
            w.title("Meetings")
            w.geometry("1100x660")
            cfg.set_capture_excluded(w, settings_getter().get("exclude_capture", False))

        # Search bar
        top = ttk.Frame(w)
        top.pack(fill="x", pady=(0, 10))
        self.v_query = tk.StringVar()
        search = ttk.Entry(top, textvariable=self.v_query, width=46)
        search.pack(side="left")
        search.bind("<KeyRelease>", lambda _e: self._filter())
        ttk.Label(top, text="  Search titles, notes and transcripts", style="Muted.TLabel").pack(side="left")
        ttk.Button(top, text="Delete meeting", style="Danger.TButton",
                   command=self._delete_meeting).pack(side="right")
        ttk.Button(top, text="Delete recordings", command=self._delete_audio).pack(side="right", padx=6)
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")

        # Bottom actions (packed before the pane so they never get pushed off-screen)
        bot = ttk.Frame(w)
        bot.pack(side="bottom", fill="x", pady=(10, 0))
        self.lbl_status = ttk.Label(w, text="Files: N = notes, T = transcript, A = audio, V = screen video",
                                    style="Muted.TLabel")
        self.lbl_status.pack(side="bottom", anchor="w", pady=(6, 0))
        ttk.Button(bot, text="Export notes (HTML / PDF)", style="Accent.TButton",
                   command=self._export).pack(side="left")
        ttk.Button(bot, text="▶ Play video", command=self._play_video).pack(side="left", padx=(6, 0))
        ttk.Button(bot, text="Copy notes", command=self._copy_notes).pack(side="left", padx=6)
        ttk.Button(bot, text="Open folder", command=self._open_selected).pack(side="left")
        self.tpl_map = cfg.list_templates()
        self.tpl_names = {label: key for key, (label, _p) in self.tpl_map.items()}
        self.v_tpl = tk.StringVar(value=next(iter(self.tpl_names), "Executive Minutes (Standard)"))
        ttk.Combobox(bot, textvariable=self.v_tpl, values=list(self.tpl_names),
                     state="readonly", width=26).pack(side="left", padx=(16, 4))
        ttk.Button(bot, text="Regenerate notes", command=self._regen_notes).pack(side="left")
        ttk.Button(bot, text="Re-run speakers", command=self._rerun_speakers).pack(side="left", padx=6)

        # Main split pane
        pane = ttk.PanedWindow(w, orient="horizontal")
        pane.pack(fill="both", expand=True)

        left_o, left = theme.card(pane, padding=0)
        pane.add(left_o, weight=2)
        cols = ("date", "title", "duration", "size", "flags")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for c, label, width, anchor, stretch in (
                ("date", "Date", 130, "w", False), ("title", "Title", 200, "w", True),
                ("duration", "Length", 70, "e", False), ("size", "Size", 75, "e", False),
                ("flags", "Files", 55, "center", False)):
            self.tree.heading(c, text=label, anchor=anchor)
            self.tree.column(c, width=width, anchor=anchor, stretch=stretch)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        right = ttk.Frame(pane)
        pane.add(right, weight=3)
        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True, padx=(10, 0))
        self.txt_notes = self._text_tab(self.nb, "Notes")
        self.txt_tx = self._text_tab(self.nb, "Transcript")

        self.refresh()

    def _text_tab(self, nb, title):
        fr = ttk.Frame(nb)
        nb.add(fr, text=title)
        t = tk.Text(fr, wrap="word")
        theme.style_text(t)
        sb = ttk.Scrollbar(fr, command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return t

    def _scan_folder(self, folder: Path) -> dict:
        when = ent.folder_datetime(folder)
        date_s = when.strftime("%Y-%m-%d  %H:%M") if when else ""
        title = ent.meeting_title(folder)
        total_b = sum(f.stat().st_size for f in folder.iterdir() if f.is_file())
        has_n = (folder / "notes.md").exists() or (folder / "claude_prompt.md").exists()
        has_t = (folder / "transcript.md").exists()
        has_a = any(folder.glob("audio_*.wav"))
        flags = ("N" if has_n else "-") + ("T" if has_t else "-") + ("A" if has_a else "-")             + ("V" if (folder / "meeting_video.mp4").exists() else "")
        secs = ent.meeting_seconds(folder)
        blob = title.lower()
        for fn in ("notes.md", "transcript.md"):
            p = folder / fn
            if p.exists():
                try:
                    blob += "\n" + p.read_text(encoding="utf-8", errors="ignore").lower()
                except OSError:
                    pass
        return {"folder": folder, "date": date_s, "title": title,
                "duration": ent.human_duration(secs) if secs else "",
                "size": ent.human_size(total_b), "flags": flags, "blob": blob}

    def refresh(self):
        self.meetings = [self._scan_folder(d) for d in ent.list_meetings(self.out_dir)]
        self._filter()

    def select_folder(self, folder: Path):
        for idx, item in enumerate(self.meetings):
            if item["folder"] == folder and self.tree.exists(str(idx)):
                self.tree.selection_set(str(idx))
                self.tree.see(str(idx))
                return

    def _audit(self, event, **kw):
        ent.audit(self.out_dir, event, self.settings_getter().get("audit_enabled", True), **kw)

    def _play_video(self):
        if not self.selected:
            return
        v = self.selected / "meeting_video.mp4"
        if v.exists():
            _open_path(v)
        else:
            self.lbl_status.config(text="No screen recording for this meeting.")

    def _export(self):
        if not self.selected:
            return
        out = ent.export_notes_html(self.selected)
        if out is None:
            self.lbl_status.config(text="This meeting has no notes.md yet - generate notes first.")
            return
        self._audit("notes_exported", meeting=self.selected.name, format="html")
        self.lbl_status.config(text=f"Exported {out.name}. In the browser, press Ctrl+P → Save as PDF.")
        _open_path(out)

    def _delete_meeting(self):
        if not self.selected:
            return
        folder = self.selected
        if not messagebox.askyesno(
                "Delete meeting",
                f"Permanently delete this meeting?\n\n{ent.meeting_title(folder)}\n\n"
                "Audio, transcript and notes will be removed. This cannot be undone.",
                icon="warning", parent=self.w):
            return
        shutil.rmtree(folder, ignore_errors=True)
        self._audit("meeting_deleted", meeting=folder.name)
        self.selected = None
        self.txt_notes.delete("1.0", "end")
        self.txt_tx.delete("1.0", "end")
        self.refresh()
        self.on_change()
        self.lbl_status.config(text="Meeting deleted.")

    def _filter(self):
        q = self.v_query.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        for idx, item in enumerate(self.meetings):
            if not q or q in item["blob"]:
                self.tree.insert("", "end", iid=str(idx),
                                 values=(item["date"], item["title"], item["duration"],
                                         item["size"], item["flags"]))

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        item = self.meetings[int(sel[0])]
        self.selected = item["folder"]
        self._load_preview(self.selected)

    def _load_preview(self, folder: Path):
        self.txt_notes.delete("1.0", "end")
        self.txt_tx.delete("1.0", "end")
        notes_p = folder / "notes.md"
        prompt_p = folder / "claude_prompt.md"
        tx_p = folder / "transcript.md"
        if notes_p.exists():
            render_markdown(self.txt_notes, notes_p.read_text(encoding="utf-8", errors="ignore"))
        elif prompt_p.exists():
            self.txt_notes.insert("1.0", "(No API notes saved — showing claude_prompt.md)\n\n" +
                                  prompt_p.read_text(encoding="utf-8", errors="ignore"))
        else:
            self.txt_notes.insert("1.0", "(No notes generated for this meeting yet.)")
        if tx_p.exists():
            render_transcript(self.txt_tx, tx_p.read_text(encoding="utf-8", errors="ignore"))
        else:
            self.txt_tx.insert("1.0", "(No transcript.md found.)")

    def _open_selected(self):
        if self.selected:
            _open_path(self.selected)

    def _copy_notes(self):
        text = self.txt_notes.get("1.0", "end-1c").strip()
        if not text:
            return
        if mn._copy_to_clipboard(text):
            self.lbl_status.config(text="Notes copied to privacy clipboard.")
        else:
            self.w.clipboard_clear()
            self.w.clipboard_append(text)
            self.lbl_status.config(text="Notes copied to clipboard.")

    def _regen_notes(self):
        if not self.selected:
            return
        tx_p = self.selected / "transcript.md"
        if not tx_p.exists():
            messagebox.showwarning("Regenerate notes", "No transcript.md in this folder.", parent=self.w)
            return
        tpl_key = self.tpl_names.get(self.v_tpl.get(), "executive_minutes")
        transcript = tx_p.read_text(encoding="utf-8", errors="ignore")
        # Bug fix V1.6.2: use the meeting's own title and date, not the folder
        # name and today's date.
        item = next((m for m in self.meetings if m["folder"] == self.selected), None)
        title = (item and item["title"]) or self.selected.name
        date_s = (item and item["date"]) or time.strftime("%d %B %Y, %H:%M")
        folder = self.selected
        s = self.settings_getter()
        self.lbl_status.config(text=f"Generating notes with template '{self.v_tpl.get()}'...")

        def work():
            try:
                mn.make_notes(folder, transcript, title, date_s,
                              template_key=tpl_key, claude_model=s.get("claude_model"))
                self._audit("notes_regenerated", meeting=folder.name, template=tpl_key)
                self.w.after(0, lambda: (self._load_preview(folder),
                                         self.refresh(), self.on_change(),
                                         self.lbl_status.config(text="Notes regenerated.")))
            except Exception as e:
                self.w.after(0, lambda e=e: self.lbl_status.config(text=f"Error: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _rerun_speakers(self):
        if not self.selected:
            return
        if not (self.selected / "audio_others.wav").exists():
            messagebox.showwarning("Re-run speakers",
                                   "audio_others.wav is missing (audio may have been deleted).",
                                   parent=self.w)
            return
        if self.on_rerun_speakers:
            self.lbl_status.config(text="Running speaker separation in background...")
            self.on_rerun_speakers(self.selected, lambda: self.w.after(0, lambda: (
                self._load_preview(self.selected),
                self.lbl_status.config(text="Speaker naming updated.")
            )))

    def _delete_audio(self):
        if not self.selected:
            return
        wavs = [*self.selected.glob("audio_*.wav"), *self.selected.glob("meeting_video.mp4")]
        if not wavs:
            self.lbl_status.config(text="No recordings (audio / video) in this meeting folder.")
            return
        mb = sum(f.stat().st_size for f in wavs) / (1024 * 1024)
        if not messagebox.askyesno("Delete recordings",
                                   f"Delete {len(wavs)} recording file(s) ({mb:.1f} MB) from this meeting?\n"
                                   "Your transcript.md and notes.md will be kept.",
                                   parent=self.w):
            return
        for f in wavs:
            f.unlink(missing_ok=True)
        self._audit("audio_deleted", meeting=self.selected.name, files=len(wavs))
        self.refresh()
        self.on_change()
        self.lbl_status.config(text=f"Deleted audio ({mb:.1f} MB freed).")


# --------------------------------------------------------------------------
# Formatted previews (V2.0)
# --------------------------------------------------------------------------
def _tags(t: tk.Text):
    c, f = theme.C, theme.FONT
    t.tag_configure("h1", font=(f, 15, "bold"), spacing1=theme.px(4), spacing3=theme.px(6))
    t.tag_configure("h2", font=(f, 12, "bold"), foreground=c["accent"],
                    spacing1=theme.px(12), spacing3=theme.px(4))
    t.tag_configure("h3", font=(f, 10, "bold"), spacing1=theme.px(8))
    t.tag_configure("muted", foreground=c["muted"])
    t.tag_configure("bold", font=(f, 10, "bold"))
    t.tag_configure("bullet", lmargin1=theme.px(8), lmargin2=theme.px(24))
    t.tag_configure("th", font=(f, 9, "bold"), foreground=c["muted"], background=c["surface2"])
    t.tag_configure("speaker", font=(f, 10, "bold"), foreground=c["accent"])
    t.tag_configure("time", foreground=c["faint"], font=("Consolas", 9))
    t.tag_configure("pin", foreground=c["warning"], font=(f, 10, "bold"))


def _insert_inline(t: tk.Text, text: str, base=()):
    for part in re.split(r"(\*\*.+?\*\*)", text):
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            t.insert("end", part[2:-2], base + ("bold",))
        elif part:
            t.insert("end", part, base)


def render_markdown(t: tk.Text, md: str):
    """Readable notes: headings, bullets, tables laid out with tab stops."""
    _tags(t)
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        st = lines[i].strip()
        if st.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            rows = [ent._cells(lines[i])]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(ent._cells(lines[i]))
                i += 1
            ncol = max(len(r) for r in rows)
            widths = [max(len(r[k]) if k < len(r) else 0 for r in rows) for k in range(ncol)]
            stops, acc = [], 0
            for w in widths[:-1]:
                acc += min(46, max(10, w + 3))
                stops.append(theme.px(acc * 7.2))
            tag = f"tbl{i}"
            t.tag_configure(tag, tabs=tuple(stops), lmargin1=theme.px(8), lmargin2=theme.px(8),
                            spacing1=theme.px(3), spacing3=theme.px(3))
            for n, r in enumerate(rows):
                for k, cell in enumerate(r):
                    if k:
                        t.insert("end", "\t", (tag,))
                    _insert_inline(t, cell, (tag, "th") if n == 0 else (tag,))
                t.insert("end", "\n", (tag,))
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", st)
        if m:
            level = min(3, len(m.group(1)))
            t.insert("end", m.group(2) + "\n", (f"h{level}",))
        elif re.match(r"^[-*]\s+", st):
            t.insert("end", "•  ", ("bullet",))
            _insert_inline(t, re.sub(r"^[-*]\s+", "", st), ("bullet",))
            t.insert("end", "\n", ("bullet",))
        elif re.fullmatch(r"_.*_", st):
            t.insert("end", st.strip("_") + "\n", ("muted",))
        else:
            _insert_inline(t, lines[i].rstrip())
            t.insert("end", "\n")
        i += 1


def render_transcript(t: tk.Text, text: str):
    _tags(t)
    for line in text.splitlines():
        m = re.match(r"^\[(\d+:\d\d:\d\d)\]\s+(.*)$", line)
        if not m:
            t.insert("end", line + "\n")
            continue
        t.insert("end", m.group(1) + "   ", ("time",))
        rest = m.group(2)
        if rest.startswith("[BOOKMARK]"):
            t.insert("end", "★ " + rest[10:].strip() + "\n", ("pin",))
            continue
        who, sep, said = rest.partition(": ")
        if sep and len(who) <= 40:
            t.insert("end", who, ("speaker",))
            t.insert("end", "  " + said + "\n")
        else:
            t.insert("end", rest + "\n")
