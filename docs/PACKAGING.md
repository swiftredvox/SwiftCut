# Packaging and releasing

## What a release contains (`dist\release\`)

| File | What it is |
|---|---|
| `SwiftCut-x.y.z-Setup.exe` | Inno Setup installer: per-user (no admin), Start menu, optional desktop shortcut, optional Explorer entry, upgrades in place, clean uninstall |
| `SwiftCut-x.y.z-portable.zip` | The app folder: unzip and run `SwiftCut.exe` |
| `ffmpeg-9.0.2.tar.xz` | Source of the bundled FFmpeg (GPL-3.0 requires offering it) |
| `SHA256SUMS.txt` | Checksums of all of the above |

Build everything with:

```
powershell -ExecutionPolicy Bypass -File packaging\release.ps1
```

The script runs the full test suite (and stops if anything fails), fetches the pinned FFmpeg
(`packaging\ffmpeg.lock`, checksum-verified), builds the app folder with PyInstaller (Qt
translations are dropped; SwiftCut is English-only), checks that the built `SwiftCut.exe
--version` matches, then creates the ZIP, the installer, the FFmpeg source copy and the checksums.

## The installer (`packaging\swiftcut.iss`)

- Installs to `%LOCALAPPDATA%\Programs\SwiftCut` with `PrivilegesRequired=lowest`, so no UAC prompt.
- Tasks: the Explorer entry (on by default; runs `SwiftCut.exe --register-shell`) and a desktop
  shortcut (off by default). Unticking the Explorer task on an upgrade runs `--unregister-shell`.
- Uninstall runs `--unregister-shell` and removes `HKCU\Software\SwiftCut` (settings) and
  `%LOCALAPPDATA%\SwiftCut` (logs).
- The AppId is fixed, so new versions upgrade in place. SwiftCut is closed first if it's running.
- The installed app re-points the Explorer entry at itself on startup if it was moved or upgraded.

## Publishing on GitHub

1. Commit and push the version bump and CHANGELOG.
2. `gh release create vX.Y.Z dist/release/* --draft --title "SwiftCut X.Y.Z" --notes-file <notes>`
3. Test the draft's downloads on another PC.
4. Publish the draft.

## Not signed

Releases are unsigned: SwiftCut is a zero-budget project and signing certificates cost money every
year. Windows SmartScreen shows "Windows protected your PC" the first time, and Smart App
Control (where it's on) blocks unsigned apps outright. The README explains both, and the
optional SHA-256 checks.

## Known caveats

- **The first launch after installing can take a few extra seconds** while Windows Defender
  scans the bundled FFmpeg (about 200 MB).
- **Download size is about 130 MB** (ZIP), mostly FFmpeg's "essentials" build. A custom slim
  FFmpeg was considered and dropped (effort and risk).
