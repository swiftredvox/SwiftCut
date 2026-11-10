"""End-to-end exports of generated videos, verified by decoding the outputs.

Each source frame's brightness encodes its frame number (see conftest), so `frame_numbers()`
tells us exactly which frames ended up in the output.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from swiftcut.core import exporter, ffmpeg_tools, media_info, thumbnails
from swiftcut.core.export_plan import Encoder, ExportOptions, PlanError, TrimMethod, build_plan
from swiftcut.core.media_info import ProbeError

from ..conftest import ffmpeg, frame_numbers, numbered_video, requires_ffmpeg

pytestmark = requires_ffmpeg


def load(path):
    info = media_info.probe(str(path))
    return info, media_info.scan_frames(info)


def export(path, out, first, last, **opts):
    """Trim frames first..last (inclusive) of `path` into `out`."""
    info, grid = load(path)
    p = build_plan(info, grid, grid.times[first], grid.frame_end(last), ExportOptions(**opts), str(out),
                   ffmpeg_tools.find_ffmpeg().ffmpeg)
    result = exporter.run_export(p)
    return p, result, *load(out)


def expected(first, last):
    return [n % 64 for n in range(first, last + 1)]


# -- frame accuracy ---------------------------------------------------------------------------
@pytest.mark.parametrize("fps", [24, 30, 60])
def test_precise_trim_is_frame_exact(media_dir, tmp_path, fps):
    src = numbered_video(media_dir / f"cfr{fps}.mp4", fps=fps, seconds=4, audio=1)
    first, last = 17, fps * 3 - 5
    _p, _r, info, grid = export(src, tmp_path / "out.mp4", first, last)
    assert frame_numbers(tmp_path / "out.mp4") == expected(first, last)
    assert len(grid) == last - first + 1
    assert info.has_audio
    assert info.audio[0].codec == "aac"


def test_audio_stays_in_sync(media_dir, tmp_path):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    _p, _r, info, grid = export(src, tmp_path / "out.mp4", 30, 89)   # exactly 2.000 s
    probe = ffmpeg_tools.run([ffmpeg_tools.find_ffmpeg().ffprobe, "-v", "error", "-select_streams", "a:0",
                              "-show_entries", "stream=duration,start_time", "-of", "csv=p=0",
                              str(tmp_path / "out.mp4")]).stdout.strip().split(",")
    a_start, a_dur = float(probe[0]), float(probe[1])
    assert abs(a_start) < 0.03
    assert a_dur == pytest.approx(2.0, abs=0.03)
    assert grid.end_time == pytest.approx(2.0, abs=0.001)


def test_first_and_last_frames_selectable(media_dir, tmp_path):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    _p, _r, _i, grid = export(src, tmp_path / "a.mp4", 0, 0)
    assert frame_numbers(tmp_path / "a.mp4") == [0]
    _info, sgrid = load(src)
    last = sgrid.last_index
    export(src, tmp_path / "b.mp4", last - 2, last)
    assert frame_numbers(tmp_path / "b.mp4") == expected(last - 2, last)


def test_variable_frame_rate_source(media_dir, tmp_path):
    vfr = media_dir / "vfr.mkv"
    if not vfr.exists():
        base = numbered_video(media_dir / "cfr30_noaudio.mp4", fps=30, seconds=4)
        # Drop every third frame -> irregular 33/66 ms gaps, timestamps preserved.
        ffmpeg("-i", str(base), "-vf", "select='not(eq(mod(n\\,3)\\,1))'", "-fps_mode", "passthrough",
               "-c:v", "libx264", "-qp", "2", str(vfr))
    info, grid = load(vfr)
    gaps = {round(b - a, 3) for a, b in zip(grid.times, grid.times[1:])}
    assert len(gaps) >= 2, "test source should be VFR"
    first, last = 10, 50
    want = frame_numbers(vfr)[first:last + 1]
    export(vfr, tmp_path / "out.mkv", first, last, container="mkv")
    assert frame_numbers(tmp_path / "out.mkv") == want


def test_audio_leading_video_is_normalised(media_dir, tmp_path):
    src = numbered_video(media_dir / "cfr30_audio.mkv", fps=30, seconds=4, audio=1)
    info, grid = load(src)
    assert grid.times[0] == 0.0
    assert grid.origin == pytest.approx(info.video_offset, abs=2e-3)
    export(src, tmp_path / "out.mkv", 10, 40)
    assert frame_numbers(tmp_path / "out.mkv") == expected(10, 40)
    export(src, tmp_path / "first.mkv", 0, 5)
    assert frame_numbers(tmp_path / "first.mkv") == expected(0, 5)


def test_mpegts_with_start_offset(media_dir, tmp_path):
    ts = media_dir / "offset.ts"
    if not ts.exists():
        base = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
        ffmpeg("-i", str(base), "-c", "copy", "-output_ts_offset", "1.4", str(ts))
    info, grid = load(ts)
    assert info.start_time > 1.0          # timestamps are normalised to the file start
    assert grid.times[0] == 0.0 and grid.origin < 0.1   # times start at the first video frame
    export(ts, tmp_path / "out.mp4", 20, 50, container="mp4")
    assert frame_numbers(tmp_path / "out.mp4") == expected(20, 50)


# -- dimensions / orientation -----------------------------------------------------------------
def test_odd_dimensions_are_padded_not_cropped(media_dir, tmp_path):
    src = numbered_video(media_dir / "odd.mkv", size="321x241", seconds=2, vcodec="ffv1")
    p, _r, info, _g = export(src, tmp_path / "out.mp4", 5, 20, container="mp4")
    assert (info.video.width, info.video.height) == (322, 242)
    assert any("odd" in n for n in p.notes)
    assert frame_numbers(tmp_path / "out.mp4") == expected(5, 20)


def test_non_standard_screen_capture_size_preserved(media_dir, tmp_path):
    src = numbered_video(media_dir / "capture.mp4", size="1772x996", seconds=2)
    _p, _r, info, _g = export(src, tmp_path / "out.mp4", 3, 40)
    assert (info.video.width, info.video.height) == (1772, 996)


def test_rotated_source_keeps_orientation(media_dir, tmp_path):
    src = media_dir / "rotated.mp4"
    if not src.exists():
        base = numbered_video(media_dir / "portrait_base.mp4", size="320x240", seconds=2)
        ffmpeg("-display_rotation:v:0", "90", "-i", str(base), "-c", "copy", str(src))
    info, _ = load(src)
    assert abs(info.video.rotation) == 90
    assert info.video.display_size == (240, 320)
    _p, _r, out, _g = export(src, tmp_path / "out.mp4", 2, 20)
    assert out.video.display_size == (240, 320)


# -- audio ------------------------------------------------------------------------------------
def test_no_audio_source(media_dir, tmp_path):
    src = numbered_video(media_dir / "cfr30_noaudio.mp4", fps=30, seconds=4)
    _p, _r, info, _g = export(src, tmp_path / "out.mp4", 1, 30)
    assert not info.has_audio


def test_mute_removes_audio(media_dir, tmp_path):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    _p, _r, info, _g = export(src, tmp_path / "out.mp4", 1, 30, mute=True)
    assert not info.has_audio


def test_multiple_audio_tracks_kept_or_selected(media_dir, tmp_path):
    src = numbered_video(media_dir / "two_tracks.mkv", seconds=2, audio=2)
    info, _ = load(src)
    assert len(info.audio) == 2
    _p, _r, out, _g = export(src, tmp_path / "all.mkv", 0, 30)
    assert len(out.audio) == 2
    assert frame_numbers(tmp_path / "all.mkv") == expected(0, 30)
    _p, _r, out, _g = export(src, tmp_path / "one.mkv", 0, 30, audio_tracks=[info.audio[1].index])
    assert len(out.audio) == 1


# -- formats ----------------------------------------------------------------------------------
@pytest.mark.parametrize("container,codec,vcodec,acodec", [
    ("mov", "h264", "h264", "aac"),
    ("mkv", "hevc", "hevc", "aac"),
    ("webm", "vp9", "vp9", "opus"),
])
def test_output_formats(media_dir, tmp_path, container, codec, vcodec, acodec):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    out = tmp_path / f"out.{container}"
    _p, _r, info, _g = export(src, out, 10, 40, container=container, video_codec=codec)
    assert info.video.codec == vcodec
    assert info.audio[0].codec == acodec
    assert frame_numbers(out) == expected(10, 40)


@pytest.mark.parametrize("ext,args", [
    ("mov", ["-c:v", "libx264", "-qp", "2"]),
    ("webm", ["-c:v", "libvpx-vp9", "-lossless", "1", "-deadline", "realtime", "-cpu-used", "8"]),
])
def test_other_input_containers(media_dir, tmp_path, ext, args):
    src = media_dir / f"input.{ext}"
    if not src.exists():
        base = numbered_video(media_dir / "cfr30_noaudio.mp4", fps=30, seconds=4)
        ffmpeg("-i", str(base), *args, str(src))
    out = tmp_path / f"out.{ext}"
    export(src, out, 5, 25)
    assert frame_numbers(out) == expected(5, 25)


def test_nvenc_when_available(media_dir, tmp_path):
    if not ffmpeg_tools.encoder_works("h264_nvenc"):
        pytest.skip("No NVENC on this machine")
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    _p, _r, info, _g = export(src, tmp_path / "out.mp4", 10, 40, encoder=Encoder.NVENC)
    assert info.video.codec == "h264"
    assert frame_numbers(tmp_path / "out.mp4") == expected(10, 40)


# -- fast trim --------------------------------------------------------------------------------
def test_fast_trim_starts_on_keyframe(media_dir, tmp_path):
    src = numbered_video(media_dir / "gop30.mp4", fps=30, seconds=4, audio=1, gop=30)
    info, grid = load(src)
    assert grid.keyframes[:3] == pytest.approx([0.0, 1.0, 2.0], abs=1e-3)
    p, _r, out, ogrid = export(src, tmp_path / "out.mp4", 40, 80, method=TrimMethod.FAST)
    assert p.actual_start == pytest.approx(1.0, abs=1e-3)
    frames = frame_numbers(tmp_path / "out.mp4")
    assert frames[0] == 30
    # Stream copy cuts by decode order: with B-frames the tail may overshoot by the reorder depth.
    assert 80 % 64 in frames
    assert 51 <= len(frames) <= 51 + 2 * info.video.has_b_frames + 1
    assert any("B-frames" in n for n in p.notes) == bool(info.video.has_b_frames)
    assert out.video.codec == "h264" and out.has_audio


def test_fast_trim_on_keyframe_is_exact(media_dir, tmp_path):
    src = numbered_video(media_dir / "gop30.mp4", fps=30, seconds=4, audio=1, gop=30)
    export(src, tmp_path / "out.mp4", 60, 89, method=TrimMethod.FAST)
    frames = frame_numbers(tmp_path / "out.mp4")
    assert frames[0] == 60 % 64 and 30 <= len(frames) <= 35


def test_fast_trim_without_b_frames_is_exact_at_both_ends(media_dir, tmp_path):
    # Like ShareX output: baseline H.264 (no B-frames), keyframe every second.
    src = numbered_video(media_dir / "baseline.mp4", fps=30, seconds=4, gop=30,
                         extra_out=("-profile:v", "baseline"))
    info, _ = load(src)
    assert info.video.has_b_frames == 0
    p, _r, _o, _g = export(src, tmp_path / "out.mp4", 30, 80, method=TrimMethod.FAST)
    assert frame_numbers(tmp_path / "out.mp4") == expected(30, 80)
    assert p.notes == []


def test_fast_trim_incompatible_container_refused(media_dir, tmp_path):
    src = numbered_video(media_dir / "gop30.mp4", fps=30, seconds=4, audio=1, gop=30)
    info, grid = load(src)
    with pytest.raises(PlanError):
        build_plan(info, grid, 1.0, 2.0, ExportOptions(method=TrimMethod.FAST, container="webm"),
                   str(tmp_path / "x.webm"))


# -- safety -----------------------------------------------------------------------------------
def test_overwrite_source_is_safe(media_dir, tmp_path):
    src = tmp_path / "rec.mp4"
    src.write_bytes(numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1).read_bytes())
    export(src, src, 15, 44)
    assert frame_numbers(src) == expected(15, 44)
    assert [f.name for f in tmp_path.iterdir()] == ["rec.mp4"]


def test_existing_destination_untouched_on_failure(media_dir, tmp_path):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    dest = tmp_path / "keep.mp4"
    dest.write_bytes(b"precious")
    info, grid = load(src)
    p = build_plan(info, grid, 0.0, 1.0, ExportOptions(), str(dest), ffmpeg_tools.find_ffmpeg().ffmpeg)
    p.args.insert(p.args.index("-c:v") + 1, "no_such_encoder")
    p.args.pop(p.args.index("no_such_encoder") + 1)
    with pytest.raises(exporter.ExportError):
        exporter.run_export(p)
    assert dest.read_bytes() == b"precious"
    assert [f.name for f in tmp_path.iterdir()] == ["keep.mp4"]


def test_cancellation_cleans_up(media_dir, tmp_path):
    src = numbered_video(media_dir / "big.mp4", fps=60, seconds=20, size="1920x1080", audio=1)
    info, grid = load(src)
    dest = tmp_path / "out.mp4"
    p = build_plan(info, grid, 0.0, grid.end_time, ExportOptions(), str(dest), ffmpeg_tools.find_ffmpeg().ffmpeg)
    p.args[p.args.index("-preset") + 1] = "veryslow"
    cancel = threading.Event()
    progress: list[float] = []
    threading.Timer(1.0, cancel.set).start()
    t0 = time.perf_counter()
    with pytest.raises(exporter.ExportCancelled):
        exporter.run_export(p, progress.append, cancel)
    assert time.perf_counter() - t0 < 5
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_progress_reported(media_dir, tmp_path):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    info, grid = load(src)
    p = build_plan(info, grid, 0.0, grid.end_time, ExportOptions(), str(tmp_path / "o.mp4"),
                   ffmpeg_tools.find_ffmpeg().ffmpeg)
    seen: list[float] = []
    exporter.run_export(p, seen.append)
    assert seen and seen[-1] == 1.0 and all(0 <= f <= 1 for f in seen)


# -- inputs -----------------------------------------------------------------------------------
def test_invalid_files_rejected(tmp_path):
    bad = tmp_path / "notes.mp4"
    bad.write_text("definitely not a video")
    with pytest.raises(ProbeError):
        media_info.probe(str(bad))
    with pytest.raises(ProbeError):
        media_info.probe(str(tmp_path / "missing.mp4"))


def test_audio_only_file_rejected(tmp_path):
    a = tmp_path / "tone.m4a"
    ffmpeg("-f", "lavfi", "-i", "sine=duration=1", str(a))
    with pytest.raises(ProbeError, match="video stream"):
        media_info.probe(str(a))


def test_truncated_file_still_opens_or_fails_cleanly(media_dir, tmp_path):
    src = numbered_video(media_dir / "cfr30.mkv", fps=30, seconds=4, audio=1)
    cut = tmp_path / "cut.mkv"
    data = src.read_bytes()
    cut.write_bytes(data[: len(data) // 2])
    try:
        info, grid = load(cut)
    except ProbeError:
        return
    assert 0 < len(grid) < 120


def test_unicode_and_spaces_in_paths(media_dir, tmp_path):
    folder = tmp_path / "Récordings 2026 ✂"
    folder.mkdir()
    src = folder / "clip (final) é.mp4"
    src.write_bytes(numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1).read_bytes())
    export(src, folder / "trimmed ü.mp4", 3, 9)
    assert frame_numbers(folder / "trimmed ü.mp4") == expected(3, 9)


# -- thumbnails -------------------------------------------------------------------------------
@pytest.mark.parametrize("seconds", [4, 120])
def test_thumbnails_short_and_long(media_dir, seconds):
    src = numbered_video(media_dir / f"thumbs{seconds}.mp4", fps=30, seconds=seconds, size="320x180")
    info = media_info.probe(str(src))
    got: dict[int, bytes] = {}
    thumbnails.generate(info, 12, 40, lambda i, w, h, d: got.__setitem__(i, d), threading.Event())
    assert sorted(got) == list(range(12))
    w, h = thumbnails.thumb_size(info, 40)
    assert all(len(d) == w * h * 3 for d in got.values())


def test_spread_order_covers_everything():
    for n in (1, 2, 7, 40):
        order = thumbnails.spread_order(n)
        assert sorted(order) == list(range(n))


# -- privacy ----------------------------------------------------------------------------------
def _tags(path) -> str:
    return ffmpeg_tools.run([ffmpeg_tools.find_ffmpeg().ffprobe, "-v", "error", "-show_entries",
                             "format_tags:stream_tags:stream_side_data=rotation", "-of", "compact",
                             str(path)]).stdout


@pytest.fixture(scope="module")
def tagged_source(media_dir):
    src = media_dir / "tagged.mov"
    if not src.exists():
        base = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
        ffmpeg("-display_rotation:v:0", "90", "-i", str(base), "-map", "0", "-c", "copy",
               "-metadata", "title=Secret title", "-metadata", "comment=private note",
               "-metadata", "location=+51.5074-000.1278/", "-metadata", "creation_time=2021-05-01T10:00:00Z",
               "-metadata:s:a:0", "title=My microphone", str(src))
    return src


@pytest.mark.parametrize("method", [TrimMethod.PRECISE, TrimMethod.FAST])
def test_strip_metadata(tagged_source, tmp_path, method):
    assert "Secret title" in _tags(tagged_source)
    out = tmp_path / "clean.mp4"
    export(tagged_source, out, 0, 40, method=method, container="mp4", strip_metadata=True)
    tags = _tags(out)
    for secret in ("Secret title", "private note", "51.5074", "2021-05-01", "My microphone", "Lavf", "Lavc"):
        assert secret not in tags, f"{secret!r} survived: {tags}"
    if method == TrimMethod.PRECISE:
        assert b"x264 - core" not in out.read_bytes()      # encoder-info SEI removed
        info, _ = load(out)
        assert info.video.display_size == (240, 320)      # rotation applied to pixels
    else:
        assert "rotation" in tags                          # display matrix kept on copy


def test_metadata_kept_by_default(tagged_source, tmp_path):
    out = tmp_path / "kept.mp4"
    export(tagged_source, out, 0, 40, container="mp4")
    assert "Secret title" in _tags(out)


# -- snapshot -----------------------------------------------------------------------------------
def _png_frame_number(path) -> int:
    from PySide6.QtGui import QImage
    img = QImage(str(path))
    assert not img.isNull()
    small = img.scaled(8, 8).convertToFormat(QImage.Format.Format_Grayscale8)
    level = sum(small.pixelColor(x, y).red() for x in range(8) for y in range(8)) / 64
    return round((level * 219 / 255 + 16) / 4) % 64          # undo full-range expansion


@pytest.mark.parametrize("name,kwargs", [
    ("cfr30.mp4", dict(fps=30, seconds=4, audio=1)),
    ("cfr30_audio.mkv", dict(fps=30, seconds=4, audio=1)),     # audio leads video (origin > 0)
])
def test_snapshot_is_frame_exact(media_dir, tmp_path, name, kwargs):
    from swiftcut.core import snapshot
    src = numbered_video(media_dir / name, **kwargs)
    info, grid = load(src)
    for k in (5, 30, 47):
        out = tmp_path / f"f{k}.png"
        snapshot.save_frame(info, grid, k, str(out))
        assert _png_frame_number(out) == k % 64


def test_snapshot_full_resolution_and_rotation(media_dir, tmp_path):
    from PySide6.QtGui import QImage
    from swiftcut.core import snapshot
    src = media_dir / "rotated.mp4"
    if not src.exists():
        base = numbered_video(media_dir / "portrait_base.mp4", size="320x240", seconds=2)
        ffmpeg("-display_rotation:v:0", "90", "-i", str(base), "-c", "copy", str(src))
    info, grid = load(src)
    out = tmp_path / "r.png"
    snapshot.save_frame(info, grid, 3, str(out))
    img = QImage(str(out))
    assert (img.width(), img.height()) == (240, 320)
    assert snapshot.default_name(info, 3) == "rotated - frame 4.png"


# -- suggested trim (still footage) -------------------------------------------------------------
def _still_ends(media_dir, name="still_ends.mp4", long=False):
    src = media_dir / name
    if not src.exists():
        d = 150 if long else 2
        ffmpeg("-f", "lavfi", "-i", f"testsrc2=s=320x180:r=30:d={d},tpad=start_mode=clone:start_duration=1"
               ":stop_mode=clone:stop_duration=1.5", "-c:v", "libx264", "-preset", "ultrafast",
               "-pix_fmt", "yuv420p", str(src))
    return src


@pytest.mark.parametrize("long", [False, True])
def test_still_start_and_end_detected(media_dir, long):
    from swiftcut.core import stillness
    src = _still_ends(media_dir, "still_ends_long.mp4" if long else "still_ends.mp4", long)
    info, grid = load(src)
    moving = 150 if long else 2
    s = stillness.detect(info, grid)
    assert s.start == pytest.approx(1.0, abs=0.04)                 # last still frame before motion
    assert s.end == pytest.approx(1.0 + moving, abs=0.04)          # first still frame kept at the end


def test_no_suggestion_for_constant_motion(media_dir):
    from swiftcut.core import stillness
    info, grid = load(numbered_video(media_dir / "preview.mp4", fps=30, seconds=4))
    # Every frame of the numbered video differs, so nothing is still.
    assert not stillness.detect(info, grid).any


# -- size limit (F4) ----------------------------------------------------------------------------
def _busy_clip(media_dir):
    src = media_dir / "busy_720p.mp4"
    if not src.exists():
        ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=12", "-f", "lavfi", "-i", "sine=duration=12",
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-shortest", str(src))
    return src


@pytest.mark.parametrize("codec,container", [("h264", "mp4"), ("hevc", "mkv"), ("vp9", "webm")])
def test_size_limit_is_respected(media_dir, tmp_path, codec, container):
    src = _busy_clip(media_dir)
    info, grid = load(src)
    out = tmp_path / f"o.{container}"
    p = build_plan(info, grid, 1.0, 9.0, ExportOptions(container=container, video_codec=codec, size_limit_mb=1.5),
                   str(out), ffmpeg_tools.find_ffmpeg().ffmpeg)
    r = exporter.run_export(p)
    assert r.size_bytes <= 1_500_000
    assert r.size_bytes >= 1_500_000 * 0.75            # uses most of the budget (good quality)
    out_info, _ = load(out)
    assert out_info.has_audio and out_info.duration == pytest.approx(8.0, abs=0.1)
    leftovers = [f for f in os.listdir(p.work_dir) if f.startswith(os.path.basename(p.cleanup_prefix))]
    assert leftovers == []                              # 2-pass logs cleaned up


def test_size_limit_keeps_frame_accuracy(media_dir, tmp_path):
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    _p, r, _info, _grid = export(src, tmp_path / "s.mp4", 12, 70, size_limit_mb=0.5)
    assert r.size_bytes <= 500_000
    assert frame_numbers(tmp_path / "s.mp4") == expected(12, 70)


def test_size_overshoot_is_retried(media_dir, tmp_path, monkeypatch):
    """Force the first attempt to overshoot; the job must shrink the budget and succeed."""
    import dataclasses
    import swiftcut.core.export_plan as ep
    from PySide6.QtWidgets import QApplication
    from swiftcut.ui.workers import ExportJob
    QApplication.instance() or QApplication([])          # full app: later UI tests reuse it
    src = _busy_clip(media_dir)
    info, grid = load(src)
    out = tmp_path / "retry.mp4"
    monkeypatch.setattr(ep, "SIZE_MARGIN", 1.6)          # deliberately over-budget first time
    opts = ExportOptions(size_limit_mb=1.0)
    ff = ffmpeg_tools.find_ffmpeg().ffmpeg
    first = build_plan(info, grid, 1.0, 9.0, opts, str(out), ff)
    job = ExportJob(first, lambda scale: build_plan(info, grid, 1.0, 9.0,
                                                    dataclasses.replace(opts, size_scale=scale), str(out), ff))
    results, failures = [], []
    job.finished.connect(results.append)
    job.failed.connect(lambda m, d: failures.append(m))
    job._run()                                           # run synchronously
    assert not failures, failures
    assert results and results[0].size_bytes <= 1_000_000


def test_thumbnails_sample_slot_centres(media_dir):
    """Each filmstrip thumbnail must show the frame at the middle of its slot."""
    src = numbered_video(media_dir / "cfr30.mp4", fps=30, seconds=4, audio=1)
    info = media_info.probe(str(src))
    got: dict[int, bytes] = {}
    thumbnails.generate(info, 10, 40, lambda i, w, h, d: got.__setitem__(i, d), threading.Event())
    for i in range(10):
        centre = thumbnails.slot_time(i, 10, info.duration)        # 0.2, 0.6, 1.0, ...
        data = got[i]
        rgb_mean = sum(data) / len(data)                           # grey frames: R=G=B
        frame_no = round((rgb_mean * 219 / 255 + 16) / 4) % 64
        want = round(centre * 30) % 64
        if want < 4 or want > 58:
            continue          # brightness outside the video range clips in RGB; unreadable
        assert frame_no == want, f"slot {i}: frame {frame_no}"
