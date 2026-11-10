"""GIF / animated WebP export (F9)."""
from __future__ import annotations

import os

import pytest

from swiftcut.core import animation, exporter, ffmpeg_tools, media_info
from swiftcut.core.animation import AnimationOptions, build_animation_plan

from ..conftest import frame_numbers, numbered_video, requires_ffmpeg
from .test_ui_smoke import qapp, wait_for, window  # noqa: F401  (fixtures)

pytestmark = requires_ffmpeg


def _load(path):
    info = media_info.probe(str(path))
    return info, media_info.scan_frames(info)


def _export(src, out, first, last, **opts):
    info, grid = _load(src)
    plan = build_animation_plan(info, grid, grid.times[first], grid.frame_end(last), AnimationOptions(**opts),
                                str(out), ffmpeg_tools.find_ffmpeg().ffmpeg)
    return plan, exporter.run_export(plan)


def test_plan_shapes():
    from tests.unit.test_export_plan import GRID, make_info
    info = make_info(w=1920, h=1080)
    p = build_animation_plan(info, GRID, 1.0, 3.0, AnimationOptions(format="gif", width=960, fps=15), "o.gif")
    fc = p.args[p.args.index("-filter_complex") + 1]
    assert "fps=15" in fc and "scale=960:-1" in fc and "palettegen" in fc and "paletteuse" in fc
    assert p.args[p.args.index("-loop") + 1] == "0" and p.args[-1] == "o.gif" and p.kind == "animation"
    p = build_animation_plan(info, GRID, 1.0, 3.0, AnimationOptions(format="webp", width=None, fps=None,
                                                                     loop=False, quality="high"), "o.webp")
    assert "libwebp_anim" in p.args and p.args[p.args.index("-quality") + 1] == "85"
    assert p.args[p.args.index("-loop") + 1] == "1"                 # WebP: play once
    assert "-filter_complex" not in p.args                          # nothing to resize or retime
    small = make_info(w=320, h=240)
    p = build_animation_plan(small, GRID, 1.0, 2.0, AnimationOptions(width=960), "o.gif")
    assert "scale" not in p.args[p.args.index("-filter_complex") + 1]   # never upscaled
    assert animation.output_size(small, AnimationOptions(width=960)) == (320, 240)


def test_gif_is_frame_exact(media_dir, tmp_path):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    out = tmp_path / "clip.gif"
    _export(src, out, 10, 40, format="gif", width=None, fps=None)
    assert out.read_bytes()[:6] == b"GIF89a"
    assert frame_numbers(out) == [n % 64 for n in range(10, 41)]


def test_gif_frame_rate_size_and_loop(media_dir, tmp_path):
    src = numbered_video(media_dir / "capture.mp4", size="1772x996", seconds=2)
    out = tmp_path / "small.gif"
    _export(src, out, 0, 59, format="gif", width=480, fps=10, loop=True)
    info = media_info.probe(str(out))
    assert (info.video.width, info.video.height) == (480, 270)
    assert len(frame_numbers(out)) in (19, 20, 21)                  # 2 s at 10 fps
    assert b"NETSCAPE2.0" in out.read_bytes()                       # loop forever
    once = tmp_path / "once.gif"
    _export(src, once, 0, 29, format="gif", width=320, fps=10, loop=False)
    assert b"NETSCAPE2.0" not in once.read_bytes()


def test_webp_animation(media_dir, tmp_path):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    out = tmp_path / "clip.webp"
    plan, r = _export(src, out, 12, 41, format="webp", width=None, fps=None, quality="high")
    data = out.read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP" and b"ANIM" in data[:200]
    frames = frame_numbers(out)
    assert len(frames) == 30
    assert frames[0] == 12 and frames[-1] == 41


def test_estimate_is_in_the_right_ballpark(media_dir, tmp_path):
    src = numbered_video(media_dir / "busy_720p.mp4", fps=30, seconds=12, audio=1) \
        if not (media_dir / "busy_720p.mp4").exists() else media_dir / "busy_720p.mp4"
    info, grid = _load(src)
    for fmt in ("gif", "webp"):
        opts = AnimationOptions(format=fmt)
        out = tmp_path / f"e.{fmt}"
        _p, r = _export(src, out, 60, 209, **vars(opts))
        est = animation.estimate_animation_bytes(info, grid, opts, 5.0)
        assert 0.6 <= est / r.size_bytes <= 1.6, f"{fmt}: estimate {est} vs actual {r.size_bytes}"


def test_dialog_and_save_flow(window, qapp, media_dir, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox
    from swiftcut.ui.animation_dialog import AnimationDialog
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    window.open_file(str(src))
    assert wait_for(qapp, lambda: window.model.grid is not None and window.model.grid.exact)
    window.model.set_start_index(10)
    window.model.set_end_index(40)

    dlg = AnimationDialog(window.info, window.model.grid, window.model.duration,
                          window.settings.animation_options(), window)
    assert wait_for(qapp, lambda: "about" in dlg.estimate.text(), 15)
    dlg.fmt.setIndex(1, emit=True)                                  # WebP
    assert "WebP" in dlg.format_note.text()
    dlg.close()

    chosen = AnimationOptions(format="gif", width=None, fps=None, loop=True, quality="high")
    monkeypatch.setattr(AnimationDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(AnimationDialog, "options", lambda self: chosen)
    out = tmp_path / "anim.gif"
    seen = {}

    def fake_save(parent, caption, suggested, filters):
        seen["s"] = suggested
        return str(out), filters
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake_save))
    window.save_animation()
    assert os.path.basename(seen["s"]) == "cfr30.gif"
    assert wait_for(qapp, lambda: window.export_job is None and out.exists(), 30)
    assert frame_numbers(out) == [n % 64 for n in range(10, 41)]
    assert window.settings.animation_options().quality == "high"   # remembered

    # Typing the video's own name still can't replace it: ".gif" is always added.
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a: (str(src), "")))
    window.save_animation()
    assert wait_for(qapp, lambda: window.export_job is None, 30)
    assert (src.parent / (src.name + ".gif")).exists() and src.read_bytes()[:4] != b"GIF8"
    (src.parent / (src.name + ".gif")).unlink()
