"""Preview-audio controls: on/off toggle, volume slider and output-device menu.

These only affect what you *hear* while previewing. Whether the saved file contains audio is
set separately by the "Include audio" switch next to Save.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QHBoxLayout, QMenu, QSlider, QToolButton, QWidget

from . import icons, theme

SLIDER_STYLE = f"""
QSlider::groove:horizontal {{ height: 4px; background: {theme.SURFACE_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {theme.ACCENT}; border-radius: 2px; }}
QSlider::sub-page:horizontal:disabled {{ background: {theme.BORDER_STRONG}; }}
QSlider::handle:horizontal {{ background: {theme.TEXT}; width: 12px; height: 12px; margin: -4px 0;
                              border-radius: 6px; }}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
QSlider::handle:horizontal:disabled {{ background: {theme.TEXT_FAINT}; }}
"""


class AudioControl(QWidget):
    enabledChanged = Signal(bool)      # preview audio on/off
    volumeChanged = Signal(float)      # 0..1
    deviceChosen = Signal(object)      # bytes id, or None for "system default"

    def __init__(self, device_source, parent: QWidget | None = None):
        """`device_source()` returns (devices, current_choice_id, active_device)."""
        super().__init__(parent)
        self._device_source = device_source
        self._on = True
        self._has_audio = True
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.toggle = QToolButton()
        self.toggle.setFixedSize(34, 34)
        self.toggle.setIconSize(QSize(18, 18))
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.toggle.clicked.connect(lambda: self.set_enabled_state(not self._on, emit=True))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setFixedWidth(84)
        self.slider.setStyleSheet(SLIDER_STYLE)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.valueChanged.connect(self._slider_moved)
        self.device_btn = QToolButton()
        self.device_btn.setFixedSize(22, 34)
        self.device_btn.setIcon(icons.icon("chev-down", theme.TEXT_MUTED, 14))
        self.device_btn.setIconSize(QSize(14, 14))
        self.device_btn.setToolTip("Choose audio output device")
        self.device_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.device_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.device_btn.clicked.connect(self._show_devices)
        lay.addWidget(self.toggle)
        lay.addSpacing(2)
        lay.addWidget(self.slider)
        lay.addSpacing(2)
        lay.addWidget(self.device_btn)
        self._hint = ""
        self._refresh()

    # -- state ------------------------------------------------------------------------------
    def set_shortcut_hint(self, hint: str) -> None:
        self._hint = hint
        self._refresh()

    def set_has_audio(self, has: bool) -> None:
        self._has_audio = has
        self._refresh()

    def set_enabled_state(self, on: bool, emit: bool = False) -> None:
        self._on = on
        self._refresh()
        if emit:
            self.enabledChanged.emit(on)

    def is_on(self) -> bool:
        return self._on

    def set_volume(self, v: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(v * 100)))
        self.slider.blockSignals(False)
        self._refresh()

    def nudge_volume(self, delta: int) -> None:
        self.slider.setValue(max(0, min(100, self.slider.value() + delta)))

    def _slider_moved(self, value: int) -> None:
        if not self._on and value > 0:
            self.set_enabled_state(True, emit=True)   # moving the slider turns sound back on
        self.volumeChanged.emit(value / 100)
        self._refresh()

    def _refresh(self) -> None:
        audible = self._on and self._has_audio and self.slider.value() > 0
        self.toggle.setIcon(icons.icon("volume" if audible else "volume-off",
                                       theme.TEXT if audible else theme.TEXT_MUTED, 18))
        hint = f" ({self._hint})" if self._hint else ""
        if not self._has_audio:
            tip = "This video has no audio"
        elif self._on:
            tip = f"Preview audio is on. Click to turn it off{hint}"
        else:
            tip = f"Preview audio is off. Click to turn it on{hint}"
        self.toggle.setToolTip(tip + "\nThis only affects what you hear, not the saved file.")
        self.slider.setToolTip(f"Preview volume: {self.slider.value()}%  (scroll to adjust)")
        self.slider.setEnabled(self._has_audio)
        self.toggle.setEnabled(self._has_audio)

    def wheelEvent(self, e) -> None:  # noqa: N802 - scroll anywhere on the group to change volume
        self.nudge_volume(5 if e.angleDelta().y() > 0 else -5)

    # -- devices ----------------------------------------------------------------------------
    def _show_devices(self) -> None:
        devices, chosen, active = self._device_source()
        menu = QMenu(self)
        title = menu.addAction("Audio output")
        title.setEnabled(False)
        group = QActionGroup(menu)
        default = QAction(f"System default", menu, checkable=True)
        default.setChecked(chosen is None)
        default.triggered.connect(lambda: self.deviceChosen.emit(None))
        group.addAction(default)
        menu.addAction(default)
        menu.addSeparator()
        for d in devices:
            did = bytes(d.id())
            label = d.description()
            if did == bytes(active.id()):
                label += "   • playing"
            act = QAction(label, menu, checkable=True)
            act.setChecked(chosen == did)
            act.triggered.connect(lambda _c=False, i=did: self.deviceChosen.emit(i))
            group.addAction(act)
            menu.addAction(act)
        if not devices:
            none = menu.addAction("No audio devices found")
            none.setEnabled(False)
        menu.exec(self.device_btn.mapToGlobal(self.device_btn.rect().bottomLeft()))
