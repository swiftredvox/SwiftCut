"""Optional Explorer right-click command, "Trim with SwiftCut", registered per-user (no admin).

Registered under HKCU\\Software\\Classes\\SystemFileAssociations\\.<ext>\\shell\\SwiftCut.Trim,
which adds the verb regardless of which app is the default for that file type. Removing it
deletes exactly those keys and nothing else. On Windows 11 it appears under "Show more options"
(classic menu); the compact top-level menu only accepts packaged (MSIX) extensions.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

VERB_KEY = "SwiftCut.Trim"
VERB_TEXT = "Trim with SwiftCut"
DEFAULT_BASE = r"Software\Classes\SystemFileAssociations"
VIDEO_EXTENSIONS = ["mp4", "m4v", "mov", "mkv", "webm", "avi", "wmv", "flv",
                    "mpg", "mpeg", "ts", "mts", "m2ts", "3gp", "ogv"]


def launch_command() -> str:
    """The command Explorer runs, with %1 for the clicked file."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        exe = pythonw
    script = Path(__file__).resolve().parents[2] / "SwiftCut.pyw"
    return f'"{exe}" "{script}" "%1"'


def icon_path() -> str:
    if getattr(sys, "frozen", False):
        return f"{sys.executable},0"
    return str(Path(__file__).resolve().parents[1] / "resources" / "swiftcut.ico")


def _winreg():
    import winreg  # Windows only
    return winreg


def _verb_path(ext: str, base: str) -> str:
    return rf"{base}\.{ext}\shell\{VERB_KEY}"


def is_enabled(base: str = DEFAULT_BASE) -> bool:
    if sys.platform != "win32":
        return False
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _verb_path("mp4", base) + r"\command"):
            return True
    except OSError:
        return False


def registered_command(base: str = DEFAULT_BASE) -> str | None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _verb_path("mp4", base) + r"\command") as k:
            return winreg.QueryValueEx(k, "")[0]
    except OSError:
        return None


def enable(base: str = DEFAULT_BASE, extensions: list[str] | None = None) -> None:
    winreg = _winreg()
    cmd, icon = launch_command(), icon_path()
    for ext in extensions or VIDEO_EXTENSIONS:
        path = _verb_path(ext, base)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, VERB_TEXT)
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
            # Single-file command: hide it when several files are selected.
            winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Single")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path + r"\command", 0, winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
    _notify_shell()
    log.info("context menu enabled: %s", cmd)


def disable(base: str = DEFAULT_BASE, extensions: list[str] | None = None) -> None:
    winreg = _winreg()
    for ext in extensions or VIDEO_EXTENSIONS:
        path = _verb_path(ext, base)
        for sub in (path + r"\command", path):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except FileNotFoundError:
                pass
            except OSError as e:
                log.warning("could not delete %s: %s", sub, e)
    _notify_shell()
    log.info("context menu removed")


def refresh_if_stale(base: str = DEFAULT_BASE) -> None:
    """If enabled but pointing at an old location (project moved), rewrite the command."""
    if is_enabled(base) and registered_command(base) != launch_command():
        enable(base)


def _notify_shell() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:  # noqa: BLE001 - purely cosmetic refresh
        pass
