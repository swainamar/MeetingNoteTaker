"""
Enterprise features for Meeting Note-Taker 2.0 (no extra packages):

  - Audit log        : append-only meetings/audit.jsonl of who recorded / exported / deleted what.
  - Data retention   : auto-delete audio after N days and whole meetings after M days.
  - Action items     : one tracker across all meetings, parsed from notes.md tables.
  - Export           : print-ready HTML notes (Save as PDF from the browser), CSV of action items.
  - Outlook          : title + attendee names from the Outlook meeting happening now.
  - Meeting metadata : meetings/<folder>/meeting.json (title, start, duration, app, attendees).
"""

import csv
import datetime as dt
import getpass
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_AUDIT_LOCK = threading.Lock()
FOLDER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})?_(.*)$")


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------
def audit(out_dir: Path, event: str, enabled: bool = True, **fields):
    """Append one JSON line: time, Windows user, event, details. Never raises."""
    if not enabled:
        return
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    rec = {"time": dt.datetime.now().isoformat(timespec="seconds"), "user": user, "event": event}
    rec.update({k: (str(v) if isinstance(v, Path) else v) for k, v in fields.items()})
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOCK, open(out_dir / "audit.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"Audit log write failed: {e}")


def read_audit(out_dir: Path, limit: int = 500) -> list[dict]:
    p = out_dir / "audit.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return list(reversed(out))


# --------------------------------------------------------------------------
# Meeting metadata
# --------------------------------------------------------------------------
def folder_datetime(folder: Path) -> dt.datetime | None:
    m = FOLDER_RE.match(folder.name)
    if not m:
        return None
    try:
        return dt.datetime.strptime(f"{m.group(1)} {m.group(2)}{m.group(3)}{m.group(4) or '00'}",
                                    "%Y-%m-%d %H%M%S")
    except ValueError:
        return None


def read_meta(folder: Path) -> dict:
    try:
        data = json.loads((folder / "meeting.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_meta(folder: Path, **fields):
    data = read_meta(folder)
    data.update(fields)
    try:
        (folder / "meeting.json").write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                             encoding="utf-8")
    except OSError as e:
        print(f"Could not write meeting.json: {e}")


def meeting_title(folder: Path) -> str:
    meta = read_meta(folder)
    if meta.get("title"):
        return str(meta["title"])
    m = FOLDER_RE.match(folder.name)
    return m.group(5).replace("-", " ") if m else folder.name


def meeting_seconds(folder: Path) -> float:
    meta = read_meta(folder)
    if meta.get("seconds"):
        return float(meta["seconds"])
    best = 0.0
    try:
        import soundfile as sf
        for w in folder.glob("audio_*.wav"):
            try:
                best = max(best, sf.info(str(w)).duration)
            except Exception:
                pass
    except ImportError:
        pass
    return best


def list_meetings(out_dir: Path) -> list[Path]:
    if not out_dir.exists():
        return []
    return sorted((d for d in out_dir.iterdir() if d.is_dir() and FOLDER_RE.match(d.name)),
                  key=lambda p: p.name, reverse=True)


def folder_size(folder: Path) -> int:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def human_duration(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    h, m = sec // 3600, sec % 3600 // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


# --------------------------------------------------------------------------
# Data retention
# --------------------------------------------------------------------------
def apply_retention(out_dir: Path, audio_days: int, meeting_days: int, audit_on: bool = True) -> dict:
    """Delete audio older than audio_days and meetings older than meeting_days (0 = keep)."""
    now = dt.datetime.now()
    freed, audio_n, meet_n = 0, 0, 0
    for folder in list_meetings(out_dir):
        when = folder_datetime(folder)
        if when is None:
            continue
        age = (now - when).days
        if meeting_days and age >= meeting_days:
            size = folder_size(folder)
            shutil.rmtree(folder, ignore_errors=True)
            if not folder.exists():
                freed += size
                meet_n += 1
            continue
        if audio_days and age >= audio_days:
            for w in [*folder.glob("audio_*.wav"), *folder.glob("meeting_video.mp4"),
                      *folder.glob("screen_*.mkv")]:
                try:
                    size = w.stat().st_size
                    w.unlink()
                    freed += size
                    audio_n += 1
                except OSError:
                    pass
    if audio_n or meet_n:
        audit(out_dir, "retention_purge", audit_on, audio_files=audio_n, meetings=meet_n,
              freed_bytes=freed, audio_days=audio_days, meeting_days=meeting_days)
    return {"audio_files": audio_n, "meetings": meet_n, "freed": freed}


# --------------------------------------------------------------------------
# Action items
# --------------------------------------------------------------------------
def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_action_items(notes: str) -> list[dict]:
    """Rows of the first Markdown table under a heading that mentions actions
    / next steps / commitments / follow-up."""
    lines = notes.splitlines()
    out, in_section = [], False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("#"):
            in_section = bool(re.search(r"action|next step|commitment|follow", line, re.I))
        elif in_section and line.strip().startswith("|") and i + 1 < len(lines) \
                and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            header = [h.lower() for h in _cells(line)]

            def col(*keys):
                return next((n for n, h in enumerate(header) if any(k in h for k in keys)), None)
            c_owner = col("owner", "who")
            c_due = col("due", "when", "eta", "target", "date")
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = _cells(lines[i])
                action = row[0] if row else ""
                if action and not re.fullmatch(r"(none( recorded)?|n/?a|-+|not stated)\.?", action, re.I):
                    out.append({
                        "action": action,
                        "owner": row[c_owner] if c_owner is not None and c_owner < len(row) else "",
                        "due": row[c_due] if c_due is not None and c_due < len(row) else "",
                    })
                i += 1
            in_section = False
            continue
        i += 1
    return out


def _item_id(action: str) -> str:
    return hashlib.sha1(action.strip().lower().encode("utf-8")).hexdigest()[:12]


def _notes_text(folder: Path) -> str:
    p = folder / "notes.md"
    try:
        return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
    except OSError:
        return ""


def all_action_items(out_dir: Path) -> list[dict]:
    items = []
    for folder in list_meetings(out_dir):
        notes = _notes_text(folder)
        if not notes:
            continue
        state = read_meta(folder).get("actions", {})
        when = folder_datetime(folder)
        for it in parse_action_items(notes):
            iid = _item_id(it["action"])
            items.append({**it, "id": iid, "done": bool(state.get(iid)), "folder": folder,
                          "meeting": meeting_title(folder),
                          "date": when.strftime("%Y-%m-%d") if when else ""})
    return items


def set_action_done(folder: Path, item_id: str, done: bool):
    meta = read_meta(folder)
    actions = meta.get("actions", {})
    actions[item_id] = bool(done)
    write_meta(folder, actions=actions)


def export_actions_csv(items: list[dict], path: Path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Status", "Action", "Owner", "Due", "Meeting", "Date"])
        for it in items:
            w.writerow(["Done" if it["done"] else "Open", it["action"], it["owner"],
                        it["due"], it["meeting"], it["date"]])


# --------------------------------------------------------------------------
# Export notes as print-ready HTML
# --------------------------------------------------------------------------
def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def markdown_to_html(md: str) -> str:
    """Small, safe Markdown subset: headings, bullets, numbered lists, tables, paragraphs."""
    out, lines, i, in_list = [], md.splitlines(), 0, None
    def close_list():
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None
    while i < len(lines):
        line = lines[i]
        st = line.strip()
        if st.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            close_list()
            head = _cells(line)
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(h)}</th>" for h in head)
                       + "</tr></thead><tbody>")
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in _cells(lines[i])) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", st)
        if m:
            close_list()
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
        elif re.match(r"^[-*]\s+", st):
            if in_list != "ul":
                close_list()
                out.append("<ul>")
                in_list = "ul"
            item = re.sub(r"^[-*]\s+", "", st)
            out.append(f"<li>{_inline(item)}</li>")
        elif re.match(r"^\d+[.)]\s+", st):
            if in_list != "ol":
                close_list()
                out.append("<ol>")
                in_list = "ol"
            item = re.sub(r"^\d+[.)]\s+", "", st)
            out.append(f"<li>{_inline(item)}</li>")
        elif st:
            close_list()
            out.append(f"<p>{_inline(st)}</p>")
        else:
            close_list()
        i += 1
    close_list()
    return "\n".join(out)


HTML_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>
body{{font-family:'Segoe UI',system-ui,sans-serif;color:#0F172A;max-width:860px;margin:40px auto;padding:0 24px;line-height:1.55}}
header{{border-bottom:3px solid #2563EB;padding-bottom:12px;margin-bottom:24px}}
header h1{{margin:0;font-size:26px}} header .meta{{color:#64748B;font-size:13px;margin-top:4px}}
h2{{font-size:17px;margin-top:28px;color:#1E3A8A;border-bottom:1px solid #E2E8F0;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}}
th{{background:#F1F5F9;text-align:left}} th,td{{border:1px solid #E2E8F0;padding:7px 10px;vertical-align:top}}
code{{background:#F1F5F9;padding:1px 4px;border-radius:3px}}
footer{{margin-top:40px;color:#94A3B8;font-size:11px;border-top:1px solid #E2E8F0;padding-top:8px}}
@media print{{body{{margin:0}} a{{color:inherit}}}}
</style></head><body>
<header><h1>{title}</h1><div class="meta">{meta}</div></header>
{body}
<footer>Generated by Meeting Note-Taker 2.0 &middot; {generated} &middot; Confidential - internal use</footer>
</body></html>"""


def export_notes_html(folder: Path) -> Path | None:
    notes = _notes_text(folder)
    if not notes:
        return None
    meta = read_meta(folder)
    when = folder_datetime(folder)
    bits = [when.strftime("%d %B %Y, %H:%M") if when else ""]
    if meta.get("seconds"):
        bits.append(human_duration(meta["seconds"]))
    if meta.get("attendees"):
        bits.append("Attendees: " + ", ".join(meta["attendees"][:15]))
    # Drop the "# title / _date_" header make_notes writes; the page has its own.
    body_md = re.sub(r"\A#\s.*\n(_.*_\n)?", "", notes)
    page = HTML_PAGE.format(title=html.escape(meeting_title(folder)),
                            meta=html.escape("  ·  ".join(b for b in bits if b)),
                            body=markdown_to_html(body_md),
                            generated=dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    out = folder / "notes.html"
    out.write_text(page, encoding="utf-8")
    return out


# --------------------------------------------------------------------------
# Outlook (classic) - meeting happening now
# --------------------------------------------------------------------------
_OUTLOOK_PS = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try { $ol = [Runtime.InteropServices.Marshal]::GetActiveObject('Outlook.Application') } catch { exit 0 }
$items = $ol.GetNamespace('MAPI').GetDefaultFolder(9).Items
$items.IncludeRecurrences = $true
$items.Sort('[Start]')
$now = Get-Date
$f = "[Start] <= '" + $now.AddMinutes(15).ToString('g') + "' AND [End] >= '" + $now.ToString('g') + "'"
foreach ($i in $items.Restrict($f)) {
  if ($i.AllDayEvent) { continue }
  $att = @()
  if ($env:NT_ATTENDEES -eq '1') { foreach ($r in $i.Recipients) { $att += $r.Name } }
  [pscustomobject]@{ subject = $i.Subject; start = $i.Start.ToString('s'); end = $i.End.ToString('s');
                     organizer = $i.Organizer; attendees = $att } | ConvertTo-Json -Compress
  break
}
"""


def current_outlook_meeting(with_attendees: bool = True, timeout: float = 12.0) -> dict | None:
    """The Outlook calendar item happening now (or starting within 15 min), or None.
    Reads classic Outlook through Windows PowerShell COM; no data leaves the PC."""
    if sys.platform != "win32":
        return None
    import os
    env = dict(os.environ, NT_ATTENDEES="1" if with_attendees else "0")
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _OUTLOOK_PS],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"Outlook lookup skipped: {e}")
        return None
    line = (r.stdout or "").strip().splitlines()
    if not line:
        return None
    try:
        data = json.loads(line[-1])
    except ValueError:
        return None
    att = data.get("attendees") or []
    if isinstance(att, str):
        att = [att]
    data["attendees"] = [a for a in att if a and "@" not in a][:60]
    return data
