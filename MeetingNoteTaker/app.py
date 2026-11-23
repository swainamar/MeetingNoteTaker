"""
Meeting Note-Taker 2.0 - Enterprise Edition (Windows)
=====================================================
One main window with a sidebar:

  Dashboard     KPIs, current session, meters, recent meetings, open actions
  Live meeting  live transcript + rolling AI key points (no extra window)
  Meetings      searchable library, preview, export (HTML/PDF), regenerate, delete
  Action items  every action item from every meeting, tick off, export CSV
  Voices        saved voice profiles (biometric data) with consent-aware controls
  Settings      all settings incl. Privacy & Compliance (retention, audit, Outlook)
  Audit log     who recorded / exported / deleted what, and when

Plus a compact always-on-top mini recorder, light/dark theme, Outlook meeting
titles and attendee name suggestions, data retention and an audit trail.
The recording / transcription engine is unchanged from V1.6.2.
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
from tkinter import filedialog, messagebox, simpledialog, ttk

import enterprise as ent
import indic_asr
import live_notes as ln
import meeting_notes as mn
import settings as cfg
import speakers
import theme
import screen_picker
import screen_rec
import ui_dialogs as ui
from teams_autorecorder import (END_AFTER_S, MEETING_APPS, MIN_KEEP_S, START_AFTER_S,
                                Processor, Recorder, meeting_in_call)

VERSION = "2.2"
APP_NAME = "Meeting Note-Taker"
DEFAULT_TITLES = ("", "Meeting")
MODES = ("🎙  Audio only", "🖥  Audio + screen")
RETENTION_EVERY_S = 6 * 3600


# --------------------------------------------------------------------------
# Live meeting page (reuses LiveWindow logic, embedded instead of a popup)
# --------------------------------------------------------------------------
class LivePanel(ln.LiveWindow):
    def __init__(self, parent, root, every_min: float, template_key_getter,
                 preferred_ollama: str = "", claude_model: str | None = None):
        self.root, self.every_s = root, every_min * 60
        self.template_key_getter = template_key_getter
        self.preferred_ollama = preferred_ollama
        self.claude_model = claude_model
        self.live = None
        self.seen = -1
        self.busy = False
        self.pending_notes = None
        self.last_summary = ""
        self.summarized_idx = 0
        self.next_auto = 0.0
        self.engine_name, self.engine = None, None
        self.win = root
        threading.Thread(target=self._find_engine, daemon=True).start()

        head = ttk.Frame(parent)
        head.pack(fill="x", pady=(0, 10))
        self.head = ttk.Label(head, text="Waiting for a meeting…", style="H2.TLabel")
        self.head.pack(side="left")
        self.ai_state = ttk.Label(head, text="", style="Muted.TLabel")
        self.ai_state.pack(side="right")

        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 10))
        self.btn_ai = ttk.Button(bar, text="Update key points now", style="Accent.TButton",
                                 command=self.update_notes)
        self.btn_ai.pack(side="left")
        ttk.Button(bar, text="★ Pin moment", command=self.pin_moment).pack(side="left", padx=6)
        ttk.Button(bar, text="Send so far to Claude", command=self.send_to_claude).pack(side="left")
        ttk.Button(bar, text="Copy transcript", command=self.copy).pack(side="left", padx=6)

        body = ttk.Frame(parent)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)
        self.tx = self._card_text(body, "Transcript", 0, (0, 8))
        self.notes = self._card_text(body, "Key points so far", 1, (8, 0))
        self.notes.insert("1.0", "Key points appear here during the meeting.")

    def _card_text(self, parent, title, col, padx):
        outer, inner = theme.card(parent, padding=12)
        outer.grid(row=0, column=col, sticky="nsew", padx=padx)
        ttk.Label(inner, text=title, style="Card.H3.TLabel").pack(anchor="w", pady=(0, 6))
        fr = ttk.Frame(inner, style="Card.TFrame")
        fr.pack(fill="both", expand=True)
        t = tk.Text(fr, wrap="word", height=10)
        theme.style_text(t)
        t.configure(highlightthickness=0)
        sb = ttk.Scrollbar(fr, command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return t

    def attach(self, live, title: str):
        self.live, self.seen = live, -1
        self.last_summary = ""
        self.summarized_idx = 0
        self.next_auto = time.monotonic() + self.every_s
        self.head.config(text=f"Live: {title}")
        self.tx.delete("1.0", "end")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", "Key points appear here during the meeting.")

    def refresh_config(self, every_min: float, exclude_capture: bool,
                       preferred_ollama: str = "", claude_model: str | None = None):
        self.every_s = every_min * 60
        self.preferred_ollama = preferred_ollama
        self.claude_model = claude_model
        threading.Thread(target=self._find_engine, daemon=True).start()


# --------------------------------------------------------------------------
# Compact always-on-top recorder
# --------------------------------------------------------------------------
class MiniRecorder:
    def __init__(self, app: "App"):
        self.app = app
        self.w = w = tk.Toplevel(app.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=theme.C["sidebar"])
        cfg.set_capture_excluded(w, app.s.get("exclude_capture", False))
        f = tk.Frame(w, bg=theme.C["sidebar"], padx=10, pady=8)
        f.pack()
        self.pill = theme.Pill(f, "Idle")
        self.pill.pack(side="left")
        self.timer = tk.Label(f, text="", bg=theme.C["sidebar"], fg="#FFFFFF",
                              font=(theme.FONT, 11, "bold"), padx=10)
        self.timer.pack(side="left")
        self.meter = ui.AudioMeter(f, width=70, height=8)
        self.meter.configure(bg=theme.C["sidebar_active"])
        self.meter.pack(side="left", padx=(0, 8))
        self.buttons = {}
        for key, text, cmd in (("start", "●", lambda: app.start(auto=False)),
                               ("pause", "❚❚", app.toggle_pause),
                               ("stop", "■", lambda: app.stop("stopped by you")),
                               ("pin", "★", app.pin_moment),
                               ("expand", "⤢", app.exit_compact)):
            b = tk.Label(f, text=text, bg=theme.C["sidebar"], fg="#FFFFFF", cursor="hand2",
                         font=(theme.FONT, 12), padx=6)
            b.pack(side="left")
            b.bind("<Button-1>", lambda _e, c=cmd, k=key: self._click(k, c))
            self.buttons[key] = b
        for widget in (w, f, self.pill, self.timer):
            widget.bind("<ButtonPress-1>", self._grab)
            widget.bind("<B1-Motion>", self._drag)
        theme.scale_tree(w)
        w.update_idletasks()
        x = w.winfo_screenwidth() - w.winfo_reqwidth() - theme.px(24)
        w.geometry(f"+{x}+{theme.px(24)}")
        self._ofs = (0, 0)

    def _click(self, key, cmd):
        if self.buttons[key].cget("fg") != theme.C["sidebar_muted"]:
            cmd()

    def _grab(self, e):
        self._ofs = (e.x_root - self.w.winfo_x(), e.y_root - self.w.winfo_y())

    def _drag(self, e):
        self.w.geometry(f"+{e.x_root - self._ofs[0]}+{e.y_root - self._ofs[1]}")

    def update(self, pill_text, pill_kind, timer, level, recording, paused):
        self.pill.set(pill_text, pill_kind)
        self.timer.config(text=timer)
        self.meter.set_level(level)
        on, off = "#FFFFFF", theme.C["sidebar_muted"]
        self.buttons["start"].config(fg=off if recording else theme.C["danger"])
        self.buttons["pause"].config(fg=on if recording else off, text="▶" if paused else "❚❚")
        self.buttons["stop"].config(fg=on if recording else off)
        self.buttons["pin"].config(fg=on if recording else off)

    def destroy(self):
        try:
            self.w.destroy()
        except tk.TclError:
            pass


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
class App:
    PAGES = [("dashboard", "Dashboard", "⌂"), ("live", "Live meeting", "●"),
             ("meetings", "Meetings", "☰"), ("actions", "Action items", "✓"),
             ("voices", "Voices", "♪"), ("settings", "Settings", "⚙"),
             ("audit", "Audit log", "≡")]
    SUBTITLES = {
        "dashboard": "Your meetings at a glance",
        "live": "Live transcript and rolling key points",
        "meetings": "Search, review, export and manage recorded meetings",
        "actions": "Every action item from every meeting, in one place",
        "voices": "Saved voice profiles used to name speakers automatically",
        "settings": "Recording, transcription, AI, privacy and compliance",
        "audit": "Who recorded, exported or deleted what, and when",
    }

    def __init__(self, settings_data: dict, out_dir: Path):
        self.s = settings_data
        self.out_dir = out_dir
        self.proc = Processor(lambda: self.s,
                              on_finished=lambda _f: self.later(self.refresh_data))
        self.rec: Recorder | None = None
        self.stopping = False
        self.started_at = None
        self.auto_started = False
        self.skip_this_call = False
        self.on_s = self.off_s = 0
        self.watch = {k: MEETING_APPS[k] for k in self.s["apps"] if k in MEETING_APPS}
        self.app_now = None
        self.auto_title = None
        self.hidden = False
        self.mini: MiniRecorder | None = None
        self.current_page = None
        self.last_retention = 0.0
        self.action_items: list[dict] = []
        self.indic_ok = indic_asr.is_available()
        self.ui_q = queue.Queue()   # work handed from threads to the Tk thread

        theme.enable_dpi()          # crisp text on high-DPI (e.g. 200%) screens
        # Own taskbar icon instead of Python's. Started from MeetingNoteTaker.exe the exe already
        # owns the window, and an explicit ID would stop a pinned exe from matching the window.
        if sys.platform == "win32" and Path(sys.executable).stem.lower() != "meetingnotetaker":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MeetingNoteTaker.Enterprise")
            except (AttributeError, OSError):
                pass
        self.root = root = tk.Tk()
        icon = Path(__file__).parent / "assets" / "logo.ico"
        if icon.exists():
            try:
                root.iconbitmap(default=str(icon))      # every window gets the logo
            except tk.TclError:
                pass
        theme.apply(root, self.s.get("theme", "light"))
        root.title(f"{APP_NAME} {VERSION}")
        px = theme.px
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(px(1280), int(sw * 0.92)), min(px(820), int(sh * 0.88))
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}")
        root.minsize(min(px(1040), sw), min(px(660), sh))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        cfg.set_capture_excluded(root, self.s.get("exclude_capture", False))

        self._build_sidebar()
        main = ttk.Frame(root)
        main.pack(side="left", fill="both", expand=True)
        self._build_topbar(main)
        self.content = ttk.Frame(main, padding=(24, 20, 24, 20))
        self.content.pack(fill="both", expand=True)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        self.pages = {}
        for key, _label, _icon in self.PAGES:
            f = ttk.Frame(self.content)
            f.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = f
        self._build_dashboard(self.pages["dashboard"])
        self.live_panel = LivePanel(self.pages["live"], root, self.s.get("live_every", 5.0),
                                    self._selected_template_key,
                                    preferred_ollama=self.s.get("ollama_model", ""),
                                    claude_model=self.s.get("claude_model"))
        self.history = ui.HistoryWindow(root, out_dir, lambda: self.s,
                                        on_rerun_speakers=self.proc.rerun_speakers,
                                        container=self.pages["meetings"],
                                        on_change=self.refresh_data)
        self._build_actions(self.pages["actions"])
        self._build_voices(self.pages["voices"])
        self.settings_view = ui.SettingsDialog(root, self.s, MEETING_APPS, on_save=self._apply_settings,
                                               container=self.pages["settings"], out_dir=out_dir)
        self._build_audit(self.pages["audit"])
        self.show("dashboard")
        theme.scale_tree(root)

        self._hotkey_stop = cfg.start_global_hotkey(lambda: self.later(self.toggle_hidden))
        self._wake_stop = cfg.start_wake_listener(out_dir / ".instance_port",
                                                  lambda: self.later(self.show_window))
        self.audit("app_started", version=VERSION)
        self.refresh_data()
        self.refresh()
        root.after(1500, self._run_retention)
        root.after(100, self.pump_meters)
        root.after(1000, self.tick)

    # ------------------------------------------------------------------ build
    def _build_sidebar(self):
        c = theme.C
        side = ttk.Frame(self.root, style="Side.TFrame", width=theme.px(228))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = ttk.Frame(side, style="Side.TFrame", padding=(20, 22, 20, 18))
        brand.pack(fill="x")
        ttk.Label(brand, text="◉  NoteTaker", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(brand, text=f"ENTERPRISE EDITION  ·  v{VERSION}", style="SideMuted.TLabel").pack(anchor="w", pady=(2, 0))
        tk.Frame(side, bg=c["sidebar_active"], height=1).pack(fill="x", padx=16, pady=(0, 10))
        self.nav = {}
        for key, label, icon in self.PAGES:
            b = ttk.Button(side, text=f"  {icon}    {label}", style="Nav.TButton",
                           command=lambda k=key: self.show(k), takefocus=False)
            b.pack(fill="x", padx=10, pady=1)
            self.nav[key] = b
        bottom = ttk.Frame(side, style="Side.TFrame", padding=(10, 10, 10, 16))
        bottom.pack(side="bottom", fill="x")
        ttk.Button(bottom, text="  🎙    Voice to Prompt", style="Nav.TButton",
                   command=self.open_voice_prompt, takefocus=False).pack(fill="x")
        ttk.Button(bottom, text="  ⤡    Compact recorder", style="Nav.TButton",
                   command=self.enter_compact, takefocus=False).pack(fill="x")
        ttk.Button(bottom, text="  ⊘    Hide  (Ctrl+Shift+H)", style="Nav.TButton",
                   command=self.hide_window, takefocus=False).pack(fill="x", pady=(1, 10))
        ttk.Label(bottom, text="Transcription runs on this PC. Tell participants you are recording.",
                  style="SideMuted.TLabel", justify="left", wraplength=180).pack(anchor="w", padx=10)

    def _build_topbar(self, parent):
        c = theme.C
        top = ttk.Frame(parent, style="Top.TFrame", padding=(24, 14, 24, 14))
        top.pack(fill="x")
        tk.Frame(parent, bg=c["border"], height=1).pack(fill="x")
        # Controls are packed first so they keep their space; the page title gives way.
        right = ttk.Frame(top, style="Top.TFrame")
        right.pack(side="right")
        left = ttk.Frame(top, style="Top.TFrame")
        left.pack(side="left", fill="x", expand=True)
        self.page_title = ttk.Label(left, text="", style="Top.H1.TLabel")
        self.page_title.pack(anchor="w")
        self.page_sub = ttk.Label(left, text="", style="Top.Muted.TLabel")
        self.page_sub.pack(anchor="w")

        self.pill = theme.Pill(right, "Idle")
        self.pill.pack(side="left", padx=(0, 10))
        # Idle: mode + Start.  Recording: timer + Pause / Stop / Pin.  Only one set is shown.
        self.idle_box = ttk.Frame(right, style="Top.TFrame")
        self.rec_box = ttk.Frame(right, style="Top.TFrame")
        self.mode_var = tk.StringVar(value=MODES[1] if self.s.get("screen_record") else MODES[0])
        self.cmb_mode = ttk.Combobox(self.idle_box, textvariable=self.mode_var, values=MODES,
                                     state="readonly", width=16)
        self.cmb_mode.bind("<<ComboboxSelected>>", lambda _e: self._on_mode_change())
        self.cmb_mode.pack(side="left", padx=(0, 8))
        self.btn_start = ttk.Button(self.idle_box, text="●  Start", style="Accent.TButton",
                                    command=lambda: self.start(auto=False))
        self.btn_start.pack(side="left", padx=3)
        self.timer = ttk.Label(self.rec_box, text="", style="Top.H2.TLabel", width=8)
        self.timer.pack(side="left", padx=(0, 6))
        self.btn_pause = ttk.Button(self.rec_box, text="❚❚  Pause", command=self.toggle_pause, width=-9)
        self.btn_stop = ttk.Button(self.rec_box, text="■  Stop", style="Danger.TButton", width=-7,
                                   command=lambda: self.stop("stopped by you"))
        self.btn_pin = ttk.Button(self.rec_box, text="★  Pin", command=self.pin_moment, width=-6)
        for b in (self.btn_pause, self.btn_stop, self.btn_pin):
            b.pack(side="left", padx=3)
        self.mic_on = tk.BooleanVar(value=self.s.get("mic_on", True))
        self.btn_mic = ttk.Button(right, text="", command=self.toggle_mic, width=-10)
        self.idle_box.pack(side="left")
        self.btn_mic.pack(side="left", padx=3)
        self._controls_rec = False

    def _kpi(self, parent, col, label):
        outer, inner = theme.card(parent, padding=(18, 14))
        outer.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
        ttk.Label(inner, text=label.upper(), style="Card.Muted.TLabel").pack(anchor="w")
        value = ttk.Label(inner, text="–", style="Card.KPI.TLabel")
        value.pack(anchor="w", pady=(2, 0))
        sub = ttk.Label(inner, text="", style="Card.Muted.TLabel")
        sub.pack(anchor="w")
        return value, sub

    def _build_dashboard(self, page):
        page.columnconfigure(0, weight=3)
        page.columnconfigure(1, weight=2)
        page.rowconfigure(2, weight=1)

        kpis = ttk.Frame(page)
        kpis.grid(row=0, column=0, columnspan=2, sticky="ew")
        for i in range(4):
            kpis.columnconfigure(i, weight=1, uniform="kpi")
        self.k_meet = self._kpi(kpis, 0, "Meetings · 30 days")
        self.k_hours = self._kpi(kpis, 1, "Time recorded · 30 days")
        self.k_actions = self._kpi(kpis, 2, "Open action items")
        self.k_storage = self._kpi(kpis, 3, "Storage used")

        # Current session
        so, s = theme.card(page)
        so.grid(row=1, column=0, sticky="nsew", pady=(16, 0), padx=(0, 8))
        s.columnconfigure(1, weight=1)
        ttk.Label(s, text="Current session", style="Card.H2.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        self.lbl_detect = ttk.Label(s, text="", style="Card.Muted.TLabel")
        self.lbl_detect.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(s, text="Title", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.title_var = tk.StringVar(value="Meeting")
        ttk.Entry(s, textvariable=self.title_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(s, text="Notes template", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=4, padx=(0, 12))
        self.tpl_map = cfg.list_templates()
        self.tpl_label_to_key = {label: key for key, (label, _p) in self.tpl_map.items()}
        cur_key = self.s.get("template", "executive_minutes")
        cur_label = self.tpl_map.get(cur_key, next(iter(self.tpl_map.values())))[0]
        self.tpl_var = tk.StringVar(value=cur_label)
        self.cmb_tpl = ttk.Combobox(s, textvariable=self.tpl_var, values=list(self.tpl_label_to_key),
                                    state="readonly")
        self.cmb_tpl.grid(row=3, column=1, columnspan=2, sticky="ew", pady=4)
        self.cmb_tpl.bind("<<ComboboxSelected>>", self._on_template_change)
        ttk.Label(s, text="Call audio", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=(10, 2))
        self.meter_others = ui.AudioMeter(s, width=300, height=10)
        self.meter_others.grid(row=4, column=1, sticky="w", pady=(10, 2))
        ttk.Label(s, text="Your mic", style="Card.TLabel").grid(row=5, column=0, sticky="w", pady=2)
        self.meter_me = ui.AudioMeter(s, width=300, height=10)
        self.meter_me.grid(row=5, column=1, sticky="w", pady=2)
        ttk.Label(s, text="Record", style="Card.TLabel").grid(row=6, column=0, sticky="w", pady=(12, 2))
        rf = ttk.Frame(s, style="Card.TFrame")
        rf.grid(row=6, column=1, columnspan=2, sticky="w", pady=(12, 2))
        for m in MODES:
            ttk.Radiobutton(rf, text=m.replace("  ", " "), value=m, variable=self.mode_var,
                            style="Card.TRadiobutton", command=self._on_mode_change
                            ).pack(side="left", padx=(0, 18))
        ttk.Label(s, text="Screen", style="Card.TLabel").grid(row=7, column=0, sticky="w", pady=2)
        tf = ttk.Frame(s, style="Card.TFrame")
        tf.grid(row=7, column=1, columnspan=2, sticky="w", pady=2)
        self.lbl_target = ttk.Label(tf, text="", style="Card.TLabel")
        self.lbl_target.pack(side="left")
        self.btn_target = ttk.Button(tf, text="Choose screen / area…", style="Ghost.TButton",
                                     command=self.choose_target)
        self.btn_target.pack(side="left", padx=(10, 0))
        self.ask_var = tk.BooleanVar(value=self.s.get("screen_ask", True))
        ttk.Checkbutton(s, text="Ask which screen or area each time",
                        variable=self.ask_var, style="Card.TCheckbutton", command=self._on_ask_toggle
                        ).grid(row=8, column=1, columnspan=2, sticky="w", pady=(2, 2))
        self.auto_var = tk.BooleanVar(value=self.s.get("auto_record", True))
        ttk.Checkbutton(s, text="Auto-record when a call starts", variable=self.auto_var,
                        style="Card.TCheckbutton", command=self._on_auto_toggle
                        ).grid(row=9, column=0, columnspan=3, sticky="w", pady=(10, 2))
        self.lbl_info = ttk.Label(s, text="", style="Card.Warn.TLabel", wraplength=560, justify="left")
        self.lbl_info.grid(row=10, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self._update_target_label()

        # Recent meetings
        ro, r = theme.card(page)
        ro.grid(row=1, column=1, rowspan=2, sticky="nsew", pady=(16, 0), padx=(8, 0))
        head = ttk.Frame(r, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="Recent meetings", style="Card.H2.TLabel").pack(side="left")
        ttk.Button(head, text="View all →", style="Ghost.TButton",
                   command=lambda: self.show("meetings")).pack(side="right")
        self.recent = ttk.Treeview(r, columns=("date", "title", "len"), show="headings",
                                   selectmode="browse", height=8)
        for col, text, w, anchor, stretch in (("date", "Date", 110, "w", False),
                                              ("title", "Title", 160, "w", True),
                                              ("len", "Length", 60, "e", False)):
            self.recent.heading(col, text=text, anchor=anchor)
            self.recent.column(col, width=w, anchor=anchor, stretch=stretch)
        self.recent.pack(fill="both", expand=True, pady=(8, 0))
        self.recent.bind("<Double-1>", self._open_recent)

        # Open action items preview
        ao, a = theme.card(page)
        ao.grid(row=2, column=0, sticky="nsew", pady=(16, 0), padx=(0, 8))
        head = ttk.Frame(a, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="Open action items", style="Card.H2.TLabel").pack(side="left")
        ttk.Button(head, text="Open tracker →", style="Ghost.TButton",
                   command=lambda: self.show("actions")).pack(side="right")
        self.dash_actions = ttk.Treeview(a, columns=("action", "owner", "due"), show="headings",
                                         selectmode="none", height=4)
        for col, text, w, stretch in (("action", "Action", 300, True), ("owner", "Owner", 120, False),
                                      ("due", "Due", 100, False)):
            self.dash_actions.heading(col, text=text, anchor="w")
            self.dash_actions.column(col, width=w, stretch=stretch)
        self.dash_actions.pack(fill="both", expand=True, pady=(8, 0))

    def _build_actions(self, page):
        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(0, 10))
        self.act_filter = tk.StringVar(value="Open")
        cmb = ttk.Combobox(bar, textvariable=self.act_filter, values=["Open", "Done", "All"],
                           state="readonly", width=8)
        cmb.pack(side="left")
        cmb.bind("<<ComboboxSelected>>", lambda _e: self._fill_actions())
        self.act_query = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.act_query, width=30)
        e.pack(side="left", padx=8)
        e.bind("<KeyRelease>", lambda _e: self._fill_actions())
        ttk.Button(bar, text="Export CSV", command=self._export_actions).pack(side="right")
        ttk.Button(bar, text="Open meeting", command=self._open_action_meeting).pack(side="right", padx=6)
        ttk.Button(bar, text="✓  Mark done / open", style="Accent.TButton",
                   command=self._toggle_action).pack(side="right")

        outer, inner = theme.card(page, padding=0)
        outer.pack(fill="both", expand=True)
        cols = ("status", "action", "owner", "due", "meeting", "date")
        self.act_tree = ttk.Treeview(inner, columns=cols, show="headings", selectmode="extended")
        for col, text, w, stretch in (("status", "", 40, False), ("action", "Action", 420, True),
                                      ("owner", "Owner", 140, False), ("due", "Due", 110, False),
                                      ("meeting", "Meeting", 220, False), ("date", "Date", 100, False)):
            self.act_tree.heading(col, text=text, anchor="w")
            self.act_tree.column(col, width=w, stretch=stretch, anchor="center" if col == "status" else "w")
        self.act_tree.tag_configure("done", foreground=theme.C["faint"])
        sb = ttk.Scrollbar(inner, orient="vertical", command=self.act_tree.yview)
        self.act_tree.configure(yscrollcommand=sb.set)
        self.act_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.act_tree.bind("<Double-1>", lambda _e: self._toggle_action())
        self.lbl_actions = ttk.Label(page, text="Action items are read from each meeting's notes "
                                                "(Claude API notes). Double-click to tick off.",
                                     style="Muted.TLabel")
        self.lbl_actions.pack(anchor="w", pady=(8, 0))

    def _build_voices(self, page):
        outer, inner = theme.card(page)
        outer.pack(fill="both", expand=True)
        ttk.Label(inner, text="Saved voice profiles", style="Card.H2.TLabel").pack(anchor="w")
        ttk.Label(inner, text="Voice fingerprints are biometric data. Keep a profile only with the "
                              "person's consent; delete it when they ask.",
                  style="Card.Danger.TLabel").pack(anchor="w", pady=(2, 10))
        bar = ttk.Frame(inner, style="Card.TFrame")
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(bar, text="Rename / merge", command=self._rename_voice).pack(side="left")
        ttk.Button(bar, text="Delete", command=self._delete_voice).pack(side="left", padx=6)
        ttk.Button(bar, text="Open folder", command=lambda: ui._open_path(speakers.VOICES)
                   if speakers.VOICES.exists() else None).pack(side="left")
        ttk.Button(bar, text="Delete ALL voice data", style="Danger.TButton",
                   command=self._delete_all_voices).pack(side="right")
        self.voice_tree = ttk.Treeview(inner, columns=("name", "updated"), show="headings",
                                       selectmode="browse")
        self.voice_tree.heading("name", text="Name", anchor="w")
        self.voice_tree.heading("updated", text="Last updated", anchor="w")
        self.voice_tree.column("updated", width=180, stretch=False)
        self.voice_tree.pack(fill="both", expand=True)

    def _build_audit(self, page):
        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(0, 10))
        ttk.Label(bar, text="Latest 500 events. Stored in meetings\\audit.jsonl (append-only).",
                  style="Muted.TLabel").pack(side="left")
        ttk.Button(bar, text="Open file", command=lambda: ui._open_path(self.out_dir / "audit.jsonl")
                   if (self.out_dir / "audit.jsonl").exists() else None).pack(side="right")
        ttk.Button(bar, text="Refresh", command=self._fill_audit).pack(side="right", padx=6)
        outer, inner = theme.card(page, padding=0)
        outer.pack(fill="both", expand=True)
        self.audit_tree = ttk.Treeview(inner, columns=("time", "user", "event", "details"),
                                       show="headings", selectmode="browse")
        for col, text, w, stretch in (("time", "Time", 160, False), ("user", "User", 110, False),
                                      ("event", "Event", 170, False), ("details", "Details", 500, True)):
            self.audit_tree.heading(col, text=text, anchor="w")
            self.audit_tree.column(col, width=w, stretch=stretch)
        sb = ttk.Scrollbar(inner, orient="vertical", command=self.audit_tree.yview)
        self.audit_tree.configure(yscrollcommand=sb.set)
        self.audit_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ------------------------------------------------------------- navigation
    def show(self, key: str):
        self.current_page = key
        self.pages[key].tkraise()
        for k, b in self.nav.items():
            b.configure(style="NavActive.TButton" if k == key else "Nav.TButton")
        label = next(lbl for k, lbl, _ in self.PAGES if k == key)
        self.page_title.config(text=label)
        self.page_sub.config(text=self.SUBTITLES.get(key, ""))
        if key == "meetings":
            self.history.refresh()
        elif key == "voices":
            self._fill_voices()
        elif key == "audit":
            self._fill_audit()

    def _open_recent(self, _e=None):
        sel = self.recent.selection()
        if not sel:
            return
        folder = Path(sel[0])
        self.show("meetings")
        self.history.select_folder(folder)

    # ------------------------------------------------------------------- data
    def audit(self, event: str, **kw):
        ent.audit(self.out_dir, event, self.s.get("audit_enabled", True), **kw)

    def refresh_data(self):
        """Recompute dashboard data in the background, then draw it."""
        def work():
            try:
                now = dt.datetime.now()
                folders = ent.list_meetings(self.out_dir)
                recent30 = [f for f in folders
                            if (w := ent.folder_datetime(f)) and (now - w).days < 30]
                secs30 = sum(ent.meeting_seconds(f) for f in recent30)
                storage = sum(ent.folder_size(f) for f in folders)
                rows = []
                for f in folders[:12]:
                    w = ent.folder_datetime(f)
                    sec = ent.meeting_seconds(f)
                    rows.append((str(f), w.strftime("%d %b %H:%M") if w else "",
                                 ent.meeting_title(f), ent.human_duration(sec) if sec else ""))
                items = ent.all_action_items(self.out_dir)
                data = dict(n30=len(recent30), secs30=secs30, total=len(folders),
                            storage=storage, rows=rows, items=items)
                self.later(lambda: self._draw_data(data))
            except Exception:
                print(f"Dashboard refresh failed:\n{traceback.format_exc()}")
        threading.Thread(target=work, daemon=True).start()

    def _draw_data(self, d):
        open_items = [i for i in d["items"] if not i["done"]]
        self.k_meet[0].config(text=str(d["n30"]))
        self.k_meet[1].config(text=f"{d['total']} in library")
        self.k_hours[0].config(text=ent.human_duration(d["secs30"]) if d["secs30"] else "0m")
        self.k_hours[1].config(text="across recorded calls")
        self.k_actions[0].config(text=str(len(open_items)))
        self.k_actions[1].config(text=f"{len(d['items']) - len(open_items)} completed")
        self.k_storage[0].config(text=ent.human_size(d["storage"]))
        ret = self.s.get("audio_retention_days") or 0
        self.k_storage[1].config(text=f"audio kept {ret} days" if ret else "no retention limit")
        self.recent.delete(*self.recent.get_children())
        for iid, date_s, title, length in d["rows"]:
            self.recent.insert("", "end", iid=iid, values=(date_s, title, length))
        self.dash_actions.delete(*self.dash_actions.get_children())
        for it in open_items[:6]:
            self.dash_actions.insert("", "end", values=(it["action"], it["owner"] or "Unassigned",
                                                        it["due"] or "—"))
        self.action_items = d["items"]
        self._fill_actions()

    def _fill_actions(self):
        q = self.act_query.get().strip().lower()
        mode = self.act_filter.get()
        self.act_tree.delete(*self.act_tree.get_children())
        shown = 0
        for idx, it in enumerate(self.action_items):
            if mode == "Open" and it["done"] or mode == "Done" and not it["done"]:
                continue
            if q and q not in f"{it['action']} {it['owner']} {it['meeting']}".lower():
                continue
            self.act_tree.insert("", "end", iid=str(idx), tags=("done",) if it["done"] else (),
                                 values=("✓" if it["done"] else "○", it["action"], it["owner"],
                                         it["due"], it["meeting"], it["date"]))
            shown += 1
        total_open = sum(1 for i in self.action_items if not i["done"])
        self.lbl_actions.config(text=f"{shown} shown · {total_open} open · "
                                     f"{len(self.action_items)} total.  Type above to filter by action, owner or meeting; "
                                     "double-click to tick off.")

    def _selected_actions(self):
        return [self.action_items[int(i)] for i in self.act_tree.selection()]

    def _toggle_action(self):
        items = self._selected_actions()
        for it in items:
            it["done"] = not it["done"]
            ent.set_action_done(it["folder"], it["id"], it["done"])
            self.audit("action_item_" + ("done" if it["done"] else "reopened"),
                       meeting=it["folder"].name, action=it["action"][:120])
        if items:
            self.refresh_data()

    def _open_action_meeting(self):
        items = self._selected_actions()
        if items:
            self.show("meetings")
            self.history.select_folder(items[0]["folder"])

    def _export_actions(self):
        rows = [self.action_items[int(i)] for i in self.act_tree.get_children()]
        if not rows:
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile=f"action_items_{dt.date.today():%Y-%m-%d}.csv")
        if not path:
            return
        ent.export_actions_csv(rows, Path(path))
        self.audit("action_items_exported", rows=len(rows), file=Path(path).name)
        self.lbl_actions.config(text=f"Exported {len(rows)} rows to {Path(path).name}.")

    def _fill_voices(self):
        self.voice_tree.delete(*self.voice_tree.get_children())
        for name in speakers.list_profiles():
            p = speakers.VOICES / f"{name}.npy"
            when = dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if p.exists() else ""
            self.voice_tree.insert("", "end", iid=name, values=(name, when))

    def _rename_voice(self):
        sel = self.voice_tree.selection()
        if not sel:
            return
        old = sel[0]
        new = simpledialog.askstring("Rename voice profile",
                                     f"New name for '{old}' (an existing name will merge):",
                                     initialvalue=old, parent=self.root)
        if new and speakers.rename_profile(old, new):
            self.audit("voice_profile_renamed", old=old, new=new)
            self._fill_voices()

    def _delete_voice(self):
        sel = self.voice_tree.selection()
        if sel and messagebox.askyesno("Delete voice profile",
                                       f"Permanently delete the voice profile for '{sel[0]}'?",
                                       parent=self.root):
            speakers.delete_profile(sel[0])
            self.audit("voice_profile_deleted", name=sel[0])
            self._fill_voices()

    def _delete_all_voices(self):
        names = speakers.list_profiles()
        if not names:
            return
        if messagebox.askyesno("Delete ALL voice data",
                               f"Permanently delete all {len(names)} saved voice profiles?\n"
                               "This cannot be undone.", icon="warning", parent=self.root):
            for n in names:
                speakers.delete_profile(n)
            self.audit("voice_profiles_all_deleted", count=len(names))
            self._fill_voices()

    def _fill_audit(self):
        self.audit_tree.delete(*self.audit_tree.get_children())
        for rec in ent.read_audit(self.out_dir):
            details = ", ".join(f"{k}={v}" for k, v in rec.items() if k not in ("time", "user", "event"))
            self.audit_tree.insert("", "end", values=(rec.get("time", "").replace("T", "  "),
                                                      rec.get("user", ""), rec.get("event", ""), details))

    def _run_retention(self):
        self.last_retention = time.monotonic()
        a, m = int(self.s.get("audio_retention_days") or 0), int(self.s.get("meeting_retention_days") or 0)
        if not (a or m):
            return

        def work():
            res = ent.apply_retention(self.out_dir, a, m, self.s.get("audit_enabled", True))
            if res["audio_files"] or res["meetings"]:
                print(f"Retention: removed {res['audio_files']} audio files, {res['meetings']} meetings.")
                self.later(self.refresh_data)
        threading.Thread(target=work, daemon=True).start()

    # --------------------------------------------------------------- settings
    def _selected_template_key(self) -> str:
        return self.tpl_label_to_key.get(self.tpl_var.get(), "executive_minutes")

    def _on_template_change(self, _event=None):
        self.s["template"] = self._selected_template_key()
        cfg.save_settings(self.s)

    def _on_auto_toggle(self):
        self.s["auto_record"] = self.auto_var.get()
        cfg.save_settings(self.s)

    def _apply_settings(self, updated: dict):
        changed = sorted(k for k in updated if updated.get(k) != self.s.get(k))
        self.s = updated
        self.watch = {k: MEETING_APPS[k] for k in self.s["apps"] if k in MEETING_APPS}
        self.auto_var.set(self.s.get("auto_record", True))
        cfg.set_capture_excluded(self.root, self.s.get("exclude_capture", False))
        self.live_panel.refresh_config(self.s.get("live_every", 5.0), self.s.get("exclude_capture", False),
                                       preferred_ollama=self.s.get("ollama_model", ""),
                                       claude_model=self.s.get("claude_model"))
        self.tpl_map = cfg.list_templates()
        self.tpl_label_to_key = {label: key for key, (label, _p) in self.tpl_map.items()}
        self.cmb_tpl.config(values=list(self.tpl_label_to_key))
        self.indic_ok = indic_asr.is_available()
        self.mode_var.set(MODES[1] if self.s.get("screen_record") else MODES[0])
        self.ask_var.set(self.s.get("screen_ask", True))
        self._update_target_label()
        if changed:
            self.audit("settings_changed", keys=",".join(changed))
        if "start_with_windows" in changed:
            def toggle_startup(on=bool(self.s.get("start_with_windows"))):
                try:
                    import shortcuts
                    shortcuts.set_startup(on)
                except Exception as e:
                    self.later(lambda: messagebox.showwarning(
                        APP_NAME, f"Could not change the Windows start-up setting:\n{e}", parent=self.root))
            threading.Thread(target=toggle_startup, daemon=True).start()
        if "audio_retention_days" in changed or "meeting_retention_days" in changed:
            self._run_retention()
        self.refresh_data()
        self.refresh()

    # ---------------------------------------------------------------- windows
    def hide_window(self):
        if self.hidden:
            return
        self.hidden = True
        if self.mini:
            self.mini.w.withdraw()
        self.root.withdraw()

    def show_window(self):
        self.hidden = False
        if self.mini:
            self.mini.w.deiconify()
        else:
            self.root.deiconify()
            self.root.lift()

    def toggle_hidden(self):
        self.show_window() if self.hidden else self.hide_window()

    def open_voice_prompt(self):
        import subprocess
        exe = Path(sys.executable)
        exe = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
        try:
            subprocess.Popen([str(exe), str(Path(__file__).parent / "voice_prompt.py")],
                             cwd=str(Path(__file__).parent))
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not start Voice to Prompt:\n{e}", parent=self.root)

    def enter_compact(self):
        if self.mini:
            return
        self.mini = MiniRecorder(self)
        self.root.withdraw()

    def exit_compact(self):
        if self.mini:
            self.mini.destroy()
            self.mini = None
        self.hidden = False
        self.root.deiconify()
        self.root.lift()

    # ---------------------------------------------------------------- actions
    def start(self, auto: bool):
        if self.rec or self.stopping:
            return
        now = dt.datetime.now()
        if auto and self.app_now and self.title_var.get() in DEFAULT_TITLES + (self.auto_title,):
            self.auto_title = f"{self.app_now} call"
            self.title_var.set(self.auto_title)
        title = self.title_var.get() or "Meeting"
        folder = mn.new_meeting_folder(self.out_dir, title, now)
        ent.write_meta(folder, title=title, started=now.isoformat(timespec="seconds"),
                       app=self.app_now or "", auto=auto, user_template=self._selected_template_key())
        live = None
        if self.s.get("live_enabled", True):
            live = ln.LiveTranscriber(folder, self.s.get("model", "auto"), self.s.get("device", "auto"),
                                      self.s.get("language") or None, my_name=self.s.get("my_name", "Me"),
                                      asr_engine=self.s.get("asr_engine", "auto"),
                                      auto_languages=self.s.get("auto_languages") or [])
            self.live_panel.attach(live, title)
        self.rec = Recorder(folder, live, preferred_speaker=self.s.get("preferred_speaker", ""),
                            preferred_mic=self.s.get("preferred_mic", ""),
                            save_16k=self.s.get("save_16k", True))
        self.started_at, self.auto_started = now, auto
        if not self.mic_on.get():
            self.rec.muted.set()
        try:
            self.rec.start()
        except Exception as e:
            if live:
                live.cancel()
            self.rec = None
            messagebox.showerror(APP_NAME, f"Could not start recording:\n{e}", parent=self.root)
            return
        self.rec.screen = None
        self.rec.screen_pending = False
        self.rec.t0 = time.monotonic()
        if self.s.get("screen_record", False):
            if self.s.get("screen_ask", True):
                self._ask_screen_for(self.rec)          # audio keeps recording meanwhile
            else:
                self._start_screen(self.rec, self._saved_target())
        self.audit("recording_started", meeting=folder.name, auto=auto, app=self.app_now or "",
                   mode="audio+screen" if self.s.get("screen_record") else "audio")
        if self.s.get("outlook_integration", True):
            self._lookup_outlook(folder)
        if auto and self.s.get("compact_on_record", False) and not self.hidden:
            self.enter_compact()
        elif auto and not self.hidden and not self.mini:
            self.root.deiconify()
            self.root.lift()
            self.root.bell()
        self.refresh()

    # ------------------------------------------------------- record mode / screen
    def _saved_target(self) -> dict:
        return {"monitor": int(self.s.get("screen_monitor", 0) or 0), "area": self.s.get("screen_area")}

    def _update_target_label(self):
        if not self.s.get("screen_record"):
            txt = "Not recorded (audio only)"
        elif self.s.get("screen_ask", True):
            txt = "Asks each time"
        else:
            txt = screen_picker.describe(self._saved_target())
        self.lbl_target.config(text=txt)

    def _on_mode_change(self):
        self.s["screen_record"] = self.mode_var.get() == MODES[1]
        if self.s["screen_record"] and not screen_rec.find_ffmpeg():
            self.s["screen_record"] = False
            self.mode_var.set(MODES[0])
            messagebox.showwarning(APP_NAME, "Screen recording needs FFmpeg. "
                                             "Download the portable ZIP again (FFmpeg is included in it).",
                                   parent=self.root)
        cfg.save_settings(self.s)
        self._update_target_label()
        self.refresh()

    def _on_ask_toggle(self):
        self.s["screen_ask"] = self.ask_var.get()
        cfg.save_settings(self.s)
        self._update_target_label()

    def _remember_target(self, target: dict, ask: bool | None = None):
        self.s["screen_monitor"] = int(target.get("monitor", 0))
        self.s["screen_area"] = target.get("area")
        if ask is not None:
            self.s["screen_ask"] = ask
            self.ask_var.set(ask)
        cfg.save_settings(self.s)
        self._update_target_label()

    def _with_screens(self, then):
        """Grab previews in the background, then call then(screens) on the Tk thread."""
        self.flash("Getting screen previews…")

        def work():
            screens = screen_picker.grab_screens()
            self.later(lambda: then(screens))
        threading.Thread(target=work, daemon=True).start()

    def choose_target(self):
        """Pick the default screen / area outside a recording."""
        def show(screens):
            if not screens:
                messagebox.showwarning(APP_NAME, "No screen could be captured (is FFmpeg installed?).",
                                       parent=self.root)
                return

            def done(target, remember):
                if target:
                    self._remember_target(target, ask=False if remember else None)
                    if not self.s.get("screen_record"):
                        self.mode_var.set(MODES[1])
                        self._on_mode_change()
            screen_picker.ScreenPicker(self.root, screens, self._saved_target(), done,
                                           timeout_s=None, intro="Choose what to record when the "
                                           "screen is recorded.",
                                           exclude_capture=self.s.get("exclude_capture", False))
        self._with_screens(show)

    def _ask_screen_for(self, rec):
        rec.screen_pending = True

        def show(screens):
            if self.rec is not rec:                    # recording already ended
                return
            if not screens:
                rec.screen_pending = False
                self.flash("Screen recording unavailable: no screen could be captured.")
                return

            def done(target, remember):
                rec.screen_pending = False
                if target:
                    self._remember_target(target, ask=False if remember else None)
                    if self.rec is rec:
                        self._start_screen(rec, target)
                else:
                    self.flash("Recording audio only for this call.")
                self.refresh()
            screen_picker.ScreenPicker(self.root, screens, self._saved_target(), done, timeout_s=20,
                                       exclude_capture=self.s.get("exclude_capture", False))
        self._with_screens(show)

    def _start_screen(self, rec, target: dict):
        sr = screen_rec.ScreenRecorder(rec.folder, monitor=int(target.get("monitor", 0)),
                                       fps=int(self.s.get("screen_fps", 5) or 5),
                                       area=target.get("area"), audio_t0=rec.t0)
        if sr.start():
            rec.screen = sr
            if rec.paused.is_set():
                threading.Thread(target=sr.pause, daemon=True).start()
            ent.write_meta(rec.folder, screen_target=target)
            self.audit("screen_recording_started", meeting=rec.folder.name,
                       target=screen_picker.describe(target))
        else:
            self.flash("Screen recording could not start: " + (sr.error or "FFmpeg missing"))
        self.refresh()

    def _lookup_outlook(self, folder: Path):
        rec = self.rec
        with_att = self.s.get("outlook_attendees", True)

        def work():
            info = ent.current_outlook_meeting(with_attendees=with_att)
            if not info:
                return
            ent.write_meta(folder, outlook_subject=info.get("subject") or "",
                           attendees=info.get("attendees") or [], organizer=info.get("organizer") or "")

            def apply():
                subj = (info.get("subject") or "").strip()
                if subj and self.rec is rec and self.title_var.get() in DEFAULT_TITLES + (self.auto_title,):
                    self.title_var.set(subj[:80])
                    self.auto_title = subj[:80]
                    self.live_panel.head.config(text=f"Live: {subj[:80]}")
            self.later(apply)
        threading.Thread(target=work, daemon=True).start()

    def pin_moment(self):
        if not self.rec:
            return
        note = simpledialog.askstring("Pin moment", "Short note for this moment (optional):",
                                      parent=self.mini.w if self.mini else self.root)
        if note is None:
            return
        if self.rec.live:
            sec = self.rec.live.add_bookmark(note)
            self.flash(f"★ Pinned at {ln.stamp(sec)}")
        else:
            self.flash("★ Pinning needs live transcription (Settings → General).")

    def flash(self, text: str):
        self._flash_text, self._flash_until = text, time.monotonic() + 6

    def toggle_mic(self):
        self.mic_on.set(not self.mic_on.get())
        if self.rec:
            (self.rec.muted.clear if self.mic_on.get() else self.rec.muted.set)()
        self.refresh()

    def toggle_pause(self):
        if not self.rec:
            return
        resuming = self.rec.paused.is_set()
        (self.rec.paused.clear if resuming else self.rec.paused.set)()
        sr = getattr(self.rec, "screen", None)
        if sr:
            threading.Thread(target=sr.resume if resuming else sr.pause, daemon=True).start()
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
        self.refresh()

        def finish_stop():
            rec.stop()
            if getattr(rec, "screen", None):
                rec.screen.stop()
            self.later(lambda: self._after_stop(rec, reason, auto_started, title, started_str, tpl_key))
        threading.Thread(target=finish_stop, daemon=True).start()

    def _after_stop(self, rec: Recorder, reason, auto_started, title, started_str, tpl_key):
        self.stopping = False
        if reason == "stopped by you" and auto_started and meeting_in_call(self.watch):
            self.skip_this_call = True
        if rec.errors:
            messagebox.showerror(APP_NAME, "Recording problem:\n" + "\n".join(rec.errors), parent=self.root)
        if rec.seconds < MIN_KEEP_S:
            if rec.live:
                rec.live.cancel()
            shutil.rmtree(rec.folder, ignore_errors=True)
            self.audit("recording_discarded", meeting=rec.folder.name, seconds=round(rec.seconds),
                       reason=f"shorter than {MIN_KEEP_S}s")
        else:
            ent.write_meta(rec.folder, title=title, seconds=round(rec.seconds, 1),
                           ended=dt.datetime.now().isoformat(timespec="seconds"))
            self.audit("recording_stopped", meeting=rec.folder.name, seconds=round(rec.seconds), reason=reason)
            self.live_panel.ended()
            if getattr(rec, "screen", None):
                self._finish_video(rec.screen, rec.folder)
            self.proc.add(rec.folder, title, started_str, rec.live, template_key=tpl_key)
        if self.title_var.get() == self.auto_title:
            self.title_var.set("Meeting")
        self.refresh_data()
        self.refresh()

    def _finish_video(self, sr, folder: Path):
        """Join the screen segments with the call audio in the background."""
        def work():
            out = sr.finalize()
            if out:
                ent.write_meta(folder, video=out.name)
                self.audit("screen_video_saved", meeting=folder.name, size_bytes=out.stat().st_size)
                self.later(lambda: self.flash(f"Screen recording saved: {out.name}"))
            else:
                self.later(lambda: self.flash("Screen recording could not be saved (see log)."))
            self.later(self.refresh_data)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ loops
    def later(self, fn):
        """Run fn on the Tk thread (safe to call from any thread)."""
        self.ui_q.put(fn)

    def _drain(self):
        while True:
            try:
                fn = self.ui_q.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception:
                print("UI task failed:\n" + traceback.format_exc())

    def pump_meters(self):
        self._drain()
        others = me = 0.0
        if self.rec and not self.rec.paused.is_set():
            others = self.rec.levels["others"]
            me = 0.0 if self.rec.muted.is_set() else self.rec.levels["me"]
        self.meter_others.set_level(others)
        self.meter_me.set_level(me)
        if self.mini:
            self.mini.meter.set_level(max(others, me))
        self.root.after(80, self.pump_meters)

    def tick(self):
        try:
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
            self.live_panel.tick(recording=bool(self.rec))
            while not self.proc.requests.empty():
                self._show_name_dialog(self.proc.requests.get())
            if time.monotonic() - self.last_retention > RETENTION_EVERY_S:
                self._run_retention()
            self.refresh(in_call)
        except Exception:
            print(f"Tick error:\n{traceback.format_exc()}")
        self.root.after(1000, self.tick)

    def _show_name_dialog(self, req):
        names = []
        folder = req.get("folder")
        if folder is not None and self.s.get("outlook_attendees", True):
            names = ent.read_meta(folder).get("attendees") or []
        speakers.NameDialog(
            self.root, req["info"], req["suggest"], req["default"],
            on_done=lambda res, r=req: (r.__setitem__("result", res), r["event"].set(),
                                        self._audit_names(r, res)),
            exclude_capture=self.s.get("exclude_capture", False), names=names)

    def _audit_names(self, req, res):
        if res:
            _typed, remember = res
            if remember:
                self.audit("voice_profiles_saved", count=len(remember),
                           meeting=req["folder"].name if req.get("folder") else "")

    def refresh(self, in_call: bool | None = None):
        rec = self.rec
        if self.stopping:
            pill = ("Stopping…", "warning")
        elif rec and rec.paused.is_set():
            pill = ("❚❚  Paused", "warning")
        elif rec:
            pill = ("●  Recording + screen" if getattr(rec, "screen", None)
                    else "●  Recording · choose screen" if getattr(rec, "screen_pending", False)
                    else "●  Recording", "danger")
        elif self.proc.pending:
            pill = (f"{self.proc.stage or 'Processing'}…", "info")
        elif self.auto_var.get():
            pill = ("Waiting for a call", "muted")
        else:
            pill = ("Idle", "muted")
        self.pill.set(*pill)
        timer = ""
        if rec:
            s = int(rec.seconds)
            timer = f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
        self.timer.config(text=timer)

        active = bool(rec) and not self.stopping
        want_rec = bool(rec or self.stopping)
        if want_rec != self._controls_rec:
            self._controls_rec = want_rec
            (self.idle_box if want_rec else self.rec_box).pack_forget()
            (self.rec_box if want_rec else self.idle_box).pack(side="left", before=self.btn_mic)
        self.btn_start.state(["disabled"] if (rec or self.stopping) else ["!disabled"])
        self.cmb_mode.state(["disabled"] if (rec or self.stopping) else ["!disabled", "readonly"])
        for b in (self.btn_pause, self.btn_stop, self.btn_pin):
            b.state(["!disabled"] if active else ["disabled"])
        self.btn_pause.config(text="▶  Resume" if rec and rec.paused.is_set() else "❚❚  Pause")
        self.btn_mic.config(text="🎤  Mic on" if self.mic_on.get() else "🔇  Mic off")

        if in_call is not None:
            apps = ", ".join(label for label, _ in self.watch.values())
            self.lbl_detect.config(text=(f"Call detected in {self.app_now}" if in_call
                                         else "No call detected") + f"   ·   Watching: {apps}")
        bits = []
        if getattr(self, "_flash_until", 0) > time.monotonic():
            bits.append(self._flash_text)
        if rec and not rec.paused.is_set():
            if rec.seconds > 20 and time.monotonic() - rec.last_others_sound > 20:
                bits.append("No call audio detected – check the speaker device in Settings → Audio & AI.")
            if rec.live and rec.live.behind_s > 90:
                bits.append(f"Live transcript is {rec.live.behind_s:.0f} s behind.")
            if rec.errors:
                bits.append("Warning: " + rec.errors[0])
        if cfg.indic_needed(self.s) and not self.indic_ok:
            bits.append("Odia needs the Indic pack: run 'Install Indic Languages (Odia).bat'.")
        if self.proc.pending:
            bits.append(f"Processing {self.proc.pending} meeting(s): {self.proc.stage or 'queued'}…")
        self.lbl_info.config(text="\n".join(bits))
        if self.mini:
            self.mini.update(pill[0], pill[1], timer, 0.0, active, bool(rec and rec.paused.is_set()))

    def on_close(self):
        if self.rec:
            if not messagebox.askyesno(APP_NAME, "A recording is running. Stop it and quit?\n"
                                                 "(It will NOT be transcribed.)", parent=self.root):
                return
            self.rec.stop()
            if self.rec.live:
                self.rec.live.cancel()
            self.audit("recording_abandoned", meeting=self.rec.folder.name)
        elif self.proc.pending and not messagebox.askyesno(
                APP_NAME, f"{self.proc.pending} meeting(s) are still being processed.\n"
                          "Quit anyway? You can re-run them later from Meetings.", parent=self.root):
            return
        if self._hotkey_stop:
            self._hotkey_stop.set()
        if self._wake_stop:
            self._wake_stop.set()
        self.audit("app_closed")
        self.root.destroy()


def main():
    saved = cfg.load_settings()
    p = argparse.ArgumentParser(description=f"{APP_NAME} {VERSION} - Enterprise Edition")
    p.add_argument("--model", default=None)
    p.add_argument("--device", default=None, choices=["auto", "cpu", "cuda"])
    p.add_argument("--language", default=None)
    p.add_argument("--no-mic", action="store_true")
    p.add_argument("--no-ai", action="store_true")
    p.add_argument("--no-live", action="store_true")
    p.add_argument("--theme", default=None, choices=["light", "dark"])
    p.add_argument("--compact", action="store_true", help="Start as the compact recorder")
    p.add_argument("--out", type=Path, default=Path(__file__).parent / "meetings")
    args = p.parse_args()
    for key, val in (("model", args.model), ("device", args.device), ("language", args.language),
                     ("theme", args.theme)):
        if val is not None:
            saved[key] = val
    if args.no_mic:
        saved["mic_on"] = False
    if args.no_ai:
        saved["ai_enabled"] = False
    if args.no_live:
        saved["live_enabled"] = False

    import warnings
    # V2.1: soundcard warns on every glitchy loopback block (8,600 lines in 6 min).
    warnings.filterwarnings("once", message="data discontinuity in recording")
    args.out.mkdir(parents=True, exist_ok=True)
    if cfg.wake_existing_instance(args.out / ".instance_port"):
        return
    cfg.ensure_glossary()
    cfg.ensure_templates()
    cfg.setup_rotating_log(args.out / "notetaker.log")
    print(f"\n=== Started {APP_NAME} {VERSION} ({dt.datetime.now():%Y-%m-%d %H:%M}) ===")
    app = App(saved, args.out)
    if args.compact:
        app.root.after(300, app.enter_compact)
    app.root.mainloop()


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
        cfg.show_error_popup(f"{APP_NAME} Error", f"{APP_NAME} could not start.\n\n" + err[-1200:]
                             + "\n\nFull details were saved to meetings\\notetaker.log")
        raise
