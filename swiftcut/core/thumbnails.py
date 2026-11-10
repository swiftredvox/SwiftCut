"""Filmstrip thumbnail extraction (raw RGB frames from FFmpeg), cancellable and progressive."""
from __future__ import annotations

import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import ffmpeg_tools
from .media_info import MediaInfo

log = logging.getLogger(__name__)

# (slot index, width, height, rgb24 bytes)
ThumbFn = Callable[[int, int, int, bytes], None]

SINGLE_PASS_MAX_SECONDS = 90.0


def thumb_size(info: MediaInfo, height: int) -> tuple[int, int]:
    w, h = info.video.display_size
    width = max(2, int(round(height * w / h / 2)) * 2)
    return width, max(2, height // 2 * 2)


def slot_time(i: int, count: int, duration: float) -> float:
    return (i + 0.5) * duration / count


def spread_order(count: int) -> list[int]:
    """Coarse-to-fine order (0, mid, quarters, ...) so the strip fills evenly while loading."""
    order, seen = [], set()
    step = count
    while step >= 1:
        for i in range(0, count, step):
            j = min(count - 1, i + step // 2) if step > 1 else i
            if j not in seen:
                seen.add(j)
                order.append(j)
        step //= 2
    order += [i for i in range(count) if i not in seen]
    return order


def generate(info: MediaInfo, count: int, height: int, on_thumb: ThumbFn,
             cancel: threading.Event) -> None:
    ff = ffmpeg_tools.find_ffmpeg()
    w, h = thumb_size(info, height)
    scale = f"scale={w}:{h}:flags=bilinear"
    if info.duration <= SINGLE_PASS_MAX_SECONDS:
        _single_pass(ff.ffmpeg, info, count, w, h, scale, on_thumb, cancel)
    else:
        _seek_each(ff.ffmpeg, info, count, w, h, scale, on_thumb, cancel)


def _single_pass(ffmpeg: str, info: MediaInfo, count: int, w: int, h: int, scale: str,
                 on_thumb: ThumbFn, cancel: threading.Event) -> None:
    """Decode once and sample `count` evenly spaced frames - cheapest for short clips."""
    rate = count / info.duration
    # Sample the frame at the middle of each slot. Shifting the timeline back by half a slot and
    # taking the frame at-or-after each fps tick was verified frame-exact (other variants drifted).
    vf = f"setpts=PTS-{0.5 / rate:.6f}/TB,fps=fps={rate:.6f}:start_time=0:round=up,{scale}"
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "0",
            "-i", info.path, "-map", f"0:{info.video.index}", "-an", "-sn", "-vf", vf,
            "-frames:v", str(count), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    proc = ffmpeg_tools.popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              stdin=subprocess.DEVNULL)
    frame_bytes = w * h * 3
    i = 0
    try:
        assert proc.stdout is not None
        while i < count:
            if cancel.is_set():
                break
            buf = proc.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            on_thumb(i, w, h, buf)
            i += 1
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    if i < count and not cancel.is_set():
        log.debug("single-pass produced %d/%d thumbnails; filling the rest by seeking", i, count)
        _seek_each(ffmpeg, info, count, w, h, scale, on_thumb, cancel, only=range(i, count))


def _seek_each(ffmpeg: str, info: MediaInfo, count: int, w: int, h: int, scale: str,
               on_thumb: ThumbFn, cancel: threading.Event, only=None) -> None:
    """One fast keyframe-seek per thumbnail, a few in parallel - scales to long videos."""
    wanted = set(only) if only is not None else None
    order = [i for i in spread_order(count) if wanted is None or i in wanted]

    def one(i: int) -> None:
        if cancel.is_set():
            return
        t = slot_time(i, count, info.duration)
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-ss", f"{t:.3f}", "-i", info.path, "-map", f"0:{info.video.index}",
                "-an", "-sn", "-frames:v", "1", "-vf", scale,
                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        try:
            res = subprocess.run(args, capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
                                 creationflags=ffmpeg_tools.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            return
        if len(res.stdout) >= w * h * 3 and not cancel.is_set():
            on_thumb(i, w, h, res.stdout[: w * h * 3])

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="thumb") as pool:
        list(pool.map(one, order))


def frames_at(info: MediaInfo, times: list[float], height: int, on_thumb: ThumbFn,
              cancel: threading.Event, origin: float = 0.0) -> None:
    """Decode the exact frame at each time (video timeline) in parallel; on_thumb(i, ...) per item.

    Used for zoomed filmstrips, where the whole-video thumbnails are too far apart.
    """
    ff = ffmpeg_tools.find_ffmpeg().ffmpeg
    w, h = thumb_size(info, height)
    scale = f"scale={w}:{h}:flags=bilinear"

    def one(item: tuple[int, float]) -> None:
        i, t = item
        if cancel.is_set():
            return
        seek = max(0.0, t + origin - 0.001)
        args = [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{seek:.6f}", "-i", info.path,
                "-map", f"0:{info.video.index}", "-an", "-sn", "-frames:v", "1", "-vf", scale,
                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        try:
            res = subprocess.run(args, capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
                                 creationflags=ffmpeg_tools.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            return
        if len(res.stdout) >= w * h * 3 and not cancel.is_set():
            on_thumb(i, w, h, res.stdout[: w * h * 3])

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="zoomthumb") as pool:
        list(pool.map(one, list(enumerate(times))))
