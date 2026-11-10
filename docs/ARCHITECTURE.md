# SwiftCut: architecture and decisions

## Stack: Python 3.12 + PySide6 (Qt 6.12) + FFmpeg

| Option | Startup | Playback | Dev speed | Packaging | Verdict |
|---|---|---|---|---|---|
| **Python + PySide6 + FFmpeg** | ~0.7 s | Qt Multimedia (FFmpeg backend, HW decode) | Fastest to iterate, no compile | PyInstaller (verified) | **Chosen** |
| C# WPF + LibVLCSharp/FFmpeg | ~0.3 s | VLC or MediaElement (weak seeking) | Good, needs the .NET SDK | Excellent | Close second |
| C++ Qt + FFmpeg | ~0.2 s | Same Qt Multimedia | Slow iteration, build toolchain | Good | Overkill |
| Electron/Tauri | 1 s+ | `<video>`: no MKV, poor frame stepping | Good | Good | Rejected |

Qt 6.5+ ships an FFmpeg-based multimedia backend, so preview and export share the same decoding
family. That gives consistent format support (MP4/MOV/MKV/WebM/AVI…) and hardware decode. A stack
that runs from source with no build step was weighted heavily.

## Core idea: one frame-timing model

`FrameGrid` holds the presentation timestamp of every frame, read once with `ffprobe` packet
scanning, which needs no decoding (0.05 to 0.4 s even for 10-minute files). Everything snaps to
it:

- Times start at 0 on the **first video frame**. `FrameGrid.origin` records where that frame
  sits in the container timeline (audio often starts ~21 ms earlier in MKV/TS), and every
  FFmpeg seek adds it. The preview calibrates against the first frame Qt actually shows.
- The selection is stored as **inclusive frame indices** (`TrimModel`), never as free floats.
  `start = times[first]` and `end = frame_end(last)`.
- Until the scan finishes, a uniform grid from the nominal FPS is used and labelled "~".
- Variable frame rate works naturally because real timestamps are used.

**Preview.** Qt reports milliseconds and shows the last frame whose start time is strictly
before the requested position (measured). `PlayerController` therefore seeks to
`floor(t_ms) + 1` and treats the timestamp of the frame actually shown as the truth. Scrubbing
seeks are coalesced so slow-to-seek files (e.g. ShareX clips with only one keyframe) stay
responsive.

**Export** (`export_plan.py`, pure and unit-tested):

- *Precise*: input `-ss (start - ε)`, output `-t (end - start)`, re-encode,
  `-fps_mode passthrough` (no frame duplication or drop for VFR). ε = min(1 ms, half the
  shortest frame interval) guards against float rounding.
- *Fast*: stream copy from the keyframe at or before `start`. The UI shows that start point
  before saving. With B-frames FFmpeg limits copied packets by decode order, so the end can run
  a few frames over. This is detected (`has_b_frames`) and disclosed.
- Odd dimensions: H.264/H.265 need even sizes, so 1 px of black padding is added (never
  cropping or scaling) and the user is told. VP9 and Fast Trim keep exact sizes.
- Rotation metadata is applied when re-encoding, so orientation is preserved. 10-bit sources
  stay 10-bit with H.265; H.264 output is 8-bit for compatibility (with a note).
- Default quality: x264 `veryfast` at CRF 16. It measured about 2× faster than `medium`/CRF 18
  with equal-or-better SSIM, for about 8% more size. NVENC (`p5`, CQ 19) is offered only when a
  test encode succeeds on this machine. It stays opt-in: on short 1080p-class clips it measured
  *slower* than the CPU (encoder start-up cost) and made files about 50% larger, and it only
  wins on long or 4K exports.
- Audio is re-encoded (AAC, or Opus for WebM) in Precise mode, so it is cut sample-accurately.
  It is copied in Fast mode. Subtitles, data and attachments are not carried over.

## Safety

`exporter.run_export` writes to a uniquely named temp file beside the destination, validates it
with ffprobe (video stream present, duration plausible, audio present if expected), then
`os.replace`s it into place (atomic on one volume, retried if antivirus briefly locks it). On
error or cancel the temp file is deleted and the destination is untouched. When overwriting the
**source**, the preview player releases the file first. The trimmed file is reopened afterwards.
If the export fails or is cancelled, the original is reopened with the selection restored.
Leftover temp files from a crash are cleaned up when a file in that folder is next opened.

## Threading

Nothing slow runs on the UI thread. Probe, frame scan, thumbnails, NVENC detection and export each
run on a Python thread and report back through Qt signals (queued to the GUI thread). Each load
carries a token, so results for a file that has since been replaced are ignored. Thumbnails use
one decode pass for clips up to 90 s, and parallel keyframe seeks (4 workers, coarse-to-fine
order) for longer ones.

## Explorer integration

Per-user verb under `HKCU\Software\Classes\SystemFileAssociations\.<ext>\shell\SwiftCut.Trim`
for 15 video extensions. No admin rights are needed. It works whatever app is the default player,
and is removed by deleting exactly those keys. On Windows 11 it appears under *Show more
options*. The compact menu requires an MSIX-packaged shell extension. The installer registers
the entry with `--register-shell`, and if the app is moved or upgraded, `refresh_if_stale()`
rewrites the command on the next start.

## Feature notes

- **Single window** (`single_instance.py`): the first instance listens on a per-user local
  socket; later launches send `{"open": path}` and exit. The server owns its client sockets
  explicitly and never acts from inside a socket signal. An earlier version crashed on
  shutdown, which a regression test now covers.
- **Suggested trim** (`core/stillness.py`): FFmpeg `freezedetect` on a 320 px copy, only on the
  first and last 60 s of long files (about 0.1 s for typical clips). The noise threshold is
  -50 dB: -55 dB missed encoder-noisy stills, and -45 dB flagged real motion. It keeps one still
  frame at each side.
- **Undo/redo**: `TrimModel` stores history as times (so it survives the exact-timestamp grid
  replacing the provisional one). Bursts of nudges collapse into one step after 0.7 s of
  quiet; discrete actions (set start/end, reset, typed times, T) are each one step.
- **Preview audio = saved audio**: the player plays the first saved track, or is silent if
  none will be saved. This is layered on top of the user's own preview mute.
- **Metadata stripping**: `-map_metadata -1`, bitexact flags (no encoder tags), and removal of
  encoder-info SEI from re-encoded H.264/H.265. Copied bitstreams are never altered.
- **Drag and drop**: Qt's video widget has an inner render surface that accepted drags and
  then rejected them. Drops are now disabled on it, so they reach the window.
- **Supply chain**: `requirements*.txt` pin exact versions with SHA-256 hashes (generated by
  `tools/lock_deps.py`, verified with `pip --require-hashes`). FFmpeg is pinned in
  `packaging/ffmpeg.lock` and verified by `tools/get_ffmpeg.ps1`.
- **Quiet tests**: UI tests show windows on the secondary monitor, unfocused and behind other
  windows (`tests/ui_helpers.py`). Shortcuts are fired directly, so no keyboard focus is
  needed. Screenshots use `PrintWindow`, so covered windows still capture.
- **Size limits**: `size_budget()` turns MB plus duration into video and audio bitrates (decimal
  MB, 4% margin, audio drops to 64 kbit/s when tight), refusing when under 60 kbit/s.
  Two-pass CPU encode (x264/x265/VP9; NVENC can't target size reliably). Pass logs live in
  %TEMP% under a bare name, because x265's option syntax uses ':' (so no `C:` paths), with
  FFmpeg's cwd set there. If the result overshoots, `ExportJob` re-plans with a proportionally
  smaller budget, up to twice.
- **Hover preview**: a frameless tooltip-type window (child widgets were hidden under the
  GPU-drawn video surface). It shows the nearest filmstrip thumbnail instantly, then the exact
  frame from a "latest request wins" decoder thread with a 150-frame LRU cache.
- **Thumbnail sampling fix**: thumbnails now sample exactly at slot centres
  (`setpts=PTS-half/TB,fps=...:start_time=0:round=up`, verified frame by frame). The old
  filter chain drifted by a quarter of a slot.
- **Licensing**: MIT for SwiftCut. Third-party texts are in `licenses/`, and
  `THIRD_PARTY_NOTICES.md` is bundled and shown in Settings → About → Licences & credits.
- **Precise only**: the UI always plans `TrimMethod.PRECISE`. The FAST path in `export_plan.py`
  stays implemented and tested, but is unused.
- **Size estimate** (`core/estimate.py`): encodes three 2 s slices (at 20/50/80%) with the exact
  settings into Matroska on a pipe, measures bytes/s, scales by the selection length, and adds
  the audio bitrate. The rate is cached per settings key (file, container, codec, quality,
  encoder), so trimming updates instantly and only setting changes trigger re-measurement
  (debounced 450 ms). Measured accuracy: ±3% on busy footage, about +25% on long static clips
  (every sample carries a keyframe).

## Preview robustness

- **Time boxes** apply only on Return. Esc or clicking elsewhere discards, and an app-wide
  mouse filter makes sure a click anywhere ends the edit (nothing else takes keyboard focus).
  They show the live value unless the user is typing. A validator allows only digits, ':' and
  '.'. Space is refused at ShortcutOverride, so play/pause still works.
- **Seeks** are coalesced (frame steps and nudges included), and the playhead reports the
  requested frame immediately. While paused, a frame only updates the playhead if it is the
  latest requested one. Late frames from superseded seeks are ignored, and a frame arriving
  while a newer seek is queued just triggers that seek. A watchdog re-issues a seek that
  produced no picture (twice at most).
- **Audio-track switching** is deferred to a clean event-loop pass, and video-frame delivery is
  disconnected during `setActiveAudioTrack`. With it connected, Qt's switch could deadlock
  (Qt waiting on its decoder thread, which was waiting to call back into Python). Reproduced
  under test, and verified fixed over repeated runs.

## Timeline zoom and animations

- **Timeline zoom**: the timeline maps times through a visible window (`view_start`, `view_len`;
  0 = fit). Zoom keeps the anchor time under the mouse, with the deepest zoom at about 28 px per
  frame. Filmstrip slots are laid out on whole-video multiples so they don't shimmer while
  panning. Exact frames for visible slots are decoded in parallel (`thumbnails.frames_at`) and
  cached by frame index. The view follows the playhead only when it actually moves, so a
  routine update can't undo a manual zoom.
- **GIF / WebP** (`core/animation.py`): the same frame-exact seek as video saves.
  - GIF uses a per-clip palette (`palettegen stats_mode=diff` + `paletteuse sierra2_4a`, with
    rectangle diffing).
  - WebP uses `libwebp_anim` in lossy mode.
  - Neither is ever upscaled, metadata is always stripped, and output is validated by header
    (GIF is also probed).
  - The size estimate encodes a 2 s sample from the middle.
- **Test hygiene**: an autouse fixture answers every QMessageBox automatically, and the window
  fixture cancels running exports before closing. Tests never put a blocking dialog on screen.

## Trade-offs and deliberately not done

- No smart-cut (re-encode only the first GOP, copy the rest). It's faster for long files but
  fiddly to make reliable across codecs. Precise trim of short clips already takes seconds.

