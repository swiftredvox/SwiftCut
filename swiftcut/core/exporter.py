"""Run an ExportPlan safely: temp file -> validate -> atomic replace. Cancellable, with progress.

The destination is never touched until a complete, validated output exists. When the user
overwrites the source itself, the caller must release any handles on it first (the preview
player), because Windows refuses to replace a file another handle has open.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import ffmpeg_tools, media_info
from .export_plan import ExportPlan
from .paths import temp_path_for

log = logging.getLogger(__name__)


class ExportCancelled(Exception):
    pass


class ExportError(RuntimeError):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


@dataclass
class ExportResult:
    output_path: str
    duration: float
    size_bytes: int
    elapsed: float


ProgressFn = Callable[[float], None]   # fraction 0..1


def _friendly_error(stderr: str) -> str:
    s = stderr.lower()
    if "no space left" in s or "disk full" in s:
        return "The disk is full. Free some space or save to another drive."
    if "permission denied" in s:
        return "Permission was denied while writing the file."
    if "invalid data found" in s:
        return "The source file contains data FFmpeg couldn't decode. It may be damaged."
    if "cannot load nvcuda" in s or "nvenc" in s and ("error" in s or "failed" in s):
        return "The NVIDIA hardware encoder failed. Try exporting with the CPU encoder."
    if "could not find tag for codec" in s or "not supported" in s:
        return "The chosen output format doesn't support these streams. Try MKV or Precise Trim."
    return "FFmpeg could not export the video."


def _validate(path: str, expected: float, expect_audio: bool) -> media_info.MediaInfo:
    try:
        info = media_info.probe(path)
    except media_info.ProbeError as e:
        raise ExportError("The exported file failed validation and was discarded.", str(e))
    if expected > 0.2 and info.duration < min(expected * 0.5, expected - 0.5):
        raise ExportError("The exported file is much shorter than expected and was discarded.",
                          f"expected {expected:.3f}s, got {info.duration:.3f}s")
    if expect_audio and not info.has_audio:
        raise ExportError("The exported file is missing its audio and was discarded.")
    return info


def _validate_animation(path: str, fmt: str, expected: float) -> None:
    """GIF / animated WebP: check the file header, and for GIF also probe it."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError as e:
        raise ExportError("The exported animation couldn't be read back and was discarded.", str(e))
    ok = head.startswith((b"GIF87a", b"GIF89a")) if fmt == "gif" else (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    if not ok or os.path.getsize(path) < 64:
        raise ExportError("The exported animation is not a valid file and was discarded.")
    if fmt == "gif":
        _validate(path, expected, expect_audio=False)


def _replace(src: str, dest: str, attempts: int = 20) -> None:
    """os.replace with retries - antivirus/indexers briefly lock new files on Windows."""
    last: OSError | None = None
    for _ in range(attempts):
        try:
            os.replace(src, dest)
            return
        except PermissionError as e:
            last = e
            time.sleep(0.15)
    raise ExportError(
        "The destination file is in use by another program, so it couldn't be replaced. "
        "Close any app that has it open and try again.", str(last))


class SizeExceeded(ExportError):
    """A size-limited export came out too big (the caller may retry with a smaller budget)."""

    def __init__(self, actual: int, limit: int):
        super().__init__(f"The file came out at {actual / 1e6:.2f} MB, over the {limit / 1e6:g} MB limit.")
        self.actual, self.limit = actual, limit


def _run_ffmpeg(args: list[str], duration: float, progress: ProgressFn | None,
                cancel: threading.Event | None, cwd: str | None) -> None:
    """Run one FFmpeg command with -progress parsing. Raises ExportCancelled / ExportError."""
    stderr_tail: deque[str] = deque(maxlen=40)
    log.info("ffmpeg: %s", subprocess.list2cmdline(args))
    try:
        proc = ffmpeg_tools.popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
                                  stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        raise ExportError("FFmpeg could not be started.", str(e))

    def drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_tail.append(line.rstrip())
    err_thread = threading.Thread(target=drain_stderr, daemon=True)
    err_thread.start()
    cancelled = False
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                cancelled = True
                proc.kill()
                break
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and progress and duration > 0:
                try:
                    progress(min(1.0, max(0.0, int(value) / 1e6 / duration)))
                except ValueError:
                    pass
        # FFmpeg may still be finishing (moov atom) after its last progress line.
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                cancelled = True
                proc.kill()
                break
            time.sleep(0.05)
        proc.wait()
        err_thread.join(timeout=2)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if cancelled:
        raise ExportCancelled()
    if proc.returncode != 0:
        stderr = "\n".join(stderr_tail)
        log.error("ffmpeg exited %s:\n%s", proc.returncode, stderr)
        raise ExportError(_friendly_error(stderr), stderr)


def run_export(plan: ExportPlan, progress: ProgressFn | None = None,
               cancel: threading.Event | None = None) -> ExportResult:
    dest = plan.output_path
    tmp = temp_path_for(dest)
    commands = [*plan.pre_passes, plan.args[:-1] + [tmp]]
    t0 = time.perf_counter()
    log.info("export start -> %s (%d pass%s)", dest, len(commands), "es" if len(commands) > 1 else "")
    try:
        for n, args in enumerate(commands):
            # Spread progress evenly across passes (an analysis pass costs about the same).
            sub = (lambda f, n=n: progress((n + f) / len(commands))) if progress else None
            _run_ffmpeg(args, plan.duration, sub, cancel, plan.work_dir)
        if progress:
            progress(1.0)
        if plan.kind == "animation":
            _validate_animation(tmp, plan.container, plan.duration)
            info = None
        else:
            info = _validate(tmp, plan.duration, expect_audio=bool(plan.audio_streams))
        if plan.size_limit_bytes:
            actual = os.path.getsize(tmp)
            if actual > plan.size_limit_bytes:
                raise SizeExceeded(actual, plan.size_limit_bytes)
        _replace(tmp, dest)
    except BaseException:
        _remove_quietly(tmp)
        raise
    finally:
        if plan.cleanup_prefix:
            folder, prefix = os.path.split(plan.cleanup_prefix)
            for p in Path(folder).glob(prefix + "*"):
                _remove_quietly(str(p))
    elapsed = time.perf_counter() - t0
    out_duration = info.duration if info is not None else plan.duration
    log.info("export done in %.2fs: %s (%.3fs)", elapsed, dest, out_duration)
    return ExportResult(dest, out_duration, os.path.getsize(dest), elapsed)


def _remove_quietly(path: str) -> None:
    for _ in range(10):
        try:
            if os.path.exists(path):
                os.remove(path)
            return
        except PermissionError:
            time.sleep(0.1)
        except OSError:
            return
    log.warning("could not remove temporary file %s", path)


def cleanup_stale_temp_files(folder: str) -> int:
    """Remove leftovers from a crashed export (older than an hour) in `folder`."""
    removed = 0
    try:
        for p in Path(folder).glob("~*.swiftcut-*.partial.*"):
            if time.time() - p.stat().st_mtime > 3600:
                p.unlink(missing_ok=True)
                removed += 1
    except OSError:
        pass
    return removed
