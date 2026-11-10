"""Background jobs bridged to the GUI thread with Qt signals.

Each job runs on a plain Python thread; PySide queues signal emissions to receivers living in
the GUI thread, so slots always run on the GUI thread. Jobs carry a `token` so results from a
file that has since been replaced (user opened another video) can be ignored.
"""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from ..core import estimate, exporter, ffmpeg_tools, media_info, snapshot, stillness, thumbnails
from ..core.export_plan import ExportPlan

log = logging.getLogger(__name__)

# Jobs stay referenced until their thread ends. Otherwise a job dropped by the window (e.g. the
# user opened another file) could be garbage-collected while its thread is still emitting.
_LIVE: set[QObject] = set()


def _start(job: QObject, target, name: str) -> None:
    _LIVE.add(job)

    def run() -> None:
        try:
            target()
        finally:
            _LIVE.discard(job)
    threading.Thread(target=run, name=name, daemon=True).start()


class LoadJob(QObject):
    probed = Signal(int, object)        # token, MediaInfo
    scanned = Signal(int, object)       # token, FrameGrid
    failed = Signal(int, str)           # token, message

    def __init__(self, token: int, path: str):
        super().__init__()
        self.token, self.path = token, path
        self.cancel = threading.Event()

    def start(self) -> None:
        _start(self, self._run, "load")

    def _run(self) -> None:
        try:
            info = media_info.probe(self.path)
        except (media_info.ProbeError, ffmpeg_tools.FFmpegNotFound) as e:
            self.failed.emit(self.token, str(e))
            return
        except Exception as e:  # noqa: BLE001
            log.exception("probe crashed")
            self.failed.emit(self.token, f"Unexpected error while reading the file: {e}")
            return
        self.probed.emit(self.token, info)
        try:
            grid = media_info.scan_frames(info, self.cancel)
        except InterruptedError:
            return
        except Exception:  # noqa: BLE001 - fall back to the nominal frame grid
            log.exception("frame scan failed; using nominal frame rate")
            return
        if not self.cancel.is_set():
            self.scanned.emit(self.token, grid)


class ThumbJob(QObject):
    thumb = Signal(int, int, QImage)    # token, slot, image

    def __init__(self, token: int, info: media_info.MediaInfo, count: int, height: int):
        super().__init__()
        self.token, self.info, self.count, self.height = token, info, count, height
        self.cancel = threading.Event()

    def start(self) -> None:
        _start(self, self._run, "thumbs")

    def _run(self) -> None:
        def on_thumb(i: int, w: int, h: int, data: bytes) -> None:
            img = QImage(data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
            self.thumb.emit(self.token, i, img)
        try:
            thumbnails.generate(self.info, self.count, self.height, on_thumb, self.cancel)
        except Exception:  # noqa: BLE001 - thumbnails are cosmetic
            log.exception("thumbnail generation failed")


class ExportJob(QObject):
    progress = Signal(float)
    finished = Signal(object)           # ExportResult
    failed = Signal(str, str)           # message, technical detail
    cancelled = Signal()

    def __init__(self, plan: ExportPlan, rebuild=None):
        """`rebuild(scale)` returns a new plan with a smaller size budget (for size-limit retries)."""
        super().__init__()
        self.plan = plan
        self.rebuild = rebuild
        self.cancel_event = threading.Event()

    def start(self) -> None:
        _start(self, self._run, "export")

    def cancel(self) -> None:
        self.cancel_event.set()

    def _run(self) -> None:
        try:
            plan, scale = self.plan, 1.0
            for attempt in range(3):
                try:
                    result = exporter.run_export(plan, self.progress.emit, self.cancel_event)
                    break
                except exporter.SizeExceeded as e:
                    if self.rebuild is None or attempt == 2:
                        raise
                    # Overshot the limit: shrink the budget in proportion and encode again.
                    scale *= e.limit / e.actual * 0.97
                    log.info("size limit overshoot (%d > %d); retrying at scale %.3f", e.actual, e.limit, scale)
                    plan = self.rebuild(scale)
                    self.plan = plan
        except exporter.ExportCancelled:
            self.cancelled.emit()
        except exporter.ExportError as e:
            self.failed.emit(str(e), e.detail)
        except Exception as e:  # noqa: BLE001
            log.exception("export crashed")
            self.failed.emit("An unexpected error stopped the export.", repr(e))
        else:
            self.finished.emit(result)


class StillnessJob(QObject):
    done = Signal(int, object)          # token, stillness.Suggestion

    def __init__(self, token: int, info, grid):
        super().__init__()
        self.token, self.info, self.grid = token, info, grid
        self.cancel = threading.Event()

    def start(self) -> None:
        _start(self, self._run, "stillness")

    def _run(self) -> None:
        try:
            result = stillness.detect(self.info, self.grid, self.cancel)
        except Exception:  # noqa: BLE001 - suggestions are optional
            log.exception("still-footage detection failed")
            return
        if not self.cancel.is_set():
            self.done.emit(self.token, result)


class ZoomThumbJob(QObject):
    """Exact frames for the slots of a zoomed filmstrip."""

    thumb = Signal(int, int, QImage)    # token, frame index, image

    def __init__(self, token: int, info, items: list[tuple[int, float]], height: int, origin: float):
        super().__init__()
        self.token, self.info, self.items, self.height, self.origin = token, info, items, height, origin
        self.cancel = threading.Event()

    def start(self) -> None:
        _start(self, self._run, "zoom-thumbs")

    def _run(self) -> None:
        idx = [i for i, _t in self.items]

        def on_thumb(k: int, w: int, h: int, data: bytes) -> None:
            img = QImage(data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
            self.thumb.emit(self.token, idx[k], img)
        try:
            thumbnails.frames_at(self.info, [t for _i, t in self.items], self.height, on_thumb,
                                 self.cancel, self.origin)
        except Exception:  # noqa: BLE001 - cosmetic
            log.exception("zoom thumbnails failed")


class EstimateJob(QObject):
    done = Signal(object, float)        # settings key, video bytes per second

    def __init__(self, key, info, grid, opts):
        super().__init__()
        self.key, self.info, self.grid, self.opts = key, info, grid, opts
        self.cancel = threading.Event()

    def start(self) -> None:
        _start(self, self._run, "estimate")

    def _run(self) -> None:
        try:
            rate = estimate.measure_video_rate(self.info, self.grid, self.opts, self.cancel)
        except estimate.EstimateCancelled:
            return
        except Exception:  # noqa: BLE001 - an estimate is a nice-to-have
            log.exception("size estimate failed")
            return
        if not self.cancel.is_set():
            self.done.emit(self.key, rate)


class SnapshotJob(QObject):
    done = Signal(str)                  # saved path
    failed = Signal(str)

    def __init__(self, info, grid, index: int, path: str):
        super().__init__()
        self.info, self.grid, self.index, self.path = info, grid, index, path

    def start(self) -> None:
        _start(self, self._run, "snapshot")

    def _run(self) -> None:
        try:
            self.done.emit(snapshot.save_frame(self.info, self.grid, self.index, self.path))
        except Exception as e:  # noqa: BLE001
            log.exception("snapshot failed")
            self.failed.emit(str(e))


class EncoderProbe(QObject):
    done = Signal(dict)                 # {"h264_nvenc": bool, "hevc_nvenc": bool}

    def start(self) -> None:
        _start(self, self._run, "encoders")

    def _run(self) -> None:
        self.done.emit({name: ffmpeg_tools.encoder_works(name) for name in ("h264_nvenc", "hevc_nvenc")})
