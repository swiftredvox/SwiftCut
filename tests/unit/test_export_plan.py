import pytest

from swiftcut.core.export_plan import (Encoder, ExportOptions, PlanError, Quality, TrimMethod,
                                       build_plan, copy_compatible, default_container,
                                       resolve_video_codec)
from swiftcut.core.media_info import AudioStream, MediaInfo, VideoStream
from swiftcut.core.timecode import FrameGrid


def make_info(path="C:/v/clip.mp4", w=1920, h=1080, vcodec="h264", audio=("aac",), pix="yuv420p",
              rotation=0) -> MediaInfo:
    return MediaInfo(
        path=path, size_bytes=1000, format_name="mov,mp4", duration=10.0, start_time=0.0, bit_rate=0,
        video=VideoStream(0, vcodec, w, h, 30.0, 30.0, pix_fmt=pix, rotation=rotation),
        audio=[AudioStream(i + 1, c, 2, 48000) for i, c in enumerate(audio)],
    )


GRID = FrameGrid([i / 30 for i in range(300)], 10.0, exact=True, keyframes=[0.0, 2.0, 4.0, 6.0, 8.0])


def plan(info=None, start=1.0, end=3.0, out="C:/v/out.mp4", **opts):
    return build_plan(info or make_info(), GRID, start, end, ExportOptions(**opts), out)


def arg(p, flag):
    return p.args[p.args.index(flag) + 1]


def test_precise_seeks_just_before_first_frame_and_keeps_duration():
    p = plan(start=1.0, end=3.0)
    assert float(arg(p, "-ss")) == pytest.approx(1.0 - 0.001)
    assert float(arg(p, "-t")) == pytest.approx(2.0)
    assert p.actual_start == 1.0
    assert p.duration == pytest.approx(2.0)
    assert arg(p, "-c:v") == "libx264"
    assert arg(p, "-crf") == "16"
    assert arg(p, "-preset") == "veryfast"
    assert arg(p, "-fps_mode") == "passthrough"
    assert arg(p, "-f") == "mp4"
    assert "+faststart" in p.args
    assert p.args[-1] == "C:/v/out.mp4"
    assert arg(p, "-c:a") == "aac"


def test_precise_at_zero_does_not_seek_negative():
    p = plan(start=0.0, end=1.0)
    assert float(arg(p, "-ss")) == 0.0


def test_seek_epsilon_is_bounded_by_frame_interval():
    tight = FrameGrid([0, 0.0005, 0.001, 0.0015], 0.002, exact=True)
    p = build_plan(make_info(), tight, 0.001, 0.0015 + 0.0005, ExportOptions(), "o.mp4")
    assert float(arg(p, "-ss")) == pytest.approx(0.001 - 0.00025)


def test_invalid_selection_rejected():
    with pytest.raises(PlanError):
        plan(start=3.0, end=1.0)
    with pytest.raises(PlanError):
        plan(start=1.0, end=1.0)


def test_fast_trim_starts_on_previous_keyframe():
    p = plan(start=3.0, end=5.0, method=TrimMethod.FAST)
    assert p.actual_start == 2.0
    assert float(arg(p, "-ss")) == pytest.approx(2.0 + 0.001)   # nudged after the keyframe
    assert float(arg(p, "-t")) == pytest.approx(3.0 - 0.002)
    assert p.duration == pytest.approx(3.0)
    assert arg(p, "-c") == "copy"
    assert p.video_codec is None
    assert any("keyframe" in n for n in p.notes)


def test_fast_trim_on_keyframe_is_exact_and_silent():
    p = plan(start=4.0, end=5.0, method=TrimMethod.FAST)
    assert p.actual_start == 4.0
    assert p.notes == []


def test_fast_trim_incompatible_container_rejected():
    with pytest.raises(PlanError):
        plan(method=TrimMethod.FAST, container="webm", out="o.webm")


def test_fast_trim_into_mkv_always_allowed():
    p = plan(info=make_info(vcodec="prores", audio=("pcm_s24le",)), method=TrimMethod.FAST,
             container="mkv", out="o.mkv")
    assert arg(p, "-f") == "matroska"


def test_odd_dimensions_are_padded_and_disclosed():
    p = plan(info=make_info(w=1001, h=601))
    assert arg(p, "-vf") == "pad=ceil(iw/2)*2:ceil(ih/2)*2"
    assert any("1001x601" in n for n in p.notes)


def test_even_dimensions_untouched():
    assert "-vf" not in plan().args


def test_vp9_keeps_odd_dimensions():
    p = plan(info=make_info(w=1001, h=601), container="webm", video_codec="vp9", out="o.webm")
    assert "-vf" not in p.args
    assert arg(p, "-c:v") == "libvpx-vp9"
    assert arg(p, "-c:a") == "libopus"


def test_rotation_uses_display_size_for_padding():
    p = plan(info=make_info(w=1080, h=1921, rotation=-90))
    assert "-vf" in p.args   # display size is 1921x1080 -> odd width


def test_webm_forces_vp9():
    assert resolve_video_codec("webm", "h264") == "vp9"
    assert resolve_video_codec("mp4", "vp9") == "h264"


def test_hevc_cpu_and_nvenc():
    p = plan(video_codec="hevc", quality=Quality.SMALL)
    assert arg(p, "-c:v") == "libx265" and arg(p, "-crf") == "28" and arg(p, "-tag:v") == "hvc1"
    p = plan(video_codec="hevc", encoder=Encoder.NVENC)
    assert arg(p, "-c:v") == "hevc_nvenc" and arg(p, "-cq") == "19"


def test_ten_bit_hevc_keeps_ten_bit_and_h264_warns():
    info = make_info(pix="yuv420p10le")
    assert arg(plan(info=info, video_codec="hevc"), "-pix_fmt") == "yuv420p10le"
    p = plan(info=info)
    assert arg(p, "-pix_fmt") == "yuv420p"
    assert any("10-bit" in n for n in p.notes)


def test_mute_removes_audio():
    p = plan(mute=True)
    assert "-an" in p.args
    assert p.audio_streams == []


def test_track_selection_maps_only_chosen_tracks():
    info = make_info(audio=("aac", "aac"))
    p = plan(info=info, audio_tracks=[2])
    maps = [p.args[i + 1] for i, a in enumerate(p.args) if a == "-map"]
    assert maps == ["0:0", "0:2"]
    p = plan(info=info)
    maps = [p.args[i + 1] for i, a in enumerate(p.args) if a == "-map"]
    assert maps == ["0:0", "0:1", "0:2"]


def test_no_audio_source():
    p = plan(info=make_info(audio=()))
    assert "-an" in p.args


def test_default_container():
    assert default_container(make_info(path="a.MOV"), TrimMethod.PRECISE) == "mov"
    assert default_container(make_info(path="a.m4v"), TrimMethod.PRECISE) == "mp4"
    assert default_container(make_info(path="a.avi"), TrimMethod.PRECISE) == "mp4"
    assert default_container(make_info(path="a.avi", vcodec="mpeg4", audio=("mp3",)), TrimMethod.FAST) == "mp4"
    assert default_container(make_info(path="a.avi", vcodec="msmpeg4v3", audio=("mp3",)), TrimMethod.FAST) == "mkv"


def test_copy_compatibility():
    assert copy_compatible(make_info(), "mp4", [1]) is None
    assert copy_compatible(make_info(), "webm", [1]) is not None
    assert copy_compatible(make_info(audio=("opus",)), "mov", [1]) is not None
    assert copy_compatible(make_info(audio=("opus",)), "mov", []) is None   # track excluded


def test_end_clamped_to_video_length():
    p = plan(start=9.0, end=50.0)
    assert p.duration <= 2.1


def test_unindexed_containers_seek_on_output_side():
    info = make_info(path="C:/v/cam.ts")
    info.format_name = "mpegts"
    p = plan(info=info)
    assert p.args.index("-i") < p.args.index("-ss")
    p = plan()   # mp4: fast input seek
    assert p.args.index("-ss") < p.args.index("-i")


def test_strip_metadata_flags():
    p = plan(strip_metadata=True)
    assert arg(p, "-map_metadata") == "-1"
    assert "+bitexact" in p.args
    assert arg(p, "-bsf:v") == "filter_units=remove_types=6"
    p = plan(strip_metadata=True, video_codec="hevc")
    assert arg(p, "-bsf:v") == "filter_units=remove_types=39|40"
    p = plan(strip_metadata=True, method=TrimMethod.FAST)
    assert "-bsf:v" not in p.args                          # never alter copied bitstreams
    assert arg(plan(), "-map_metadata") == "0"


# -- size limit (F4) ------------------------------------------------------------------------
def test_size_budget_math():
    from swiftcut.core.export_plan import size_budget
    b = size_budget(make_info(), duration=20.0, n_audio=1, limit_mb=10)
    total = (b.video_kbps + b.audio_kbps) * 20 / 8 * 1000     # bytes
    assert total <= 10_000_000 * 0.97
    assert b.audio_kbps == 128 and b.video_kbps > 3500


def test_size_budget_squeezes_audio_then_refuses():
    from swiftcut.core.export_plan import size_budget
    assert size_budget(make_info(), 600, 1, 10).audio_kbps == 64      # tight: audio drops to 64k
    with pytest.raises(PlanError, match="too long"):
        size_budget(make_info(), 3600, 1, 8)


def test_two_pass_plan():
    p = plan(size_limit_mb=10, encoder=Encoder.NVENC)
    assert len(p.pre_passes) == 1
    first = p.pre_passes[0]
    assert "-pass" in first and first[first.index("-pass") + 1] == "1"
    assert first[-3:] == ["-f", "null", "-"] and "-an" in first
    assert arg(p, "-pass") == "2" and arg(p, "-c:v") == "libx264"       # CPU, even if NVENC chosen
    assert any("CPU encoder" in n for n in p.notes)
    assert p.size_limit_bytes == 10_000_000 and p.work_dir and p.cleanup_prefix
    assert ":" not in arg(p, "-passlogfile")                            # relative name, see x265


def test_two_pass_hevc_stats_name_has_no_drive_colon():
    p = plan(size_limit_mb=10, video_codec="hevc")
    params = arg(p, "-x265-params")
    assert params.startswith("pass=2:stats=swiftcut-2pass-") and "C:" not in params


def test_size_limit_ignored_for_fast_trim():
    p = plan(start=4.0, end=5.0, method=TrimMethod.FAST, size_limit_mb=10)
    assert not p.pre_passes and p.size_limit_bytes is None


def test_estimate_key_and_audio_rate():
    from swiftcut.core import estimate
    info = make_info(audio=("aac", "aac"))
    base = ExportOptions()
    assert estimate.settings_key(info, base) == estimate.settings_key(info, ExportOptions(mute=True))
    assert estimate.settings_key(info, base) != estimate.settings_key(info, ExportOptions(quality=Quality.SMALL))
    assert estimate.audio_rate(info, base) == 2 * 192_000 / 8
    assert estimate.audio_rate(info, ExportOptions(mute=True)) == 0
    assert estimate.audio_rate(info, ExportOptions(audio_tracks=[1])) == 192_000 / 8
    assert estimate.estimate_bytes(100_000, info, ExportOptions(mute=True), 10) == int(1_000_000 * 1.01)
