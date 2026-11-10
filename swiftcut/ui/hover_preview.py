"""Timeline hover preview (F8): a floating card showing the frame under the mouse.

The card appears instantly with the nearest filmstrip thumbnail, then sharpens to the exact frame
once a background worker has decoded it. The worker only ever decodes the *latest* request, so
sweeping the mouse quickly never builds up a queue.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from collections import OrderedDict

from PySide6.QtCore import QObject, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..core import ffmpeg_tools
from ..core.export_plan import has_reliable_seeking
from ..core.media_info import MediaInfo
from ..core.timecode import FrameGrid, format_time
from . import theme

log = logging.getLogger(__name__)

CARD_WIDTH = 224          # logical px
DECODE_WIDTH = 448        # decoded frame width (2x for sharpness on high-DPI screens)


class HoverFrameWorker(QObject):
    """Decodes single frames on demand, newest request wins, with a small LRU cache."""

    frameReady = Signal(int, int, QImage)       # token, frame index, image

    def __init__(self) -> None:
        super().__init__()
        self._cond = threading.Condition()
        self._request: tuple[int, MediaInfo, FrameGrid, int] | None = None
        self._cache: OrderedDict[tuple[int, int], QImage] = OrderedDict()
        self._stop = False
        self._thread = threading.Thread(target=self._loop, name="hover-frames", daemon=True)
        self._thread.start()

    def cached(self, token: int, index: int) -> QImage | None:
        img = self._cache.get((token, index))
        if img is not None:
            self._cache.move_to_end((token, index))
        return img

    def request(self, token: int, info: MediaInfo, grid: FrameGrid, index: int) -> None:
        with self._cond:
            self._request = (token, info, grid, index)
            self._cond.notify()

    def clear(self) -> None:
        with self._cond:
            self._request = None
        self._cache.clear()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()

    def _loop(self) -> None:
        while True:
            with self._cond:
                while self._request is None and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                token, info, grid, index = self._request
                self._request = None
            if (token, index) in self._cache:
                continue
            img = self._decode(info, grid, index)
            if img is not None:
                self._cache[(token, index)] = img
                while len(self._cache) > 150:
                    self._cache.popitem(last=False)
                try:
                    self.frameReady.emit(token, index, img)
                except RuntimeError:       # owner gone (window closed)
                    return

    @staticmethod
    def _decode(info: MediaInfo, grid: FrameGrid, index: int) -> QImage | None:
        try:
            ff = ffmpeg_tools.find_ffmpeg().ffmpeg
        except ffmpeg_tools.FFmpegNotFound:
            return None
        w, h = info.video.display_size
        out_w = min(DECODE_WIDTH, w) // 2 * 2
        out_h = max(2, int(round(out_w * h / w / 2)) * 2)
        eps = min(0.001, grid.min_interval / 2)
        seek = max(0.0, grid.times[index] + grid.origin - eps)
        args = [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{seek:.6f}", "-i", info.path,
                "-map", f"0:{info.video.index}", "-an", "-sn", "-frames:v", "1",
                "-vf", f"scale={out_w}:{out_h}:flags=bilinear", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        try:
            res = subprocess.run(args, capture_output=True, timeout=15, stdin=subprocess.DEVNULL,
                                 creationflags=ffmpeg_tools.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            return None
        need = out_w * out_h * 3
        if len(res.stdout) < need:
            return None
        return QImage(res.stdout[:need], out_w, out_h, out_w * 3, QImage.Format.Format_RGB888).copy()


class HoverCard(QWidget):
    """The floating preview card (frame + time + frame number).

    It's a frameless tooltip-style window rather than a child widget: the video surface is drawn
    by the GPU on top of sibling widgets, which hid a child card where the two overlapped.
    """

    def __init__(self, anchor: QWidget):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.anchor = anchor                       # coordinates passed to place() are in its space
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._img: QImage | None = None
        self._label = ""
        self._exact = False
        self.hide()

    def set_content(self, img: QImage | None, label: str, aspect: float, exact: bool) -> None:
        self._img, self._label, self._exact = img, label, exact
        img_h = int(round(CARD_WIDTH / max(aspect, 0.2)))
        img_h = max(60, min(img_h, 200))
        self.resize(QSize(CARD_WIDTH + 8, img_h + 8 + 22))
        self.update()

    def place(self, center_x: int, bottom_y: int) -> None:
        a = self.anchor
        x = max(6, min(center_x - self.width() // 2, a.width() - self.width() - 6))
        self.move(a.mapToGlobal(QPoint(x, bottom_y - self.height())))
        if not self.isVisible():
            self.show()
        self.raise_()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        p.setBrush(QColor(theme.SURFACE_2))
        p.drawRoundedRect(r, 9, 9)
        img_rect = QRectF(4, 4, self.width() - 8, self.height() - 8 - 22)
        clip = QPainterPath()
        clip.addRoundedRect(img_rect, 6, 6)
        p.save()
        p.setClipPath(clip)
        p.fillRect(img_rect, QColor("#000"))
        if self._img is not None and not self._img.isNull():
            # Fit inside the frame area, keeping aspect ratio.
            iw, ih = self._img.width(), self._img.height()
            scale = min(img_rect.width() / iw, img_rect.height() / ih)
            dw, dh = iw * scale, ih * scale
            target = QRectF(img_rect.center().x() - dw / 2, img_rect.center().y() - dh / 2, dw, dh)
            p.drawImage(target, self._img)
        p.restore()
        p.setPen(QColor(theme.TEXT))
        f = p.font()
        f.setPointSizeF(8.5)
        p.setFont(f)
        p.drawText(QRectF(8, self.height() - 24, self.width() - 16, 22),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter, self._label)


class HoverPreview(QObject):
    """Glue between the timeline, the card and the decoder."""

    def __init__(self, card_parent: QWidget):
        super().__init__(card_parent)
        self.card = HoverCard(card_parent)
        self.worker = HoverFrameWorker()
        self.worker.frameReady.connect(self._on_frame)
        self.enabled = True
        self._token = 0
        self._info: MediaInfo | None = None
        self._grid: FrameGrid | None = None
        self._thumb_source = None          # callable(t) -> QImage | None
        self._index: int | None = None
        self._pos = (0, 0)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(70)
        self._debounce.timeout.connect(self._request_exact)

    def set_media(self, token: int, info: MediaInfo | None, grid: FrameGrid | None, thumb_source) -> None:
        self._token, self._info, self._grid, self._thumb_source = token, info, grid, thumb_source
        self.worker.clear()
        self.hide()

    def hide(self) -> None:
        self._index = None
        self._debounce.stop()
        self.card.hide()

    def hover(self, t: float, center_x: int, bottom_y: int) -> None:
        if not self.enabled or self._info is None or self._grid is None or t < 0:
            self.hide()
            return
        g = self._grid
        idx = g.index_at(t)
        self._pos = (center_x, bottom_y)
        if idx == self._index and self.card.isVisible():
            self.card.place(center_x, bottom_y)
            return
        self._index = idx
        w, h = self._info.video.display_size
        label = f"{format_time(g.times[idx])}   ·   frame {idx + 1}"
        exact = self.worker.cached(self._token, idx)
        img = exact if exact is not None else (self._thumb_source(g.times[idx]) if self._thumb_source else None)
        self.card.set_content(img, label, w / h if h else 16 / 9, exact is not None)
        self.card.place(center_x, bottom_y)
        if exact is None and g.exact and has_reliable_seeking(self._info):
            self._debounce.start()

    def _request_exact(self) -> None:
        if self._index is not None and self._info is not None and self._grid is not None:
            self.worker.request(self._token, self._info, self._grid, self._index)

    def _on_frame(self, token: int, index: int, img: QImage) -> None:
        if token == self._token and index == self._index and self.card.isVisible() and self._grid is not None:
            w, h = self._info.video.display_size
            label = f"{format_time(self._grid.times[index])}   ·   frame {index + 1}"
            self.card.set_content(img, label, w / h if h else 16 / 9, True)
            self.card.place(*self._pos)

    def shutdown(self) -> None:
        self.worker.stop()
