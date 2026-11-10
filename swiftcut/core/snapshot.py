"""Save a single video frame as a full-resolution PNG (frame-exact, orientation applied)."""
from __future__ import annotations

from pathlib import Path

from . import ffmpeg_tools
from .export_plan import has_reliable_seeking
from .media_info import MediaInfo
from .timecode import FrameGrid


class SnapshotError(RuntimeError):
    pass


def default_name(info: MediaInfo, index: int) -> str:
    return f"{Path(info.path).stem} - frame {index + 1}.png"


def build_args(info: MediaInfo, grid: FrameGrid, index: int, out_path: str, ffmpeg: str = "ffmpeg") -> list[str]:
    index = max(0, min(grid.last_index, index))
    eps = min(0.001, grid.min_interval / 2)
    seek = max(0.0, grid.times[index] + grid.origin - eps)
    args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if has_reliable_seeking(info):
        args += ["-ss", f"{seek:.6f}", "-i", info.path]
    else:
        args += ["-i", info.path, "-ss", f"{seek:.6f}"]
    args += ["-map", f"0:{info.video.index}", "-frames:v", "1", "-an", "-sn", "-dn",
             "-map_metadata", "-1", "-c:v", "png", "-f", "image2", "-update", "1", out_path]
    return args


def save_frame(info: MediaInfo, grid: FrameGrid, index: int, out_path: str) -> str:
    ff = ffmpeg_tools.find_ffmpeg().ffmpeg
    res = ffmpeg_tools.run(build_args(info, grid, index, out_path, ff), timeout=60)
    if res.returncode != 0 or not Path(out_path).is_file() or Path(out_path).stat().st_size == 0:
        raise SnapshotError("The frame couldn't be saved. " + (res.stderr.strip().splitlines() or [""])[-1])
    return out_path
