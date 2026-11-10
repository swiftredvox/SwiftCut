"""Find still (unchanging) footage at the start and end of a recording, to suggest trim points.

Screen recordings typically open and close on a frozen image while the recorder is being
started and stopped. FFmpeg's freezedetect filter finds those stretches; we only look at the
first and last minute of long files, on a downscaled copy, so this stays quick.
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
from dataclasses import dataclass

from . import ffmpeg_tools
from .media_info import MediaInfo
from .timecode import FrameGrid

log = logging.getLogger(__name__)

WINDOW = 60.0            # seconds analysed at each end of long videos
MIN_STILL = 0.4          # ignore pauses shorter than this
NOISE = "-50dB"          # tolerance for encoder noise on "identical" frames (tuned: -55 too strict, -45 false hits)
MIN_KEEP = 0.5           # never suggest leaving less than this


@dataclass
class Suggestion:
    start: float | None  # time of the last still frame before motion (video timeline), or None
    end: float | None    # exclusive end: when the trailing still begins + one frame, or None

    @property
    def any(self) -> bool:
        return self.start is not None or self.end is not None


_RE = re.compile(r"lavfi\.freezedetect\.(freeze_start|freeze_end):\s*([-\d.]+)")


def _freezes(info: MediaInfo, ss: float | None, dur: float | None,
             cancel: threading.Event | None) -> list[tuple[float, float | None]]:
    """(start, end) of frozen stretches, in container seconds. end is None if it runs to EOF."""
    ff = ffmpeg_tools.find_ffmpeg().ffmpeg
    args = [ff, "-hide_banner", "-nostdin", "-loglevel", "info", "-copyts"]
    if ss is not None:
        args += ["-ss", f"{ss:.3f}"]
    args += ["-i", info.path]
    if dur is not None:
        args += ["-t", f"{dur:.3f}"]
    args += ["-map", f"0:{info.video.index}", "-an", "-sn",
             "-vf", f"scale=320:-2,freezedetect=n={NOISE}:d={MIN_STILL}", "-f", "null", "-"]
    proc = ffmpeg_tools.popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    out: list[tuple[float, float | None]] = []
    assert proc.stderr is not None
    try:
        for line in proc.stderr:
            if cancel is not None and cancel.is_set():
                proc.kill()
                return []
            m = _RE.search(line)
            if not m:
                continue
            t = float(m.group(2))
            if m.group(1) == "freeze_start":
                out.append((t, None))
            elif out and out[-1][1] is None:
                out[-1] = (out[-1][0], t)
    finally:
        proc.wait()
    return out


def detect(info: MediaInfo, grid: FrameGrid, cancel: threading.Event | None = None) -> Suggestion:
    base = info.start_time + grid.origin            # container time of our t = 0
    total = grid.end_time
    if total <= 2 * WINDOW + 10:
        spans = _freezes(info, None, None, cancel)
        tail_spans = spans
    else:
        spans = _freezes(info, None, WINDOW, cancel)
        tail_spans = _freezes(info, base + total - WINDOW, None, cancel)
    rel = lambda t: t - base  # noqa: E731

    first, last = grid.times[0], grid.times[-1]
    tol = 1.5 * grid.typical_interval
    start = end = None
    # Leading still: a freeze beginning on the first frame and ending before the video does.
    for s, e in spans:
        if rel(s) <= first + tol and e is not None and rel(e) < total - MIN_KEEP:
            # Keep the last still frame, so the clip opens on the image just before motion.
            start = grid.times[max(0, grid.index_at(rel(e) + 1e-4) - 1)]
            break
    # Trailing still: a freeze that runs to the end (no end reported, or ends on the last frame).
    for s, e in reversed(tail_spans):
        if e is None or rel(e) >= last - tol:
            if rel(s) > first + tol:
                end = grid.frame_end(grid.index_at(rel(s) + 1e-4))   # keep the first still frame
            break
    if start is not None and end is not None and end - start < MIN_KEEP:
        start = end = None
    log.info("suggested trim: start=%s end=%s", start, end)
    return Suggestion(start, end)
