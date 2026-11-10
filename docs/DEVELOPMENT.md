# Developing SwiftCut

This page is for people who want to build or change SwiftCut from its source code. You don't
need any of it to use SwiftCut: just download it from the Releases page (see the README).

## Setup and everyday commands

| What | How |
|---|---|
| First-time setup | `setup.bat` (creates `.venv` and installs exact, checksum-verified packages from `requirements-dev.txt`) |
| Pinned FFmpeg | `powershell -ExecutionPolicy Bypass -File tools\get_ffmpeg.ps1` (version, URL and SHA-256 in `packaging\ffmpeg.lock`) |
| Run | `run.bat` (or `tools\make_shortcut.ps1` for Start menu / desktop shortcuts to the dev build) |
| Run with a debug console | `run-debug.bat` |
| Tests | `test.bat` (about 190 unit, integration and UI tests; UI windows open unfocused on a secondary monitor) |
| Generate test videos (optional) | `.venv\Scripts\python tools\make_samples.py` (writes synthetic clips to `samples\`; not part of the app or the repo) |
| Benchmark | `.venv\Scripts\python tools\benchmark.py` |
| Update locked packages | upgrade in `.venv`, then `.venv\Scripts\python tools\lock_deps.py` |
| Release | `powershell -ExecutionPolicy Bypass -File packaging\release.ps1` (tests, app folder, ZIP, installer, FFmpeg source, SHA256SUMS in `dist\release\`) |

Requirements: Windows 10/11 x64, Python 3.11+ (3.12 used), Inno Setup 6 for the installer
(`winget install JRSoftware.InnoSetup`). SwiftCut finds FFmpeg in this order:
`$SWIFTCUT_FFMPEG_DIR`, the bundled or `vendor\ffmpeg` copy, PATH, then common install folders.

There is nothing to rebuild after a code change: edit, then run `run.bat` again.

## Releasing a new version

1. Bump `__version__` in `swiftcut/__init__.py` (and `pyproject.toml`), and add a CHANGELOG entry.
2. Run `packaging\release.ps1`. It refuses to continue if any test fails.
3. Create the GitHub release (draft first) and upload everything from `dist\release\`. See
   `docs/PACKAGING.md`.

## Project layout

```
SwiftCut.pyw            windowless launcher (dev); the packaged build runs SwiftCut.exe
swiftcut/
  app.py                startup, logging, theme, CLI switches (--version, --register-shell, ...)
  single_instance.py    hand files to an already-running window
  diagnostics.py        version/system summary for bug reports (no file names)
  core/                 Qt-free logic, unit-testable
    ffmpeg_tools.py     find/run FFmpeg, encoder detection
    media_info.py       ffprobe metadata + exact per-frame timestamps and keyframes
    timecode.py         time parsing/formatting, FrameGrid (frame timing model)
    export_plan.py      selection + options -> FFmpeg command (incl. size limits / two-pass)
    exporter.py         temp file -> validate -> atomic replace; cancel; progress
    animation.py        GIF / animated WebP plans and size estimates
    estimate.py         output-size estimate from sample encodes
    stillness.py        suggested trim points (frozen footage at start / end)
    thumbnails.py       filmstrip and zoomed-frame extraction
    snapshot.py         save a frame as PNG
    paths.py            output naming, temp paths, writability checks
    shell_integration.py  Explorer "Trim with SwiftCut" (per-user registry)
  ui/                   PySide6 interface (main_window, player, timeline, dialogs, widgets...)
  resources/            app icon
tests/                  unit/, integration/ (real FFmpeg exports checked frame by frame), UI tests
tools/                  benchmark, sample generator, icon, shortcuts, dependency lock, FFmpeg fetch
packaging/              PyInstaller spec, build/release scripts, Inno Setup script, ffmpeg.lock
docs/                   architecture, packaging, known issues, images
```

## Performance (desktop PC with an NVIDIA GPU)

Exports trim the middle half of generated clips (`tools/benchmark.py`).

| Clip | Open + frame scan | Filmstrip | Save (CPU) | Save (GPU) |
|---|---|---|---|---|
| 720p30, 30 s | 0.10 s | 0.4 s | 1.6 s | 1.3 s |
| 1772×996 30 fps, 60 s (ShareX-like) | 0.12 s | 0.9 s | 3.9 s | 2.6 s |
| 1080p60, 60 s | 0.18 s | 1.5 s | 7.2 s | 5.4 s |
| 4K30, 20 s | 0.26 s | 1.4 s | 5.3 s | 4.2 s |
| 720p30, 10 min | 0.40 s | 2.1 s | 31 s | 19 s |

App start to window: about 0.6 to 0.8 s.

See also [ARCHITECTURE.md](ARCHITECTURE.md), [KNOWN_ISSUES.md](KNOWN_ISSUES.md) and
[PACKAGING.md](PACKAGING.md).
