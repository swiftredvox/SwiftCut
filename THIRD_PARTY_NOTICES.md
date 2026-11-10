# Third-party notices

SwiftCut's own code is released under the MIT licence (see `LICENSE`). The packaged app also
includes the components below. Each keeps its own licence; the full texts are in the
`licenses` folder.

## Qt for Python (PySide6) and Qt 6.12.0
- Licence: GNU Lesser General Public License v3 (`licenses/LGPL-3.0.txt`, which builds on
  `licenses/GPL-3.0.txt`).
- Copyright (C) The Qt Company Ltd. and other contributors.
- Source code: https://download.qt.io/official_releases/QtForPython/ and https://code.qt.io/
- Qt's libraries ship as separate, replaceable files in the app folder, as the LGPL requires.

## FFmpeg 9.0.2 (Windows "essentials" build by gyan.dev)
- Licence: GNU General Public License v3 (`licenses/GPL-3.0.txt`). This build is configured with
  `--enable-gpl --enable-version3`.
- Copyright (C) 2000-2026 the FFmpeg developers.
- Source code for this exact version: https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz
  (a copy is also attached to every SwiftCut release on GitHub)
  Build details: https://www.gyan.dev/ffmpeg/builds/
- SwiftCut runs `ffmpeg.exe` and `ffprobe.exe` as separate programs; it does not link to them.

## Python 3.12
- Licence: Python Software Foundation License (`licenses/Python-PSF.txt`).
- Copyright (C) Python Software Foundation; see the licence file for full details.
- Source code: https://www.python.org/downloads/source/

## Icons and artwork
The SwiftCut logo and interface icons are original to this project (MIT).
