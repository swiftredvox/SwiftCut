"""Timeline zoom (F7): view maths, interaction, follow-playhead and sharp zoomed filmstrips."""
from __future__ import annotations

import pytest

from ..conftest import numbered_video, requires_ffmpeg
from ..ui_helpers import show_in_background, trigger_key
from .test_ui_smoke import qapp, wait_for, window  # noqa: F401  (fixtures)

pytestmark = requires_ffmpeg


def _wheel(widget, x, dy, ctrl=False):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    pos = QPointF(x, 40)
    mods = Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier
    ev = QWheelEvent(pos, widget.mapToGlobal(pos), QPoint(0, 0), QPoint(0, dy), Qt.MouseButton.NoButton,
                     mods, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, ev)


def _mouse(widget, kind, x, y):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    types = {"press": QMouseEvent.Type.MouseButtonPress, "move": QMouseEvent.Type.MouseMove,
             "release": QMouseEvent.Type.MouseButtonRelease}
    btns = Qt.MouseButton.NoButton if kind == "release" else Qt.MouseButton.LeftButton
    pos = QPointF(x, y)
    QApplication.sendEvent(widget, QMouseEvent(types[kind], pos, widget.mapToGlobal(pos), Qt.MouseButton.LeftButton,
                                               btns, Qt.KeyboardModifier.NoModifier))


@pytest.fixture
def loaded(window, qapp, media_dir):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.model.grid is not None and window.model.grid.exact
                    and window.player.ready)
    return window


def test_zoom_keeps_point_under_mouse_and_has_limits(loaded):
    tl = loaded.timeline
    assert not tl.zoomed and not loaded.zoom_out_btn.isEnabled()
    anchor_t = 1.234
    x_before = tl._x(anchor_t)
    _wheel(tl, x_before, 120, ctrl=True)                 # Ctrl + wheel up = zoom in at the mouse
    assert tl.zoomed and tl.zoom_factor == pytest.approx(1.6, rel=0.01)
    assert tl._x(anchor_t) == pytest.approx(x_before, abs=1.0)
    assert loaded.zoom_out_btn.isEnabled()
    for _ in range(40):                                  # can't zoom forever
        tl.zoom_in(anchor_t)
    px_per_frame = tl._x(1 / 30) - tl._x(0)
    assert px_per_frame <= 30 and not tl.can_zoom_in()
    assert not loaded.zoom_in_btn.isEnabled()
    trigger_key(loaded, "0")                             # 0 = whole video
    assert not tl.zoomed and tl.visible() == (0.0, pytest.approx(loaded.model.total))


def test_zoom_keys_and_buttons(loaded):
    tl = loaded.timeline
    trigger_key(loaded, "=")
    assert tl.zoom_factor == pytest.approx(1.6, rel=0.01)
    loaded.zoom_in_btn.click()
    assert tl.zoom_factor == pytest.approx(2.56, rel=0.01)
    trigger_key(loaded, "-")
    loaded.zoom_out_btn.click()
    assert not tl.zoomed


def test_plain_wheel_still_steps_frames(loaded, qapp):
    tl = loaded.timeline
    loaded.player.seek_frame(20)
    assert wait_for(qapp, lambda: loaded.player.current_index() == 20)
    _wheel(tl, 300, -120)                                 # wheel down = next frame
    assert wait_for(qapp, lambda: loaded.player.current_index() == 21)
    assert not tl.zoomed


def test_view_follows_playhead_and_overview_pans(loaded, qapp):
    tl = loaded.timeline
    tl.set_view(0.0, 0.8)                                 # zoomed onto the first 0.8 s
    loaded.player.seek_frame(90)                          # 3.0 s: off screen
    assert wait_for(qapp, lambda: tl.visible()[0] <= 3.0 <= tl.visible()[1])
    ov = tl._overview()
    _mouse(tl, "press", ov.left() + ov.width() * 0.10, ov.center().y())   # click near the start
    _mouse(tl, "release", ov.left() + ov.width() * 0.10, ov.center().y())
    a, b = tl.visible()
    assert a <= 0.4 <= b                                  # window centred around 10% of 4 s


def test_handle_drag_is_precise_when_zoomed(loaded, qapp):
    from swiftcut.ui.timeline import TRACK_H, TRACK_TOP
    tl, g = loaded.timeline, loaded.model.grid
    tl.set_view(1.0, 0.5)                                 # 15 frames across the whole track
    loaded.model.set_start_index(31)                      # 1.033 s, on screen
    x0 = tl._x(loaded.model.start_time) - 6
    y = TRACK_TOP + TRACK_H / 2
    target = g.times[40]
    _mouse(tl, "press", x0, y)
    _mouse(tl, "move", tl._x(target) - 6, y)
    _mouse(tl, "release", tl._x(target) - 6, y)
    assert loaded.model.start_idx == 40                   # one frame is ~50 px here: exact


def test_zoomed_filmstrip_gets_exact_frames(loaded, qapp):
    from PySide6.QtGui import QImage
    tl, g = loaded.timeline, loaded.model.grid
    tl.set_view(1.0, 0.6)
    assert wait_for(qapp, lambda: len(tl.zoom_thumbs) >= 3, 15)
    i, img = next(iter(tl.zoom_thumbs.items()))
    small = img.scaled(8, 8).convertToFormat(QImage.Format.Format_Grayscale8)
    level = sum(small.pixelColor(a, b).red() for a in range(8) for b in range(8)) / 64
    if 4 <= i % 64 <= 58:                                 # readable brightness range
        assert round((level * 219 / 255 + 16) / 4) % 64 == i % 64
    assert all(g.index_at(1.0) - 2 <= k <= g.index_at(1.6) + 2 for k in tl.zoom_thumbs)


def test_new_file_resets_zoom(loaded, qapp, media_dir):
    tl = loaded.timeline
    tl.zoom_in()
    assert tl.zoomed
    loaded.open_file(str(numbered_video(media_dir / "cfr30_audio.mkv", fps=30, seconds=4, audio=1)))
    assert wait_for(qapp, lambda: loaded.model.grid is not None and loaded.model.grid.exact)
    assert not tl.zoomed and not tl.zoom_thumbs
