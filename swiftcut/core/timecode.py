"""Time formatting/parsing and the frame grid. All times are float seconds from file start."""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field


def format_time(t: float, show_ms: bool = True, force_hours: bool = False) -> str:
    """'0:07.250' or '1:02:03.004'."""
    total_ms = int(round(max(t, 0.0) * 1000.0))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    base = f"{h}:{m:02d}:{s:02d}" if (h or force_hours) else f"{m}:{s:02d}"
    return f"{base}.{ms:03d}" if show_ms else base


_TIME_RE = re.compile(r"^(?:(\d+):)?(?:(\d+):)?(\d+(?:[.,]\d*)?)$")


def parse_time(text: str) -> float:
    """Parse '75', '75.5s', '1:15', '1:15.5', '0:01:15.500'. Raises ValueError."""
    s = text.strip().lower()
    if s.endswith("s"):
        s = s[:-1].strip()
    m = _TIME_RE.match(s)
    if not m:
        raise ValueError(f"'{text}' is not a valid time")
    a, b, sec = m.groups()
    secs = float(sec.replace(",", "."))
    if a is not None and b is not None:
        hours, mins = int(a), int(b)
    elif a is not None:
        hours, mins = 0, int(a)
    else:
        hours, mins = 0, 0
    if a is not None and secs >= 60:
        raise ValueError(f"Seconds must be below 60 in '{text}'")
    if b is not None and mins >= 60:
        raise ValueError(f"Minutes must be below 60 in '{text}'")
    return hours * 3600 + mins * 60 + secs


@dataclass
class FrameGrid:
    """Presentation timestamps of every video frame, sorted, in seconds.

    Times are relative to the first video frame (times[0] == 0), which is what the user sees.
    `origin` is where that first frame sits in the container's own timeline (e.g. 0.021 s when
    the audio starts slightly earlier); FFmpeg seeks must add it.

    Frame i is on screen from times[i] until frame_end(i). Until the real timestamps have been
    scanned, `uniform()` provides a grid derived from the nominal frame rate (exact=False).
    """

    times: list[float]
    duration: float
    exact: bool = False
    keyframes: list[float] = field(default_factory=list)
    origin: float = 0.0

    @classmethod
    def uniform(cls, fps: float, duration: float, origin: float = 0.0) -> "FrameGrid":
        fps = fps if fps and fps > 0 else 30.0
        n = max(1, int(round(duration * fps)))
        return cls([i / fps for i in range(n)], max(duration, 1 / fps), exact=False, keyframes=[0.0],
                   origin=origin)

    def __len__(self) -> int:
        return len(self.times)

    @property
    def last_index(self) -> int:
        return len(self.times) - 1

    @property
    def typical_interval(self) -> float:
        if len(self.times) < 2:
            return self.duration or 1 / 30
        n = len(self.times)
        return (self.times[-1] - self.times[0]) / (n - 1)

    @property
    def min_interval(self) -> float:
        if len(self.times) < 2:
            return self.typical_interval
        return max(1e-4, min(b - a for a, b in zip(self.times, self.times[1:])))

    def frame_end(self, i: int) -> float:
        """Exclusive end time of frame i."""
        if i + 1 < len(self.times):
            return self.times[i + 1]
        last = self.times[-1]
        # Last frame lasts until the container ends, or one typical interval if that's unknown.
        if self.duration > last + 1e-4:
            return min(self.duration, last + 2 * self.typical_interval)
        return last + self.typical_interval

    @property
    def end_time(self) -> float:
        return self.frame_end(self.last_index)

    def index_at(self, t: float) -> int:
        """Index of the frame on screen at time t."""
        i = bisect.bisect_right(self.times, t + 1e-6) - 1
        return min(max(i, 0), self.last_index)

    def nearest_index(self, t: float) -> int:
        i = bisect.bisect_left(self.times, t)
        if i <= 0:
            return 0
        if i >= len(self.times):
            return self.last_index
        return i if (self.times[i] - t) < (t - self.times[i - 1]) else i - 1

    def keyframe_at_or_before(self, t: float) -> float:
        if not self.keyframes:
            return 0.0
        i = bisect.bisect_right(self.keyframes, t + 1e-6) - 1
        return self.keyframes[max(i, 0)]
