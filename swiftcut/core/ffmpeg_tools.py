"""Locate FFmpeg/FFprobe and run them without flashing console windows."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

# Windows: stop child processes opening a console window when running under pythonw/frozen.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Well-known install locations, checked after the bundled copy and PATH.
_KNOWN_DIRS = [
    r"C:\Program Files (x86)\FFmpeg\bin",
    r"C:\Program Files\FFmpeg\bin",
    r"C:\ffmpeg\bin",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
    os.path.expandvars(r"%USERPROFILE%\scoop\shims"),
    r"C:\ProgramData\chocolatey\bin",
]


class FFmpegNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class FFmpegPaths:
    ffmpeg: str
    ffprobe: str


def app_root() -> Path:
    """Project root in development; the unpacked bundle directory when frozen."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def _candidate_dirs() -> list[Path]:
    root = app_root()
    dirs = [root / "ffmpeg", root / "vendor" / "ffmpeg" / "bin", root / "vendor" / "ffmpeg"]
    env = os.environ.get("SWIFTCUT_FFMPEG_DIR")
    if env:
        dirs.insert(0, Path(env))
    return dirs


@lru_cache(maxsize=1)
def find_ffmpeg() -> FFmpegPaths:
    """Return ffmpeg/ffprobe paths. Order: $SWIFTCUT_FFMPEG_DIR, bundled copy, PATH, known dirs."""
    for d in _candidate_dirs():
        f, p = d / "ffmpeg.exe", d / "ffprobe.exe"
        if f.is_file() and p.is_file():
            return FFmpegPaths(str(f), str(p))
    f, p = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if f and p:
        return FFmpegPaths(f, p)
    for d in map(Path, _KNOWN_DIRS):
        f, p = d / "ffmpeg.exe", d / "ffprobe.exe"
        if f.is_file() and p.is_file():
            return FFmpegPaths(str(f), str(p))
    raise FFmpegNotFound(
        "FFmpeg could not be found. Install FFmpeg, add it to PATH, or place ffmpeg.exe and "
        "ffprobe.exe in the 'vendor/ffmpeg/bin' folder inside the SwiftCut folder."
    )


def run(args: list[str], timeout: float | None = 60) -> subprocess.CompletedProcess:
    """Run a command and capture text output, with no console window."""
    log.debug("run: %s", subprocess.list2cmdline(args))
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
    )


def popen(args: list[str], **kw) -> subprocess.Popen:
    log.debug("popen: %s", subprocess.list2cmdline(args))
    return subprocess.Popen(args, creationflags=CREATE_NO_WINDOW, **kw)


@lru_cache(maxsize=1)
def ffmpeg_version() -> str:
    """e.g. '9.0.2' (or a git build id), or '' if FFmpeg can't be run."""
    try:
        first = run([find_ffmpeg().ffmpeg, "-hide_banner", "-version"], timeout=10).stdout.splitlines()[0]
    except (FFmpegNotFound, OSError, subprocess.TimeoutExpired, IndexError):
        return ""
    parts = first.split()
    ver = parts[2] if len(parts) > 2 and parts[0] == "ffmpeg" else ""
    return ver.split("-essentials")[0].split("-full")[0]


@lru_cache(maxsize=None)
def encoder_works(encoder: str) -> bool:
    """True if FFmpeg can actually open `encoder` here (e.g. NVENC needs a GPU and driver)."""
    try:
        paths = find_ffmpeg()
    except FFmpegNotFound:
        return False
    args = [paths.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=black:s=256x256:r=30:d=0.1", "-frames:v", "1",
            "-c:v", encoder, "-f", "null", "-"]
    try:
        ok = run(args, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    log.info("encoder probe %s: %s", encoder, "available" if ok else "unavailable")
    return ok
