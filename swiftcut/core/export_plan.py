"""Turn a trim selection plus export options into a concrete FFmpeg command.

Pure functions only - no processes are started here - so every decision is unit-testable.

Timing model
------------
The selection is [start, end): `start` is the presentation time of the first kept frame and
`end` is the time at which the last kept frame stops being displayed (the next frame's time).

* Precise: input-side `-ss` with re-encoding makes FFmpeg decode from the previous keyframe and
  discard everything before `start`, so the first output frame is exactly the selected one. We
  seek a hair *before* the frame (half the shortest frame interval, capped at 1 ms) so float
  rounding can never drop it, and output `-t` of (end - start) keeps frames whose time is < end.
* Fast: stream copy can only begin on a keyframe, so we start at the keyframe at or before
  `start` (shown to the user beforehand) and copy packets up to `end`. FFmpeg limits copied
  packets by decode order, so with B-frames the end can overshoot by the reorder depth (a few
  frames); without B-frames (e.g. ShareX's baseline H.264) the end is exact.
"""
from __future__ import annotations

import os
import secrets
import tempfile
from dataclasses import dataclass, field
from enum import Enum

from .media_info import MediaInfo
from .timecode import FrameGrid


class TrimMethod(str, Enum):
    PRECISE = "precise"
    FAST = "fast"


class Quality(str, Enum):
    HIGH = "high"
    BALANCED = "balanced"
    SMALL = "small"


class Encoder(str, Enum):
    CPU = "cpu"
    NVENC = "nvenc"


# container key -> (FFmpeg muxer, default extension, label)
CONTAINERS: dict[str, tuple[str, str, str]] = {
    "mp4": ("mp4", "mp4", "MP4"),
    "mov": ("mov", "mov", "MOV (QuickTime)"),
    "mkv": ("matroska", "mkv", "MKV (Matroska)"),
    "webm": ("webm", "webm", "WebM"),
}
EXTENSION_TO_CONTAINER = {"mp4": "mp4", "m4v": "mp4", "mov": "mov", "mkv": "mkv", "webm": "webm"}

VIDEO_CODECS: dict[str, str] = {"h264": "H.264", "hevc": "H.265 (HEVC)", "vp9": "VP9"}
VIDEO_CODEC_TIPS: dict[str, str] = {
    "h264": "Plays everywhere. The safe choice.",
    "hevc": "Smaller files at the same quality; some older devices can't play it.",
    "vp9": "Open format used by WebM and browsers; slower to encode.",
}
CONTAINER_VIDEO_CODECS: dict[str, list[str]] = {
    "mp4": ["h264", "hevc"],
    "mov": ["h264", "hevc"],
    "mkv": ["h264", "hevc", "vp9"],
    "webm": ["vp9"],
}

# Codecs each container accepts for stream copy (Fast Trim). Conservative on purpose.
COPY_VIDEO_OK: dict[str, set[str]] = {
    "mp4": {"h264", "hevc", "av1", "mpeg4", "vp9", "mpeg2video"},
    "mov": {"h264", "hevc", "prores", "mpeg4", "mjpeg", "png", "qtrle"},
    "mkv": None,  # type: ignore[dict-item]  # Matroska takes anything
    "webm": {"vp8", "vp9", "av1"},
}
COPY_AUDIO_OK: dict[str, set[str]] = {
    "mp4": {"aac", "mp3", "ac3", "eac3", "alac", "opus", "flac"},
    "mov": {"aac", "mp3", "alac", "ac3", "pcm_s16le", "pcm_s24le", "pcm_f32le"},
    "mkv": None,  # type: ignore[dict-item]
    "webm": {"opus", "vorbis"},
}

# Quality ladders. x264 "veryfast" at CRF 16 measured ~2x faster than "medium" at CRF 18 with
# equal-or-better SSIM (0.9978 vs 0.9977) for ~8% more size on a 1772x996 capture - the right
# trade for a trimming tool. "Smaller file" spends encode time to save space.
CRF = {
    "libx264": {Quality.HIGH: 16, Quality.BALANCED: 21, Quality.SMALL: 26},
    "libx265": {Quality.HIGH: 20, Quality.BALANCED: 24, Quality.SMALL: 28},
    "libvpx-vp9": {Quality.HIGH: 28, Quality.BALANCED: 33, Quality.SMALL: 38},
    "nvenc": {Quality.HIGH: 19, Quality.BALANCED: 24, Quality.SMALL: 29},
}
X264_PRESET = {Quality.HIGH: "veryfast", Quality.BALANCED: "faster", Quality.SMALL: "medium"}
X265_PRESET = {Quality.HIGH: "fast", Quality.BALANCED: "fast", Quality.SMALL: "medium"}


@dataclass
class ExportOptions:
    method: TrimMethod = TrimMethod.PRECISE
    container: str = "source"          # "source" or a key of CONTAINERS
    video_codec: str = "h264"
    quality: Quality = Quality.HIGH
    encoder: Encoder = Encoder.CPU
    audio_tracks: list[int] | None = None   # absolute stream indices; None = all
    mute: bool = False
    strip_metadata: bool = False            # remove titles, dates, GPS, encoder tags, chapters
    size_limit_mb: float | None = None      # fit the file under this many MB (two-pass encode)
    size_scale: float = 1.0                 # internal: shrink the budget when retrying an overshoot


@dataclass
class ExportPlan:
    args: list[str]                     # full argv, output path last
    output_path: str
    duration: float                     # expected output duration (for progress)
    actual_start: float                 # where the output really begins in the source
    container: str
    video_codec: str | None             # None when stream-copying
    audio_streams: list[int]
    notes: list[str] = field(default_factory=list)   # user-facing remarks (padding, keyframes...)
    pre_passes: list[list[str]] = field(default_factory=list)  # e.g. the analysis pass of a 2-pass encode
    size_limit_bytes: int | None = None
    cleanup_prefix: str | None = None       # temp files (2-pass logs) to delete afterwards
    work_dir: str | None = None             # FFmpeg's working directory (where 2-pass logs go)
    kind: str = "video"                     # "video" | "animation" (GIF / WebP)


class PlanError(ValueError):
    """The requested export cannot be performed with these settings."""


def default_container(info: MediaInfo, method: TrimMethod) -> str:
    c = EXTENSION_TO_CONTAINER.get(info.extension)
    if c:
        return c
    if method == TrimMethod.FAST:
        return "mp4" if copy_compatible(info, "mp4", [a.index for a in info.audio]) is None else "mkv"
    return "mp4"


def resolve_container(info: MediaInfo, opts: ExportOptions) -> str:
    return default_container(info, opts.method) if opts.container in ("", "source") else opts.container


def resolve_video_codec(container: str, requested: str) -> str:
    allowed = CONTAINER_VIDEO_CODECS[container]
    return requested if requested in allowed else allowed[0]


def selected_audio(info: MediaInfo, opts: ExportOptions) -> list[int]:
    if opts.mute:
        return []
    available = [a.index for a in info.audio]
    if opts.audio_tracks is None:
        return available
    return [i for i in available if i in opts.audio_tracks]


def copy_compatible(info: MediaInfo, container: str, audio: list[int]) -> str | None:
    """None if the source streams can be copied into `container`, else a reason."""
    vok = COPY_VIDEO_OK.get(container)
    if vok is not None and info.video.codec not in vok:
        return f"{info.video.codec.upper()} video can't be stored in {CONTAINERS[container][2]} without re-encoding."
    aok = COPY_AUDIO_OK.get(container)
    if aok is not None:
        for a in info.audio:
            if a.index in audio and a.codec not in aok:
                return f"{a.codec.upper()} audio can't be stored in {CONTAINERS[container][2]} without re-encoding."
    return None


def fast_trim_start(grid: FrameGrid, start: float) -> float:
    """Where a stream-copy trim will actually begin: the keyframe at or before `start`."""
    return grid.keyframe_at_or_before(start)


# Containers without a seek index: FFmpeg's fast input seek can land after the keyframe it
# needs, producing undecodable output. For these we seek on the output side instead (decode
# from the start and discard) - slower, but always correct.
UNINDEXED_FORMATS = {"mpegts", "mpeg", "mpegvideo", "h264", "hevc", "m4v"}


def has_reliable_seeking(info: MediaInfo) -> bool:
    return not (set(info.format_name.split(",")) & UNINDEXED_FORMATS)


def _seek_args(info: MediaInfo, seek: float, duration: float) -> list[str]:
    if has_reliable_seeking(info):
        return ["-ss", _fmt(seek), "-i", info.path, "-t", _fmt(duration)]
    return ["-i", info.path, "-ss", _fmt(seek), "-t", _fmt(duration)]


def _fmt(t: float) -> str:
    return f"{max(t, 0.0):.6f}"


def _needs_even_dims(vcodec_lib: str) -> bool:
    return vcodec_lib in ("libx264", "libx265", "h264_nvenc", "hevc_nvenc")


def _video_encoder_args(info: MediaInfo, codec: str, opts: ExportOptions, notes: list[str]) -> tuple[str, list[str]]:
    ten_bit = "10" in info.video.pix_fmt or "12" in info.video.pix_fmt
    q = opts.quality
    if codec == "vp9":
        lib = "libvpx-vp9"
        args = ["-c:v", lib, "-crf", str(CRF[lib][q]), "-b:v", "0", "-deadline", "good",
                "-cpu-used", "4", "-row-mt", "1", "-pix_fmt", "yuv420p"]
        return lib, args
    if opts.encoder == Encoder.NVENC:
        lib = "h264_nvenc" if codec == "h264" else "hevc_nvenc"
        pix = "p010le" if (codec == "hevc" and ten_bit) else "yuv420p"
        args = ["-c:v", lib, "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                "-cq", str(CRF["nvenc"][q]), "-b:v", "0", "-pix_fmt", pix]
    elif codec == "hevc":
        lib = "libx265"
        pix = "yuv420p10le" if ten_bit else "yuv420p"
        args = ["-c:v", lib, "-crf", str(CRF[lib][q]), "-preset", X265_PRESET[q], "-pix_fmt", pix,
                "-x265-params", "log-level=error"]
    else:
        lib = "libx264"
        args = ["-c:v", lib, "-crf", str(CRF[lib][q]), "-preset", X264_PRESET[q], "-pix_fmt", "yuv420p"]
        if ten_bit:
            notes.append("The source is 10-bit; H.264 output is 8-bit for compatibility. Choose H.265 to keep 10-bit.")
    if codec == "hevc":
        args += ["-tag:v", "hvc1"]  # lets Apple players / Windows Photos recognise HEVC in MP4/MOV
    return lib, args


MB = 1_000_000                 # sizes use decimal megabytes, the stricter reading of "10 MB"
SIZE_MARGIN = 0.96             # leave room for container overhead and encoder variance
MIN_VIDEO_KBPS = 60


@dataclass
class SizeBudget:
    video_kbps: int
    audio_kbps: int            # per audio track
    quality: str               # "good" | "ok" | "low"


def size_budget(info: MediaInfo, duration: float, n_audio: int, limit_mb: float, scale: float = 1.0) -> SizeBudget:
    """Bitrates that fit `duration` seconds into `limit_mb`. Raises PlanError if impossible."""
    total_kbps = limit_mb * MB * 8 * SIZE_MARGIN * scale / max(duration, 0.1) / 1000
    audio_kbps = 128 if n_audio else 0
    if n_audio and audio_kbps * n_audio > total_kbps * 0.2:
        audio_kbps = 64
    video_kbps = int(total_kbps - audio_kbps * n_audio)
    if video_kbps < MIN_VIDEO_KBPS:
        raise PlanError(f"This selection is too long to fit in {limit_mb:g} MB at a watchable quality. "
                        "Shorten the selection, raise the size limit, or turn off audio.")
    w, h = info.video.display_size
    fps = info.video.fps or 30
    bpp = video_kbps * 1000 / max(1.0, w * h * fps)      # bits per pixel per frame
    quality = "good" if bpp >= 0.06 else "ok" if bpp >= 0.025 else "low"
    return SizeBudget(video_kbps, audio_kbps, quality)


def _sized_video_args(codec: str, kbps: int, passlog: str, pass_no: int) -> tuple[str, list[str]]:
    """CPU encoder settings for one pass of a two-pass, bitrate-targeted encode."""
    if codec == "vp9":
        return "libvpx-vp9", ["-c:v", "libvpx-vp9", "-b:v", f"{kbps}k", "-deadline", "good", "-cpu-used", "4",
                              "-row-mt", "1", "-pix_fmt", "yuv420p", "-pass", str(pass_no), "-passlogfile", passlog]
    if codec == "hevc":
        stats = passlog + ".x265.log"
        return "libx265", ["-c:v", "libx265", "-b:v", f"{kbps}k", "-preset", "medium", "-pix_fmt", "yuv420p",
                           "-x265-params", f"pass={pass_no}:stats={stats}:log-level=error",
                           "-tag:v", "hvc1"]
    return "libx264", ["-c:v", "libx264", "-b:v", f"{kbps}k", "-preset", "medium", "-pix_fmt", "yuv420p",
                       "-pass", str(pass_no), "-passlogfile", passlog]


def _audio_encoder_args(container: str, info: MediaInfo, audio: list[int], kbps: int | None = None) -> list[str]:
    if not audio:
        return ["-an"]
    if container == "webm":
        return ["-c:a", "libopus", "-b:a", f"{kbps}k" if kbps else "160k"]
    args = ["-c:a", "aac"]
    for out_i, idx in enumerate(audio):
        ch = next((a.channels for a in info.audio if a.index == idx), 2)
        rate = f"{kbps}k" if kbps else ("384k" if ch > 2 else "192k")
        args += [f"-b:a:{out_i}", rate]
    return args


def build_plan(info: MediaInfo, grid: FrameGrid, start: float, end: float,
               opts: ExportOptions, output_path: str, ffmpeg: str = "ffmpeg") -> ExportPlan:
    """Build the FFmpeg invocation that writes the trimmed clip to `output_path`."""
    total = grid.end_time if grid.exact else max(info.duration, grid.end_time)
    if not (0 <= start < end):
        raise PlanError("The start of the selection must be before its end.")
    if end - start < 1e-3:
        raise PlanError("The selection is too short to export.")
    end = min(end, total + 1.0)

    container = resolve_container(info, opts)
    if container not in CONTAINERS:
        raise PlanError(f"Unsupported output format: {container}")
    muxer = CONTAINERS[container][0]
    audio = selected_audio(info, opts)
    notes: list[str] = []
    eps = min(0.001, grid.min_interval / 2)

    args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if opts.method == TrimMethod.FAST:
        reason = copy_compatible(info, container, audio)
        if reason:
            raise PlanError(reason + " Use Precise Trim or choose MKV.")
        actual = fast_trim_start(grid, start)
        if actual < start - 1e-6:
            notes.append(f"Fast Trim starts at the previous keyframe, {start - actual:.3f}s earlier than selected.")
        if info.video.has_b_frames:
            notes.append("This video uses B-frames, so a Fast Trim may end a few frames late.")
        abs_start = actual + grid.origin
        seek = abs_start + eps if abs_start > 0 else 0.0
        duration = end - actual
        # Copied packets are limited relative to the seek point; stop just short of `end`.
        args += _seek_args(info, seek, end + grid.origin - seek - eps)
        args += ["-map", f"0:{info.video.index}"] + [x for i in audio for x in ("-map", f"0:{i}")]
        args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
        vcodec = None
    else:
        actual = start
        duration = end - start
        seek = max(0.0, start + grid.origin - eps)
        args += _seek_args(info, seek, duration)
        head = list(args)                       # everything up to the input (shared by both passes)
        args += ["-map", f"0:{info.video.index}"] + [x for i in audio for x in ("-map", f"0:{i}")]
        vcodec = resolve_video_codec(container, opts.video_codec)
        budget = None
        if opts.size_limit_mb:
            budget = size_budget(info, duration, len(audio), opts.size_limit_mb, opts.size_scale)
            # A bare name, resolved against work_dir: x265's options use ':' as a separator, so a
            # Windows path ("C:/...") would break them.
            passlog = f"swiftcut-2pass-{secrets.token_hex(4)}"
            lib, vargs = _sized_video_args(vcodec, budget.video_kbps, passlog, 2)
            if opts.encoder == Encoder.NVENC:
                notes.append("Size limits use the CPU encoder, which hits a target size reliably.")
        else:
            lib, vargs = _video_encoder_args(info, vcodec, opts, notes)
        w, h = info.video.display_size
        vf: list[str] = []
        if _needs_even_dims(lib) and (w % 2 or h % 2):
            vf = ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]
            notes.append(f"{w}x{h} has an odd dimension; 1 px of padding was added because "
                         f"{VIDEO_CODECS[vcodec].split(' ')[0]} requires even sizes.")
        args += vf + vargs
        args += _audio_encoder_args(container, info, audio, budget.audio_kbps if budget else None)
        args += ["-fps_mode", "passthrough"]
        if budget is not None:
            _lib, p1 = _sized_video_args(vcodec, budget.video_kbps, passlog, 1)
            first_pass = head + ["-map", f"0:{info.video.index}"] + vf + p1 + [
                "-an", "-fps_mode", "passthrough", "-progress", "pipe:1", "-nostats", "-f", "null", "-"]
            notes.append(f"Two-pass encode at {budget.video_kbps} kbit/s video to stay under "
                         f"{opts.size_limit_mb:g} MB.")

    if opts.strip_metadata:
        # Drop container + stream metadata (titles, dates, GPS location, comments), write no
        # encoder/version tags, and remove encoder-info SEI from re-encoded H.264/H.265.
        # Rotation is display-matrix side data, not metadata, so orientation survives.
        args += ["-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact",
                 "-flags:a", "+bitexact", "-metadata:s:v", "encoder="]
        if vcodec == "h264":
            args += ["-bsf:v", "filter_units=remove_types=6"]
        elif vcodec == "hevc":
            args += ["-bsf:v", "filter_units=remove_types=39|40"]
    else:
        args += ["-map_metadata", "0"]
    args += ["-map_chapters", "-1", "-sn", "-dn"]
    if muxer in ("mp4", "mov"):
        args += ["-movflags", "+faststart"]
    args += ["-progress", "pipe:1", "-nostats", "-f", muxer, output_path]
    plan = ExportPlan(args=args, output_path=output_path, duration=duration, actual_start=actual,
                      container=container, video_codec=vcodec, audio_streams=audio, notes=notes)
    if opts.method == TrimMethod.PRECISE and opts.size_limit_mb:
        plan.pre_passes = [first_pass]
        plan.size_limit_bytes = int(opts.size_limit_mb * MB)
        plan.work_dir = tempfile.gettempdir()
        plan.cleanup_prefix = os.path.join(plan.work_dir, passlog)
    return plan
