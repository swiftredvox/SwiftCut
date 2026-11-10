"""The trim selection, stored as inclusive frame indices on the current FrameGrid."""
from __future__ import annotations

import bisect

from PySide6.QtCore import QObject, Signal

from ..core.timecode import FrameGrid


class TrimModel(QObject):
    changed = Signal()

    historyChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.grid: FrameGrid | None = None
        self.start_idx = 0
        self.end_idx = 0
        # Undo history stores (start_time, end_time): times survive the grid being replaced.
        self._undo: list[tuple[float, float]] = []
        self._redo: list[tuple[float, float]] = []
        self._committed: tuple[float, float] | None = None

    # -- grid -------------------------------------------------------------------------------
    def load(self, grid: FrameGrid) -> None:
        self.grid = grid
        self.start_idx, self.end_idx = 0, grid.last_index
        self._reset_history()
        self.changed.emit()

    def replace_grid(self, grid: FrameGrid) -> None:
        """Swap in exact timestamps (after scanning) while keeping the selection in place."""
        if self.grid is None:
            self.load(grid)
            return
        full = self.is_full
        st, en = self.start_time, self.end_time
        uncommitted = self._committed is not None and self._state() != self._committed
        self.grid = grid
        if full:
            self.start_idx, self.end_idx = 0, grid.last_index
        else:
            self.start_idx = grid.nearest_index(st)
            self.end_idx = max(self.start_idx, self._end_index_for(en))
        if not uncommitted:
            self._committed = self._state()   # re-snapping isn't a user edit
        self.changed.emit()

    def clear(self) -> None:
        self.grid = None
        self.start_idx = self.end_idx = 0
        self._reset_history()
        self.changed.emit()

    # -- undo / redo ------------------------------------------------------------------------
    def _state(self) -> tuple[float, float]:
        return (round(self.start_time, 6), round(self.end_time, 6))

    def _reset_history(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._committed = self._state() if self.grid else None
        self.historyChanged.emit()

    def commit(self) -> None:
        """Record the current selection as one undoable step (if it changed)."""
        if self.grid is None or self._committed is None:
            return
        cur = self._state()
        if cur != self._committed:
            self._undo.append(self._committed)
            del self._undo[:-200]
            self._redo.clear()
            self._committed = cur
            self.historyChanged.emit()

    def can_undo(self) -> bool:
        return bool(self._undo) or (self._committed is not None and self._state() != self._committed)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        self.commit()
        if not self._undo:
            return False
        self._redo.append(self._committed)
        self._restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        self.commit()
        if not self._redo:
            return False
        self._undo.append(self._committed)
        self._restore(self._redo.pop())
        return True

    def _restore(self, state: tuple[float, float]) -> None:
        st, en = state
        g = self.grid
        assert g is not None
        self.start_idx = g.nearest_index(st)
        self.end_idx = max(self.start_idx, self._end_index_for(en))
        self._committed = self._state()
        self.historyChanged.emit()
        self.changed.emit()

    # -- derived values -----------------------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self.grid is not None

    @property
    def total(self) -> float:
        return self.grid.end_time if self.grid else 0.0

    @property
    def start_time(self) -> float:
        return self.grid.times[self.start_idx] if self.grid else 0.0

    @property
    def end_time(self) -> float:
        return self.grid.frame_end(self.end_idx) if self.grid else 0.0

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def frame_count(self) -> int:
        return self.end_idx - self.start_idx + 1

    @property
    def is_full(self) -> bool:
        return self.grid is not None and self.start_idx == 0 and self.end_idx == self.grid.last_index

    # -- mutation ---------------------------------------------------------------------------
    def set_start_index(self, i: int) -> None:
        if not self.grid:
            return
        i = min(max(0, i), self.end_idx)
        if i != self.start_idx:
            self.start_idx = i
            self.changed.emit()

    def set_end_index(self, i: int) -> None:
        if not self.grid:
            return
        i = max(min(self.grid.last_index, i), self.start_idx)
        if i != self.end_idx:
            self.end_idx = i
            self.changed.emit()

    def set_start_time(self, t: float) -> None:
        if self.grid:
            self.set_start_index(self.grid.nearest_index(t))

    def set_end_time(self, t: float) -> None:
        if self.grid:
            self.set_end_index(self._end_index_for(t))

    def _end_index_for(self, t: float) -> int:
        """Last kept frame whose end boundary is nearest to time t."""
        g = self.grid
        assert g is not None
        bounds = g.times[1:] + [g.end_time]
        k = bisect.bisect_left(bounds, t)
        if k >= len(bounds):
            return g.last_index
        if k > 0 and (t - bounds[k - 1]) < (bounds[k] - t):
            k -= 1
        return k

    def reset(self) -> None:
        if self.grid:
            self.start_idx, self.end_idx = 0, self.grid.last_index
            self.changed.emit()
