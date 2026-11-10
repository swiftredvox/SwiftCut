"""Animated GIF / WebP export (F9): frame-exact trims, sized and timed for sharing.

GIFs use a palette made from the clip itself (palettegen + paletteuse with light dithering),
which looks far better than FFmpeg's default 256-colour web palette. Animated WebP uses
libwebp's lossy mode, usually 3-5x smaller than an equivalent GIF.
"""
from __future__ import annotations

import dataclasses
import subprocess
import threading
from dataclasses import dataclass

from . import ffmpeg_tools
from .export_plan import ExportPlan, PlanError, _seek_args
from .media_info import MediaInfo
from .timecode import FrameGrid

FORMATS = {"gif": ("GIF", "gif"), "webp": ("Animated WebP", "webp")}
WIDTHS = [1280, 960, 720, 480, 320]
FRAME_RATES = [30, 24, 20, 15, 10]
WEBP_QUALITY = {"high": 85, "balanced": 70, "small": 50}
GIF_COLOURS = {"high": 256, "balanced": 192, "small": 96}


@dataclass
class AnimationOptions:
    format: str = "gif"            # "gif" | "webp"
    width: int | None = 960        # None = original width (never upscaled)
    fps: float | None = 15         # None = source frame rate
    loop: bool = True
    quality: str = "balanced"      # "high" | "balanced" | "small"


def output_size(info: MediaInfo, opts: AnimationOptions) -> tuple[int, int]:
    w, h = info.video.display_size
    if opts.width and opts.width < w:
        return opts.width, max(2, int(round(h * opts.width / w)))
    return w, h


def effective_fps(info: MediaInfo, opts: AnimationOptions) -> float:
    src = info.video.fps or 30
    return min(src, opts.fps) if opts.fps else src


def _filters(info: MediaInfo, opts: AnimationOptions) -> str:
    parts = []
    if opts.fps and opts.fps < (info.video.fps or 1e9) - 0.01:
        parts.append(f"fps={opts.fps:g}")
    w, _h = info.video.display_size
    if opts.width and opts.width < w:
        parts.append(f"scale={opts.width}:-1:flags=lanczos")
    return ",".join(parts)


def build_animation_plan(info: MediaInfo, grid: FrameGrid, start: float, end: float, opts: AnimationOptions,
                         output_path: str, ffmpeg: str = "ffmpeg") -> ExportPlan:
    if opts.format not in FORMATS:
        raise PlanError(f"Unsupported animation format: {opts.format}")
    if not (0 <= start < end) or end - start < 1e-3:
        raise PlanError("The start of the selection must be before its end.")
    duration = end - start
    eps = min(0.001, grid.min_interval / 2)
    seek = max(0.0, start + grid.origin - eps)
    args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    args += _seek_args(info, seek, duration)
    base = _filters(info, opts)
    v = f"[0:{info.video.index}]"
    if opts.format == "gif":
        colours = GIF_COLOURS.get(opts.quality, 192)
        chain = f"{v}{base + ',' if base else ''}split[a][b];" \
                f"[a]palettegen=stats_mode=diff:max_colors={colours}[p];" \
                f"[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle[out]"
        args += ["-filter_complex", chain, "-map", "[out]", "-loop", "0" if opts.loop else "-1",
                 "-f", "gif"]
    else:
        if base:
            args += ["-filter_complex", f"{v}{base}[out]", "-map", "[out]"]
        else:
            args += ["-map", f"0:{info.video.index}"]
        args += ["-c:v", "libwebp_anim", "-lossless", "0", "-quality", str(WEBP_QUALITY.get(opts.quality, 70)),
                 "-compression_level", "4", "-loop", "0" if opts.loop else "1", "-f", "webp"]
    args += ["-an", "-sn", "-dn", "-map_metadata", "-1", "-fps_mode", "passthrough",
             "-progress", "pipe:1", "-nostats", output_path]      # output path last: see run_export
    notes = []
    if duration > 20:
        notes.append(f"A {duration:.0f} s {FORMATS[opts.format][0]} can get large; consider a shorter clip.")
    return ExportPlan(args=args, output_path=output_path, duration=duration, actual_start=start,
                      container=opts.format, video_codec=opts.format, audio_streams=[], notes=notes,
                      kind="animation")


def estimate_animation_bytes(info: MediaInfo, grid: FrameGrid, opts: AnimationOptions, duration: float,
                             cancel: threading.Event | None = None) -> int:
    """Encode a short sample from the middle of the video and scale it to `duration`."""
    ff = ffmpeg_tools.find_ffmpeg().ffmpeg
    total = grid.end_time
    sample = min(2.0, total)
    mid = max(0.0, total / 2 - sample / 2)
    s = grid.times[grid.index_at(mid)]
    e = grid.frame_end(grid.index_at(min(total, s + sample) - 1e-3))
    plan = build_animation_plan(info, grid, s, e, dataclasses.replace(opts, loop=True), "pipe:1", ff)
    args = list(plan.args)
    i = args.index("-progress")
    del args[i:i + 3]
    if cancel is not None and cancel.is_set():
        return 0
    res = subprocess.run(args, capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                         creationflags=ffmpeg_tools.CREATE_NO_WINDOW)
    if res.returncode != 0 or not res.stdout:
        raise RuntimeError("sample encode failed")
    return int(len(res.stdout) / max(e - s, 0.05) * duration)
