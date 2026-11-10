"""Filmstrip timeline with draggable trim handles, a playhead, a time ruler and zoom.

Zoom works on a visible window [view_start, view_start + view_len] of the video. At "fit" the
window is the whole video. While zoomed, a thin overview bar under the ruler shows where the
window sits (drag it to move around), the view follows the playhead, and handles auto-scroll
when dragged to an edge.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.timecode import format_time
from . import theme
from .trim_model import TrimModel

HANDLE_W = 14
TRACK_TOP = 14
TRACK_H = 58
RULER_H = 20
OVERVIEW_H = 12            # reserved under the ruler; the bar is only drawn while zoomed
MAX_PX_PER_FRAME = 28      # deepest zoom: about this many pixels per frame
ZOOM_STEP = 1.6


class Timeline(QWidget):
    seekRequested = Signal(float)        # user scrubbed to a time
    handleDragged = Signal(str, float)   # "start"/"end", time to preview
    handleReleased = Signal(str)
    scrubStarted = Signal()
    scrubFinished = Signal()
    wheelStep = Signal(int)              # +/- frames
    hoverChanged = Signal(float, float)  # time under the mouse (-1 = none), x in widget px
    viewChanged = Signal(float, float)   # visible window start, length (seconds)

    def __init__(self, model: TrimModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.model = model
        model.changed.connect(self._model_changed)
        self.playhead = 0.0
        self.thumbs: dict[int, QImage] = {}       # whole-video filmstrip (slot -> image)
        self.thumb_count = 0
        self.thumb_aspect = 16 / 9
        self.zoom_thumbs: dict[int, QImage] = {}  # exact frames for zoomed views (frame index -> image)
        self.suggestions: list[float] = []        # suggested trim points (skip still footage)
        self.view_start = 0.0
        self.view_len = 0.0                       # 0 = fit (whole video)
        self._drag: str | None = None             # "start" | "end" | "scrub" | "overview"
        self._drag_origin_x = 0.0
        self._drag_origin_t = 0.0
        self._last_drag_x = 0.0
        self._hover_x: float | None = None
        self._hover_part: str | None = None
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(30)
        self._autoscroll.timeout.connect(self._autoscroll_tick)
        self.setMouseTracking(True)
        self.setMinimumHeight(self.sizeHint().height())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        f = QFont(self.font())
        f.setPointSizeF(8)
        self._small = f

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(800, TRACK_TOP + TRACK_H + RULER_H + OVERVIEW_H + 4)

    # -- data -------------------------------------------------------------------------------
    def set_thumbnail(self, i: int, img: QImage) -> None:
        self.thumbs[i] = img
        self.update()

    def set_zoom_thumbnail(self, index: int, img: QImage) -> None:
        self.zoom_thumbs[index] = img
        if len(self.zoom_thumbs) > 600:                  # keep memory bounded
            for k in list(self.zoom_thumbs)[:200]:
                del self.zoom_thumbs[k]
        self.update()

    def reset_thumbnails(self, count: int, aspect: float) -> None:
        self.thumbs.clear()
        self.zoom_thumbs.clear()
        self.thumb_count = count
        self.thumb_aspect = aspect if aspect > 0 else 16 / 9
        self.zoom_fit()
        self.update()

    def thumb_for_time(self, t: float):
        """Best available still for time t: an exact zoomed frame, else the nearest filmstrip slot."""
        g = self.model.grid
        if g is not None and self.zoom_thumbs:
            img = self.zoom_thumbs.get(g.index_at(t))
            if img is not None:
                return img
        if not self.thumb_count or not self.model.total:
            return None
        i = min(self.thumb_count - 1, max(0, int(t / self.model.total * self.thumb_count)))
        for d in range(self.thumb_count):            # nearest loaded thumbnail
            for j in (i - d, i + d):
                if 0 <= j < self.thumb_count and j in self.thumbs:
                    return self.thumbs[j]
        return None

    def set_suggestions(self, times: list[float]) -> None:
        self.suggestions = [t for t in times if t is not None]
        self.update()

    def _snap(self, t: float) -> float:
        """Pull a dragged handle onto a suggested trim point when it's within a few pixels."""
        for s in self.suggestions:
            if abs(self._x(s) - self._x(t)) <= 7:
                return s
        return t

    def set_playhead(self, t: float) -> None:
        if abs(t - self.playhead) > 1e-6:
            self.playhead = t
            self.update()

    def _model_changed(self) -> None:
        if not self.model.loaded:
            self.view_start, self.view_len = 0.0, 0.0
        self._clamp_view()
        self.update()

    # -- zoom & view ------------------------------------------------------------------------
    @property
    def zoomed(self) -> bool:
        total = self.model.total
        return total > 0 and 0 < self.view_len < total - 1e-6

    @property
    def zoom_factor(self) -> float:
        return self.model.total / self.view_len if self.zoomed else 1.0

    def visible(self) -> tuple[float, float]:
        total = self.model.total or 1.0
        if not self.zoomed:
            return 0.0, total
        return self.view_start, self.view_start + self.view_len

    def _min_view_len(self) -> float:
        g = self.model.grid
        frame = g.typical_interval if g is not None else 1 / 30
        return max(frame * 4, frame * self._track().width() / MAX_PX_PER_FRAME)

    def can_zoom_in(self) -> bool:
        return self.model.loaded and (self.view_len or self.model.total) > self._min_view_len() * 1.01

    def set_view(self, start: float, length: float) -> None:
        total = self.model.total
        if total <= 0:
            return
        length = min(max(length, self._min_view_len()), total)
        if length >= total - 1e-6:
            start, length = 0.0, 0.0                       # fit
        changed = (abs(start - self.view_start) > 1e-9) or (abs(length - self.view_len) > 1e-9)
        self.view_start, self.view_len = start, length
        self._clamp_view()
        if changed:
            self.viewChanged.emit(*self.visible_span())
            self.update()

    def visible_span(self) -> tuple[float, float]:
        a, b = self.visible()
        return a, b - a

    def _clamp_view(self) -> None:
        total = self.model.total
        if not self.zoomed:
            self.view_start = 0.0
            return
        self.view_start = min(max(self.view_start, 0.0), total - self.view_len)

    def zoom_by(self, factor: float, anchor_t: float | None = None) -> None:
        """Zoom in (factor > 1) or out around `anchor_t` (kept at the same screen position)."""
        if not self.model.loaded:
            return
        a, b = self.visible()
        length = b - a
        if anchor_t is None:
            anchor_t = self.playhead if a <= self.playhead <= b else (a + b) / 2
        frac = (anchor_t - a) / length if length else 0.5
        new_len = length / factor
        self.set_view(anchor_t - frac * min(max(new_len, self._min_view_len()), self.model.total), new_len)

    def zoom_in(self, anchor_t: float | None = None) -> None:
        self.zoom_by(ZOOM_STEP, anchor_t)

    def zoom_out(self, anchor_t: float | None = None) -> None:
        self.zoom_by(1 / ZOOM_STEP, anchor_t)

    def zoom_fit(self) -> None:
        if self.view_len:
            self.view_start, self.view_len = 0.0, 0.0
            self.viewChanged.emit(*self.visible_span())
            self.update()

    def pan(self, dt: float) -> None:
        if self.zoomed:
            self.set_view(self.view_start + dt, self.view_len)

    def ensure_visible(self, t: float) -> None:
        """Scroll (not zoom) so t is on screen, paging ahead like a media player."""
        if not self.zoomed:
            return
        a, b = self.visible()
        margin = (b - a) * 0.04
        if t < a + margin:
            self.set_view(t - (b - a) * 0.25, self.view_len)
        elif t > b - margin:
            self.set_view(t - (b - a) * 0.15, self.view_len)

    # -- geometry ---------------------------------------------------------------------------
    def _track(self) -> QRectF:
        return QRectF(HANDLE_W, TRACK_TOP, max(10.0, self.width() - 2 * HANDLE_W), TRACK_H)

    def _overview(self) -> QRectF:
        tr = self._track()
        return QRectF(tr.left(), TRACK_TOP + TRACK_H + RULER_H + 3, tr.width(), 6)

    def _x(self, t: float) -> float:
        """Widget x for time t. Times outside the visible window map beyond the track edges."""
        tr = self._track()
        a, b = self.visible()
        return tr.left() + tr.width() * (t - a) / ((b - a) or 1.0)

    def _t(self, x: float) -> float:
        tr = self._track()
        a, b = self.visible()
        frac = min(max((x - tr.left()) / tr.width(), 0.0), 1.0)
        return a + frac * (b - a)

    def _part_at(self, pos: QPointF) -> str | None:
        if not self.model.loaded:
            return None
        if self.zoomed and self._overview().adjusted(0, -4, 0, 4).contains(pos):
            return "overview"
        xs, xe = self._x(self.model.start_time), self._x(self.model.end_time)
        tr = self._track()
        y_ok = TRACK_TOP - 4 <= pos.y() <= TRACK_TOP + TRACK_H + 4
        if y_ok:
            in_start = xs - HANDLE_W - 2 <= pos.x() <= xs + 3 and tr.left() - HANDLE_W <= xs <= tr.right() + 1
            in_end = xe - 3 <= pos.x() <= xe + HANDLE_W + 2 and tr.left() - 1 <= xe <= tr.right() + HANDLE_W
            if in_start and in_end:   # handles touch: pick the nearer one
                return "start" if pos.x() < (xs + xe) / 2 else "end"
            if in_start:
                return "start"
            if in_end:
                return "end"
        if pos.y() <= TRACK_TOP + TRACK_H + RULER_H:
            return "scrub"
        return None

    # -- mouse ------------------------------------------------------------------------------
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton or not self.model.loaded:
            return
        part = self._part_at(e.position())
        if part is None:
            return
        self.hoverChanged.emit(-1.0, 0.0)
        self._drag = part
        self._drag_origin_x = self._last_drag_x = e.position().x()
        if part == "start":
            self._drag_origin_t = self.model.start_time
        elif part == "end":
            self._drag_origin_t = self.model.end_time
        elif part == "overview":
            self._overview_drag(e.position().x(), first=True)
        else:
            self.scrubStarted.emit()
            self.seekRequested.emit(self._t(e.position().x()))
        self.update()

    def _overview_drag(self, x: float, first: bool = False) -> None:
        ov = self._overview()
        total = self.model.total
        t = min(max((x - ov.left()) / ov.width(), 0.0), 1.0) * total
        a, b = self.visible()
        if first and a <= t <= b:
            self._overview_grab = t - a                   # drag the window from where it was grabbed
        elif first:
            self._overview_grab = (b - a) / 2             # click outside: centre the window there
        self.set_view(t - self._overview_grab, self.view_len)

    def _drag_handle_to(self, x: float, fine: bool) -> None:
        tr = self._track()
        a, b = self.visible()
        if fine:
            t = self._drag_origin_t + (x - self._drag_origin_x) * 0.1 * (b - a) / tr.width()
        else:
            t = self._snap(self._t(x + (self._x(self._drag_origin_t) - self._drag_origin_x)))
        if self._drag == "start":
            self.model.set_start_time(t)
            self.handleDragged.emit("start", self.model.start_time)
        else:
            self.model.set_end_time(t)
            self.handleDragged.emit("end", self.model.end_time)
        if fine:  # re-anchor so switching Shift on/off mid-drag doesn't jump
            self._drag_origin_x = x
            self._drag_origin_t = t

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        pos = e.position()
        self._hover_x = pos.x()
        if self._drag in ("start", "end"):
            self._last_drag_x = pos.x()
            self._drag_fine = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._drag_handle_to(pos.x(), self._drag_fine)
            tr = self._track()
            near_edge = pos.x() < tr.left() + 12 or pos.x() > tr.right() - 12
            if self.zoomed and near_edge and not self._autoscroll.isActive():
                self._autoscroll.start()
            elif not near_edge:
                self._autoscroll.stop()
        elif self._drag == "scrub":
            self.seekRequested.emit(self._t(pos.x()))
        elif self._drag == "overview":
            self._overview_drag(pos.x())
        else:
            part = self._part_at(pos)
            self._hover_part = part
            tr = self._track()
            over_track = tr.left() <= pos.x() <= tr.right() and pos.y() <= tr.bottom() + 4
            if self.model.loaded and over_track and part == "scrub":
                self.hoverChanged.emit(self._t(pos.x()), pos.x())
            else:
                self.hoverChanged.emit(-1.0, 0.0)
            self.setCursor(Qt.CursorShape.SizeHorCursor if part in ("start", "end", "overview")
                           else Qt.CursorShape.PointingHandCursor if part == "scrub"
                           else Qt.CursorShape.ArrowCursor)
        self.update()

    def _autoscroll_tick(self) -> None:
        """While a handle is held at an edge of a zoomed view, keep scrolling that way."""
        if self._drag not in ("start", "end") or not self.zoomed:
            self._autoscroll.stop()
            return
        tr = self._track()
        x = self._last_drag_x
        if x < tr.left() + 12:
            self.pan(-self.view_len * 0.03)
        elif x > tr.right() - 12:
            self.pan(self.view_len * 0.03)
        else:
            self._autoscroll.stop()
            return
        self._drag_handle_to(x, False)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._autoscroll.stop()
        drag, self._drag = self._drag, None
        if drag in ("start", "end"):
            self.handleReleased.emit(drag)
        elif drag == "scrub":
            self.scrubFinished.emit()
        self.update()

    def leaveEvent(self, _e) -> None:  # noqa: N802
        self.hoverChanged.emit(-1.0, 0.0)
        self._hover_x = None
        self._hover_part = None
        self.update()

    def wheelEvent(self, e) -> None:  # noqa: N802
        if not self.model.loaded:
            return
        dy, dx = e.angleDelta().y(), e.angleDelta().x()
        mods = e.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            # Ctrl + wheel: zoom around the mouse pointer
            anchor = self._t(e.position().x())
            (self.zoom_in if dy > 0 else self.zoom_out)(anchor)
            return
        if dx and not dy and self.zoomed:                 # sideways scroll (touchpads): pan
            self.pan(-dx / 120 * self.view_len * 0.1)
            return
        steps = 1 if dy < 0 or dx < 0 else -1
        mult = 10 if mods & Qt.KeyboardModifier.ShiftModifier else 1
        self.wheelStep.emit(steps * mult)

    # -- painting ---------------------------------------------------------------------------
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        tr = self._track()
        clip = QPainterPath()
        clip.addRoundedRect(tr, 6, 6)

        # Filmstrip
        p.save()
        p.setClipPath(clip)
        p.fillRect(tr, QColor(theme.SURFACE_2))
        if self.model.loaded:
            self._paint_filmstrip(p, tr)
        p.restore()

        if not self.model.loaded:
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(tr, 6, 6)
            return

        xs, xe = self._x(self.model.start_time), self._x(self.model.end_time)

        # Excluded footage
        p.save()
        p.setClipPath(clip)
        if xs > tr.left():
            p.fillRect(QRectF(tr.left(), tr.top(), xs - tr.left(), tr.height()), theme.EXCLUDED_SHADE)
        if xe < tr.right():
            p.fillRect(QRectF(xe, tr.top(), tr.right() - xe, tr.height()), theme.EXCLUDED_SHADE)
        p.restore()

        # Everything below is clipped to the timeline band (handles may sit just outside the track).
        p.save()
        p.setClipRect(QRectF(0, 0, self.width(), TRACK_TOP + TRACK_H + RULER_H))

        # Suggested trim points: a plain black-and-white dashed line (one of the two colours always
        # contrasts with the footage), plus a small accent tick below the strip.
        if self.suggestions:
            col = QColor(theme.ACCENT)
            white = QPen(QColor(255, 255, 255, 220), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
            black = QPen(QColor(0, 0, 0, 220), 1.5, Qt.PenStyle.CustomDashLine, Qt.PenCapStyle.FlatCap)
            black.setDashPattern([2.0, 2.0])
            for s in self.suggestions:
                xs_ = self._x(s)
                if not tr.left() - 1 <= xs_ <= tr.right() + 1:
                    continue
                a, b = QPointF(xs_, tr.top() + 1), QPointF(xs_, tr.bottom() - 1)
                for pen in (white, black):
                    p.setPen(pen)
                    p.drawLine(a, b)
                p.setPen(QPen(QColor(0, 0, 0, 150), 1.0))
                p.setBrush(col)
                tri = QPainterPath()
                tri.moveTo(xs_ - 4, tr.bottom() + 6)
                tri.lineTo(xs_ + 4, tr.bottom() + 6)
                tri.lineTo(xs_, tr.bottom() + 1.5)
                tri.closeSubpath()
                p.drawPath(tri)

        # Selection frame + handles (only the parts inside the visible window)
        accent = QColor(theme.ACCENT)
        sel_l, sel_r = max(xs, tr.left()), min(xe, tr.right())
        if sel_r > sel_l:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            p.drawRect(QRectF(sel_l, tr.top() - 2, sel_r - sel_l, 2.5))
            p.drawRect(QRectF(sel_l, tr.bottom() - 0.5, sel_r - sel_l, 2.5))
        if tr.left() - 1 <= xs <= tr.right() + 1:
            self._paint_handle(p, QRectF(xs - HANDLE_W, tr.top() - 2, HANDLE_W, tr.height() + 4), left=True,
                               hot=self._drag == "start" or self._hover_part == "start")
        if tr.left() - 1 <= xe <= tr.right() + 1:
            self._paint_handle(p, QRectF(xe, tr.top() - 2, HANDLE_W, tr.height() + 4), left=False,
                               hot=self._drag == "end" or self._hover_part == "end")

        # Hover guide
        if self._hover_x is not None and self._drag is None and self._hover_part == "scrub":
            hx = min(max(self._hover_x, tr.left()), tr.right())
            p.setPen(QPen(QColor(255, 255, 255, 70), 1))
            p.drawLine(QPointF(hx, tr.top()), QPointF(hx, tr.bottom()))

        # Playhead
        xp = self._x(self.playhead)
        if tr.left() - 1 <= xp <= tr.right() + 1:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 110))
            p.drawRect(QRectF(xp - 2, tr.top(), 4, tr.height()))
            p.setBrush(QColor("#ffffff"))
            p.drawRect(QRectF(xp - 1, tr.top() - 6, 2, tr.height() + 8))
            knob = QPainterPath()
            knob.addRoundedRect(QRectF(xp - 6, 2, 12, 9), 3, 3)
            p.drawPath(knob)

        self._paint_ruler(p, tr)

        # Floating time bubble while dragging a handle or scrubbing
        bubble_t = None
        if self._drag == "start":
            bubble_t, bx = self.model.start_time, xs
        elif self._drag == "end":
            bubble_t, bx = self.model.end_time, xe
        elif self._drag == "scrub":
            bubble_t, bx = self.playhead, xp
        if bubble_t is not None:
            self._paint_bubble(p, min(max(bx, tr.left()), tr.right()), format_time(bubble_t))
        p.restore()

        if self.zoomed:
            self._paint_overview(p)

    def _paint_filmstrip(self, p: QPainter, tr: QRectF) -> None:
        if not self.thumb_count:
            return
        slot_w = max(16.0, tr.height() * self.thumb_aspect)
        a, b = self.visible()
        span = (b - a) or 1.0
        g = self.model.grid
        # Slots are laid out on whole-video multiples so they don't shimmer while panning.
        px_per_s = tr.width() / span
        first = math.floor(a * px_per_s / slot_w)
        last = math.ceil(b * px_per_s / slot_w)
        total = self.model.total or 1.0
        for j in range(first, last + 1):
            x = tr.left() + j * slot_w - a * px_per_s
            center_t = (j + 0.5) * slot_w / px_per_s
            img = None
            if self.zoomed and g is not None:
                img = self.zoom_thumbs.get(g.index_at(min(center_t, total)))
            if img is None:
                i = min(self.thumb_count - 1, max(0, int(center_t / total * self.thumb_count)))
                img = self.thumbs.get(i)
            target = QRectF(x, tr.top(), slot_w, tr.height())
            if img is not None:
                # Cover-fit the slot, cropping the image centre.
                src_w = img.height() * slot_w / tr.height()
                sx = max(0.0, (img.width() - src_w) / 2)
                p.drawImage(target, img, QRectF(sx, 0, min(src_w, img.width()), img.height()))
            else:
                grad = QLinearGradient(target.topLeft(), target.bottomLeft())
                grad.setColorAt(0, QColor(theme.SURFACE_3))
                grad.setColorAt(1, QColor(theme.SURFACE_2))
                p.fillRect(target, grad)
            p.fillRect(QRectF(x + slot_w - 1, tr.top(), 1, tr.height()), QColor(0, 0, 0, 60))

    def slot_times(self) -> list[float]:
        """Centre times of the filmstrip slots currently on screen (for fetching zoomed frames)."""
        tr = self._track()
        if not self.model.loaded or not self.zoomed:
            return []
        slot_w = max(16.0, tr.height() * self.thumb_aspect)
        a, b = self.visible()
        px_per_s = tr.width() / ((b - a) or 1.0)
        first = math.floor(a * px_per_s / slot_w)
        last = math.ceil(b * px_per_s / slot_w)
        total = self.model.total
        return [min(total, (j + 0.5) * slot_w / px_per_s) for j in range(first, last + 1)]

    def _paint_handle(self, p: QPainter, r: QRectF, left: bool, hot: bool) -> None:
        color = QColor(theme.ACCENT_HOVER if hot else theme.ACCENT)
        path = QPainterPath()
        rad = 6.0
        if left:
            path.moveTo(r.right(), r.top())
            path.lineTo(r.left() + rad, r.top())
            path.quadTo(r.left(), r.top(), r.left(), r.top() + rad)
            path.lineTo(r.left(), r.bottom() - rad)
            path.quadTo(r.left(), r.bottom(), r.left() + rad, r.bottom())
            path.lineTo(r.right(), r.bottom())
        else:
            path.moveTo(r.left(), r.top())
            path.lineTo(r.right() - rad, r.top())
            path.quadTo(r.right(), r.top(), r.right(), r.top() + rad)
            path.lineTo(r.right(), r.bottom() - rad)
            path.quadTo(r.right(), r.bottom(), r.right() - rad, r.bottom())
            path.lineTo(r.left(), r.bottom())
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(path)
        # Grip
        p.setBrush(QColor(theme.ACCENT_TEXT))
        cx = r.center().x()
        p.drawRoundedRect(QRectF(cx - 1.25, r.center().y() - 9, 2.5, 18), 1.25, 1.25)

    def _paint_ruler(self, p: QPainter, tr: QRectF) -> None:
        total = self.model.total
        if total <= 0:
            return
        a, b = self.visible()
        span = (b - a) or 1.0
        p.setFont(self._small)
        fm = p.fontMetrics()
        min_px = fm.horizontalAdvance("0:00.000") + 22
        steps = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]
        step = next((s for s in steps if s / span * tr.width() >= min_px), steps[-1])
        y = tr.bottom() + 4
        # Frame ticks when frames are far enough apart to see
        g = self.model.grid
        if g is not None and self.zoomed:
            px_per_frame = g.typical_interval / span * tr.width()
            if px_per_frame >= 6:
                p.setPen(QColor(255, 255, 255, 40))
                i0, i1 = g.index_at(a), g.index_at(b)
                for i in range(i0, min(i1 + 2, len(g))):
                    x = self._x(g.times[i])
                    if tr.left() <= x <= tr.right():
                        p.drawLine(QPointF(x, y), QPointF(x, y + 2))
        p.setPen(QColor(theme.TEXT_FAINT))
        decimals = 3 if step < 0.1 else 2 if step < 1 else 0
        t = math.ceil(a / step - 1e-9) * step
        while t <= b + 1e-9:
            x = self._x(t)
            p.drawLine(QPointF(x, y), QPointF(x, y + 3))
            label = format_time(t, show_ms=decimals > 0)
            if decimals == 2:
                label = label[:-1]  # 0:01.50 - centiseconds are enough at this zoom
            w = fm.horizontalAdvance(label)
            lx = min(max(x - w / 2, 0), self.width() - w)
            p.drawText(QPointF(lx, y + 4 + fm.ascent()), label)
            t += step

    def _paint_overview(self, p: QPainter) -> None:
        """Mini-map of the whole video: selection, playhead and the zoomed window (draggable)."""
        ov = self._overview()
        total = self.model.total or 1.0
        fx = lambda t: ov.left() + ov.width() * min(max(t / total, 0.0), 1.0)  # noqa: E731
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.SURFACE_3))
        p.drawRoundedRect(ov, 3, 3)
        sel = QColor(theme.ACCENT)
        sel.setAlpha(90)
        p.setBrush(sel)
        p.drawRect(QRectF(fx(self.model.start_time), ov.top() + 1, fx(self.model.end_time) - fx(self.model.start_time),
                          ov.height() - 2))
        a, b = self.visible()
        win = QRectF(fx(a), ov.top() - 1.5, max(6.0, fx(b) - fx(a)), ov.height() + 3)
        p.setPen(QPen(QColor(theme.TEXT), 1.2))
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(win, 3, 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawRect(QRectF(fx(self.playhead) - 0.75, ov.top() - 1, 1.5, ov.height() + 2))

    def _paint_bubble(self, p: QPainter, x: float, text: str) -> None:
        p.setFont(self._small)
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 12, fm.height() + 4
        bx = min(max(x - w / 2, 0), self.width() - w)
        r = QRectF(bx, TRACK_TOP + TRACK_H + 3, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.SURFACE_3))
        p.drawRoundedRect(r, 5, 5)
        p.setPen(QColor(theme.TEXT))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
