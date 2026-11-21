"""
Meeting Auto Note-Taker (Windows) - engine + classic UI (used by app.py in 2.0)
========================================
Runs in a small always-on-top window (hidden from screen sharing by default).
Automatically detects calls in Teams, Zoom, Webex, Slack, GoTo, RingCentral,
Skype (and optionally browsers), displays live audio level meters for both
Others and Me, transcribes live with custom glossary support, lets you pin
important moments, separates speakers using full segment spans, and generates
meeting notes from selectable Markdown templates.
"""

import argparse
import datetime as dt
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

try:
    import winreg
except ImportError:
    winreg = None

import indic_asr
import live_notes as ln
import meeting_notes as mn
import settings as cfg
import speakers
import ui_dialogs as ui

MIC_KEY = (r"Software\Microsoft\Windows\CurrentVersion"
           r"\CapabilityAccessManager\ConsentStore\microphone")
START_AFTER_S = 3      # a meeting app must hold the mic this long before we start
END_AFTER_S = 8        # ...and release it this long before we stop
MIN_KEEP_S = 15        # recordings shorter than this are discarded (mic tests etc.)

MEETING_APPS = {
    "teams": ("Teams", ("teams",)),
    "zoom": ("Zoom", ("zoom",)),
    "webex": ("Webex", ("webex", "ciscocollab", "atmgr")),
    "slack": ("Slack", ("slack",)),
    "goto": ("GoTo Meeting", ("gotomeeting", "g2m", "goto")),
    "ringcentral": ("RingCentral", ("ringcentral",)),
    "skype": ("Skype", ("skype",)),
    "browser": ("Browser meeting", ("chrome", "msedge", "firefox", "brave", "opera")),
}


def _app_using_mic(path: str, watch: dict):
    if winreg is None:
        return None
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        return None
    with key:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(key, i)
            except OSError:
                return None
            i += 1
            low = name.lower()
            if "python" in low:                       # never ourselves
                continue
            app = next((label for label, words in watch.values()
                        if any(w in low for w in words)), None)
            if not app:
                continue
            try:
                with winreg.OpenKey(key, name) as sub:
                    start, _ = winreg.QueryValueEx(sub, "LastUsedTimeStart")
                    stop, _ = winreg.QueryValueEx(sub, "LastUsedTimeStop")
                if start and stop == 0:               # 0 = still in use
                    return app
            except OSError as e:
                print(f"Could not read mic status for {name}: {e}")


def meeting_in_call(watch: dict):
    """Name of the meeting app currently using the mic, or None."""
    return (_app_using_mic(MIC_KEY, watch) or
            _app_using_mic(MIC_KEY + r"\NonPackaged", watch))


# --------------------------------------------------------------------------
# Recorder with pause, mute, live level telemetry, and preferred devices
# --------------------------------------------------------------------------
class Recorder:
    def __init__(self, folder: Path, live=None, preferred_speaker: str = "",
                 preferred_mic: str = "", save_16k: bool = True):
        self.folder, self.live = folder, live
        self.preferred_speaker = preferred_speaker
        self.preferred_mic = preferred_mic
        self.save_16k = save_16k
        self.stop_evt = threading.Event()
        self.paused = threading.Event()
        self.muted = threading.Event()   # set = your mic is OFF
        self.errors = []
        self.frames = {"others": 0, "me": 0}
        self.threads = []
        self.levels = {"others": 0.0, "me": 0.0}
        self.last_others_sound = time.monotonic()

    def _counter(self, kind):
        def cb(n):
            self.frames[kind] += n
        return cb

    def _feed(self, kind):
        return (lambda samples: self.live.feed(kind, samples)) if self.live else None

    def _on_level(self, kind):
        def cb(rms: float):
            self.levels[kind] = rms
            if kind == "others" and rms > 0.004:
                self.last_others_sound = time.monotonic()
        return cb

    def start(self):
        self.last_others_sound = time.monotonic()
        self.threads = [
            threading.Thread(target=mn.record_stream, daemon=True,
                             args=("others", self.folder / "audio_others.wav",
                                   self.stop_evt, self.errors, self.paused, self._counter("others")),
                             kwargs={"on_audio": self._feed("others"),
                                     "on_level": self._on_level("others"),
                                     "preferred_device": self.preferred_speaker,
                                     "save_16k": self.save_16k}),
            threading.Thread(target=mn.record_stream, daemon=True,
                             args=("me", self.folder / "audio_me.wav",
                                   self.stop_evt, self.errors, self.paused,
                                   self._counter("me"), self.muted),
                             kwargs={"on_audio": self._feed("me"),
                                     "on_level": self._on_level("me"),
                                     "preferred_device": self.preferred_mic,
                                     "save_16k": self.save_16k}),
        ]
        for t in self.threads:
            t.start()

    def stop(self):
        self.stop_evt.set()
        for t in self.threads:
            t.join(timeout=5)
        self.levels = {"others": 0.0, "me": 0.0}

    @property
    def seconds(self) -> float:
        # Bug fix V1.6.2: count BOTH streams. Before, only "others" counted, so
        # if call audio failed but the mic worked, the whole meeting folder was
        # deleted as a "<15 s recording".
        return max(self.frames.values()) / mn.SAMPLE_RATE


# --------------------------------------------------------------------------
# Background processing (transcribe + speakers + notes), one meeting at a time
# --------------------------------------------------------------------------
class Processor:
    def __init__(self, settings_getter, on_finished=None):
        self.settings_getter = settings_getter
        self.on_finished = on_finished          # V2.0: called with (folder) after each job
        self.jobs = queue.Queue()
        self.requests = queue.Queue()
        self.pending = 0
        self._pending_lock = threading.Lock()
        self.current_folder: Path | None = None
        self.stage = ""                         # V2.0: shown on the dashboard
        threading.Thread(target=self._loop, daemon=True).start()

    def _bump(self, n: int):
        with self._pending_lock:
            self.pending += n

    def add(self, folder: Path, title: str, date: str, live=None, template_key: str = "executive_minutes"):
        self._bump(1)
        self.jobs.put(("meeting", folder, title, date, live, template_key, None))

    def rerun_speakers(self, folder: Path, on_complete=None):
        self._bump(1)
        self.jobs.put(("speakers", folder, "", "", None, "", on_complete))

    def _loop(self):
        while True:
            kind, folder, title, date, live, template_key, on_complete = self.jobs.get()
            self.current_folder = folder
            s = self.settings_getter()
            my_name = s.get("my_name", "Me") or "Me"
            try:
                if kind == "speakers":
                    tx_path = folder / "transcript.md"
                    transcript = tx_path.read_text(encoding="utf-8", errors="ignore") if tx_path.exists() else ""
                    transcript = speakers.identify(
                        folder, transcript, my_name, s.get("speaker_threshold", speakers.DEFAULT_THRESHOLD),
                        s.get("num_speakers"), ask=self._ask,
                        max_speakers=s.get("max_speakers", speakers.DEFAULT_MAX_SPEAKERS))
                    tx_path.write_text(transcript, encoding="utf-8")
                    if on_complete:
                        on_complete()
                    continue

                self.stage = "Transcribing"
                if live and not s.get("final_pass", False):
                    live.finish()
                    transcript = live.text()
                else:
                    if live:
                        live.cancel()
                    transcript = mn.transcribe(folder, s.get("model", "auto"),
                                               s.get("language") or None,
                                               s.get("device", "auto"),
                                               my_name=my_name,
                                               asr_engine=s.get("asr_engine", "auto"),
                                               auto_languages=s.get("auto_languages") or [])

                if s.get("speakers_enabled", True):
                    self.stage = "Separating speakers"
                    try:
                        transcript = speakers.identify(
                            folder, transcript, my_name,
                            s.get("speaker_threshold", speakers.DEFAULT_THRESHOLD),
                            s.get("num_speakers"), ask=self._ask,
                            max_speakers=s.get("max_speakers", speakers.DEFAULT_MAX_SPEAKERS))
                        (folder / "transcript.md").write_text(transcript, encoding="utf-8")
                    except Exception:
                        print(f"Speaker naming skipped:\n{traceback.format_exc()}")
                elif my_name != "Me":
                    # Bug fix: still apply --my-name even when speaker separation is OFF
                    transcript = transcript.replace("] Me: ", f"] {my_name}: ")
                    (folder / "transcript.md").write_text(transcript, encoding="utf-8")

                if s.get("ai_enabled", True):
                    self.stage = "Writing notes"
                    mn.make_notes(folder, transcript, title, date,
                                  template_key=template_key,
                                  claude_model=s.get("claude_model"))
                    import enterprise as ent
                    ent.audit(folder.parent, "notes_generated", s.get("audit_enabled", True),
                              meeting=folder.name, template=template_key,
                              engine="Claude API" if (folder / "notes.md").exists() else "clipboard hand-off")
                print(f"Finished: {folder}")
            except Exception:
                print(f"Processing failed for {folder}:\n{traceback.format_exc()}")
            finally:
                self._bump(-1)
                self.stage = ""
                self.current_folder = None
                if self.on_finished:
                    try:
                        self.on_finished(folder)
                    except Exception as e:
                        print(f"on_finished failed: {e}")

    def _ask(self, info, suggest, default):
        """Ask the window to show 'Who was speaking?' and wait (max 10 min)."""
        req = {"info": info, "suggest": suggest, "default": default,
               "folder": self.current_folder,
               "event": threading.Event(), "result": None}
        self.requests.put(req)
        if not req["event"].wait(timeout=600):
            print("No names given within 10 minutes; using saved voices / Speaker N.")
        return req["result"]


# --------------------------------------------------------------------------
# Control window
# --------------------------------------------------------------------------
class App:
    def __init__(self, settings_data: dict, out_dir: Path):
        self.s = settings_data
        self.out_dir = out_dir
        self.proc = Processor(lambda: self.s)
        self.rec: Recorder | None = None
        self.stopping = False
        self.started_at = None
        self.auto_started = False
        self.skip_this_call = False
        self.on_s = self.off_s = 0
        self.watch = {k: MEETING_APPS[k] for k in self.s["apps"] if k in MEETING_APPS}
        self.app_now = None
        self.auto_title = None
        self.live_win = None
        self.history_win = None
        self.hidden = False
        self._live_was_open = False
        self._hotkey_stop = None
        self._wake_stop = None

        self.root = root = tk.Tk()
        root.title("Meeting Note-Taker V1.5")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        cfg.set_capture_excluded(root, self.s.get("exclude_capture", False))

        pad = {"padx": 10, "pady": 3}
        self.status = tk.Label(root, text="", font=("Segoe UI", 12, "bold"))
        self.status.pack(**pad)
        self.timer = tk.Label(root, text="", font=("Segoe UI", 10))
        self.timer.pack()

        # Title + Template row
        row = tk.Frame(root)
        row.pack(fill="x", **pad)
        tk.Label(row, text="Title:", width=8, anchor="w").pack(side="left")
        self.title_var = tk.StringVar(value="Meeting")
        tk.Entry(row, textvariable=self.title_var, width=28).pack(side="left", padx=4, fill="x", expand=True)

        tpl_row = tk.Frame(root)
        tpl_row.pack(fill="x", **pad)
        tk.Label(tpl_row, text="Template:", width=8, anchor="w").pack(side="left")
        self.tpl_map = cfg.list_templates()
        self.tpl_label_to_key = {label: key for key, (label, _p) in self.tpl_map.items()}
        cur_key = self.s.get("template", "executive_minutes")
        cur_label = self.tpl_map.get(cur_key, next(iter(self.tpl_map.values())))[0]
        self.tpl_var = tk.StringVar(value=cur_label)
        self.cmb_tpl = ttk.Combobox(tpl_row, textvariable=self.tpl_var,
                                    values=list(self.tpl_label_to_key),
                                    state="readonly", width=26)
        self.cmb_tpl.pack(side="left", padx=4, fill="x", expand=True)
        self.cmb_tpl.bind("<<ComboboxSelected>>", self._on_template_change)

        # Live Audio Level Meters
        meters = tk.Frame(root)
        meters.pack(fill="x", padx=10, pady=(4, 4))
        tk.Label(meters, text="Others:", font=("Segoe UI", 8), width=7, anchor="w").grid(row=0, column=0, sticky="w")
        self.meter_others = ui.AudioMeter(meters, width=230, height=10)
        self.meter_others.grid(row=0, column=1, padx=4, pady=2)
        tk.Label(meters, text="Me:", font=("Segoe UI", 8), width=7, anchor="w").grid(row=1, column=0, sticky="w")
        self.meter_me = ui.AudioMeter(meters, width=230, height=10)
        self.meter_me.grid(row=1, column=1, padx=4, pady=2)

        # Primary recording controls
        btns = tk.Frame(root)
        btns.pack(**pad)
        self.btn_start = tk.Button(btns, text="● Start", width=8,
                                   command=lambda: self.start(auto=False))
        self.btn_pause = tk.Button(btns, text="⏸ Pause", width=8,
                                   command=self.toggle_pause)
        self.btn_stop = tk.Button(btns, text="■ Stop", width=8,
                                  command=lambda: self.stop("stopped by you"))
        self.btn_pin = tk.Button(btns, text="★ Pin", width=7,
                                 command=self.pin_moment)
        for b in (self.btn_start, self.btn_pause, self.btn_stop, self.btn_pin):
            b.pack(side="left", padx=2)

        self.mic_on = tk.BooleanVar(value=self.s.get("mic_on", True))
        self.btn_mic = tk.Button(root, width=36, command=self.toggle_mic,
                                 font=("Segoe UI", 9, "bold"))
        self.btn_mic.pack(pady=(2, 4))

        # Utility buttons: Live Notes | Past Meetings | Settings
        tools = tk.Frame(root)
        tools.pack(pady=(0, 4))
        self.btn_live = tk.Button(tools, text="Live notes", width=9, command=self.open_live)
        self.btn_live.pack(side="left", padx=2)
        tk.Button(tools, text="History", width=8, command=self.open_history).pack(side="left", padx=2)
        tk.Button(tools, text="⚙ Settings", width=9, command=self.open_settings).pack(side="left", padx=2)
        tk.Button(tools, text="Hide", width=6, command=self.hide_window).pack(side="left", padx=2)

        if self.s.get("live_enabled", True):
            self._ensure_live_window()

        self.auto_var = tk.BooleanVar(value=self.s.get("auto_record", True))
        tk.Checkbutton(root, text="Auto-record calls", variable=self.auto_var).pack()
        self.lbl_watch = tk.Label(root, text="", font=("Segoe UI", 8), fg="gray40", wraplength=320)
        self.lbl_watch.pack()
        self._update_watch_label()

        self.info = tk.Label(root, text="", font=("Segoe UI", 8), fg="gray40", wraplength=320)
        self.info.pack(**pad)
        tk.Label(root, text="Remember: tell participants you are recording.",
                 font=("Segoe UI", 8), fg="firebrick").pack(pady=(0, 8))

        tk.Label(root, text="Hide / Unhide anytime: Ctrl+Shift+H  (or re-open launcher)",
                 font=("Segoe UI", 8), fg="gray40").pack(pady=(0, 4))

        self._hotkey_stop = cfg.start_global_hotkey(lambda: self.root.after(0, self.toggle_hidden))
        self._wake_stop = cfg.start_wake_listener(
            self.out_dir / ".instance_port",
            lambda: self.root.after(0, self.show_window),
        )

        self.refresh()
        root.after(100, self.pump_meters)
        root.after(1000, self.tick)

    def _selected_template_key(self) -> str:
        return self.tpl_label_to_key.get(self.tpl_var.get(), "executive_minutes")

    def _on_template_change(self, _event=None):
        self.s["template"] = self._selected_template_key()
        cfg.save_settings(self.s)

    def _ensure_live_window(self):
        if self.live_win is None:
            self.live_win = ln.LiveWindow(
                self.root, self.s.get("live_every", 5.0),
                exclude_capture=self.s.get("exclude_capture", False),
                template_key_getter=self._selected_template_key,
                preferred_ollama=self.s.get("ollama_model", ""),
                claude_model=self.s.get("claude_model"),
            )
            self.live_win.win.withdraw()

    def _update_watch_label(self):
        names = ", ".join(label for label, _ in self.watch.values())
        hidden = " | Hidden from screen share" if self.s.get("exclude_capture", False) else ""
        self.lbl_watch.config(text=f"Watching: {names}{hidden}")

    # ----- hide / unhide (stealth while screen sharing) -----
    def hide_window(self):
        if self.hidden:
            return
        self.hidden = True
        self._live_was_open = bool(self.live_win and self.live_win.win.state() != "withdrawn")
        if self.live_win:
            self.live_win.win.withdraw()
        self.root.withdraw()

    def show_window(self):
        self.hidden = False
        self.root.deiconify()
        self.root.lift()
        if self._live_was_open and self.live_win:
            self.live_win.win.deiconify()

    def toggle_hidden(self):
        if self.hidden:
            self.show_window()
        else:
            self.hide_window()

    # ----- dialogs -----
    def open_live(self):
        if not self.s.get("live_enabled", True):
            messagebox.showinfo("Live notes", "Live notes are disabled in Settings.", parent=self.root)
            return
        self._ensure_live_window()
        self.live_win.win.deiconify()
        self.live_win.win.lift()

    def open_history(self):
        if self.history_win and self.history_win.w.winfo_exists():
            self.history_win.w.deiconify()
            self.history_win.w.lift()
            self.history_win.refresh()
            return
        self.history_win = ui.HistoryWindow(
            self.root, self.out_dir, lambda: self.s,
            on_rerun_speakers=self.proc.rerun_speakers,
        )

    def open_settings(self):
        ui.SettingsDialog(self.root, self.s, MEETING_APPS, on_save=self._apply_settings)

    def _apply_settings(self, updated: dict):
        self.s = updated
        self.watch = {k: MEETING_APPS[k] for k in self.s["apps"] if k in MEETING_APPS}
        self.auto_var.set(self.s.get("auto_record", True))
        cfg.set_capture_excluded(self.root, self.s.get("exclude_capture", False))
        if self.live_win:
            self.live_win.refresh_config(
                self.s.get("live_every", 5.0),
                self.s.get("exclude_capture", False),
                preferred_ollama=self.s.get("ollama_model", ""),
                claude_model=self.s.get("claude_model"),
            )
        # Refresh template dropdown in case user added a template
        self.tpl_map = cfg.list_templates()
        self.tpl_label_to_key = {label: key for key, (label, _p) in self.tpl_map.items()}
        self.cmb_tpl.config(values=list(self.tpl_label_to_key))
        self._update_watch_label()
        self.refresh()

    # ----- actions -----
    def start(self, auto: bool):
        if self.rec or self.stopping:
            return
        now = dt.datetime.now()
        if auto and self.app_now and self.title_var.get() in ("Meeting", "", self.auto_title):
            self.auto_title = f"{self.app_now} call"
            self.title_var.set(self.auto_title)
        folder = mn.new_meeting_folder(self.out_dir, self.title_var.get(), now)
        live = None
        if self.s.get("live_enabled", True):
            self._ensure_live_window()
            live = ln.LiveTranscriber(folder, self.s.get("model", "auto"),
                                      self.s.get("device", "auto"),
                                      self.s.get("language") or None,
                                      my_name=self.s.get("my_name", "Me"),
                                      asr_engine=self.s.get("asr_engine", "auto"),
                                      auto_languages=self.s.get("auto_languages") or [])
            self.live_win.attach(live, self.title_var.get())
        self.rec = Recorder(
            folder, live,
            preferred_speaker=self.s.get("preferred_speaker", ""),
            preferred_mic=self.s.get("preferred_mic", ""),
            save_16k=self.s.get("save_16k", True),
        )
        self.started_at, self.auto_started = now, auto
        if not self.mic_on.get():
            self.rec.muted.set()
        try:
            self.rec.start()
        except Exception as e:
            if live:
                live.cancel()
            self.rec = None
            messagebox.showerror("Meeting Note-Taker", f"Could not start recording:\n{e}")
            return
        if auto and not self.hidden:
            self.root.deiconify()
            self.root.lift()
            self.root.bell()
        self.refresh()

    def pin_moment(self):
        if not self.rec:
            return
        note = simpledialog.askstring("Pin moment", "Short note for this moment (optional):",
                                      parent=self.root)
        if note is None:
            return
        if self.rec.live:
            sec = self.rec.live.add_bookmark(note)
            self.info.config(text=f"★ Pinned at {ln.stamp(sec)}")
        else:
            self.info.config(text="★ Pin requires Live Notes enabled.")

    def toggle_mic(self):
        self.mic_on.set(not self.mic_on.get())
        if self.rec:
            if self.mic_on.get():
                self.rec.muted.clear()
            else:
                self.rec.muted.set()
        self.refresh()

    def toggle_pause(self):
        if not self.rec:
            return
        if self.rec.paused.is_set():
            self.rec.paused.clear()
        else:
            self.rec.paused.set()
        self.refresh()

    def stop(self, reason: str):
        if not self.rec or self.stopping:
            return
        self.stopping = True
        rec, self.rec = self.rec, None
        auto_started = self.auto_started
        title = self.title_var.get() or "Meeting"
        started_str = f"{self.started_at:%d %B %Y, %H:%M}"
        tpl_key = self._selected_template_key()
        self.status.config(text="Stopping...", fg="darkorange")
        self.refresh()

        def finish_stop():
            rec.stop()
            self.root.after(0, lambda: self._after_stop(rec, reason, auto_started, title, started_str, tpl_key))

        threading.Thread(target=finish_stop, daemon=True).start()

    def _after_stop(self, rec: Recorder, reason: str, auto_started: bool,
                    title: str, started_str: str, tpl_key: str):
        self.stopping = False
        if reason == "stopped by you" and auto_started and meeting_in_call(self.watch):
            self.skip_this_call = True
        if rec.errors:
            messagebox.showerror("Meeting Note-Taker",
                                 "Recording problem:\n" + "\n".join(rec.errors))
        if rec.seconds < MIN_KEEP_S:
            if rec.live:
                rec.live.cancel()
            shutil.rmtree(rec.folder, ignore_errors=True)
            print(f"Discarded {rec.seconds:.0f}s recording ({reason}).")
        else:
            print(f"Stopped ({reason}) after {rec.seconds / 60:.1f} min.")
            if self.live_win:
                self.live_win.ended()
            self.proc.add(rec.folder, title, started_str, rec.live, template_key=tpl_key)
        self.refresh()

    # ----- loops -----
    def pump_meters(self):
        if self.rec and not self.rec.paused.is_set():
            self.meter_others.set_level(self.rec.levels["others"])
            self.meter_me.set_level(0.0 if self.rec.muted.is_set() else self.rec.levels["me"])
        else:
            self.meter_others.set_level(0.0)
            self.meter_me.set_level(0.0)
        self.root.after(80, self.pump_meters)

    def tick(self):
        if self.rec and len(self.rec.errors) >= 2:
            self.stop("recording error")

        self.app_now = meeting_in_call(self.watch)
        in_call = bool(self.app_now)
        self.on_s, self.off_s = (self.on_s + 1, 0) if in_call else (0, self.off_s + 1)
        if self.off_s >= END_AFTER_S:
            self.skip_this_call = False

        if self.auto_var.get() and not self.stopping:
            if not self.rec and not self.skip_this_call and self.on_s >= START_AFTER_S:
                self.start(auto=True)
            elif self.rec and self.auto_started and self.off_s >= END_AFTER_S:
                self.stop("call ended")

        if self.live_win:
            self.live_win.tick(recording=bool(self.rec))
        while not self.proc.requests.empty():
            req = self.proc.requests.get()
            speakers.NameDialog(
                self.root, req["info"], req["suggest"], req["default"],
                on_done=lambda res, r=req: (r.__setitem__("result", res), r["event"].set()),
                exclude_capture=self.s.get("exclude_capture", False),
            )
        self.refresh(in_call)
        self.root.after(1000, self.tick)

    def refresh(self, in_call: bool | None = None):
        if self.stopping:
            self.status.config(text="Stopping...", fg="darkorange")
        elif self.rec and self.rec.paused.is_set():
            self.status.config(text="⏸ Paused", fg="darkorange")
        elif self.rec:
            self.status.config(text="● Recording", fg="red")
        else:
            self.status.config(text="Waiting for a call..." if self.auto_var.get()
                               else "Idle", fg="gray20")
        if self.rec:
            s = int(self.rec.seconds)
            self.timer.config(text=f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")
        else:
            self.timer.config(text="")

        if self.mic_on.get():
            self.btn_mic.config(text="My voice: ON   (click to turn off)", fg="darkgreen")
        else:
            self.btn_mic.config(text="My voice: OFF   (click to turn on)", fg="firebrick")

        active = bool(self.rec) and not self.stopping
        self.btn_start.config(state="disabled" if (self.rec or self.stopping) else "normal")
        self.btn_pause.config(state="normal" if active else "disabled",
                              text="▶ Resume" if self.rec and self.rec.paused.is_set()
                              else "⏸ Pause")
        self.btn_stop.config(state="normal" if active else "disabled")
        self.btn_pin.config(state="normal" if active else "disabled")

        bits = []
        if in_call is not None:
            bits.append(f"Call detected: {self.app_now}" if in_call else "No call")
        if self.rec and not self.rec.paused.is_set():
            silent_for = time.monotonic() - self.rec.last_others_sound
            if self.rec.seconds > 20 and silent_for > 20:
                bits.append("No 'Others' audio detected — check speaker in Settings")
        if self.rec and self.rec.live and self.rec.live.behind_s > 90:
            bits.append(f"Live transcript {self.rec.live.behind_s:.0f} s behind")
        if self.rec and self.rec.errors:
            bits.append("Warning: " + self.rec.errors[0])
        if self.proc.pending:
            bits.append(f"Processing {self.proc.pending} meeting(s)...")
        if cfg.indic_needed(self.s) and not indic_asr.is_available():
            bits.append("Odia needs the Indic pack: run 'Install Indic Languages (Odia).bat'")
        self.info.config(text="   |   ".join(bits))

    def on_close(self):
        if self.rec:
            if not messagebox.askyesno("Meeting Note-Taker",
                                       "A recording is running. Stop it and quit?\n"
                                       "(It will NOT be transcribed.)"):
                return
            self.rec.stop()
            if self.rec.live:
                self.rec.live.cancel()
        elif self.proc.pending and not messagebox.askyesno(
                "Meeting Note-Taker",
                f"{self.proc.pending} meeting(s) are still being processed.\n"
                "Quit anyway? You can re-run them later from Past meetings."):
            return
        if self._hotkey_stop:
            self._hotkey_stop.set()
        if self._wake_stop:
            self._wake_stop.set()
        self.root.destroy()


def main():
    saved = cfg.load_settings()
    p = argparse.ArgumentParser(description="Auto-record meetings and take notes, live (V1.5).")
    p.add_argument("--model", default=None)
    p.add_argument("--device", default=None, choices=["auto", "cpu", "cuda"])
    p.add_argument("--language", default=None)
    p.add_argument("--no-mic", action="store_true")
    p.add_argument("--no-ai", action="store_true")
    p.add_argument("--apps", default=None)
    p.add_argument("--no-live", action="store_true")
    p.add_argument("--live-every", type=float, default=None)
    p.add_argument("--final-pass", action="store_true")
    p.add_argument("--my-name", default=None)
    p.add_argument("--no-speakers", action="store_true")
    p.add_argument("--num-speakers", type=int, default=None)
    p.add_argument("--speaker-threshold", type=float, default=None)
    p.add_argument("--template", default=None)
    p.add_argument("--out", type=Path, default=Path(__file__).parent / "meetings")
    args = p.parse_args()

    # Apply explicit CLI overrides on top of saved JSON settings
    if args.model:
        saved["model"] = args.model
    if args.device:
        saved["device"] = args.device
    if args.language is not None:
        saved["language"] = args.language
    if args.no_mic:
        saved["mic_on"] = False
    if args.no_ai:
        saved["ai_enabled"] = False
    if args.no_live:
        saved["live_enabled"] = False
    if args.live_every is not None:
        saved["live_every"] = args.live_every
    if args.final_pass:
        saved["final_pass"] = True
    if args.my_name:
        saved["my_name"] = args.my_name
    if args.no_speakers:
        saved["speakers_enabled"] = False
    if args.num_speakers is not None:
        saved["num_speakers"] = args.num_speakers
    if args.speaker_threshold is not None:
        saved["speaker_threshold"] = args.speaker_threshold
    if args.template:
        saved["template"] = args.template
    if args.apps:
        parsed_apps = [a.strip().lower() for a in args.apps.split(",") if a.strip()]
        bad = [a for a in parsed_apps if a not in MEETING_APPS]
        if bad or not parsed_apps:
            p.error(f"unknown app(s) {bad}; choose from {', '.join(MEETING_APPS)}")
        saved["apps"] = parsed_apps

    args.out.mkdir(parents=True, exist_ok=True)
    if cfg.wake_existing_instance(args.out / ".instance_port"):
        return
    cfg.ensure_glossary()
    cfg.ensure_templates()
    cfg.setup_rotating_log(args.out / "notetaker.log")
    print(f"\n=== Started Meeting Note-Taker V1.5 ({dt.datetime.now():%Y-%m-%d %H:%M}) ===")

    App(saved, args.out).root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        err = traceback.format_exc()
        try:
            log_p = Path(__file__).parent / "meetings" / "notetaker.log"
            log_p.parent.mkdir(parents=True, exist_ok=True)
            with open(log_p, "a", encoding="utf-8") as lf:
                lf.write("\n=== Startup/Runtime Crash ===\n" + err + "\n")
        except Exception:
            pass
        cfg.show_error_popup(
            "Meeting Note-Taker Error",
            "Meeting Note-Taker could not start.\n\n"
            + err[-1200:]
            + "\n\nFull details were saved to meetings\\notetaker.log",
        )
        raise
