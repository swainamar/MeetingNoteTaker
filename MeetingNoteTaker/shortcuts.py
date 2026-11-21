"""
Windows shortcuts with the app logo (Desktop, Start menu, Startup).

A .bat file cannot carry its own icon, so the launcher creates proper
.lnk shortcuts that start the app directly with pythonw (no console flash)
and show assets/logo.ico.

    python shortcuts.py install      Desktop + Start menu shortcuts
    python shortcuts.py remove       remove them (and the Startup one)
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICON = HERE / "assets" / "logo.ico"
NAME = "Meeting Note-Taker"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def _special(folder: str) -> Path:
    """Desktop / Programs / Startup via the shell (handles OneDrive-redirected Desktops)."""
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                        f"[Environment]::GetFolderPath('{folder}')"],
                       capture_output=True, text=True, creationflags=NO_WINDOW, timeout=20)
    p = (r.stdout or "").strip()
    if not p:
        raise OSError(f"Could not resolve the {folder} folder")
    return Path(p)


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def create(lnk: Path, args: str = "", description: str = "") -> Path:
    lnk.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(lnk) + ");"
        "$s.TargetPath = " + _ps_quote(_pythonw()) + ";"
        "$s.Arguments = " + _ps_quote(f'"{HERE / "app.py"}" {args}'.strip()) + ";"
        "$s.WorkingDirectory = " + _ps_quote(HERE) + ";"
        "$s.IconLocation = " + _ps_quote(f"{ICON},0") + ";"
        "$s.Description = " + _ps_quote(description or NAME) + ";"
        "$s.Save()")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, creationflags=NO_WINDOW, timeout=30)
    if r.returncode != 0 or not lnk.exists():
        raise OSError(f"Could not create {lnk}: {r.stderr.strip()[-300:]}")
    return lnk


def desktop_lnk() -> Path:
    return _special("Desktop") / f"{NAME}.lnk"


def startmenu_lnk() -> Path:
    return _special("Programs") / f"{NAME}.lnk"


def startup_lnk() -> Path:
    return _special("Startup") / f"{NAME}.lnk"


def install() -> list[Path]:
    desc = "Records meetings and writes notes - runs locally"
    return [create(desktop_lnk(), "", desc), create(startmenu_lnk(), "", desc)]


def set_startup(enabled: bool):
    lnk = startup_lnk()
    if enabled:
        create(lnk, "--compact", f"{NAME} (starts with Windows)")
    elif lnk.exists():
        lnk.unlink()


def remove():
    for lnk in (desktop_lnk(), startmenu_lnk(), startup_lnk()):
        if lnk.exists():
            lnk.unlink()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "install"
    if cmd == "install":
        for p in install():
            print(f"Created shortcut: {p}")
    elif cmd == "remove":
        remove()
        print("Shortcuts removed.")
    else:
        sys.exit("usage: shortcuts.py install|remove")
