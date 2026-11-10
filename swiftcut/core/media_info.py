"""Media inspection via ffprobe: stream metadata and exact per-frame timestamps."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from . import ffmpeg_tools
from .timecode import FrameGrid

log = logging.getLogger(__name__)


class ProbeError(RuntimeError):
    """The file could not be read as a video."""


def _rate(text: str | None) -> float:
    try:
        f = Fraction(text or "0")
        return float(f) if f > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass
class VideoStream:
    index: int
    codec: str
    width: int
    height: int
    fps: float                 # average frame rate
    r_fps: float               # "real" base rate reported by FFmpeg
    pix_fmt: str = ""
    profile: str = ""
    rotation: int = 0          # degrees, from the display matrix
    bit_rate: int = 0
    frame_count: int = 0
    has_b_frames: int = 0      # >0 means decode order differs from display order

    @property
    def display_size(self) -> tuple[int, int]:
        if abs(self.rotation) % 180 == 90:
            return self.height, self.width
        return self.width, self.height


@dataclass
class AudioStream:
    index: int                 # absolute stream index in the file
    codec: str
    channels: int
    sample_rate: int
    language: str = ""
    title: str = ""
    bit_rate: int = 0

    def label(self, n: int) -> str:
        parts = [f"Track {n}"]
        if self.title:
            parts.append(self.title)
        elif self.language and self.language != "und":
            parts.append(self.language)
        layout = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(self.channels, f"{self.channels} ch")
        parts.append(f"{self.codec.upper()} {layout}")
        return " - ".join(parts)


@dataclass
class MediaInfo:
    path: str
    size_bytes: int
    format_name: str
    duration: float
    start_time: float
    bit_rate: int
    video: VideoStream
    audio: list[AudioStream] = field(default_factory=list)
    other_streams: int = 0     # subtitles / data / attachments (not carried into exports)
    video_offset: float = 0.0  # container time of the first video frame (audio may start earlier)

    @property
    def has_audio(self) -> bool:
        return bool(self.audio)

    @property
    def extension(self) -> str:
        return Path(self.path).suffix.lower().lstrip(".")

    @property
    def maybe_vfr(self) -> bool:
        v = self.video
        return bool(v.r_fps and v.fps and abs(v.r_fps - v.fps) / v.r_fps > 0.01)


def probe(path: str, timeout: float = 30) -> MediaInfo:
    """Read container and stream metadata. Raises ProbeError with a user-friendly message."""
    if not os.path.isfile(path):
        raise ProbeError("The file does not exist or cannot be accessed.")
    ff = ffmpeg_tools.find_ffmpeg()
    args = [ff.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", path]
    try:
        res = ffmpeg_tools.run(args, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ProbeError("Reading the file took too long. It may be on a slow or disconnected drive.")
    if res.returncode != 0:
        detail = (res.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        log.warning("ffprobe failed for %s: %s", path, res.stderr)
        raise ProbeError(f"This file could not be read as a video ({detail[0]}).")
    try:
        data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        raise ProbeError("FFprobe returned unreadable information for this file.")
    return parse_probe(path, data)


def parse_probe(path: str, data: dict) -> MediaInfo:
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = None
    audio: list[AudioStream] = []
    other = 0
    for s in streams:
        ctype = s.get("codec_type")
        disp = s.get("disposition") or {}
        tags = s.get("tags") or {}
        if ctype == "video" and not disp.get("attached_pic") and video is None:
            rotation = 0
            for sd in s.get("side_data_list") or []:
                if "rotation" in sd:
                    rotation = int(round(_float(sd.get("rotation"))))
            if not rotation and "rotate" in tags:
                rotation = -int(_float(tags.get("rotate")))
            video = VideoStream(
                index=int(s.get("index", 0)),
                codec=s.get("codec_name", "unknown"),
                width=int(s.get("width") or 0),
                height=int(s.get("height") or 0),
                fps=_rate(s.get("avg_frame_rate")) or _rate(s.get("r_frame_rate")),
                r_fps=_rate(s.get("r_frame_rate")),
                pix_fmt=s.get("pix_fmt", ""),
                profile=s.get("profile", ""),
                rotation=rotation,
                bit_rate=int(_float(s.get("bit_rate"))),
                frame_count=int(_float(s.get("nb_frames"))),
                has_b_frames=int(_float(s.get("has_b_frames"))),
            )
        elif ctype == "audio":
            audio.append(AudioStream(
                index=int(s.get("index", 0)),
                codec=s.get("codec_name", "unknown"),
                channels=int(s.get("channels") or 0),
                sample_rate=int(_float(s.get("sample_rate"))),
                language=tags.get("language", ""),
                title=tags.get("title", ""),
                bit_rate=int(_float(s.get("bit_rate"))),
            ))
        else:
            other += 1
    if video is None or not video.width or not video.height:
        raise ProbeError("This file does not contain a video stream SwiftCut can use.")
    duration = _float(fmt.get("duration"))
    if duration <= 0:
        for s in streams:
            if int(s.get("index", -1)) == video.index:
                duration = _float(s.get("duration"))
    if duration <= 0:
        raise ProbeError("The video's duration could not be determined. The file may be incomplete.")
    video_offset = 0.0
    for s in streams:
        if int(s.get("index", -1)) == video.index and s.get("start_time") not in (None, "N/A"):
            video_offset = max(0.0, _float(s.get("start_time")) - _float(fmt.get("start_time")))
    try:
        size = os.path.getsize(path)
    except OSError:
        size = int(_float(fmt.get("size")))
    return MediaInfo(
        path=path, size_bytes=size, format_name=fmt.get("format_name", ""),
        duration=duration, start_time=_float(fmt.get("start_time")),
        bit_rate=int(_float(fmt.get("bit_rate"))), video=video, audio=audio, other_streams=other,
        video_offset=video_offset,
    )


def scan_frames(info: MediaInfo, cancel: threading.Event | None = None) -> FrameGrid:
    """Read every video packet's timestamp and keyframe flag (no decoding, so it's quick).

    Returned times start at 0 on the first displayed frame; `grid.origin` records where that
    frame sits in the container timeline (what FFmpeg's -ss is measured against).
    """
    ff = ffmpeg_tools.find_ffmpeg()
    args = [ff.ffprobe, "-v", "error", "-select_streams", f"{info.video.index}",
            "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", info.path]
    # -select_streams takes a stream specifier; a bare number is the absolute index.
    proc = ffmpeg_tools.popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    times: list[float] = []
    keys: list[float] = []
    origin = info.start_time
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise InterruptedError
            pts, _, flags = line.strip().partition(",")
            if not pts or pts == "N/A":
                continue
            try:
                t = float(pts) - origin
            except ValueError:
                continue
            times.append(t)
            if "K" in flags:
                keys.append(t)
    finally:
        proc.wait()
    if not times:
        raise ProbeError("No video frames were found in this file.")
    times.sort()
    keys.sort()
    # Drop exact duplicate timestamps (can occur with broken muxers).
    dedup = [times[0]]
    for t in times[1:]:
        if t - dedup[-1] > 1e-6:
            dedup.append(t)
    # Packets with negative times (pre-roll / edit lists) are not displayed.
    shown = [t for t in dedup if t >= -1e-6] or dedup
    first = max(shown[0], 0.0)
    rel = [max(0.0, t - first) for t in shown]
    rel_keys = [k - first for k in keys if k >= first - 1e-6] or [0.0]
    return FrameGrid(times=rel, duration=max(info.duration - first, rel[-1]), exact=True,
                     keyframes=rel_keys, origin=first)
