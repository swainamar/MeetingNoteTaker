"""
Screen recording (V2.1) with FFmpeg's Desktop Duplication grabber (ddagrab).

  - Records one chosen screen (works with mixed-DPI multi-monitor setups,
    where the older gdigrab captures the wrong area).
  - GPU-side capture, downscaled to at most 1920 px wide, H.264 at 5 fps:
    roughly 100-300 MB per hour for typical slide / screen-share content.
  - Pause = close the current segment; Resume = start a new one, so the
    video stays in sync with the audio (which also skips paused time).
  - After the call the segments are joined and muxed with the call audio
    into meeting_video.mp4.
  - Windows hidden with "Hide from screen sharing" are not captured.
"""

import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VIDEO_NAME = "meeting_video.mp4"


def find_ffmpeg() -> str | None:
    """FFmpeg from PATH (never the current folder), else the imageio-ffmpeg copy."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip('"')
        if not d or not os.path.isabs(d):
            continue
        exe = os.path.join(d, "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        if os.path.isfile(exe):
            return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def monitor_count() -> int:
    if sys.platform != "win32":
        return 1
    try:
        return max(1, ctypes.windll.user32.GetSystemMetrics(80))   # SM_CMONITORS
    except (AttributeError, OSError):
        return 1


class ScreenRecorder:
    def __init__(self, folder: Path, monitor: int = 0, fps: int = 5, max_width: int = 1920,
                 area: list | None = None, audio_t0: float | None = None):
        """area = [x, y, w, h] as fractions of the screen (None = whole screen).
        audio_t0 = time.monotonic() when the audio started, to keep the video in sync."""
        self.folder = folder
        self.area = area if area and len(area) == 4 else None
        self.audio_t0 = audio_t0
        self.offset = 0.0                      # seconds of audio before the first video frame
        self.monitor = max(0, int(monitor))
        self.fps = max(1, min(30, int(fps)))
        self.max_width = max_width
        self.ffmpeg = find_ffmpeg()
        self.segments: list[Path] = []
        self.proc: subprocess.Popen | None = None
        self.error = ""
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg)

    def _cmd(self, out: Path) -> list[str]:
        crop = ""
        if self.area:
            x, y, w, h = (max(0.0, min(1.0, float(v))) for v in self.area)
            crop = (f"crop=w='trunc(iw*{w}/2)*2':h='trunc(ih*{h}/2)*2':"
                    f"x='trunc(iw*{x})':y='trunc(ih*{y})',")
        vf = (f"ddagrab=output_idx={self.monitor}:framerate={self.fps}:draw_mouse=1,"
              f"hwdownload,format=bgra,{crop}"
              f"scale='trunc(min({self.max_width},iw)/2)*2':-2,format=yuv420p")
        # No -nostdin: stop() sends 'q' on stdin so FFmpeg closes the file cleanly.
        return [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", vf,
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage", "-crf", "30",
                "-g", str(self.fps * 10), "-pix_fmt", "yuv420p", str(out)]

    def start(self) -> bool:
        with self._lock:
            if not self.ffmpeg:
                self.error = "FFmpeg not found"
                return False
            if self.proc and self.proc.poll() is None:
                return True
            out = self.folder / f"screen_{len(self.segments):02d}.mkv"
            if not self.segments and self.audio_t0 is not None:
                self.offset = max(0.0, time.monotonic() - self.audio_t0)
            try:
                self.proc = subprocess.Popen(self._cmd(out), stdin=subprocess.PIPE,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                             creationflags=NO_WINDOW)
            except OSError as e:
                self.error = str(e)
                self.proc = None
                return False
            self.segments.append(out)
            return True

    def _close(self):
        p, self.proc = self.proc, None
        if not p:
            return
        try:
            if p.poll() is None:
                p.stdin.write(b"q")             # FFmpeg finishes the file cleanly on 'q'
                p.stdin.flush()
                p.wait(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            p.kill()
        try:
            err = p.stderr.read().decode("utf-8", "ignore").strip() if p.stderr else ""
        except OSError:
            err = ""
        if err:
            self.error = err[-500:]
            print(f"Screen recorder: {self.error}")

    def pause(self):
        with self._lock:
            self._close()

    def resume(self):
        self.start()

    def stop(self):
        with self._lock:
            self._close()

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def finalize(self) -> Path | None:
        """Join segments and add the call audio -> meeting_video.mp4. Returns the path."""
        segs = [s for s in self.segments if s.exists() and s.stat().st_size > 0]
        if not segs or not self.ffmpeg:
            return None
        lst = self.folder / "screen_segments.txt"
        lst.write_text("".join(f"file '{s.name}'\n" for s in segs), encoding="utf-8")
        audio = [a for a in (self.folder / "audio_others.wav", self.folder / "audio_me.wav") if a.exists()]
        out = self.folder / VIDEO_NAME
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        if self.offset > 0.05:                 # video started after the audio (screen picker)
            cmd += ["-itsoffset", f"{self.offset:.3f}"]
        cmd += ["-f", "concat", "-safe", "0", "-i", lst.name]
        for a in audio:
            cmd += ["-i", a.name]
        if len(audio) == 2:
            cmd += ["-filter_complex", "[1:a][2:a]amix=inputs=2:duration=longest:normalize=0[a]",
                    "-map", "0:v", "-map", "[a]"]
        elif len(audio) == 1:
            cmd += ["-map", "0:v", "-map", "1:a"]
        cmd += ["-c:v", "copy"]
        if audio:
            cmd += ["-c:a", "aac", "-b:a", "96k"]
        cmd += ["-movflags", "+faststart", out.name]
        r = subprocess.run(cmd, cwd=str(self.folder), capture_output=True, creationflags=NO_WINDOW)
        if r.returncode != 0 or not out.exists():
            print("Video mux failed:", r.stderr.decode("utf-8", "ignore")[-500:])
            return None
        for s in segs:
            s.unlink(missing_ok=True)
        lst.unlink(missing_ok=True)
        return out
