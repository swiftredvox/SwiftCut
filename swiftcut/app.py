"""Application entry point: logging, theming, the main window and command-line file opening."""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

_T0 = time.perf_counter()


def log_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "SwiftCut" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def setup_logging() -> str:
    folder = log_dir()
    handlers: list[logging.Handler] = [
        # Two files of up to 512 KB each: logs never exceed about 1 MB in total.
        RotatingFileHandler(Path(folder) / "swiftcut.log", maxBytes=512_000, backupCount=1, encoding="utf-8")
    ]
    if sys.stderr is not None and not getattr(sys, "frozen", False):
        handlers.append(logging.StreamHandler())
    level = logging.DEBUG if os.environ.get("SWIFTCUT_DEBUG") else logging.INFO
    logging.basicConfig(level=level, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    return folder


def clear_logs(folder: str | None = None) -> int:
    """Empty the current log and delete rotated copies. Returns bytes freed."""
    folder = folder or log_dir()
    freed = 0
    for h in logging.getLogger().handlers:
        if isinstance(h, RotatingFileHandler) and Path(h.baseFilename).parent == Path(folder):
            h.acquire()
            try:
                if h.stream:
                    h.stream.flush()
                    freed += os.path.getsize(h.baseFilename)
                    h.stream.seek(0)
                    h.stream.truncate()
            finally:
                h.release()
    for p in Path(folder).glob("swiftcut.log*"):
        if p.name != "swiftcut.log":
            try:
                freed += p.stat().st_size
                p.unlink()
            except OSError:
                pass
    main_log = Path(folder) / "swiftcut.log"
    if main_log.exists() and not any(isinstance(h, RotatingFileHandler) for h in logging.getLogger().handlers):
        freed += main_log.stat().st_size
        main_log.write_text("", encoding="utf-8")
    return freed


def dark_title_bar(widget) -> None:
    """Ask DWM for a dark title bar matching the header colour (Windows 10 20H1+ / 11)."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))   # immersive dark mode
        caption = ctypes.c_int(0x001C1715)                                           # #15171c as BGR
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
        border = ctypes.c_int(0x00332A26)                                            # #262a33
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


def _cli(argv: list[str]) -> int | None:
    """Non-GUI switches (used by the installer). Returns an exit code, or None to start the app."""
    if "--version" in argv:
        from . import __version__
        print(__version__)
        return 0
    if "--register-shell" in argv or "--unregister-shell" in argv:
        from .core import shell_integration
        try:
            if "--register-shell" in argv:
                shell_integration.enable()
            else:
                shell_integration.disable()
        except OSError as e:
            logging.getLogger("swiftcut").error("shell integration failed: %s", e)
            return 1
        return 0
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    # Installer switches run first and write no log files: the uninstaller calls
    # --unregister-shell after deciding what to delete, so a new log folder would be left behind.
    code = _cli(argv)
    if code is not None:
        return code
    folder = setup_logging()
    log = logging.getLogger("swiftcut")

    if sys.platform == "win32":
        try:  # own taskbar identity/icon rather than python.exe's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SwiftCut.SwiftCut")
        except Exception:  # noqa: BLE001
            pass

    os.environ.setdefault("QT_FFMPEG_DEBUG", "0")
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import APP_NAME, __version__
    from .core import exporter
    from .ui import theme
    from .ui.main_window import MainWindow
    from .ui.settings import Settings

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("SwiftCut")
    app.setApplicationVersion(__version__)
    theme.apply(app)

    class DarkTitleBars(QObject):
        def eventFilter(self, obj, ev):  # noqa: N802
            if ev.type() == QEvent.Type.Show and obj.isWidgetType() and obj.isWindow():
                dark_title_bar(obj)
            return False
    title_filter = DarkTitleBars()
    app.installEventFilter(title_filter)

    def excepthook(etype, value, tb):
        log.error("Unhandled exception:\n%s", "".join(traceback.format_exception(etype, value, tb)))
        try:
            QMessageBox.critical(None, APP_NAME, f"Something went wrong:\n{value}\n\nDetails are in the log:\n{folder}")
        except Exception:  # noqa: BLE001
            pass
    sys.excepthook = excepthook

    files = [a for a in argv[1:] if not a.startswith("-")]
    single = "--new-window" not in argv and "--screenshot" not in argv
    if single:
        from .single_instance import InstanceServer, send_to_running
        target = os.path.abspath(files[0]) if files else None
        if send_to_running({"open": target}):
            log.info("handed %s to the running SwiftCut", target or "focus request")
            return 0

    if getattr(sys, "frozen", False):
        # After an upgrade or move, keep the Explorer entry pointing at this installed copy.
        try:
            from .core import shell_integration
            shell_integration.refresh_if_stale()
        except Exception:  # noqa: BLE001 - cosmetic
            pass

    window = MainWindow(Settings(), folder)
    if single:
        server = InstanceServer(parent=window)
        server.message.connect(window.handle_instance_message)
        server.listen()
        app.aboutToQuit.connect(server.close)
    window.show()
    log.info("SwiftCut %s started in %.0f ms", __version__, (time.perf_counter() - _T0) * 1000)
    try:
        from .core.ffmpeg_tools import find_ffmpeg
        log.info("using FFmpeg at %s", find_ffmpeg().ffmpeg)
    except Exception as e:  # noqa: BLE001
        log.warning("FFmpeg not found: %s", e)

    if files:
        window.open_file(files[0])
        exporter.cleanup_stale_temp_files(os.path.dirname(os.path.abspath(files[0])))

    if "--screenshot" in argv:  # dev aid: grab the window after a delay, then quit
        from PySide6.QtCore import QTimer
        idx = argv.index("--screenshot")
        out = argv[idx + 1] if idx + 1 < len(argv) else "swiftcut-screenshot.png"
        QTimer.singleShot(int(os.environ.get("SWIFTCUT_SHOT_DELAY", "2500")),
                          lambda: (window.grab().save(out), app.quit()))
    return app.exec()
