"""Customisable keyboard shortcuts.

Each action has an ordered list of key sequences. Defaults include two styles side by side:
classic editor keys (arrows, I/O, Space) and a left-hand WSAD set (A/D step, Q/E mark, W loop,
S audio). Users can change any of them in the Keyboard Shortcuts dialog; only differences from
the defaults are stored (HKCU\\Software\\SwiftCut\\SwiftCut\\keys).
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QKeySequence

MAX_KEYS = 3


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    group: str
    defaults: tuple[str, ...]


ACTIONS: list[Action] = [
    Action("play_pause", "Play / pause the selection", "Playback", ("Space", "K")),
    Action("frame_back", "Previous frame", "Playback", ("Left", "A")),
    Action("frame_fwd", "Next frame", "Playback", ("Right", "D")),
    Action("back_1s", "Back 1 second", "Playback", ("Shift+Left", "Shift+A")),
    Action("fwd_1s", "Forward 1 second", "Playback", ("Shift+Right", "Shift+D")),
    Action("back_10s", "Back 10 seconds", "Playback", ("Ctrl+Left", "Ctrl+A")),
    Action("fwd_10s", "Forward 10 seconds", "Playback", ("Ctrl+Right", "Ctrl+D")),
    Action("go_start", "Jump to selection start", "Playback", ("Home",)),
    Action("go_end", "Jump to selection end", "Playback", ("End",)),
    Action("loop", "Loop the selection on / off", "Playback", ("L", "W")),
    Action("zoom_in", "Zoom the timeline in", "Timeline", ("=", "Ctrl+=")),
    Action("zoom_out", "Zoom the timeline out", "Timeline", ("-", "Ctrl+-")),
    Action("zoom_fit", "Show the whole video", "Timeline", ("0", "Ctrl+0")),
    Action("mark_in", "Set start at playhead", "Trimming", ("I", "[", "Q")),
    Action("mark_out", "Set end at playhead (keeps that frame)", "Trimming", ("O", "]", "E")),
    Action("reset", "Reset selection to the whole video", "Trimming", ("R",)),
    Action("undo", "Undo trim change", "Trimming", ("Ctrl+Z",)),
    Action("redo", "Redo trim change", "Trimming", ("Ctrl+Y", "Ctrl+Shift+Z")),
    Action("suggest", "Apply suggested trim (skip still start / end)", "Trimming", ("T",)),
    Action("snapshot", "Save current frame as PNG", "File", ("P",)),
    Action("audio", "Preview audio on / off", "Audio", ("M", "S")),
    Action("vol_up", "Volume up", "Audio", ("Up",)),
    Action("vol_down", "Volume down", "Audio", ("Down",)),
    Action("open", "Open a video", "File", ("Ctrl+O",)),
    Action("save", "Save trimmed video", "File", ("Ctrl+S",)),
    Action("save_copy", "Save as a copy (name_trimmed)", "File", ("Ctrl+Shift+S",)),
    Action("save_animation", "Save as GIF / WebP animation", "File", ("Ctrl+G",)),
    Action("toggle_mode", "Switch Basic / Advanced", "Window", ("Ctrl+Tab",)),
    Action("shortcuts", "Show keyboard shortcuts", "Window", ("F1",)),
]
BY_ID = {a.id: a for a in ACTIONS}


def normalise(key: str) -> str:
    """Canonical text form, e.g. 'shift+a' -> 'Shift+A'."""
    return QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText)


def display(key: str) -> str:
    """Text for showing a key to the user ('Left' -> '←')."""
    text = QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText)
    for name, sym in (("Left", "←"), ("Right", "→"), ("Up", "↑"), ("Down", "↓")):
        if text == name or text.endswith("+" + name):
            text = text[: len(text) - len(name)] + sym
    return text


class Keymap(QObject):
    changed = Signal()

    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self._s = settings
        self._keys: dict[str, list[str]] = {a.id: list(a.defaults) for a in ACTIONS}
        if settings is not None:
            settings.beginGroup("keys")
            for a in ACTIONS:
                v = settings.value(a.id)
                if v is not None:
                    keys = [normalise(k) for k in str(v).split("\t") if k]
                    self._keys[a.id] = keys[:MAX_KEYS]
            settings.endGroup()

    def keys(self, action_id: str) -> list[str]:
        return list(self._keys[action_id])

    def hint(self, action_id: str, limit: int = 2) -> str:
        """'← or A' for tooltips."""
        ks = [display(k) for k in self._keys[action_id][:limit]]
        return " or ".join(ks)

    def owner(self, key: str) -> str | None:
        key = normalise(key)
        for aid, ks in self._keys.items():
            if key in ks:
                return aid
        return None

    def set_keys(self, action_id: str, keys: list[str]) -> list[str]:
        """Assign keys to an action. A key already used elsewhere is moved here.

        Returns the ids of actions that lost a key.
        """
        keys = [normalise(k) for k in keys if k][:MAX_KEYS]
        lost = []
        for k in keys:
            other = self.owner(k)
            if other and other != action_id:
                self._keys[other].remove(k)
                lost.append(other)
                self._save(other)
        self._keys[action_id] = keys
        self._save(action_id)
        self.changed.emit()
        return lost

    def reset(self) -> None:
        self._keys = {a.id: list(a.defaults) for a in ACTIONS}
        if self._s is not None:
            self._s.remove("keys")
        self.changed.emit()

    def is_default(self) -> bool:
        return all(self._keys[a.id] == [normalise(k) for k in a.defaults] for a in ACTIONS)

    def _save(self, action_id: str) -> None:
        if self._s is None:
            return
        key = f"keys/{action_id}"
        if self._keys[action_id] == [normalise(k) for k in BY_ID[action_id].defaults]:
            self._s.remove(key)
        else:
            self._s.setValue(key, "\t".join(self._keys[action_id]))
