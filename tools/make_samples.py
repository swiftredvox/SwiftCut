"""Generate a folder of test videos for trying SwiftCut by hand.

Usage:  .venv/Scripts/python tools/make_samples.py [output folder]
Default folder: the project's samples/ folder (excluded from git and from builds)

Every video shows its frame number (counting from 1, like SwiftCut's readout) and timecode, so
you can check that cuts land exactly where you expect. Audio tracks use distinct, recognisable sounds so you can tell them apart.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from swiftcut.core.ffmpeg_tools import find_ffmpeg  # noqa: E402

FONT = "C\\:/Windows/Fonts/consola.ttf"


def overlay(title: str, fps: int, size: int = 0) -> str:
    """drawtext filters: sample title, big frame counter and timecode."""
    fs = size or 48
    title = title.replace(":", "\\\\\\:")   # ':' must be double-escaped inside a filtergraph
    box = "box=1:boxcolor=black@0.55:boxborderw=12"
    return (f"drawtext=fontfile='{FONT}':text='{title}':x=(w-tw)/2:y=h*0.08:fontsize={int(fs * 0.6)}:"
            f"fontcolor=white:{box},"
            f"drawtext=fontfile='{FONT}':text='frame %{{eif\\:n+1\\:d}}':x=(w-tw)/2:y=h*0.42:fontsize={fs}:"
            f"fontcolor=0x2ee6d6:{box},"
            f"drawtext=fontfile='{FONT}':text='%{{pts\\:hms}}':x=(w-tw)/2:y=h*0.42+{int(fs * 1.4)}:"
            f"fontsize={int(fs * 0.8)}:fontcolor=white:{box}")


def run(args: list[str]) -> None:
    ff = find_ffmpeg().ffmpeg
    res = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr[-1500:])


def video(size: str, fps: int, secs: float, title: str, fs: int = 0) -> list[str]:
    return ["-f", "lavfi", "-i", f"testsrc2=s={size}:r={fps}:d={secs},{overlay(title, fps, fs)}"]


def make_all(out: Path) -> list[tuple[str, str]]:
    out.mkdir(parents=True, exist_ok=True)
    made: list[tuple[str, str]] = []

    def done(name: str, what: str) -> None:
        made.append((name, what))
        print(f"  + {name}")

    h264 = ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]

    # 1. Multi-track audio (MKV and MP4)
    for ext in ("mkv", "mp4"):
        name = f"01 Multi-track audio (3 tracks).{ext}"
        run([*video("1280x720", 30, 20, "3 audio tracks - toggle them in Advanced"),
             "-f", "lavfi", "-i", "sine=frequency=110:sample_rate=48000:duration=20",          # low hum
             "-f", "lavfi", "-i", "sine=frequency=1000:beep_factor=6:sample_rate=48000:duration=20",  # beeps
             "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.15:sample_rate=48000:duration=20",
             "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:a", *h264,
             "-c:a", "aac", "-b:a", "128k",
             "-metadata:s:a:0", "title=Game audio (low hum)",
             "-metadata:s:a:1", "title=Microphone (beeps)",
             "-metadata:s:a:2", "title=Music (static noise)",
             "-disposition:a:0", "default", str(out / name)])
        done(name, "Three labelled audio tracks you can hear apart: low hum, beeps, static.")

    # 2-6. Unusual shapes
    shapes = [
        ("02 Ultra-wide 32x9 (3840x1080).mp4", "3840x1080", "Ultra-wide 32:9", 60),
        ("03 Portrait phone 9x16 (1080x1920).mp4", "1080x1920", "Portrait 9:16", 64),
        ("04 Square 1x1 (1080x1080).mp4", "1080x1080", "Square 1:1", 56),
        ("05 Tall banner (480x1600).mp4", "480x1600", "Tall 3:10", 36),
    ]
    for name, size, title, fs in shapes:
        run([*video(size, 30, 12, title, fs), "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
             *h264, "-c:a", "aac", "-shortest", str(out / name)])
        done(name, f"Unusual aspect ratio ({size}).")

    name = "06 Odd size 1001x601 (VP9).webm"
    run([*video("1001x601", 30, 10, "Odd 1001x601", 40), "-f", "lavfi", "-i", "sine=frequency=330:duration=10",
         "-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-deadline", "realtime", "-cpu-used", "8",
         "-c:a", "libopus", "-shortest", str(out / name)])
    done(name, "Odd dimensions. Saving as MP4 adds 1px padding; saving as WebM keeps it exact.")

    name = "07 Rotated phone clip (stored sideways).mp4"
    tmp = out / "_rot_tmp.mp4"
    run([*video("1280x720", 30, 10, "Should appear PORTRAIT", 48), *h264, str(tmp)])
    run(["-display_rotation:v:0", "90", "-i", str(tmp), "-c", "copy", str(out / name)])
    tmp.unlink(missing_ok=True)
    done(name, "Stored landscape with a 'rotate 90' flag, like phone videos. It should show and save as portrait.")

    # 8-10. Timing
    name = "08 60fps frame stepping.mp4"
    run([*video("1920x1080", 60, 15, "60 fps - step with A and D"), *h264, str(out / name)])
    done(name, "60 fps with a frame counter, for checking frame-by-frame stepping.")

    name = "09 Variable frame rate.mkv"
    tmp = out / "_vfr_tmp.mp4"
    run([*video("1280x720", 30, 15, "Variable frame rate"), *h264, str(tmp)])
    run(["-i", str(tmp), "-vf", "select='not(eq(mod(n\\,3)\\,1))'", "-fps_mode", "passthrough", *h264,
         str(out / name)])
    tmp.unlink(missing_ok=True)
    done(name, "Every third frame missing, so frame numbers skip (2, 3, 5, 6...). Durations vary.")

    name = "10 AV sync (flash + beep every second).mp4"
    flash = ("drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='lt(mod(t\\,1)\\,0.1)'")
    run(["-f", "lavfi", "-i", f"testsrc2=s=1280x720:r=30:d=20,{flash},{overlay('Flash and beep together?', 30)}",
         "-f", "lavfi", "-i", "sine=frequency=1000:beep_factor=8:duration=20", *h264, "-c:a", "aac",
         "-shortest", str(out / name)])
    done(name, "The screen flashes white exactly when the beep sounds. Trim it and check they still match.")

    # 11-15. Formats and real-world shapes
    name = "11 ShareX-style (no audio, 1772x996).mp4"
    run([*video("1772x996", 30, 20, "ShareX-like recording"), "-c:v", "libx264", "-preset", "ultrafast",
         "-profile:v", "baseline", "-crf", "28", "-g", "250", "-pix_fmt", "yuv420p", str(out / name)])
    done(name, "Encoded like typical ShareX captures (baseline H.264, keyframe every 8.3s, no audio).")

    name = "12 QuickTime.mov"
    run([*video("1920x1080", 30, 10, "MOV container"), "-f", "lavfi", "-i", "sine=frequency=550:duration=10",
         *h264, "-c:a", "aac", "-shortest", str(out / name)])
    done(name, "MOV container.")

    name = "13 Camera style MPEG-TS.ts"
    run([*video("1920x1080", 30, 10, "MPEG-TS (camera style)"), "-f", "lavfi", "-i", "sine=frequency=660:duration=10",
         *h264, "-c:a", "aac", "-shortest", "-output_ts_offset", "1.4", str(out / name)])
    done(name, "MPEG-TS with a non-zero start time. The preview may seek imprecisely (a Qt limitation); saving is exact.")

    name = "14 Long 10 minutes (720p).mp4"
    run(["-f", "lavfi", "-i", f"testsrc=s=1280x720:r=30:d=600,{overlay('10 minute video', 30)}",
         "-f", "lavfi", "-i", "sine=frequency=220:beep_factor=4:duration=600",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k",
         "-shortest", str(out / name)])
    done(name, "Long file: check filmstrip loading, scrubbing and export progress/cancel.")

    name = "16 Still start and end (press T).mp4"
    run(["-f", "lavfi", "-i", f"testsrc2=s=1280x720:r=30:d=6,tpad=start_mode=clone:start_duration=2"
         f":stop_mode=clone:stop_duration=3,{overlay('Still at start and end - press T', 30)}",
         "-f", "lavfi", "-i", "sine=frequency=500:duration=11", *h264, "-c:a", "aac", "-shortest", str(out / name)])
    done(name, "2s frozen at the start and 3s at the end, like a screen recording. "
               "Dashed lines mark suggested trim points; press T to apply them.")

    name = "15 Not really a video.mp4"
    (out / name).write_text("This is a text file pretending to be a video.\n", encoding="utf-8")
    done(name, "A fake file. SwiftCut should show a clear error, not crash.")

    readme = out / "About these samples.txt"
    lines = ["SwiftCut sample videos", "======================", "",
             "Each video shows its frame number and time, so you can check cuts exactly.", ""]
    lines += [f"{n}\n    {w}\n" for n, w in made]
    readme.write_text("\n".join(lines), encoding="utf-8")
    return made


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "samples"
    print(f"Writing samples to {target}")
    make_all(target)
    print("Done.")


if __name__ == "__main__":
    main()
