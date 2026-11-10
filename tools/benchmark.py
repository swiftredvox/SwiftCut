"""Measure load / frame-scan / thumbnail / export times on generated screen-recording-like clips.

Usage:  .venv/Scripts/python tools/benchmark.py [--keep DIR]
Clips use FFmpeg's testsrc2 (moving detail, like a screen capture) with ShareX-style encoding.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swiftcut.core import exporter, ffmpeg_tools, media_info, thumbnails  # noqa: E402
from swiftcut.core.export_plan import Encoder, ExportOptions, TrimMethod, build_plan  # noqa: E402

CASES = [  # name, size, fps, seconds
    ("720p30 30s", "1280x720", 30, 30),
    ("1772x996 30fps 60s (ShareX-like)", "1772x996", 30, 60),
    ("1080p60 60s", "1920x1080", 60, 60),
    ("1440p60 30s", "2560x1440", 60, 30),
    ("4K30 20s", "3840x2160", 30, 20),
    ("720p30 10min (long)", "1280x720", 30, 600),
]


def make(path: Path, size: str, fps: int, secs: int) -> None:
    ff = ffmpeg_tools.find_ffmpeg().ffmpeg
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=s={size}:r={fps}:d={secs}",
                    "-f", "lavfi", "-i", f"sine=duration={secs}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-profile:v", "baseline", "-crf", "23",
                    "-c:a", "aac", "-shortest", str(path)], check=True)


def timed(fn):
    t0 = time.perf_counter()
    r = fn()
    return r, time.perf_counter() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", help="directory to cache generated clips")
    a = ap.parse_args()
    work = Path(a.keep) if a.keep else Path(tempfile.mkdtemp(prefix="swiftcut-bench-"))
    work.mkdir(parents=True, exist_ok=True)
    nvenc = ffmpeg_tools.encoder_works("h264_nvenc")
    print(f"{'clip':36} {'probe':>6} {'scan':>6} {'thumbs':>7} {'precise':>8} {'gpu':>6} {'fast':>6}  (seconds; exports trim the middle 50%)")
    for name, size, fps, secs in CASES:
        src = work / f"{size}_{fps}_{secs}.mp4"
        if not src.exists():
            make(src, size, fps, secs)
        info, t_probe = timed(lambda: media_info.probe(str(src)))
        grid, t_scan = timed(lambda: media_info.scan_frames(info))
        _, t_thumb = timed(lambda: thumbnails.generate(info, 40, 87, lambda *x: None, threading.Event()))
        s, e = grid.end_time * 0.25, grid.end_time * 0.75
        res = []
        for opts in (ExportOptions(), ExportOptions(encoder=Encoder.NVENC) if nvenc else None,
                     ExportOptions(method=TrimMethod.FAST)):
            if opts is None:
                res.append("   n/a")
                continue
            out = work / "out.mp4"
            p = build_plan(info, grid, s, e, opts, str(out), ffmpeg_tools.find_ffmpeg().ffmpeg)
            _, t = timed(lambda: exporter.run_export(p))
            res.append(f"{t:6.2f}")
        print(f"{name:36} {t_probe:6.2f} {t_scan:6.2f} {t_thumb:7.2f} {res[0]:>8} {res[1]:>6} {res[2]:>6}")
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
