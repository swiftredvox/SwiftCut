import os
import time
from pathlib import Path

from swiftcut.core import exporter, paths


def test_suggested_output_keeps_name_and_folder():
    assert paths.suggested_output_path(r"C:\rec\clip.mp4", "mp4") == str(Path(r"C:\rec\clip.mp4"))
    assert paths.suggested_output_path(r"C:\rec\clip.MOV", "mov") == str(Path(r"C:\rec\clip.mov"))


def test_suggested_output_changes_extension_for_other_format():
    assert paths.suggested_output_path(r"C:\rec\clip.avi", "mp4").endswith("clip.mp4")
    assert paths.suggested_output_path(r"C:\rec\clip.mp4", "mkv").endswith("clip.mkv")


def test_m4v_extension_preserved():
    assert paths.output_extension("x.m4v", "mp4") == "m4v"


def test_trimmed_alternative_avoids_collisions(tmp_path):
    base = tmp_path / "clip.mp4"
    assert paths.trimmed_alternative(str(base)).endswith("clip_trimmed.mp4")
    (tmp_path / "clip_trimmed.mp4").write_bytes(b"")
    assert paths.trimmed_alternative(str(base)).endswith("clip_trimmed_2.mp4")


def test_ensure_extension_and_container_detection():
    assert paths.ensure_extension("C:/a/out", "mkv") == "C:/a/out.mkv"
    assert paths.ensure_extension("C:/a/out.mp4", "mkv") == "C:/a/out.mp4"
    assert paths.container_for_path("x.WEBM") == "webm"
    assert paths.container_for_path("x.avi") is None


def test_same_file(tmp_path):
    f = tmp_path / "A.mp4"
    f.write_bytes(b"x")
    assert paths.same_file(str(f), str(tmp_path / "a.mp4"))          # case-insensitive on Windows
    assert paths.same_file(str(f), str(tmp_path / "." / "A.mp4"))
    assert not paths.same_file(str(f), str(tmp_path / "b.mp4"))


def test_temp_path_is_beside_destination_and_unique():
    a, b = paths.temp_path_for(r"C:\x\clip.mp4"), paths.temp_path_for(r"C:\x\clip.mp4")
    assert a != b
    assert os.path.dirname(a) == r"C:\x"
    assert a.endswith(".partial.mp4")


def test_check_destination(tmp_path):
    assert paths.check_destination(str(tmp_path / "ok.mp4")) is None
    assert "does not exist" in paths.check_destination(str(tmp_path / "missing" / "x.mp4"))
    (tmp_path / "dir.mp4").mkdir()
    assert paths.check_destination(str(tmp_path / "dir.mp4")) is not None


def test_stale_temp_cleanup(tmp_path):
    old = tmp_path / "~clip.swiftcut-abc123.partial.mp4"
    new = tmp_path / "~clip.swiftcut-def456.partial.mp4"
    keep = tmp_path / "clip.mp4"
    for f in (old, new, keep):
        f.write_bytes(b"x")
    past = time.time() - 7200
    os.utime(old, (past, past))
    assert exporter.cleanup_stale_temp_files(str(tmp_path)) == 1
    assert not old.exists() and new.exists() and keep.exists()


def test_friendly_errors():
    assert "disk is full" in exporter._friendly_error("av_interleaved_write_frame(): No space left on device")
    assert "Permission" in exporter._friendly_error("Permission denied")
    assert "NVIDIA" in exporter._friendly_error("[h264_nvenc] OpenEncodeSessionEx failed: error")
    assert exporter._friendly_error("weird") == "FFmpeg could not export the video."


def test_clear_logs(tmp_path):
    import logging
    from logging.handlers import RotatingFileHandler
    from swiftcut.app import clear_logs
    h = RotatingFileHandler(tmp_path / "swiftcut.log", maxBytes=512_000, backupCount=1, encoding="utf-8")
    root = logging.getLogger()
    root.addHandler(h)
    try:
        logging.getLogger("t").warning("C:\\secret\\path.mp4 " * 50)
        (tmp_path / "swiftcut.log.1").write_text("old" * 100)
        freed = clear_logs(str(tmp_path))
        assert freed > 0
        assert (tmp_path / "swiftcut.log").stat().st_size == 0
        assert not (tmp_path / "swiftcut.log.1").exists()
        logging.getLogger("t").warning("still logging after clear")
        h.flush()
        assert "still logging" in (tmp_path / "swiftcut.log").read_text(encoding="utf-8")
    finally:
        root.removeHandler(h)
        h.close()
