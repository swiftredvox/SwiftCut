"""Shared fixtures. Integration media is generated once per session with FFmpeg's lavfi sources.

Every generated video encodes its frame number in its brightness: frame n is a flat grey of
level (n mod 64) * 4. Reading back output frames therefore tells us exactly which source frames
an export contains - a far stronger check than durations or exit codes.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swiftcut.core import ffmpeg_tools  # noqa: E402


def _ff():
    try:
        return ffmpeg_tools.find_ffmpeg()
    except ffmpeg_tools.FFmpegNotFound:
        return None


requires_ffmpeg = pytest.mark.skipif(_ff() is None, reason="FFmpeg not available")


def ffmpeg(*args: str) -> None:
    ff = _ff()
    assert ff is not None
    res = subprocess.run([ff.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr)


def numbered_video(path: Path, *, fps: int = 30, seconds: float = 4.0, size: str = "320x240",
                   audio: int = 0, gop: int | None = None, extra_out: tuple = (), vcodec: str = "libx264",
                   acodec: str = "aac", vf_extra: str = "") -> Path:
    if path.exists():
        return path
    vf = f"geq=lum='mod(N\\,64)*4':cb=128:cr=128{vf_extra}"
    args = ["-f", "lavfi", "-i", f"color=c=black:s={size}:r={fps}:d={seconds},format=yuv420p,{vf}"]
    for k in range(audio):
        args += ["-f", "lavfi", "-i", f"sine=frequency={440 * (k + 1)}:sample_rate=48000:duration={seconds}"]
    args += ["-map", "0:v"] + [x for k in range(audio) for x in ("-map", f"{k + 1}:a")]
    args += ["-c:v", vcodec]
    if vcodec == "libx264":
        args += ["-qp", "2", "-pix_fmt", "yuv420p"]
    if gop:
        args += ["-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0"] if vcodec == "libx264" else ["-g", str(gop)]
    if audio:
        args += ["-c:a", acodec]
    tmp = path.with_name("tmp_" + path.name)
    args += [*extra_out, str(tmp)]
    ffmpeg(*args)
    tmp.replace(path)   # only cache complete files
    return path


def frame_numbers(path: str | Path) -> list[int]:
    """Decode every frame and return the frame number encoded in its brightness."""
    ff = _ff()
    res = subprocess.run([ff.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path),
                          "-vf", "scale=8:8:flags=area,format=yuv420p,extractplanes=y", "-fps_mode", "passthrough",
                          "-f", "rawvideo", "pipe:1"], capture_output=True)
    data = res.stdout
    out = []
    for i in range(0, len(data) - 63, 64):
        block = data[i:i + 64]
        out.append(round(sum(block) / 64 / 4) % 64)
    return out


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    cache = os.environ.get("SWIFTCUT_TEST_MEDIA")
    if cache:
        p = Path(cache)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return tmp_path_factory.mktemp("media")


@pytest.fixture(autouse=True)
def no_blocking_dialogs(monkeypatch):
    """Message boxes never wait for a person during tests (they'd pop up on the user's screen).

    Questions answer "No" (the safe choice), notices are logged and dismissed. Individual tests
    can still monkeypatch QMessageBox to choose a different answer.
    """
    try:
        from PySide6.QtWidgets import QMessageBox
    except ImportError:
        return
    shown: list[str] = []
    no = QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "exec", lambda self: (shown.append(self.text()), no)[1])
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: (shown.append(str(a[2:3])), no)[1]))
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: (shown.append(str(a[2:3])), QMessageBox.StandardButton.Ok)[1]))
    return shown
