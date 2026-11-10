"""Keyboard shortcuts: a readable reference that doubles as the editor."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QKeyCombination, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from . import theme
from .keymap import ACTIONS, BY_ID, MAX_KEYS, Keymap, display

_MODIFIER_KEYS = {Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
                  Qt.Key.Key_AltGr, Qt.Key.Key_CapsLock, Qt.Key.Key_unknown}

KEY_STYLE = f"""
QPushButton {{ background: {theme.SURFACE_2}; border: 1px solid {theme.BORDER_STRONG};
    border-bottom: 2px solid {theme.BORDER_STRONG}; border-radius: 6px; padding: 3px 8px;
    font-family: {theme.MONO_FAMILY}; color: {theme.TEXT}; }}
QPushButton:hover {{ border-color: {theme.ACCENT}; }}
QPushButton[empty="true"] {{ background: transparent; border: 1px dashed {theme.BORDER};
    color: {theme.TEXT_FAINT}; }}
QPushButton[empty="true"]:hover {{ border-color: {theme.BORDER_STRONG}; color: {theme.TEXT_MUTED}; }}
QPushButton[capturing="true"] {{ background: {theme.ACCENT_DIM}; border: 1px solid {theme.ACCENT};
    color: {theme.ACCENT}; font-family: {theme.FONT_FAMILY}; }}
"""


class KeyButton(QPushButton):
    """Shows one key; click it, then press a new key combination to rebind."""

    captured = Signal(str)     # new key text, or "" to clear

    def __init__(self, key: str = ""):
        self.capturing = False   # set before super(): Qt may deliver events during construction
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(104)
        self.setStyleSheet(KEY_STYLE)
        self.set_key(key)
        self.clicked.connect(self._start)

    def set_key(self, key: str) -> None:
        self.key = key
        self.setText(display(key) if key else "+ add")
        self.setToolTip("Click, then press a key. Backspace removes it, Esc cancels."
                        if key else "Click, then press a key to add a shortcut")
        self._prop("empty", not key)

    def _prop(self, name: str, value: bool) -> None:
        self.setProperty(name, value)
        self.style().unpolish(self)
        self.style().polish(self)

    def _start(self) -> None:
        self.capturing = True
        self.setText("Press a key…")
        self._prop("capturing", True)
        self.setFocus()
        self.grabKeyboard()

    def _stop(self) -> None:
        if self.capturing:
            self.capturing = False
            self.releaseKeyboard()
            self._prop("capturing", False)
            self.set_key(self.key)

    def event(self, e) -> bool:  # noqa: N802 - catch Tab before focus navigation does
        if getattr(self, "capturing", False) and e.type() == QEvent.Type.KeyPress:
            self.keyPressEvent(e)
            return True
        if getattr(self, "capturing", False) and e.type() == QEvent.Type.ShortcutOverride:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if not self.capturing:
            return super().keyPressEvent(e)
        key = Qt.Key(e.key())
        mods = e.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if key in _MODIFIER_KEYS:
            return
        if key == Qt.Key.Key_Escape and not mods:
            self._stop()
            return
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and not mods:
            self._stop()
            self.captured.emit("")
            return
        if key == Qt.Key.Key_Backtab:
            key = Qt.Key.Key_Tab
        seq = QKeySequence(QKeyCombination(mods, key)).toString(QKeySequence.SequenceFormat.PortableText)
        self._stop()
        self.captured.emit(seq)

    def focusOutEvent(self, e) -> None:  # noqa: N802
        self._stop()
        super().focusOutEvent(e)


class ShortcutsDialog(QDialog):
    def __init__(self, keymap: Keymap, parent: QWidget | None = None):
        super().__init__(parent)
        self.keymap = keymap
        self.setWindowTitle("Keyboard shortcuts")
        self.resize(640, 680)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(6)
        title = QLabel("Keyboard shortcuts")
        title.setProperty("role", "title")
        sub = QLabel("Click any key to change it, then press the new key. Backspace removes a key; "
                     "Esc cancels. Each action can have up to three keys.")
        sub.setProperty("role", "note")
        sub.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        body = QWidget()
        body.setObjectName("ShortcutBody")
        body.setStyleSheet("#ShortcutBody { background: transparent; }")
        grid = QGridLayout(body)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setContentsMargins(0, 0, 8, 0)
        self.buttons: dict[str, list[KeyButton]] = {}
        row, group = 0, None
        for a in ACTIONS:
            if a.group != group:
                group = a.group
                sec = QLabel(group.upper())
                sec.setProperty("role", "section")
                grid.addWidget(sec, row, 0, 1, MAX_KEYS + 1)
                row += 1
            grid.addWidget(QLabel(a.label), row, 0)
            btns = []
            for slot in range(MAX_KEYS):
                b = KeyButton()
                b.captured.connect(lambda k, aid=a.id, s=slot: self._assign(aid, s, k))
                grid.addWidget(b, row, slot + 1)
                btns.append(b)
            self.buttons[a.id] = btns
            row += 1
        grid.setColumnStretch(0, 1)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)

        mouse = QLabel("Mouse: drag a handle with Shift held for fine adjustment · scroll over the "
                       "timeline to step frames (Shift: 10 frames) · scroll over the speaker to change volume")
        mouse.setProperty("role", "note")
        mouse.setWordWrap(True)
        lay.addWidget(mouse)
        self.status = QLabel()
        self.status.setStyleSheet(f"color: {theme.WARNING};")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        buttons = QHBoxLayout()
        self.reset_btn = QPushButton("Reset to defaults")
        self.reset_btn.clicked.connect(self._reset)
        done = QPushButton("Done")
        done.setProperty("kind", "primary")
        done.setAutoDefault(False)
        self.reset_btn.setAutoDefault(False)
        done.clicked.connect(self.accept)
        buttons.addWidget(self.reset_btn)
        buttons.addStretch(1)
        buttons.addWidget(done)
        lay.addLayout(buttons)
        self._refresh()

    def _refresh(self) -> None:
        for aid, btns in self.buttons.items():
            keys = self.keymap.keys(aid)
            for i, b in enumerate(btns):
                b.set_key(keys[i] if i < len(keys) else "")
                # Show only one empty "+ add" slot after the last key.
                b.setVisible(i <= len(keys))
        self.reset_btn.setEnabled(not self.keymap.is_default())

    def _assign(self, action_id: str, slot: int, key: str) -> None:
        keys = self.keymap.keys(action_id)
        if key:
            if key in keys:
                self.status.setText("")
                return
            if slot < len(keys):
                keys[slot] = key
            else:
                keys.append(key)
        elif slot < len(keys):
            keys.pop(slot)
        lost = self.keymap.set_keys(action_id, keys)
        if lost:
            self.status.setText(f"“{display(key)}” was moved here from "
                                f"“{BY_ID[lost[0]].label}”.")
        else:
            self.status.setText("")
        self._refresh()

    def _reset(self) -> None:
        self.keymap.reset()
        self.status.setText("All shortcuts restored to their defaults.")
        self._refresh()
