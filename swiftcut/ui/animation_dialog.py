"""'Save as animation' dialog: GIF or animated WebP, with size, frame rate, quality and a live estimate."""
from __future__ import annotations

import dataclasses
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLayout, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from ..core import animation
from ..core.animation import AnimationOptions
from ..core.media_info import MediaInfo
from ..core.timecode import FrameGrid, format_time
from .widgets import SegmentedControl, ToggleSwitch

QUALITY_LABELS = [("high", "High"), ("balanced", "Balanced"), ("small", "Smaller file")]


class _EstimateWorker(QObject):
    done = Signal(object, int)          # options key, bytes

    def run(self, key, info, grid, opts, duration, cancel: threading.Event) -> None:
        def work() -> None:
            try:
                size = animation.estimate_animation_bytes(info, grid, opts, duration, cancel)
            except Exception:  # noqa: BLE001 - an estimate is optional
                return
            if not cancel.is_set():
                try:
                    self.done.emit(key, size)
                except RuntimeError:
                    pass
        threading.Thread(target=work, name="anim-estimate", daemon=True).start()


def _rough(n: int) -> str:
    mb = n / (1024 * 1024)
    return f"{mb:.0f} MB" if mb >= 10 else f"{mb:.1f} MB" if mb >= 1 else f"{max(1, round(n / 1024))} KB"


class AnimationDialog(QDialog):
    def __init__(self, info: MediaInfo, grid: FrameGrid, duration: float, saved: AnimationOptions,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.info, self.grid, self.duration = info, grid, duration
        self.setWindowTitle("Save as animation")
        lay = QVBoxLayout(self)
        lay.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        title = QLabel("Save as animation")
        title.setProperty("role", "title")
        sub = QLabel(f"The selected {format_time(duration)} as a silent, looping animation for chats, "
                     "docs and web pages.")
        sub.setProperty("role", "note")
        sub.setWordWrap(True)
        sub.setMinimumWidth(400)
        lay.addWidget(title)
        lay.addWidget(sub)

        self.fmt = SegmentedControl(["GIF", "WebP"])
        self.fmt.setIndex(1 if saved.format == "webp" else 0)
        self.size = QComboBox()
        w, h = info.video.display_size
        self.size.addItem(f"Original ({w} × {h})", 0)
        for width in animation.WIDTHS:
            if width < w:
                self.size.addItem(f"{width} × {round(h * width / w)}", width)
        self._select(self.size, saved.width or 0)
        self.rate = QComboBox()
        src = info.video.fps or 30
        self.rate.addItem(f"Original ({src:.3g} fps)", 0)
        for r in animation.FRAME_RATES:
            if r < src - 0.01:
                self.rate.addItem(f"{r} fps", r)
        self._select(self.rate, saved.fps or 0)
        self.quality = QComboBox()
        for key, label in QUALITY_LABELS:
            self.quality.addItem(label, key)
        self._select(self.quality, saved.quality)
        self.loop = ToggleSwitch()
        self.loop.setChecked(saved.loop)

        grid_l = QGridLayout()
        grid_l.setHorizontalSpacing(12)
        grid_l.setVerticalSpacing(8)
        rows = [("Format", self.fmt), ("Size", self.size), ("Frame rate", self.rate),
                ("Quality", self.quality), ("Loop forever", self.loop)]
        for r, (label, w_) in enumerate(rows):
            lab = QLabel(label)
            lab.setProperty("role", "muted")
            grid_l.addWidget(lab, r, 0)
            grid_l.addWidget(w_, r, 1, Qt.AlignmentFlag.AlignLeft if w_ in (self.fmt, self.loop) else Qt.AlignmentFlag(0))
        grid_l.setColumnStretch(1, 1)
        lay.addLayout(grid_l)

        self.format_note = QLabel()
        self.format_note.setProperty("role", "note")
        self.format_note.setWordWrap(True)
        self.estimate = QLabel()
        self.estimate.setProperty("role", "muted")
        lay.addWidget(self.format_note)
        lay.addWidget(self.estimate)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.save = QPushButton("Save…")
        self.save.setProperty("kind", "primary")
        self.save.setDefault(True)
        self.save.clicked.connect(self.accept)
        for b in (cancel, self.save):
            b.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save)
        lay.addLayout(buttons)

        self._sizes: dict = {}
        self._cancel = threading.Event()
        self._worker = _EstimateWorker()
        self._worker.done.connect(self._on_estimate)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._estimate_now)
        self.fmt.changed.connect(lambda _i: self._changed())
        for c in (self.size, self.rate, self.quality):
            c.currentIndexChanged.connect(lambda _i: self._changed())
        self.loop.toggled.connect(lambda _o: self._changed())
        self._changed()

    @staticmethod
    def _select(combo: QComboBox, data) -> None:
        i = combo.findData(data)
        combo.setCurrentIndex(i if i >= 0 else 0)

    def options(self) -> AnimationOptions:
        return AnimationOptions(
            format="webp" if self.fmt.index() == 1 else "gif",
            width=self.size.currentData() or None,
            fps=self.rate.currentData() or None,
            loop=self.loop.isChecked(),
            quality=self.quality.currentData() or "balanced",
        )

    def _key(self, o: AnimationOptions) -> tuple:
        return dataclasses.astuple(dataclasses.replace(o, loop=True))

    def _changed(self) -> None:
        o = self.options()
        if o.format == "webp":
            self.format_note.setText("WebP is usually 3–5× smaller than GIF with better colour, and "
                                     "plays in browsers, Discord and most modern apps.")
        else:
            self.format_note.setText("GIF plays almost everywhere, but files are larger and limited to "
                                     "256 colours.")
        size = self._sizes.get(self._key(o))
        if size is None:
            self.estimate.setText("Estimated size: measuring…")
            self._timer.start()
        else:
            self.estimate.setText(f"Estimated size: about {_rough(size)}")

    def _estimate_now(self) -> None:
        o = self.options()
        self._cancel.set()
        self._cancel = threading.Event()
        self._worker.run(self._key(o), self.info, self.grid, o, self.duration, self._cancel)

    def _on_estimate(self, key, size: int) -> None:
        self._sizes[key] = size
        if key == self._key(self.options()):
            self._changed()

    def done(self, r: int) -> None:  # noqa: A003 - QDialog API
        self._cancel.set()
        super().done(r)
