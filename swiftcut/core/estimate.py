"""Rough output-size estimate: encode a few short samples with the real settings and scale up.

Compressed size depends on the content (a static screen recording is tiny, fast motion is
large), so formulas alone can't predict it. Instead we encode three short slices spread across
the video with the exact export settings, measure their size, and scale by the selection length.
Audio is added from its known bitrate. The result is a ballpark figure, typically within ~25%.
"""
from __future__ import annotations

import dataclasses
import subprocess
import threading

from . import ffmpeg_tools
from .export_plan import (ExportOptions, TrimMethod, _audio_encoder_args, build_plan, resolve_container,
                          resolve_video_codec, selected_audio)
from .media_info import MediaInfo
from .timecode import FrameGrid

SAMPLE_SECONDS = 2.0
SAMPLES = 3
CONTAINER_OVERHEAD = 1.01


class EstimateCancelled(Exception):
    pass


def settings_key(info: MediaInfo, opts: ExportOptions) -> tuple:
    """Everything that changes the video bitrate (not duration, audio or metadata)."""
    container = resolve_container(info, opts)
    return (info.path, container, resolve_video_codec(container, opts.video_codec),
            opts.quality.value, opts.encoder.value)


def sample_windows(total: float) -> list[tuple[float, float]]:
    if total <= SAMPLE_SECONDS * SAMPLES:
        return [(0.0, total)]
    starts = [total * f - SAMPLE_SECONDS / 2 for f in (0.2, 0.5, 0.8)][:SAMPLES]
    return [(max(0.0, s), min(total, s + SAMPLE_SECONDS)) for s in starts]


def measure_video_rate(info: MediaInfo, grid: FrameGrid, opts: ExportOptions,
                       cancel: threading.Event | None = None) -> float:
    """Encoded video bytes per second for these settings, measured on short samples."""
    ff = ffmpeg_tools.find_ffmpeg().ffmpeg
    container = resolve_container(info, opts)
    sample_opts = dataclasses.replace(
        opts, method=TrimMethod.PRECISE, container="mkv", mute=True, size_limit_mb=None,
        strip_metadata=False, video_codec=resolve_video_codec(container, opts.video_codec))
    total_bytes, total_secs = 0, 0.0
    for start, end in sample_windows(grid.end_time):
        if cancel is not None and cancel.is_set():
            raise EstimateCancelled
        g_start = grid.times[grid.index_at(start)]
        g_end = grid.frame_end(grid.index_at(max(start, end - 1e-3)))
        if g_end - g_start < 0.05:
            continue
        plan = build_plan(info, grid, g_start, g_end, sample_opts, "pipe:1", ff)
        args = list(plan.args)
        i = args.index("-progress")               # stdout carries the video here, not progress
        del args[i:i + 3]
        res = subprocess.run(args, capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                             creationflags=ffmpeg_tools.CREATE_NO_WINDOW)
        if res.returncode != 0:
            continue
        total_bytes += len(res.stdout)
        total_secs += g_end - g_start
    if total_secs <= 0:
        raise RuntimeError("no sample could be encoded")
    return total_bytes / total_secs


def audio_rate(info: MediaInfo, opts: ExportOptions) -> float:
    """Audio bytes per second from the bitrates the export will use."""
    tracks = selected_audio(info, opts)
    if not tracks:
        return 0.0
    args = _audio_encoder_args(resolve_container(info, opts), info, tracks)
    kbps = 0
    for a, b in zip(args, args[1:]):
        if a.startswith("-b:a"):
            kbps += int(b.rstrip("k"))
    if resolve_container(info, opts) == "webm":
        kbps *= len(tracks)                       # one -b:a applies to every Opus track
    return kbps * 1000 / 8


def estimate_bytes(video_rate: float, info: MediaInfo, opts: ExportOptions, duration: float) -> int:
    return int((video_rate + audio_rate(info, opts)) * duration * CONTAINER_OVERHEAD)
