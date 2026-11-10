"""Diagnostic summary for bug reports (no file names or personal data)."""
from __future__ import annotations

import platform
import sys
from urllib.parse import urlencode

from . import ISSUES_URL, __version__


def windows_version() -> str:
    try:
        release, version, _csd, _ptype = platform.win32_ver()
        build = int(version.split(".")[-1]) if version else 0
        name = "Windows 11" if release in ("10", "11") and build >= 22000 else f"Windows {release}"
        return f"{name} (build {version})"
    except Exception:  # noqa: BLE001
        return platform.platform()


def summary() -> str:
    """Plain-text diagnostics: versions and capabilities only."""
    from PySide6.QtCore import qVersion
    from PySide6.QtGui import QGuiApplication

    from .core import ffmpeg_tools
    lines = [
        f"SwiftCut {__version__} ({'installed build' if getattr(sys, 'frozen', False) else 'development'})",
        f"{windows_version()}",
        f"Qt {qVersion()}, Python {sys.version.split()[0]}",
        f"FFmpeg {ffmpeg_tools.ffmpeg_version() or 'not found'}",
        f"NVIDIA encoder: {'available' if ffmpeg_tools.encoder_works('h264_nvenc') else 'not available'}",
    ]
    app = QGuiApplication.instance()
    if app is not None:
        screens = [f"{s.size().width()}x{s.size().height()} @ {s.devicePixelRatio():g}x" for s in app.screens()]
        lines.append("Screens: " + ", ".join(screens))
    return "\n".join(lines)


def report_url() -> str:
    """A new-issue link with the bug form pre-filled with version information."""
    from PySide6.QtCore import qVersion
    from .core import ffmpeg_tools
    params = {
        "template": "bug_report.yml",
        "version": __version__,
        "windows": windows_version(),
        "environment": f"Qt {qVersion()}, FFmpeg {ffmpeg_tools.ffmpeg_version() or '?'}",
    }
    return f"{ISSUES_URL}/new?{urlencode(params)}"
