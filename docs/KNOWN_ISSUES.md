# Known issues and limitations

## Behaviour

- **Preview seek speed depends on keyframe spacing.** Qt decodes from the previous keyframe
  for every seek and frame step. Typical ShareX settings (`keyint=250`, about every 8.3 s at
  30 fps) give a worst case of about 0.3 s at 1772×996, measured. A pathological file with a
  *single* keyframe over 60 s measured 1.8 s to seek near its end. Scrubbing stays smooth
  because seeks are coalesced; the preview just catches up a moment later. Exports are
  unaffected.
- **MPEG-TS/MTS preview seeking is inaccurate** (a Qt Multimedia limitation: asked for 1.0 s it
  showed 2.13 s). Exports from these files are still frame-exact (tested), because SwiftCut
  switches to decode-from-start seeking for unindexed formats, which is slower on long files.
- **Odd dimensions** (e.g. 1001×601) get 1 px of black padding for H.264/H.265 (both codecs
  require even sizes). VP9 (WebM/MKV) keeps the exact size.
- **Not carried into exports:** subtitles, chapters, data tracks and attachments. Container
  metadata (e.g. creation time) is kept.
- **Precise Trim re-encodes**, so there's a small generational quality loss. At the default
  setting (CRF 16) it's visually lossless. 
- **Preview plays one audio track at a time** (a Qt limitation). With several tracks selected
  in Advanced mode you hear the first. All selected tracks are saved.
- **Suggested trim needs truly frozen footage.** A moving mouse pointer or a ticking clock
  counts as motion, so no suggestion appears there. That's deliberate, to avoid cutting real
  content.
- **Explorer entry sits under "Show more options"** on Windows 11. Shift + right-click opens
  that full menu directly. (Windows 11 only allows top-level entries for Store-packaged apps.)
- **Estimated size is approximate**: usually within about 10%, but mostly-static long
  recordings can come in 20–30% smaller than estimated.
