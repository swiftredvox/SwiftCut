"""Drive the real main window: open a video, trim with the model, export through a patched Save dialog."""
from __future__ import annotations

import os

import pytest

from ..conftest import frame_numbers, numbered_video, requires_ffmpeg
from ..ui_helpers import show_in_background, trigger_key

pytestmark = requires_ffmpeg


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    from swiftcut.ui import theme
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


def wait_for(qapp, cond, timeout=15.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def window(qapp, tmp_path):
    from swiftcut.ui.main_window import MainWindow
    from swiftcut.ui.settings import Settings
    settings = Settings("SwiftCutTests")
    settings.clear()
    w = MainWindow(settings, str(tmp_path))
    show_in_background(w)
    yield w
    if w.export_job is not None:                 # never leave an export running (it would prompt)
        w.export_job.cancel()
        wait_for(qapp, lambda: w.export_job is None, 10)
    w.close()
    settings.clear()


def test_open_trim_save(window, qapp, media_dir, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.model.grid is not None and window.model.grid.exact)
    assert window.audio_wrap.isVisibleTo(window)
    assert window.save_btn.isEnabled()

    # Mode switches must keep the file and selection.
    window.model.set_start_index(12)
    window.model.set_end_index(47)
    window.mode.setIndex(1, emit=True)
    window.mode.setIndex(0, emit=True)
    assert (window.model.start_idx, window.model.end_idx) == (12, 47)
    assert window.start_field.edit.text() == "0:00.400"

    dest = tmp_path / "result.mp4"
    seen = {}

    def fake_dialog(parent, caption, suggested, filters):
        seen["suggested"] = suggested
        return str(dest), filters.split(";;")[0]
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake_dialog))
    window.save(copy=False)
    assert os.path.basename(seen["suggested"]) == "cfr30.mp4"
    assert os.path.dirname(seen["suggested"]) == str(media_dir)
    assert wait_for(qapp, lambda: window.export_job is None, 30)
    assert frame_numbers(dest) == [n % 64 for n in range(12, 48)]


def test_save_copy_suggests_trimmed_name(window, qapp, media_dir, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.model.loaded)
    seen = {}

    def fake_dialog(parent, caption, suggested, filters):
        seen["s"] = suggested
        return "", ""          # user cancels the dialog
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake_dialog))
    window.save(copy=True)
    assert window.export_job is None
    assert os.path.basename(seen["s"]) == "cfr30_trimmed.mp4"


def test_bad_file_shows_error(window, qapp, tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_text("nope")
    window.open_file(str(bad))
    assert wait_for(qapp, lambda: "Couldn't open" in window.empty.title.text())
    assert not window.save_btn.isEnabled()
    assert window.info is None


def test_keyboard_marks(window, qapp, media_dir):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.model.grid is not None and window.model.grid.exact and window.player.ready)
    window.player.seek_frame(20)
    window.mark_in()
    window.player.seek_frame(70)
    window.mark_out()
    assert (window.model.start_idx, window.model.end_idx) == (20, 70)
    window.reset_selection()
    assert window.model.is_full


def _shown_frame_number(window) -> int | None:
    """Frame number (brightness-encoded) of the frame the preview is currently displaying."""
    frame = window.video.videoSink().videoFrame()
    if not frame.isValid():
        return None
    img = frame.toImage().scaled(8, 8).convertToFormat(frame.toImage().Format.Format_Grayscale8)
    total = sum(img.pixelColor(x, y).red() for x in range(8) for y in range(8))
    # Qt converts limited-range video to full range: undo that to recover the encoded level.
    level = (total / 64) * 219 / 255 + 16
    return round(level / 4) % 64


@pytest.mark.parametrize("kind", ["mp4_bframes", "mkv_audio_leads", pytest.param("mpegts_offset", marks=pytest.mark.xfail(
    reason="Qt Multimedia seeks MPEG-TS inaccurately (asked for 1.0 s it shows 2.13 s); exports are unaffected",
    strict=False))])
def test_preview_shows_the_exact_frame(window, qapp, media_dir, kind):
    from ..conftest import ffmpeg
    src = numbered_video(media_dir / "preview.mp4", fps=30, seconds=4)
    if kind == "mkv_audio_leads":
        # AAC priming makes the audio start ~21 ms before the first video frame in MKV.
        src = numbered_video(media_dir / "cfr30_audio.mkv", fps=30, seconds=4, audio=1)
    if kind == "mpegts_offset":
        ts = media_dir / "preview.ts"
        if not ts.exists():
            ffmpeg("-i", str(src), "-c", "copy", "-output_ts_offset", "1.4", str(ts))
        src = ts
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.model.grid is not None and window.model.grid.exact
                    and window.player.ready and window.stack.currentWidget() is window.video)
    # Levels below 16 or above 235 clip in YUV->RGB, so use frame numbers whose level is in range.
    for k in (5, 6, 29, 30, 31, 70, 90, 100, 6):
        window.player.seek_frame(k)
        assert wait_for(qapp, lambda: _shown_frame_number(window) == k % 64, 5), \
            f"{kind}: expected frame {k}, showing {_shown_frame_number(window)}"


def test_wsad_keys_drive_the_app(window, qapp, media_dir):
    """Every WSAD key is bound and does its job (shortcuts fired directly - no focus needed)."""
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.model.grid is not None and window.model.grid.exact and window.player.ready)

    def press(key):
        assert trigger_key(window, key), f"no shortcut bound to {key}"
        for _ in range(20):
            qapp.processEvents()

    window.player.seek_frame(30)
    wait_for(qapp, lambda: window.player.current_index() == 30)
    press("D")
    assert wait_for(qapp, lambda: window.player.current_index() == 31)
    press("A")
    assert wait_for(qapp, lambda: window.player.current_index() == 30)
    press("Q")
    assert window.model.start_idx == 30
    press("Shift+D")                     # +1 s
    pl = window.player
    assert wait_for(qapp, lambda: pl.current_index() == 60), (
        f"index {pl.current_index()} time {pl.time:.4f} target {pl._target} pending {pl._pending_seek} "
        f"in_flight {pl._seek_in_flight} playing {pl.playing} ready {pl.ready}")
    press("E")
    assert window.model.end_idx == 60
    loop = window.loop_btn.isChecked()
    press("W")
    assert window.loop_btn.isChecked() != loop
    audio = window.audio_ctl.is_on()
    press("S")
    assert window.audio_ctl.is_on() != audio
    press("Ctrl+A")                      # -10 s clamps to 0
    assert wait_for(qapp, lambda: window.player.current_index() == 0)


def test_volume_and_preview_audio_are_independent_of_export_audio(window, qapp, media_dir):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.model.loaded)
    window.audio_ctl.set_volume(0.5)
    window.audio_ctl.nudge_volume(10)
    assert abs(window.player.volume() - 0.6) < 0.01
    window.toggle_preview_audio()
    assert window.player.is_muted()
    assert window.current_options().mute is False        # export still includes audio
    window.audio_switch.setChecked(False)
    assert window.current_options().mute is True
    window.audio_ctl.nudge_volume(5)                      # moving volume turns preview audio back on
    assert not window.player.is_muted()


def test_output_device_choice_persists(window, qapp):
    devices = window.player.output_devices()
    if not devices:
        pytest.skip("no audio output devices")
    dev = devices[-1]
    window._set_device(bytes(dev.id()))
    assert bytes(window.player.current_device().id()) == bytes(dev.id())
    assert window.settings.audio_device == bytes(dev.id())
    window._set_device(None)
    assert window.settings.audio_device is None


def test_shortcuts_dialog_rebinds_keys(qapp, tmp_path):
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtTest import QTest
    from swiftcut.ui.keymap import Keymap
    from swiftcut.ui.shortcuts_dialog import ShortcutsDialog
    km = Keymap(QSettings(str(tmp_path / "k.ini"), QSettings.Format.IniFormat))
    dlg = ShortcutsDialog(km)
    show_in_background(dlg, 640, 680)
    btn = dlg.buttons["reset"][1]          # the "+ add" slot after R
    assert btn.isVisible() and btn.text() == "+ add"
    btn.click()
    assert btn.capturing
    QTest.keyClick(btn, Qt.Key.Key_A)      # A currently means "previous frame"
    assert km.keys("reset") == ["R", "A"]
    assert km.keys("frame_back") == ["Left"]
    assert "moved here" in dlg.status.text()
    first = dlg.buttons["reset"][1]
    first.click()
    QTest.keyClick(first, Qt.Key.Key_Backspace)   # remove it again
    assert km.keys("reset") == ["R"]
    dlg.reset_btn.click()
    assert km.is_default()
    dlg.close()


def test_closing_while_loading_never_freezes(qapp, media_dir, tmp_path):
    """Regression: releasing the player mid-load used to re-enter Qt and deadlock."""
    from swiftcut.ui.main_window import MainWindow
    from swiftcut.ui.settings import Settings
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    settings = Settings("SwiftCutTests")
    for delay_ms in (0, 0, 5, 20, 50, 100, 200, 400):
        w = MainWindow(settings, str(tmp_path))
        show_in_background(w)
        w.open_file(str(src))
        if delay_ms:
            wait_for(qapp, lambda: False, delay_ms / 1000)
        w.close()                       # releases the player at varying points of loading
        w.deleteLater()
        qapp.processEvents()
    settings.clear()


def test_reopening_quickly_never_freezes(window, qapp, media_dir):
    a = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    b = numbered_video(media_dir / "cfr30_audio.mkv", fps=30, seconds=4, audio=1)
    for i in range(8):
        window.open_file(str(a if i % 2 else b))
        wait_for(qapp, lambda: False, 0.03 * i)
    assert wait_for(qapp, lambda: window.model.grid is not None and window.model.grid.exact
                    and window.player.ready)


def test_audio_track_toggles_drive_the_preview(window, qapp, media_dir):
    src = numbered_video(media_dir / "two_tracks.mkv", seconds=2, audio=2)
    window.mode.setIndex(1, emit=True)
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.player.ready and len(window.panel.track_checks) == 2)
    wait_for(qapp, lambda: len(window.player._player.audioTracks()) == 2, 5)
    first, second = (cb for _i, cb in window.panel.track_checks)
    assert window.player.audible() and window.player.active_track() == 0
    assert "Previewing Track 1 only" in window.panel.audio_note.text()
    first.setChecked(False)                                  # only track 2 left
    assert wait_for(qapp, lambda: window.player.active_track() == 1, 3)
    assert window.player.audible()
    second.setChecked(False)                                 # nothing will be saved -> silent
    assert not window.player.audible()
    first.setChecked(True)
    assert wait_for(qapp, lambda: window.player.active_track() == 0, 3) and window.player.audible()
    window.audio_switch.setChecked(False)                    # "Include audio" off -> silent too
    assert not window.player.audible()
    window.audio_switch.setChecked(True)
    assert window.player.audible()
    window.toggle_preview_audio()                            # user's own preview mute still works
    assert not window.player.audible()
    window.toggle_preview_audio()
    window.mode.setIndex(0, emit=True)


def _drag_events(widget, path):
    from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    pos = QPoint(widget.width() // 2, widget.height() // 2)
    enter = QDragEnterEvent(pos, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    drop = QDropEvent(QPointF(pos), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier)
    return mime, enter, drop


def _video_surface(window):
    from PySide6.QtWidgets import QWidget
    kids = window.video.findChildren(QWidget)
    return kids[0] if kids else window.video


@pytest.mark.parametrize("answer", ["yes", "no", "untrimmed"])
def test_dropping_onto_the_video_replaces_it(window, qapp, media_dir, monkeypatch, answer):
    from PySide6.QtWidgets import QApplication, QMessageBox
    a = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    b = numbered_video(media_dir / "cfr30_audio.mkv", fps=30, seconds=4, audio=1)
    window.open_file(str(a))
    assert wait_for(qapp, lambda: window.model.grid is not None and window.model.grid.exact
                    and window.stack.currentWidget() is window.video)
    if answer != "untrimmed":
        window.model.set_start_index(10)
    asked = []
    reply = QMessageBox.StandardButton.Yes if answer == "yes" else QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "exec", lambda self: asked.append(self.text()) or reply)
    target = _video_surface(window)          # the inner surface that used to swallow drops
    _mime, enter, drop = _drag_events(target, b)
    QApplication.sendEvent(target, enter)
    assert enter.isAccepted(), "drag over the video must be accepted"
    QApplication.sendEvent(window.stack.currentWidget(), drop)
    wait_for(qapp, lambda: False, 0.3)
    if answer == "no":
        assert asked == ["Replace the current video?"]
        assert window.info.path.endswith("cfr30.mp4") and window.model.start_idx == 10
        assert wait_for(qapp, lambda: window.stack.currentWidget() is window.video, 2)
    else:
        assert asked == ([] if answer == "untrimmed" else ["Replace the current video?"])
        assert wait_for(qapp, lambda: window.info is not None and window.info.path.endswith("cfr30_audio.mkv"))


def test_bottom_bar_never_clips_text(window, qapp, media_dir):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.model.loaded)
    for width in (1600, 1180, 1000, 900, 700):          # 700 is below the minimum: clamps
        window.resize(width, 760)
        wait_for(qapp, lambda: False, 0.1)
        assert window.width() >= window.minimumWidth()
        bar = window.bottom_bar
        assert bar.layout().minimumSize().width() <= bar.width(), f"bar overflows at {width}px"
        if window.save_btn.text():
            fm = window.save_btn.fontMetrics()
            assert window.save_btn.width() >= fm.horizontalAdvance(window.save_btn.text()), \
                f"save text clipped at {width}px"
        print(width, "->", window.width(), "summary" if window.summary_label.isVisible() else "-",
              repr(window.save_btn.text().strip()) or "icon")
    assert window.save_btn.isEnabled()
    window.resize(1600, 900)
    wait_for(qapp, lambda: False, 0.1)
    assert window.save_btn.text().strip() == "Save trimmed video"
    assert window.summary_label.isVisible()


def test_undo_redo_in_window(window, qapp, media_dir):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.model.grid is not None and window.model.grid.exact
                    and window.player.ready)
    window.player.seek_frame(20)
    assert wait_for(qapp, lambda: window.player.current_index() == 20)
    window.mark_in()
    for _ in range(5):                         # a burst of nudges = one undo step
        window._nudge("end", -1)
    wait_for(qapp, lambda: not window._commit_timer.isActive(), 3)
    assert (window.model.start_idx, window.model.end_idx) == (20, window.model.grid.last_index - 5)
    assert trigger_key(window, "Ctrl+Z")
    assert window.model.end_idx == window.model.grid.last_index and window.model.start_idx == 20
    assert trigger_key(window, "Ctrl+Z")
    assert window.model.is_full
    assert trigger_key(window, "Ctrl+Y")
    assert window.model.start_idx == 20
    assert trigger_key(window, "Ctrl+Shift+Z")
    assert window.model.end_idx == window.model.grid.last_index - 5


def test_instance_message_opens_file(window, qapp, media_dir, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    a = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    b = numbered_video(media_dir / "cfr30_audio.mkv", fps=30, seconds=4, audio=1)
    monkeypatch.setattr(window, "activateWindow", lambda: None)   # never steal focus in tests
    monkeypatch.setattr(window, "raise_", lambda: None)
    window.open_file(str(a))
    assert wait_for(qapp, lambda: window.model.loaded)
    window.model.set_start_index(4)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    window.handle_instance_message({"open": str(b)})
    assert wait_for(qapp, lambda: window.info is not None and window.info.path.endswith("cfr30_audio.mkv"))


def test_suggested_trim_key(window, qapp, media_dir):
    from ..conftest import ffmpeg
    src = media_dir / "still_ends.mp4"
    if not src.exists():
        ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=2,tpad=start_mode=clone:start_duration=1"
               ":stop_mode=clone:stop_duration=1.5", "-c:v", "libx264", "-preset", "ultrafast",
               "-pix_fmt", "yuv420p", str(src))
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.suggestion is not None)
    assert len(window.timeline.suggestions) == 2
    assert trigger_key(window, "T")
    assert window.model.start_time == pytest.approx(1.0, abs=0.04)
    assert window.model.end_time == pytest.approx(3.0, abs=0.04)
    assert trigger_key(window, "Ctrl+Z")
    assert window.model.is_full


def test_no_clipped_button_text_anywhere(window, qapp, media_dir, tmp_path):
    from swiftcut.ui.settings_dialog import SettingsDialog
    from swiftcut.ui.shortcuts_dialog import ShortcutsDialog
    from ..ui_helpers import clipped_buttons
    window.open_file(str(numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)))
    assert wait_for(qapp, lambda: window.model.loaded)
    for width in (1600, 1100, 900, 700):
        window.resize(width, 760)
        wait_for(qapp, lambda: False, 0.1)
        assert clipped_buttons(window) == [], f"at {width}px"
    settings = SettingsDialog(str(tmp_path), window.keymap, window)
    show_in_background(settings, 300, 200)                 # deliberately too small
    wait_for(qapp, lambda: False, 0.1)
    assert clipped_buttons(settings) == []
    settings._clear_logs()                                 # its label changes after clicking
    wait_for(qapp, lambda: False, 0.1)
    assert clipped_buttons(settings) == []
    settings.close()
    keys = ShortcutsDialog(window.keymap, window)
    show_in_background(keys, 640, 680)
    wait_for(qapp, lambda: False, 0.1)
    assert clipped_buttons(keys) == []
    keys.close()


def _hover(timeline, x):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from swiftcut.ui.timeline import TRACK_H, TRACK_TOP
    pos = QPointF(x, TRACK_TOP + TRACK_H / 2)
    ev = QMouseEvent(QMouseEvent.Type.MouseMove, pos, timeline.mapToGlobal(pos), Qt.MouseButton.NoButton,
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(timeline, ev)


def test_hover_preview_shows_exact_frame(window, qapp, media_dir):
    from PySide6.QtGui import QImage
    src = numbered_video(media_dir / "preview.mp4", fps=30, seconds=4)
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.model.grid is not None and window.model.grid.exact)
    wait_for(qapp, lambda: len(window.timeline.thumbs) > 10, 5)
    tl, card = window.timeline, window.hover.card
    target = 50                                            # hover over frame 50
    x = tl._x(window.model.grid.times[target] + 0.001)
    _hover(tl, x)
    assert card.isVisible()
    assert "frame 51" in card._label                      # readout counts from 1
    assert wait_for(qapp, lambda: card._exact, 10), "exact frame never arrived"
    img = card._img.scaled(8, 8).convertToFormat(QImage.Format.Format_Grayscale8)
    level = sum(img.pixelColor(i, j).red() for i in range(8) for j in range(8)) / 64
    assert round((level * 219 / 255 + 16) / 4) % 64 == target   # it's really frame 50
    # The card floats just above the timeline (it's a tooltip-style window, in global coordinates)
    assert card.isWindow()
    assert card.geometry().bottom() <= tl.mapToGlobal(tl.rect().topLeft()).y() + 8
    tl.leaveEvent(None)
    assert not card.isVisible()
    window.set_hover_preview(False)
    _hover(tl, x)
    assert not card.isVisible()
    assert window.settings.hover_preview is False
    window.set_hover_preview(True)


def test_size_limit_controls(window, qapp, media_dir):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    window.mode.setIndex(1, emit=True)
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.model.grid is not None and window.model.grid.exact)
    panel = window.panel
    panel._set_size_limit(10.0)
    panel._changed()
    o = window.current_options()
    assert o.size_limit_mb == 10.0
    assert "Under 10 MB" in window.summary_label.text()
    assert not panel.quality.isEnabled() and not panel.encoder.isEnabled()
    assert "good quality" in panel.size_note.text()
    panel._set_size_limit(0.05)                          # far too small for 4 s
    panel._changed()
    assert "too long" in panel.size_note.text()
    assert window.settings.export_options().size_limit_mb == 0.05
    panel._set_size_limit(None)
    panel._changed()
    assert window.current_options().size_limit_mb is None and panel.quality.isEnabled()
    window.mode.setIndex(0, emit=True)
    assert window.current_options().size_limit_mb is None   # Basic ignores Advanced-only choices


def test_size_estimate(window, qapp, media_dir):
    from swiftcut.core.export_plan import Quality
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    window.mode.setIndex(1, emit=True)
    window.panel._set_size_limit(None)
    window.panel._changed()
    window.open_file(str(src))
    assert wait_for(qapp, lambda: "about" in window.panel.estimate_note.text(), 20)
    assert window.panel.estimate_note.isVisibleTo(window.panel)
    assert " · ~" in window.summary_label.text()
    full = window.panel.estimate_note.text()
    window.model.set_end_index(window.model.grid.last_index // 4)   # a quarter of the length
    smaller = window.panel.estimate_note.text()
    assert smaller != full                                          # updates instantly, no re-measure
    window.panel._set_size_limit(10.0)
    window.panel._changed()
    assert not window.panel.estimate_note.isVisibleTo(window.panel)  # a size limit replaces it
    assert " · ~" not in window.summary_label.text()
    window.panel._set_size_limit(None)
    window.panel._select(window.panel.quality, Quality.SMALL.value)
    window.panel._changed()                                          # new settings: measured again
    assert wait_for(qapp, lambda: "about" in window.panel.estimate_note.text(), 20)
    window.panel._select(window.panel.quality, Quality.HIGH.value)
    window.panel._changed()
    window.mode.setIndex(0, emit=True)


def test_fast_trim_is_not_offered(window):
    from PySide6.QtWidgets import QLabel
    texts = " ".join(l.text() for l in window.panel.findChildren(QLabel))
    assert "TRIM METHOD" not in texts.upper() and "Fast" not in texts
    assert window.current_options().method.value == "precise"


# -- time boxes (start / end) -----------------------------------------------------------------
def _loaded(window, qapp, media_dir):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.model.grid is not None and window.model.grid.exact
                    and window.player.ready)


def test_time_box_shows_live_value_and_applies_only_on_return(window, qapp, media_dir):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    _loaded(window, qapp, media_dir)
    box = window.start_field
    window.model.set_start_index(30)
    assert box.edit.text() == "0:01.000"                      # follows the handle
    QTest.keyClicks(box.edit, "2")                            # start typing: now an edit in progress
    box.edit.setText("0:02.000")
    box._typed("")
    window.model.set_start_index(31)                          # handle moves meanwhile...
    assert box.edit.text() == "0:02.000"                      # ...typing isn't overwritten
    QTest.keyClick(box.edit, Qt.Key.Key_Return)               # Return applies
    assert window.model.start_time == pytest.approx(2.0)
    assert not box.editing and box.edit.text() == "0:02.000"
    window.model.set_start_index(45)
    assert box.edit.text() == "0:01.500"                      # live again after applying


def test_time_box_cancels_on_escape_and_click_away(window, qapp, media_dir):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    _loaded(window, qapp, media_dir)
    box = window.end_field
    before = window.model.end_idx
    box.edit.setText("0:00.500"); box._typed("")
    QTest.keyClick(box.edit, Qt.Key.Key_Escape)               # Esc discards
    assert window.model.end_idx == before and not box.editing
    box.edit.setText("0:00.500"); box._typed("")
    click = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window.timeline, click)            # clicking elsewhere discards too
    assert not box.editing and window.model.end_idx == before
    assert box.edit.text() == window.end_field.edit.text()


def test_time_box_rejects_spaces_and_letters_and_lets_space_play(window, qapp, media_dir):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    _loaded(window, qapp, media_dir)
    edit = window.start_field.edit
    edit.clear()
    QTest.keyClicks(edit, "1 a:2x3")
    assert edit.text() == "1:23"                              # only digits and separators
    override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ")
    QApplication.sendEvent(edit, override)
    assert not override.isAccepted()                          # Space goes to play/pause instead
    window.start_field.cancel_edit()


def test_nudge_moves_playhead_and_it_stays(window, qapp, media_dir):
    _loaded(window, qapp, media_dir)
    g = window.model.grid
    window.model.set_end_index(int(len(g) * 0.7))
    window.player.seek_frame(int(len(g) * 0.9))
    assert wait_for(qapp, lambda: window.player.current_index() == int(len(g) * 0.9))
    window.end_field.edit.setText("0:00.100"); window.end_field._typed("")   # a stale half-edit
    window.end_field.fwd.click()                              # nudging cancels it, then moves
    target = window.model.end_idx
    assert target == int(len(g) * 0.7) + 1
    assert wait_for(qapp, lambda: window.player.current_index() == target)
    wait_for(qapp, lambda: False, 1.0)                        # and it stays put (no flash back)
    assert window.player.current_index() == target and window.model.end_idx == target
    for _ in range(25):                                       # hold the arrow: rapid nudges
        window.end_field.fwd.click()
        wait_for(qapp, lambda: False, 0.03)
    assert wait_for(qapp, lambda: window.player.current_index() == window.model.end_idx, 5)
    vf = window.video.videoSink().videoFrame()
    assert vf.isValid()                                       # picture present, not black
    window.player.seek_frame(len(g) - 5)                      # preview past the end still works
    assert wait_for(qapp, lambda: window.player.current_index() == len(g) - 5, 5)


def test_seek_watchdog_retries_when_no_frame(window, qapp, media_dir, monkeypatch):
    _loaded(window, qapp, media_dir)
    p = window.player
    calls = []
    monkeypatch.setattr(p._player, "setPosition", lambda ms: calls.append(ms))
    p._last_seek_ms, p._seek_retries = 2500, 0                # pretend a seek to 2.5 s never arrived
    p._seek_stalled()
    assert calls == [2500] and p._seek_retries == 1
    p._seek_stalled(); p._seek_stalled()
    assert len(calls) == 2                                    # gives up after two retries
