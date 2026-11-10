"""Settings dialog: preferences, Explorer integration, diagnostics, and About / licences."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal, qVersion
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel, QLayout, QMessageBox,
                               QPushButton, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget)

from .. import APP_NAME, AUTHOR, LICENSE_NAME, RELEASES_URL, __version__
from ..core import ffmpeg_tools, shell_integration
from . import icons, theme
from .keymap import Keymap
from .shortcuts_dialog import ShortcutsDialog
from .widgets import ToggleSwitch, hline


def _switch_row(title: str, note: str, switch: ToggleSwitch) -> QHBoxLayout:
    row = QHBoxLayout()
    text = QVBoxLayout()
    text.setSpacing(2)
    t1 = QLabel(title)
    t2 = QLabel(note)
    t2.setProperty("role", "note")
    t2.setWordWrap(True)
    t2.setMinimumWidth(380)
    text.addWidget(t1)
    text.addWidget(t2)
    row.addLayout(text, 1)
    row.addWidget(switch, 0, Qt.AlignmentFlag.AlignTop)
    return row


def _button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    b.setAutoDefault(False)
    return b


class SettingsDialog(QDialog):
    hoverPreviewChanged = Signal(bool)

    def __init__(self, log_dir: str, keymap: Keymap, parent: QWidget | None = None,
                 hover_preview: bool = True):
        super().__init__(parent)
        self.keymap = keymap
        self.setWindowTitle(f"{APP_NAME} settings")
        self.log_dir = log_dir
        lay = QVBoxLayout(self)
        # Let the layout set the dialog's minimum size, so it grows instead of squeezing buttons
        # (an explicit setMinimumWidth would switch that off and clip button text).
        lay.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)

        title = QLabel("Settings")
        title.setProperty("role", "title")
        lay.addWidget(title)

        # Preferences
        self.hover_switch = ToggleSwitch()
        self.hover_switch.setChecked(hover_preview)
        self.hover_switch.toggled.connect(self.hoverPreviewChanged.emit)
        lay.addLayout(_switch_row("Timeline hover preview",
                                  "Show the frame under the mouse when you hover over the timeline.",
                                  self.hover_switch))
        self.shell_switch = ToggleSwitch()
        self.shell_switch.setChecked(shell_integration.is_enabled())
        self.shell_switch.toggled.connect(self._toggle_shell)
        self.shell_switch.setEnabled(sys.platform == "win32")
        lay.addLayout(_switch_row("Explorer right-click menu",
                                  f"Adds “{shell_integration.VERB_TEXT}” to common video files. "
                                  "On Windows 11 it's under “Show more options” (or Shift + right-click). "
                                  "Only affects your user account.", self.shell_switch))
        lay.addWidget(hline())

        # Diagnostics
        try:
            ff = ffmpeg_tools.find_ffmpeg().ffmpeg
        except ffmpeg_tools.FFmpegNotFound:
            ff = "Not found"
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for r, (k, v) in enumerate([("FFmpeg", ff), ("Logs", log_dir)]):
            kl = QLabel(k)
            kl.setProperty("role", "faint")
            vl = QLabel(v)
            vl.setWordWrap(True)
            vl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(kl, r, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(vl, r, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        logs = _button("Open log folder")
        logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.log_dir)))
        self.clear_logs_btn = _button("Clear logs")
        self.clear_logs_btn.setToolTip("Logs record file names and actions for troubleshooting.\n"
                                       "They're kept under about 1 MB; clear them any time.")
        self.clear_logs_btn.clicked.connect(self._clear_logs)
        keys = _button("Keyboard shortcuts")
        keys.clicked.connect(lambda: ShortcutsDialog(self.keymap, self).exec())
        tools.addWidget(logs)
        tools.addWidget(self.clear_logs_btn)
        tools.addWidget(keys)
        tools.addStretch(1)
        lay.addLayout(tools)
        lay.addWidget(hline())

        # About
        about = QHBoxLayout()
        about.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(icons.app_icon().pixmap(QSize(44, 44)))
        logo.setFixedSize(48, 48)
        about.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        name = QLabel(f"{APP_NAME} {__version__}")
        name.setProperty("role", "title")
        by = QLabel(f"by {AUTHOR}  ·  {LICENSE_NAME} licence")
        by.setProperty("role", "muted")
        ffv = ffmpeg_tools.ffmpeg_version()
        built = QLabel(f"Built with Qt {qVersion()}, FFmpeg {ffv or '(not found)'} "
                       f"and Python {sys.version.split()[0]}.")
        built.setProperty("role", "faint")
        self.about_text = f"{name.text()} {by.text()} {built.text()}"
        col.addWidget(name)
        col.addWidget(by)
        col.addWidget(built)
        about.addLayout(col, 1)
        lay.addLayout(about)
        links = QHBoxLayout()
        links.setSpacing(8)
        releases = _button("Releases page")
        releases.setToolTip("New versions are published here. SwiftCut never checks online by itself.")
        releases.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
        report = _button("Report a problem")
        report.setToolTip("Opens a bug report form on GitHub, pre-filled with your SwiftCut and Windows versions.")
        report.clicked.connect(self._report_problem)
        self.copy_diag_btn = _button("Copy diagnostic info")
        self.copy_diag_btn.setToolTip("Copies versions and system details (no file names) to paste into a report.")
        self.copy_diag_btn.clicked.connect(self._copy_diagnostics)
        credits = _button("Licences && credits")
        credits.clicked.connect(lambda: LicencesDialog(self).exec())
        for b in (releases, report, self.copy_diag_btn, credits):
            links.addWidget(b)
        links.addStretch(1)
        lay.addLayout(links)
        lay.addWidget(hline())

        footer = QHBoxLayout()
        footer.addStretch(1)
        close = _button("Done")
        close.setProperty("kind", "primary")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        lay.addLayout(footer)

    def _report_problem(self) -> None:
        from ..diagnostics import report_url
        QDesktopServices.openUrl(QUrl(report_url()))

    def _copy_diagnostics(self) -> None:
        from PySide6.QtWidgets import QApplication
        from ..diagnostics import summary
        QApplication.clipboard().setText(summary())
        self.copy_diag_btn.setText("Copied")

    def _clear_logs(self) -> None:
        from ..app import clear_logs
        freed = clear_logs(self.log_dir)
        self.clear_logs_btn.setText(f"Cleared {max(1, round(freed / 1024))} KB" if freed else "Logs cleared")
        self.clear_logs_btn.setEnabled(False)

    def _toggle_shell(self, on: bool) -> None:
        try:
            if on:
                shell_integration.enable()
            else:
                shell_integration.disable()
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Couldn't update the Explorer menu:\n{e}")
            self.shell_switch.blockSignals(True)
            self.shell_switch.setChecked(shell_integration.is_enabled())
            self.shell_switch.blockSignals(False)


def legal_dir() -> Path:
    """Folder holding LICENSE, THIRD_PARTY_NOTICES.md and licenses/ (project root, or the bundle)."""
    return ffmpeg_tools.app_root()


class LicencesDialog(QDialog):
    """SwiftCut's MIT licence and the third-party notices, with a link to the full texts."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Licences and credits")
        self.resize(620, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        root = legal_dir()
        notices = (root / "THIRD_PARTY_NOTICES.md")
        licence = (root / "LICENSE")
        md = ""
        if licence.is_file():
            md += f"# {APP_NAME} licence\n\n```\n{licence.read_text(encoding='utf-8').strip()}\n```\n\n"
        if notices.is_file():
            md += notices.read_text(encoding="utf-8")
        if not md:
            md = "Licence files were not found next to the application."
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setMarkdown(md)
        self.browser.setStyleSheet(f"QTextBrowser {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER};"
                                   f" border-radius: 8px; padding: 10px; }}")
        lay.addWidget(self.browser, 1)
        row = QHBoxLayout()
        folder = _button("Open full licence texts")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(root / "licenses"))))
        folder.setEnabled((root / "licenses").is_dir())
        done = _button("Close")
        done.setProperty("kind", "primary")
        done.clicked.connect(self.accept)
        row.addWidget(folder)
        row.addStretch(1)
        row.addWidget(done)
        lay.addLayout(row)


def open_in_explorer(path: str) -> None:
    """Open Explorer with `path` selected."""
    import subprocess
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
