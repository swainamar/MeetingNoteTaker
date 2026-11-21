"""
Enterprise look for Meeting Note-Taker 2.0 - no extra packages.

Builds on ttk's 'clam' theme and Tk's option database so that both ttk and
classic tk widgets (used in older dialogs) share one palette, in light or
dark mode.
"""

import tkinter as tk
from tkinter import ttk

FONT = "Segoe UI"
PALETTES = {
    "light": {
        "bg": "#F4F6FB", "surface": "#FFFFFF", "surface2": "#F8FAFC", "border": "#E2E8F0",
        "text": "#0F172A", "muted": "#64748B", "faint": "#94A3B8",
        "accent": "#2563EB", "accent_hover": "#1D4ED8", "accent_soft": "#DBEAFE",
        "danger": "#DC2626", "danger_hover": "#B91C1C", "success": "#16A34A",
        "warning": "#D97706", "info": "#0284C7",
        "sidebar": "#0F172A", "sidebar_text": "#CBD5E1", "sidebar_muted": "#64748B",
        "sidebar_active": "#1E293B", "sidebar_hover": "#172033",
        "input": "#FFFFFF", "select": "#DBEAFE", "select_text": "#0F172A",
        "meter_track": "#E5E7EB",
    },
    "dark": {
        "bg": "#0B1220", "surface": "#111A2E", "surface2": "#0F172A", "border": "#1F2A44",
        "text": "#E2E8F0", "muted": "#94A3B8", "faint": "#64748B",
        "accent": "#3B82F6", "accent_hover": "#2563EB", "accent_soft": "#1E3A8A",
        "danger": "#EF4444", "danger_hover": "#DC2626", "success": "#22C55E",
        "warning": "#F59E0B", "info": "#38BDF8",
        "sidebar": "#060B16", "sidebar_text": "#CBD5E1", "sidebar_muted": "#64748B",
        "sidebar_active": "#16213A", "sidebar_hover": "#0F1930",
        "input": "#0F172A", "select": "#1E3A8A", "select_text": "#F8FAFC",
        "meter_track": "#1F2937",
    },
}
C = dict(PALETTES["light"])   # current palette (mutated by apply)
SCALE = 1.0                   # display scaling (2.0 on a 200% screen)
_SCALED: set[str] = set()


def enable_dpi() -> float:
    """Crisp text on high-DPI screens. Call BEFORE creating tk.Tk()."""
    global SCALE
    import sys
    if sys.platform != "win32":
        return SCALE
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)      # system DPI aware
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            return SCALE
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
    except (AttributeError, OSError):
        dpi = 96
    SCALE = max(1.0, dpi / 96.0)
    return SCALE


def px(n: float) -> int:
    return int(round(n * SCALE))


def _scale_pad(v):
    if v in ("", None):
        return v
    if isinstance(v, (tuple, list)):
        return tuple(px(float(str(x))) for x in v)
    parts = str(v).split()
    return tuple(px(float(x)) for x in parts) if len(parts) > 1 else px(float(parts[0]))


def scale_tree(widget: tk.Misc):
    """Scale pixel spacing (pack/grid padding, ttk padding, Treeview columns,
    wraplength) of a widget tree once, for high-DPI screens."""
    if SCALE == 1.0:
        return
    stack = [widget]
    while stack:
        w = stack.pop()
        stack.extend(w.winfo_children())
        key = str(w)
        if key in _SCALED:
            continue
        _SCALED.add(key)
        try:
            mgr = w.winfo_manager()
            if mgr in ("pack", "grid"):
                info = w.pack_info() if mgr == "pack" else w.grid_info()
                opts = {k: _scale_pad(info[k]) for k in ("padx", "pady", "ipadx", "ipady")
                        if k in info and str(info[k]) not in ("0", "")}
                if opts:
                    (w.pack_configure if mgr == "pack" else w.grid_configure)(**opts)
            if isinstance(w, ttk.Widget) and "padding" in w.keys():
                pad = w.cget("padding")
                if pad not in ("", None, ()):
                    w.configure(padding=_scale_pad(pad))
            if isinstance(w, ttk.Treeview):
                for col in w["columns"]:
                    w.column(col, width=px(w.column(col, "width")))
            if "wraplength" in w.keys():
                wl = w.cget("wraplength")
                if str(wl) not in ("0", ""):
                    w.configure(wraplength=px(float(str(wl))))
        except (tk.TclError, ValueError):
            continue


def apply(root: tk.Misc, mode: str = "light") -> dict:
    """Apply the palette. Call once, before building windows."""
    C.clear()
    C.update(PALETTES.get(mode, PALETTES["light"]))
    c = C

    # Classic tk widgets only (Text, Listbox, tk.Button in older dialogs).
    # Patterns name tk classes explicitly: a bare "*Font" / "*Background"
    # would also override the ttk widgets' style fonts and colours.
    tk_classes = ("Toplevel", "Frame", "Label", "Button", "Checkbutton", "Radiobutton",
                  "Canvas", "Text", "Listbox", "Entry", "Spinbox", "Message", "Labelframe",
                  "Scale", "Menu")
    for cls in tk_classes:
        root.option_add(f"*{cls}.background", c["bg"])
        root.option_add(f"*{cls}.foreground", c["text"])
        root.option_add(f"*{cls}.highlightBackground", c["bg"])
        root.option_add(f"*{cls}.font", (FONT, 10))
    for pattern, value in (
        ("*activeBackground", c["surface2"]), ("*activeForeground", c["text"]),
        ("*highlightColor", c["accent"]),
        ("*Text.selectBackground", c["select"]), ("*Text.selectForeground", c["select_text"]),
        ("*Listbox.selectBackground", c["select"]), ("*Listbox.selectForeground", c["select_text"]),
        ("*Entry.selectBackground", c["select"]), ("*Entry.selectForeground", c["select_text"]),
        ("*insertBackground", c["text"]), ("*disabledForeground", c["faint"]),
        ("*Entry.background", c["input"]), ("*Text.background", c["input"]),
        ("*Listbox.background", c["input"]), ("*Text.relief", "flat"),
        ("*Button.relief", "flat"), ("*Button.background", c["surface"]),
        ("*Checkbutton.selectColor", c["input"]),
        ("*TCombobox*Listbox.background", c["input"]), ("*TCombobox*Listbox.foreground", c["text"]),
        ("*TCombobox*Listbox.font", (FONT, 10)),
        ("*TCombobox*Listbox.selectBackground", c["select"]),
        ("*TCombobox*Listbox.selectForeground", c["select_text"]),
    ):
        root.option_add(pattern, value)
    try:
        root.configure(bg=c["bg"])
    except tk.TclError:
        pass

    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=c["bg"], foreground=c["text"], font=(FONT, 10),
                bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"],
                troughcolor=c["surface2"], fieldbackground=c["input"],
                selectbackground=c["select"], selectforeground=c["select_text"],
                insertcolor=c["text"], focuscolor=c["accent"])

    # Frames
    s.configure("TFrame", background=c["bg"])
    s.configure("Card.TFrame", background=c["surface"])
    s.configure("Side.TFrame", background=c["sidebar"])
    s.configure("Top.TFrame", background=c["surface"])

    # Labels
    for name, bg in (("", c["bg"]), ("Card.", c["surface"]), ("Top.", c["surface"])):
        s.configure(f"{name}TLabel", background=bg, foreground=c["text"])
        s.configure(f"{name}Muted.TLabel", background=bg, foreground=c["muted"], font=(FONT, 9))
        s.configure(f"{name}H1.TLabel", background=bg, foreground=c["text"], font=(FONT, 18, "bold"))
        s.configure(f"{name}H2.TLabel", background=bg, foreground=c["text"], font=(FONT, 12, "bold"))
        s.configure(f"{name}H3.TLabel", background=bg, foreground=c["text"], font=(FONT, 10, "bold"))
        s.configure(f"{name}KPI.TLabel", background=bg, foreground=c["text"], font=(FONT, 22, "bold"))
        s.configure(f"{name}Danger.TLabel", background=bg, foreground=c["danger"], font=(FONT, 9))
        s.configure(f"{name}Success.TLabel", background=bg, foreground=c["success"], font=(FONT, 9, "bold"))
        s.configure(f"{name}Warn.TLabel", background=bg, foreground=c["warning"], font=(FONT, 9))
    s.configure("Side.TLabel", background=c["sidebar"], foreground=c["sidebar_text"])
    s.configure("SideMuted.TLabel", background=c["sidebar"], foreground=c["sidebar_muted"], font=(FONT, 8))
    s.configure("Brand.TLabel", background=c["sidebar"], foreground="#FFFFFF", font=(FONT, 14, "bold"))

    # Buttons
    s.configure("TButton", background=c["surface"], foreground=c["text"], padding=(px(12), px(6)),
                borderwidth=1, relief="flat", font=(FONT, 10))
    s.map("TButton", background=[("disabled", c["surface2"]), ("pressed", c["border"]),
                                 ("active", c["surface2"])],
          foreground=[("disabled", c["faint"])])
    for name, base, hover in (("Accent", c["accent"], c["accent_hover"]),
                              ("Danger", c["danger"], c["danger_hover"])):
        s.configure(f"{name}.TButton", background=base, foreground="#FFFFFF", bordercolor=base,
                    lightcolor=base, darkcolor=base, font=(FONT, 10, "bold"), padding=(px(14), px(7)))
        s.map(f"{name}.TButton", background=[("disabled", c["border"]), ("pressed", hover), ("active", hover)],
              foreground=[("disabled", c["faint"])])
    s.configure("Ghost.TButton", background=c["surface"], foreground=c["accent"], borderwidth=0,
                padding=(px(8), px(4)))
    s.map("Ghost.TButton", background=[("active", c["accent_soft"])])
    s.configure("Nav.TButton", background=c["sidebar"], foreground=c["sidebar_text"], borderwidth=0,
                anchor="w", padding=(px(18), px(10)), font=(FONT, 10), lightcolor=c["sidebar"],
                darkcolor=c["sidebar"], bordercolor=c["sidebar"])
    s.map("Nav.TButton", background=[("active", c["sidebar_hover"])])
    s.configure("NavActive.TButton", background=c["sidebar_active"], foreground="#FFFFFF",
                borderwidth=0, anchor="w", padding=(px(18), px(10)), font=(FONT, 10, "bold"),
                lightcolor=c["accent"], darkcolor=c["sidebar_active"], bordercolor=c["sidebar_active"])
    s.map("NavActive.TButton", background=[("active", c["sidebar_active"])])

    # Inputs
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        s.configure(w, fieldbackground=c["input"], foreground=c["text"], padding=px(5),
                    bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"],
                    arrowcolor=c["muted"], background=c["surface"], arrowsize=px(14))
        s.map(w, bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])],
              fieldbackground=[("readonly", c["input"])], foreground=[("readonly", c["text"])],
              selectbackground=[("readonly", c["input"])], selectforeground=[("readonly", c["text"])])
    for name, bg in (("", c["bg"]), ("Card.", c["surface"])):
        s.configure(f"{name}TCheckbutton", background=bg, foreground=c["text"],
                    indicatorbackground=c["input"], indicatorforeground=c["accent"],
                    indicatorsize=px(15), indicatormargin=(0, 0, px(8), 0), padding=px(2))
        s.map(f"{name}TCheckbutton", background=[("active", bg)],
              indicatorbackground=[("selected", c["accent"])],
              indicatorforeground=[("selected", "#FFFFFF")])
        s.configure(f"{name}TRadiobutton", background=bg, foreground=c["text"],
                    indicatorbackground=c["input"], indicatorsize=px(15),
                    indicatormargin=(0, 0, px(8), 0), padding=px(2))
        s.map(f"{name}TRadiobutton", background=[("active", bg)],
              indicatorbackground=[("selected", c["accent"])])

    _fluent_indicators(root, s)

    # Notebook
    s.configure("TNotebook", background=c["bg"], borderwidth=0, tabmargins=(0, 4, 0, 0))
    s.configure("TNotebook.Tab", background=c["bg"], foreground=c["muted"], padding=(px(14), px(7)),
                borderwidth=0, font=(FONT, 10))
    s.map("TNotebook.Tab", background=[("selected", c["surface"])],
          foreground=[("selected", c["accent"])], expand=[("selected", (0, 0, 0, 0))],
          padding=[("selected", (px(14), px(7)))])
    s.configure("Card.TNotebook", background=c["surface"])

    # Tables
    s.configure("Treeview", background=c["surface"], fieldbackground=c["surface"],
                foreground=c["text"], rowheight=px(30), borderwidth=0, font=(FONT, 10))
    s.map("Treeview", background=[("selected", c["select"])], foreground=[("selected", c["select_text"])])
    s.configure("Treeview.Heading", background=c["surface2"], foreground=c["muted"],
                font=(FONT, 9, "bold"), relief="flat", padding=(px(8), px(6)), borderwidth=0)
    s.map("Treeview.Heading", background=[("active", c["border"])])
    s.configure("TLabelframe", background=c["surface"], bordercolor=c["border"])
    s.configure("TLabelframe.Label", background=c["surface"], foreground=c["muted"],
                font=(FONT, 9, "bold"))
    s.configure("TPanedwindow", background=c["bg"])
    s.configure("Vertical.TScrollbar", background=c["surface2"], troughcolor=c["bg"],
                bordercolor=c["bg"], arrowcolor=c["muted"], gripcount=0, arrowsize=px(14))
    s.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["surface2"])
    return C


_IMAGES: list = []   # keep PhotoImages alive


def _box_image(root, size: int, fill: str, border: str, check: str | None, round_: bool, gap: int = 0):
    """Draw a checkbox / radio indicator pixel by pixel (no image files needed)."""
    img = tk.PhotoImage(master=root, width=size + gap, height=size)   # gap = space before the label
    r = size / 2.0
    rows = []
    bw = max(1, round(size / 14))
    for y in range(size):
        row = []
        for x in range(size):
            cx, cy = x + 0.5 - r, y + 0.5 - r
            if round_:
                d = (cx * cx + cy * cy) ** 0.5
                inside, edge = d <= r - 0.5, d > r - 0.5 - bw
            else:
                m = max(abs(cx), abs(cy))
                corner = abs(cx) > r - 3 and abs(cy) > r - 3 and (abs(cx) - (r - 3)) ** 2 + (abs(cy) - (r - 3)) ** 2 > 9
                inside, edge = m <= r - 0.5 and not corner, m > r - 0.5 - bw
            row.append((border if edge else fill) if inside else "")
        rows.append(row)
    if check and not round_:                       # tick: two thick strokes
        th = max(1.2, size / 9)
        def seg(px0, py0, px1, py1):
            for y in range(size):
                for x in range(size):
                    vx, vy = px1 - px0, py1 - py0
                    t = max(0, min(1, ((x - px0) * vx + (y - py0) * vy) / (vx * vx + vy * vy)))
                    dx, dy = x - (px0 + t * vx), y - (py0 + t * vy)
                    if dx * dx + dy * dy <= th * th / 2:
                        rows[y][x] = check
        seg(size * .24, size * .52, size * .42, size * .70)
        seg(size * .42, size * .70, size * .76, size * .32)
    if check and round_:                           # dot
        for y in range(size):
            for x in range(size):
                if ((x + .5 - r) ** 2 + (y + .5 - r) ** 2) ** .5 <= size * .22:
                    rows[y][x] = check
    for y, row in enumerate(rows):
        for x, col in enumerate(row):
            if col:
                img.put(col, (x, y))
    _IMAGES.append(img)
    return img


def _fluent_indicators(root, s):
    """Replace clam's 'X' checkbox with a filled Fluent-style tick box."""
    c, size = C, px(16)
    g = px(8)
    off = _box_image(root, size, c["input"], c["faint"], None, False, g)
    on = _box_image(root, size, c["accent"], c["accent"], "#FFFFFF", False, g)
    roff = _box_image(root, size, c["input"], c["faint"], None, True, g)
    ron = _box_image(root, size, c["input"], c["accent"], c["accent"], True, g)
    try:
        s.element_create("Fluent.Checkbutton.indicator", "image", off, ("selected", on))
        s.element_create("Fluent.Radiobutton.indicator", "image", roff, ("selected", ron))
    except tk.TclError:
        return                                     # already created (second apply)
    for kind in ("Checkbutton", "Radiobutton"):
        layout = [(f"{kind}.padding", {"sticky": "nswe", "children": [
            (f"Fluent.{kind}.indicator", {"side": "left", "sticky": ""}),
            (f"{kind}.focus", {"side": "left", "sticky": "w", "children": [
                (f"{kind}.label", {"sticky": "nswe"})]})]})]
        for prefix in ("", "Card."):
            s.layout(f"{prefix}T{kind}", layout)
        s.configure(f"T{kind}", padding=(px(2), px(2), px(2), px(2)))
        s.configure(f"Card.T{kind}", padding=(px(2), px(2), px(2), px(2)))


def card(parent, padding: int = 16, **pack) -> tuple[tk.Frame, ttk.Frame]:
    """A white rounded-look card. Returns (outer, inner); put content in inner."""
    outer = tk.Frame(parent, bg=C["surface"], highlightthickness=1,
                     highlightbackground=C["border"], highlightcolor=C["border"], bd=0)
    inner = ttk.Frame(outer, style="Card.TFrame", padding=padding)
    inner.pack(fill="both", expand=True)
    return outer, inner


def style_text(widget: tk.Text, surface: bool = True):
    widget.configure(bg=C["surface"] if surface else C["input"], fg=C["text"],
                     insertbackground=C["text"], relief="flat", bd=0, padx=px(10), pady=px(8),
                     highlightthickness=1, highlightbackground=C["border"],
                     highlightcolor=C["accent"], selectbackground=C["select"],
                     selectforeground=C["select_text"], font=(FONT, 10))


class Pill(tk.Label):
    """Small coloured status chip."""

    def __init__(self, parent, text="", kind="muted", **kw):
        super().__init__(parent, text=text, font=(FONT, 9, "bold"), padx=px(10), pady=px(3), bd=0, **kw)
        self.set(text, kind)

    def set(self, text: str, kind: str = "muted"):
        colors = {"danger": C["danger"], "warning": C["warning"], "success": C["success"],
                  "info": C["info"], "accent": C["accent"], "muted": C["faint"]}
        self.configure(text=text, bg=colors.get(kind, C["faint"]), fg="#FFFFFF")
