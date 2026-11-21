"""
"What should be recorded?" (V2.1)

  grab_screens()  - one preview frame per screen, taken with the same FFmpeg
                    grabber that records, so screen N in the picker is
                    exactly screen N in the video.
  ScreenPicker    - live previews of every screen: click one to record it,
                    "Select area..." to snip a region, or "Audio only".
                    Counts down and uses the last choice if nobody answers
                    (auto-recorded calls never wait).
  AreaSelector    - drag a rectangle on a full-size screenshot of the
                    screen; saved as fractions so it works at any DPI.

A target is a dict: {"monitor": int, "area": [x, y, w, h] fractions or None}.
"""

import re
import subprocess
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import screen_rec
import theme

PREVIEW_W = 1600
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def describe(target: dict | None, screens: list | None = None) -> str:
    if not target:
        return "Audio only"
    m = int(target.get("monitor", 0))
    name = f"Screen {m + 1}" + (" (main)" if m == 0 else "")
    area = target.get("area")
    if area:
        size = ""
        for s in screens or []:
            if s["index"] == m and s.get("native"):
                size = f" {round(s['native'][0] * area[2])}×{round(s['native'][1] * area[3])}"
        return f"{name} · selected area{size}"
    return f"{name} · whole screen"


def grab_screens(count: int | None = None) -> list[dict]:
    """[{index, png, native:(w,h)}] for every screen FFmpeg can capture."""
    ff = screen_rec.find_ffmpeg()
    if not ff:
        return []
    count = count or screen_rec.monitor_count()
    tmp = Path(tempfile.mkdtemp(prefix="nt_screens_"))
    results: dict[int, dict] = {}

    def one(i):
        png = tmp / f"screen_{i}.png"
        vf = (f"ddagrab=output_idx={i}:framerate=2,hwdownload,format=bgra,showinfo,"
              f"scale={PREVIEW_W}:-2")
        try:
            r = subprocess.run([ff, "-hide_banner", "-loglevel", "info", "-y", "-f", "lavfi", "-i", vf,
                                "-frames:v", "1", str(png)], capture_output=True, text=True,
                               timeout=20, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            return
        if png.exists():
            m = re.search(r"\bs:(\d+)x(\d+)", r.stderr or "")
            results[i] = {"index": i, "png": png,
                          "native": (int(m.group(1)), int(m.group(2))) if m else None}

    threads = [threading.Thread(target=one, args=(i,), daemon=True) for i in range(max(1, count))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(25)
    return [results[i] for i in sorted(results)]


# --------------------------------------------------------------------------
class AreaSelector:
    """Drag a rectangle on a screenshot. on_done([x, y, w, h] fractions) or on_done(None)."""

    def __init__(self, parent, screen: dict, on_done):
        self.on_done, self.done = on_done, False
        self.w = w = tk.Toplevel(parent)
        w.title(f"Select the area to record - Screen {screen['index'] + 1}")
        w.attributes("-topmost", True)
        w.grab_set()
        w.protocol("WM_DELETE_WINDOW", self.cancel)
        full = tk.PhotoImage(master=w, file=str(screen["png"]))
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        f = 1
        while full.width() / f > sw * 0.85 or full.height() / f > sh * 0.72:
            f += 1
        self.img = full.subsample(f) if f > 1 else full
        self.iw, self.ih = self.img.width(), self.img.height()
        self.native = screen.get("native")

        ttk.Label(w, text="Drag to select the part of the screen to record", style="H2.TLabel"
                  ).pack(anchor="w", padx=16, pady=(12, 0))
        ttk.Label(w, text="Only this area will appear in the video. Enter = use area, Esc = cancel.",
                  style="Muted.TLabel").pack(anchor="w", padx=16, pady=(2, 8))
        bar = ttk.Frame(w)
        bar.pack(side="bottom", fill="x", padx=16, pady=12)
        self.lbl = ttk.Label(bar, text="No area selected yet", style="Muted.TLabel")
        self.lbl.pack(side="left")
        ttk.Button(bar, text="Cancel", command=self.cancel).pack(side="right")
        ttk.Button(bar, text="Whole screen", command=lambda: self._finish(None)).pack(side="right", padx=6)
        self.btn_ok = ttk.Button(bar, text="Use this area", style="Accent.TButton",
                                 command=self._use, state="disabled")
        self.btn_ok.pack(side="right")

        self.c = c = tk.Canvas(w, width=self.iw, height=self.ih, highlightthickness=0, cursor="crosshair",
                               bg="#000000")
        c.pack(padx=16)
        c.create_image(0, 0, image=self.img, anchor="nw")
        self.shade = [c.create_rectangle(0, 0, 0, 0, fill="#000000", stipple="gray50", width=0)
                      for _ in range(4)]
        self.box = c.create_rectangle(0, 0, 0, 0, outline=theme.C["accent"], width=theme.px(2))
        self.rect = None
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._drag)
        c.bind("<ButtonRelease-1>", self._release)
        w.bind("<Return>", lambda _e: self._use())
        w.bind("<Escape>", lambda _e: self.cancel())
        theme.scale_tree(w)
        w.update_idletasks()
        w.geometry(f"+{max(0, (sw - w.winfo_reqwidth()) // 2)}+{max(0, (sh - w.winfo_reqheight()) // 3)}")
        w.focus_force()

    def _press(self, e):
        self.x0, self.y0 = e.x, e.y

    def _drag(self, e):
        x0, y0 = self.x0, self.y0
        x1 = min(max(e.x, 0), self.iw)
        y1 = min(max(e.y, 0), self.ih)
        l, t, r, b = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        self.rect = (l, t, r, b)
        c = self.c
        c.coords(self.box, l, t, r, b)
        c.coords(self.shade[0], 0, 0, self.iw, t)
        c.coords(self.shade[1], 0, b, self.iw, self.ih)
        c.coords(self.shade[2], 0, t, l, b)
        c.coords(self.shade[3], r, t, self.iw, b)
        if self.native:
            self.lbl.config(text=f"{round((r - l) / self.iw * self.native[0])} × "
                                 f"{round((b - t) / self.ih * self.native[1])} pixels")

    def _release(self, e):
        self._drag(e)
        ok = self.rect and (self.rect[2] - self.rect[0]) > 20 and (self.rect[3] - self.rect[1]) > 20
        self.btn_ok.state(["!disabled"] if ok else ["disabled"])

    def _use(self):
        if not self.rect or "disabled" in self.btn_ok.state():
            return
        l, t, r, b = self.rect
        self._finish([round(l / self.iw, 4), round(t / self.ih, 4),
                      round((r - l) / self.iw, 4), round((b - t) / self.ih, 4)])

    def _finish(self, area):
        if self.done:
            return
        self.done = True
        self.w.grab_release()
        self.w.destroy()
        self.on_done(("area", area))

    def cancel(self):
        if self.done:
            return
        self.done = True
        self.w.grab_release()
        self.w.destroy()
        self.on_done(("cancel", None))


# --------------------------------------------------------------------------
class ScreenPicker:
    """on_done(target_or_None, remember: bool). None = audio only."""

    def __init__(self, parent, screens: list[dict], last: dict | None, on_done,
                 timeout_s: int = 20, exclude_capture: bool = False,
                 intro: str = "Audio is already recording. Pick a screen, or select an area on it."):
        import settings as cfg
        self.on_done, self.done = on_done, False
        self.screens = screens
        self.default = last if last and any(s["index"] == last.get("monitor") for s in screens) \
            else {"monitor": screens[0]["index"], "area": None}
        self.remaining = timeout_s
        self.w = w = tk.Toplevel(parent)
        w.title("What should be recorded?")
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", lambda: self._finish(self.default))
        cfg.set_capture_excluded(w, exclude_capture)

        ttk.Label(w, text="What should be recorded?", style="H2.TLabel").pack(anchor="w", padx=18, pady=(16, 0))
        ttk.Label(w, text=intro, style="Muted.TLabel").pack(anchor="w", padx=18, pady=(2, 12))

        bottom = ttk.Frame(w)
        bottom.pack(side="bottom", fill="x", padx=18, pady=(8, 16))
        self.remember = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom, text="Remember my choice and don't ask again",
                        variable=self.remember).pack(side="left")
        ttk.Button(bottom, text="🎙  Audio only this time",
                   command=lambda: self._finish(None)).pack(side="right")
        self.lbl_count = ttk.Label(w, text="", style="Warn.TLabel")
        self.lbl_count.pack(side="bottom", anchor="w", padx=18)

        grid = ttk.Frame(w)
        grid.pack(padx=12)
        self._imgs = []
        cols = min(3, len(screens))
        for n, s in enumerate(screens):
            full = tk.PhotoImage(master=w, file=str(s["png"]))
            f = max(1, round(full.width() / 360))
            thumb = full.subsample(f)
            self._imgs += [full, thumb]
            outer, card = theme.card(grid, padding=10)
            outer.grid(row=n // cols, column=n % cols, padx=6, pady=6, sticky="n")
            sel = self.default.get("monitor") == s["index"]
            b = tk.Button(card, image=thumb, bd=0, cursor="hand2", relief="flat",
                          highlightthickness=theme.px(3),
                          highlightbackground=theme.C["accent"] if sel else theme.C["surface"],
                          command=lambda i=s["index"]: self._finish({"monitor": i, "area": None}))
            b.pack()
            native = f"  ·  {s['native'][0]}×{s['native'][1]}" if s.get("native") else ""
            ttk.Label(card, text=f"Screen {s['index'] + 1}" + (" (main)" if s["index"] == 0 else "") + native,
                      style="Card.H3.TLabel").pack(anchor="w", pady=(8, 2))
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill="x")
            ttk.Button(row, text="Record this screen", style="Accent.TButton",
                       command=lambda i=s["index"]: self._finish({"monitor": i, "area": None})
                       ).pack(side="left")
            ttk.Button(row, text="Select area…",
                       command=lambda sc=s: self._select_area(sc)).pack(side="left", padx=6)

        theme.scale_tree(w)
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        w.geometry(f"+{max(0, (sw - w.winfo_reqwidth()) // 2)}+{max(0, (sh - w.winfo_reqheight()) // 4)}")
        w.lift()
        w.bell()
        self._tick()

    def _tick(self):
        if self.done:
            return
        if self.remaining <= 0:
            self._finish(self.default)
            return
        self.lbl_count.config(text=f"No choice in {self.remaining} s → recording "
                                   f"{describe(self.default, self.screens)}")
        self.remaining -= 1
        self.w.after(1000, self._tick)

    def _select_area(self, screen):
        self.remaining = 10 ** 9            # stop the countdown while selecting
        self.lbl_count.config(text="")

        def got(res):
            kind, area = res
            if kind == "cancel":
                self.remaining = 20
                self._tick()
                return
            self._finish({"monitor": screen["index"], "area": area})
        AreaSelector(self.w, screen, got)

    def _finish(self, target):
        if self.done:
            return
        self.done = True
        try:
            self.w.destroy()
        except tk.TclError:
            pass
        self.on_done(target, bool(self.remember.get()))
