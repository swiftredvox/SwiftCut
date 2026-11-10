"""Custom controls: toggle switch, segmented control, time field and toast."""
from __future__ import annotations

from PySide6.QtCore import (Property, QEasingCurve, QEvent, QPropertyAnimation, QRectF, QRegularExpression,
                            QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRegularExpressionValidator
from PySide6.QtWidgets import (QAbstractButton, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSizePolicy, QToolButton, QWidget)

from ..core.timecode import format_time, parse_time
from . import icons, theme


class ToggleSwitch(QAbstractButton):
    """An animated on/off switch."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 22)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool) -> None:  # noqa: N802 - Qt naming
        super().setChecked(on)
        self._anim.stop()
        self._pos = 1.0 if on else 0.0
        self.update()

    def _get(self) -> float:
        return self._pos

    def _set(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, _get, _set)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(38, 22)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        off, on = QColor(theme.SURFACE_3), QColor(theme.ACCENT)
        t = self._pos
        track = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                       int(off.blue() + (on.blue() - off.blue()) * t))
        if not self.isEnabled():
            track = QColor(theme.SURFACE_2)
        p.setPen(QPen(QColor(theme.BORDER_STRONG), 1) if t < 0.5 else Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - d - 6) * t
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT_TEXT) if t > 0.5 else QColor(theme.TEXT_MUTED))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class SegmentedControl(QWidget):
    """A pill-shaped control with mutually exclusive segments (used for Basic / Advanced)."""

    changed = Signal(int)

    def __init__(self, labels: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self._labels = labels
        self._index = 0
        self._hover = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        f = QFont(self.font())
        f.setPointSizeF(9)
        f.setWeight(QFont.Weight.DemiBold)
        self.setFont(f)
        fm = self.fontMetrics()
        self._seg_w = max(fm.horizontalAdvance(t) for t in labels) + 28
        self.setFixedWidth(self._seg_w * len(labels) + 6)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def index(self) -> int:
        return self._index

    def setIndex(self, i: int, emit: bool = False) -> None:  # noqa: N802
        if i != self._index:
            self._index = i
            self.update()
            if emit:
                self.changed.emit(i)

    def _seg_at(self, x: float) -> int:
        return min(len(self._labels) - 1, max(0, int((x - 3) // self._seg_w)))

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.setIndex(self._seg_at(e.position().x()), emit=True)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        h = self._seg_at(e.position().x())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, _e) -> None:  # noqa: N802
        self._hover = -1
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.BG))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        for i, label in enumerate(self._labels):
            seg = QRectF(3 + i * self._seg_w, 3, self._seg_w, self.height() - 6)
            if i == self._index:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.SURFACE_3))
                p.drawRoundedRect(seg, seg.height() / 2, seg.height() / 2)
                p.setPen(QColor(theme.TEXT))
            else:
                p.setPen(QColor(theme.TEXT) if i == self._hover else QColor(theme.TEXT_MUTED))
            p.drawText(seg, Qt.AlignmentFlag.AlignCenter, label)


class TimeEdit(QLineEdit):
    """A time box that only accepts digits, ':' and '.', and lets Space reach the play/pause shortcut."""

    escaped = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9:.,]*"), self))

    def event(self, e) -> bool:  # noqa: N802
        # A line edit normally claims Space for typing; refusing it here lets the window's
        # play/pause shortcut fire even while this box has focus.
        if e.type() == QEvent.Type.ShortcutOverride and e.key() == Qt.Key.Key_Space:
            e.ignore()
            return False
        return super().event(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
            return
        super().keyPressEvent(e)


class TimeField(QWidget):
    """Editable time (m:ss.mmm) with frame-nudge arrows on either side.

    The box always shows the live value unless you're typing in it. Return applies what you
    typed (`edited(seconds)`); Esc or clicking elsewhere discards it. Arrows emit `nudged(+1/-1)`.
    """

    edited = Signal(float)
    nudged = Signal(int)

    def __init__(self, label: str, accent: str, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.caption = QLabel(label)
        self.caption.setProperty("role", "muted")
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{accent}; border-radius:4px;")
        self.back = self._nudge_button("chev-left", "Move one frame earlier")
        self.fwd = self._nudge_button("chev-right", "Move one frame later")
        self.edit = TimeEdit()
        self.edit.setProperty("role", "time")
        self.edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit.setFixedWidth(92)
        self.edit.setToolTip("Type a time such as 12.5, 0:12.500 or 1:02:03, then press Return.\n"
                             "Esc or clicking elsewhere cancels.")
        self.edit.returnPressed.connect(self._commit)
        self.edit.escaped.connect(self.cancel_edit)
        self.edit.textEdited.connect(self._typed)
        lay.addWidget(dot)
        lay.addSpacing(5)
        lay.addWidget(self.caption)
        lay.addSpacing(6)
        lay.addWidget(self.back)
        lay.addWidget(self.edit)
        lay.addWidget(self.fwd)
        self.back.clicked.connect(lambda: self._nudge(-1))
        self.fwd.clicked.connect(lambda: self._nudge(+1))
        self._value = 0.0
        self._dirty = False          # True while the user has typed something not yet applied

    def _nudge_button(self, name: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setIcon(icons.icon(name, theme.TEXT_MUTED, 14))
        b.setIconSize(QSize(14, 14))
        b.setFixedSize(22, 28)
        b.setToolTip(tip)
        b.setAutoRepeat(True)
        b.setAutoRepeatDelay(350)
        b.setAutoRepeatInterval(60)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    @property
    def editing(self) -> bool:
        return self._dirty

    def setValue(self, t: float) -> None:  # noqa: N802
        self._value = t
        if not self._dirty:                  # live unless the user is mid-edit
            self.edit.setText(format_time(t))
        self._set_invalid(False)

    def _typed(self, _text: str) -> None:
        self._dirty = True
        self._set_invalid(False)

    def _nudge(self, d: int) -> None:
        self.cancel_edit()                   # an arrow click abandons any half-typed time
        self.nudged.emit(d)

    def cancel_edit(self) -> None:
        """Discard typing, show the live value again and give focus back to the window."""
        self._dirty = False
        self._set_invalid(False)
        self.edit.setText(format_time(self._value))
        if self.edit.hasFocus():
            self.edit.clearFocus()

    def _set_invalid(self, bad: bool) -> None:
        if self.edit.property("invalid") != bad:
            self.edit.setProperty("invalid", bad)
            self.edit.style().unpolish(self.edit)
            self.edit.style().polish(self.edit)

    def _commit(self) -> None:
        if not self._dirty:
            self.edit.clearFocus()
            return
        try:
            t = parse_time(self.edit.text())
        except ValueError:
            self._set_invalid(True)          # stay in the box so the typo can be fixed
            return
        self._dirty = False
        self.edited.emit(t)
        self.edit.setText(format_time(self._value))
        self.edit.clearFocus()

    def setEnabled(self, on: bool) -> None:  # noqa: N802
        super().setEnabled(on)
        if not on:
            self._dirty = False
            self.edit.setText("-:--.---")


class Toast(QWidget):
    """A transient message pill floating at the bottom of its parent, with an optional action."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("Toast")
        self.setStyleSheet(f"#Toast {{ background: {theme.SURFACE_3}; border: 1px solid {theme.BORDER_STRONG};"
                           f" border-radius: 10px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 7, 8, 7)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.text = QLabel()
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.action = QPushButton()
        self.action.setProperty("kind", "ghost")
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.setStyleSheet(f"color: {theme.ACCENT}; padding: 3px 8px;")
        lay.addWidget(self.icon)
        lay.addWidget(self.text)
        lay.addWidget(self.action)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self._callback = None
        self.anchor = None   # optional callable -> (right_x, center_y) in parent coordinates
        self.action.clicked.connect(self._run_action)

    def _run_action(self) -> None:
        cb = self._callback
        self.hide()
        if cb:
            cb()

    def show_message(self, text: str, kind: str = "info", action: str | None = None,
                     callback=None, msec: int = 4500) -> None:
        color = {"info": theme.ACCENT, "error": theme.DANGER, "warn": theme.WARNING}.get(kind, theme.ACCENT)
        name = {"info": "check", "error": "alert", "warn": "info"}.get(kind, "check")
        self.icon.setPixmap(icons.pixmap(name, color, 16, self.devicePixelRatioF()))
        self.text.setText(text)
        self.action.setVisible(bool(action))
        self.action.setText(action or "")
        self._callback = callback
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(msec)

    def reposition(self) -> None:
        par = self.parentWidget()
        if par is None:
            return
        self.adjustSize()
        if self.anchor is not None:
            right, cy = self.anchor()
            self.move(max(8, right - self.width()), cy - self.height() // 2)
        else:
            self.move((par.width() - self.width()) // 2, par.height() - self.height() - 18)


def hline() -> QWidget:
    w = QWidget()
    w.setFixedHeight(1)
    w.setStyleSheet(f"background: {theme.BORDER};")
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return w
