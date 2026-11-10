"""SwiftCut main window: header, video preview, transport, timeline and the save bar."""
from __future__ import annotations

import dataclasses
import logging
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QMessageBox, QProgressBar, QPushButton, QSizePolicy, QStackedWidget,
                               QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..core import animation, estimate, export_plan, ffmpeg_tools, paths, snapshot
from ..core.export_plan import CONTAINERS, ExportOptions, PlanError, TrimMethod
from ..core.media_info import MediaInfo
from ..core.timecode import FrameGrid, format_time
from . import icons, theme
from .advanced_panel import AdvancedPanel, _human_size
from .animation_dialog import AnimationDialog
from .audio_control import AudioControl
from .hover_preview import HoverPreview
from .keymap import Keymap
from .player import PlayerController
from .settings import Settings
from .settings_dialog import SettingsDialog, open_in_explorer
from .shortcuts_dialog import ShortcutsDialog
from .timeline import TRACK_H, Timeline
from .trim_model import TrimModel
from .widgets import SegmentedControl, TimeField, Toast, ToggleSwitch
from .workers import (EncoderProbe, EstimateJob, ExportJob, LoadJob, SnapshotJob, StillnessJob, ThumbJob,
                      ZoomThumbJob)

log = logging.getLogger(__name__)

VIDEO_FILTER = ("Videos (*.mp4 *.m4v *.mov *.mkv *.webm *.avi *.wmv *.flv *.mpg *.mpeg *.ts *.mts "
                "*.m2ts *.3gp *.ogv);;All files (*.*)")
THUMB_COUNT = 40


def _tool(name: str, tip: str, size: int = 18, checkable: bool = False) -> QToolButton:
    b = QToolButton()
    b.setIcon(icons.icon(name, theme.TEXT, size))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setFixedSize(34, 34)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return b


class EmptyState(QWidget):
    """Shown before a video is loaded, while dragging a file over, or after a load error."""

    def __init__(self, on_open, parent: QWidget | None = None):
        super().__init__(parent)
        # Style only this panel (an unscoped stylesheet would leak onto every child widget).
        self.setObjectName("EmptyState")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"#EmptyState {{ background: {theme.BG}; border-radius: 11px; }}")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(10)
        self.badge = QLabel()
        self.badge.setFixedSize(76, 76)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel()
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet("font-size: 14pt; font-weight: 600;")
        self.sub = QLabel()
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub.setProperty("role", "muted")
        self.sub.setWordWrap(True)
        self.sub.setFixedWidth(440)   # fixed width so wrapped text gets its full height
        self.button = QPushButton("  Open video…")
        self.button.setProperty("kind", "primary")
        self.button.setIcon(icons.icon("open", theme.ACCENT_TEXT, 16))
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(on_open)
        self.hint = QLabel("MP4, MOV, MKV, WebM, AVI and more  ·  Ctrl+O")
        self.hint.setProperty("role", "faint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for w in (self.badge, self.title, self.sub):
            lay.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(8)
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(4)
        lay.addWidget(self.hint, 0, Qt.AlignmentFlag.AlignHCenter)
        self.show_idle()

    def _set(self, icon: str, color: str, ring: str, title: str, sub: str, button: bool) -> None:
        self.badge.setPixmap(icons.pixmap(icon, color, 34, self.devicePixelRatioF() or 2.0, 1.6))
        self.badge.setStyleSheet(f"background: {theme.SURFACE}; border: 1px solid {ring}; border-radius: 38px;")
        self.title.setText(title)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        self.button.setVisible(button)
        self.hint.setVisible(button)

    def show_idle(self) -> None:
        self._set("film", theme.ACCENT, theme.BORDER_STRONG, "Drop a video here",
                  "Trim the start and end of a recording in seconds.", True)

    def show_drop(self) -> None:
        self._set("drop", theme.ACCENT, theme.ACCENT, "Drop to open", "", False)

    def show_loading(self, name: str) -> None:
        self._set("film", theme.TEXT_MUTED, theme.BORDER, "Opening…", name, False)

    def show_error(self, message: str) -> None:
        self._set("alert", theme.DANGER, "#4a2a30", "Couldn't open this video", message, True)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, log_dir: str):
        super().__init__()
        self.settings = settings
        self.log_dir = log_dir
        self.info: MediaInfo | None = None
        self.token = 0
        self.load_job: LoadJob | None = None
        self.still_job: StillnessJob | None = None
        self.estimate_job: EstimateJob | None = None
        self.zoom_job: ZoomThumbJob | None = None
        self._zoom_thumb_timer = QTimer(self)
        self._zoom_thumb_timer.setSingleShot(True)
        self._zoom_thumb_timer.setInterval(150)     # wait for zooming/panning to settle
        self._rates: dict = {}                      # settings key -> measured video bytes/s
        self._estimate_timer = QTimer(self)
        self._estimate_timer.setSingleShot(True)
        self._estimate_timer.setInterval(450)       # wait for settings to stop changing
        self.suggestion = None
        self.thumb_job: ThumbJob | None = None
        self.export_job: ExportJob | None = None
        self._overwrite_source: str | None = None
        self._restore_sel: tuple[float, float] | None = None
        self._was_playing_before_scrub = False
        self._shown_token = -1
        self._compact_min_width: int | None = None
        # Bursts of selection changes (nudges, typing) become a single undo step.
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(700)

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.setAcceptDrops(True)
        self.setMinimumSize(820, 560)

        self.model = TrimModel()
        self.keymap = Keymap(settings.qsettings)
        self._shortcut_objs: list[QShortcut] = []
        self._build_ui()
        self.player = PlayerController(self.video, self.model, self)
        self._connect()
        self._shortcuts()
        self.keymap.changed.connect(self._shortcuts)
        # Clicking anywhere outside a time box cancels a half-typed time (nothing else here takes
        # keyboard focus, so without this the box would stay "stuck" in edit mode).
        QApplication.instance().installEventFilter(self)

        self.player.loop = settings.loop
        self.loop_btn.setChecked(settings.loop)
        self.player.set_volume(settings.volume)
        self.player.set_muted(settings.preview_muted)
        self.player.set_device(settings.audio_device)
        self.audio_ctl.set_volume(settings.volume)
        self.audio_ctl.set_enabled_state(not settings.preview_muted)
        self.panel.apply_saved(settings.export_options())
        self.mode.setIndex(1 if settings.advanced else 0)
        self._apply_mode()
        if settings.geometry is not None:
            self.restoreGeometry(settings.geometry)
        else:
            self.resize(1180, 760)
        self._set_loaded(False)
        self._check_ffmpeg()
        QTimer.singleShot(1500, self._probe_encoders)

    # ======================================================================================
    # Layout
    # ======================================================================================
    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        left = QVBoxLayout()
        left.setContentsMargins(16, 14, 16, 10)
        left.setSpacing(10)

        # Video area
        self.stage = QFrame()
        self.stage.setObjectName("Stage")
        self.stage.setStyleSheet(f"#Stage {{ background: #000; border: 1px solid {theme.BORDER}; border-radius: 12px; }}")
        stage_l = QVBoxLayout(self.stage)
        stage_l.setContentsMargins(1, 1, 1, 1)
        self.stack = QStackedWidget()
        self.empty = EmptyState(self.open_dialog)
        self.video = QVideoWidget()
        self.video.setStyleSheet("background: #000;")
        self.video.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.video)
        stage_l.addWidget(self.stack)
        left.addWidget(self.stage, 1)

        left.addLayout(self._build_transport())
        self.timeline = Timeline(self.model)
        left.addWidget(self.timeline)
        body.addLayout(left, 1)

        self.panel = AdvancedPanel()
        body.addWidget(self.panel)
        v.addLayout(body, 1)
        v.addWidget(self._build_bottom())

        self.toast = Toast(root)
        self.hover = HoverPreview(root)
        self.hover.enabled = self.settings.hover_preview

        def toast_anchor():
            # Just left of the save/progress controls, vertically centred in the bottom bar.
            r = self.action_stack.geometry()
            tl = self.action_stack.parentWidget().mapTo(root, r.topLeft())
            return tl.x() + r.width() - self.save_btn.width() - self.save_menu_btn.width() - 24, tl.y() + r.height() // 2
        self.toast.anchor = toast_anchor

    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Header")
        bar.setFixedHeight(52)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 12, 0)
        h.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icons.app_icon().pixmap(QSize(22, 22)))
        name = QLabel(APP_NAME)
        name.setProperty("role", "title")
        sep = QLabel("/")
        sep.setProperty("role", "faint")
        self.file_label = QLabel("No video open")
        self.file_label.setProperty("role", "muted")
        self.file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.meta_label = QLabel()
        self.meta_label.setProperty("role", "faint")
        h.addWidget(logo)
        h.addWidget(name)
        h.addWidget(sep)
        h.addWidget(self.file_label, 1)
        h.addWidget(self.meta_label)
        h.addSpacing(10)
        self.mode = SegmentedControl(["Basic", "Advanced"])
        self.mode.setToolTip("Advanced shows trim method, format, codec, quality and audio tracks")
        h.addWidget(self.mode)
        h.addSpacing(4)
        self.open_btn = QPushButton("  Open")
        self.open_btn.setIcon(icons.icon("open", theme.TEXT, 16))
        self.open_btn.setToolTip("Open a video (Ctrl+O)")
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        h.addWidget(self.open_btn)
        self.keys_btn = _tool("keyboard", "Keyboard shortcuts")
        h.addWidget(self.keys_btn)
        self.settings_btn = _tool("settings", "Settings")
        h.addWidget(self.settings_btn)
        return bar

    def _build_transport(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        self.back_btn = _tool("frame-back", "Previous frame")
        self.play_btn = _tool("play", "Play selection", 18)
        self.play_btn.setProperty("kind", "play")
        self.play_btn.setFixedSize(40, 40)
        self.fwd_btn = _tool("frame-fwd", "Next frame")
        self.time_label = QLabel("0:00.000")
        self.time_label.setProperty("role", "mono")
        self.total_label = QLabel(" / 0:00.000")
        self.total_label.setProperty("role", "faint")
        self.total_label.setStyleSheet(f"font-family: {theme.MONO_FAMILY}; font-size: 10pt;")
        self.frame_label = QLabel()
        self.frame_label.setProperty("role", "faint")
        row.addWidget(self.back_btn)
        row.addWidget(self.play_btn)
        row.addWidget(self.fwd_btn)
        row.addSpacing(10)
        row.addWidget(self.time_label)
        row.addWidget(self.total_label)
        row.addSpacing(8)
        row.addWidget(self.frame_label)
        row.addStretch(1)
        self.snap_btn = _tool("camera", "Save this frame as PNG")
        row.addWidget(self.snap_btn)
        row.addSpacing(4)
        self.in_btn = _tool("mark-in", "Set start at playhead")
        self.out_btn = _tool("mark-out", "Set end at playhead")
        self.loop_btn = _tool("loop", "Loop the selection", checkable=True)
        for b in (self.in_btn, self.out_btn, self.loop_btn):
            row.addWidget(b)
        row.addSpacing(4)
        self.zoom_out_btn = _tool("zoom-out", "Zoom the timeline out")
        self.zoom_in_btn = _tool("zoom-in", "Zoom the timeline in")
        row.addWidget(self.zoom_out_btn)
        row.addWidget(self.zoom_in_btn)
        row.addSpacing(6)
        self.audio_ctl = AudioControl(lambda: (self.player.output_devices(), self.player.device_id(),
                                               self.player.current_device()))
        row.addWidget(self.audio_ctl)
        return row

    def _build_bottom(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("BottomBar")
        bar.setFixedHeight(68)
        self.bottom_bar = bar
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(14)
        self.start_field = TimeField("Start", theme.ACCENT)
        self.end_field = TimeField("End", theme.ACCENT)
        h.addWidget(self.start_field)
        h.addWidget(self.end_field)
        dur_box = QVBoxLayout()
        dur_box.setSpacing(0)
        cap = QLabel("DURATION")
        cap.setProperty("role", "section")
        cap.setStyleSheet("padding-top: 0px;")
        self.dur_label = QLabel("0:00.000")
        self.dur_label.setStyleSheet(f"font-family: {theme.MONO_FAMILY}; font-size: 12pt; color: {theme.ACCENT};")
        dur_box.addStretch(1)
        dur_box.addWidget(cap)
        dur_box.addWidget(self.dur_label)
        dur_box.addStretch(1)
        h.addSpacing(4)
        h.addLayout(dur_box)
        self.reset_btn = QPushButton(self.RESET_TEXT)
        self.reset_btn.setIcon(icons.icon("reset", theme.TEXT_MUTED, 15))
        self.reset_btn.setProperty("kind", "ghost")
        self.reset_btn.setToolTip("Select the whole video again (R)")
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        h.addWidget(self.reset_btn)
        h.addStretch(1)

        self.action_stack = QStackedWidget()
        self.action_stack.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        # Page 0: ready to save
        ready = QWidget()
        r = QHBoxLayout(ready)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(12)
        self.summary_label = QLabel()
        self.summary_label.setProperty("role", "faint")
        r.addWidget(self.summary_label)
        self.audio_wrap = QWidget()
        aw = QHBoxLayout(self.audio_wrap)
        aw.setContentsMargins(0, 0, 0, 0)
        aw.setSpacing(8)
        al = QLabel("Include audio")
        al.setProperty("role", "muted")
        self.audio_label = al
        self.audio_switch = ToggleSwitch()
        self.audio_switch.setChecked(True)
        tip = "Include the audio in the saved video.\nTo choose individual tracks, use Advanced mode."
        al.setToolTip(tip)
        self.audio_switch.setToolTip(tip)
        aw.addWidget(al)
        aw.addWidget(self.audio_switch)
        r.addWidget(self.audio_wrap)
        save_group = QHBoxLayout()
        save_group.setSpacing(0)
        self.save_btn = QPushButton(self.SAVE_TEXT)
        self.save_btn.setProperty("kind", "primary")
        self.save_btn.setIcon(icons.icon("save", theme.ACCENT_TEXT, 16, disabled_color="#3d6f6b"))
        self.save_btn.setToolTip("Save As… (Ctrl+S)")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.save_btn.setStyleSheet("border-top-right-radius: 0; border-bottom-right-radius: 0;")
        self.save_menu_btn = QPushButton()
        self.save_menu_btn.setProperty("kind", "primary")
        self.save_menu_btn.setIcon(icons.icon("chev-right", theme.ACCENT_TEXT, 14, disabled_color="#3d6f6b"))
        self.save_menu_btn.setFixedWidth(30)
        self.save_menu_btn.setToolTip("More save options")
        self.save_menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.save_menu_btn.setStyleSheet("border-top-left-radius: 0; border-bottom-left-radius: 0; "
                                         "border-left: 1px solid #1a9f94; padding: 8px 6px;")
        menu = QMenu(self)
        self.copy_action = QAction("Save as a copy (name_trimmed)…", self)
        menu.addAction(self.copy_action)
        self.anim_action = QAction("Save as GIF / WebP animation\u2026", self)
        menu.addAction(self.anim_action)
        self.save_menu_btn.clicked.connect(
            lambda: menu.exec(self.save_menu_btn.mapToGlobal(self.save_menu_btn.rect().bottomLeft())))
        save_group.addWidget(self.save_btn)
        save_group.addWidget(self.save_menu_btn)
        r.addLayout(save_group)
        self.action_stack.addWidget(ready)
        # Page 1: exporting
        busy = QWidget()
        b = QHBoxLayout(busy)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(12)
        self.progress_label = QLabel("Exporting…")
        self.progress_label.setProperty("role", "muted")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(160, 8)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setProperty("kind", "danger")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        b.addWidget(self.progress_label)
        b.addWidget(self.progress)
        b.addWidget(self.cancel_btn)
        self.action_stack.addWidget(busy)
        h.addWidget(self.action_stack)
        return bar

    # ======================================================================================
    # Wiring
    # ======================================================================================
    def _connect(self) -> None:
        self.open_btn.clicked.connect(self.open_dialog)
        self.settings_btn.clicked.connect(self.show_settings)
        self.keys_btn.clicked.connect(self.show_shortcuts)
        self.mode.changed.connect(lambda _i: self._apply_mode())
        self.play_btn.clicked.connect(self.player.toggle)
        self.back_btn.clicked.connect(lambda: self.player.step(-1))
        self.fwd_btn.clicked.connect(lambda: self.player.step(1))
        self.back_btn.setAutoRepeat(True)
        self.fwd_btn.setAutoRepeat(True)
        self.in_btn.clicked.connect(self.mark_in)
        self.out_btn.clicked.connect(self.mark_out)
        self.loop_btn.toggled.connect(self._set_loop)
        self.audio_ctl.enabledChanged.connect(self._set_preview_audio)
        self.audio_ctl.volumeChanged.connect(self._set_volume)
        self.audio_ctl.deviceChosen.connect(self._set_device)
        self.reset_btn.clicked.connect(self.reset_selection)
        self.save_btn.clicked.connect(lambda: self.save(copy=False))
        self.copy_action.triggered.connect(lambda: self.save(copy=True))
        self.anim_action.triggered.connect(self.save_animation)
        self.cancel_btn.clicked.connect(self.cancel_export)
        self.audio_switch.toggled.connect(self._audio_toggled)

        self.player.timeChanged.connect(self._on_time)
        self.player.playingChanged.connect(self._on_playing)
        self.player.mediaReady.connect(self._on_media_ready)
        self.player.mediaFailed.connect(self._on_media_failed)

        self.model.changed.connect(self._on_selection)
        self._commit_timer.timeout.connect(self.model.commit)
        self.model.changed.connect(self._schedule_commit)
        self.timeline.handleReleased.connect(lambda _w: self._commit_now())
        self.snap_btn.clicked.connect(self.save_frame)
        self.timeline.seekRequested.connect(lambda t: self.player.seek(t))
        self.timeline.scrubStarted.connect(self._scrub_start)
        self.timeline.scrubFinished.connect(self._scrub_end)
        self.timeline.handleDragged.connect(self._handle_dragged)
        self.timeline.wheelStep.connect(lambda n: self.player.step(n))
        self.timeline.hoverChanged.connect(self._timeline_hover)
        self.zoom_in_btn.clicked.connect(lambda: self.timeline.zoom_in())
        self.zoom_out_btn.clicked.connect(lambda: self.timeline.zoom_out())
        self.timeline.viewChanged.connect(self._view_changed)
        self._zoom_thumb_timer.timeout.connect(self._fetch_zoom_thumbs)
        self._estimate_timer.timeout.connect(self._run_estimate)

        self.start_field.edited.connect(self._start_edited)
        self.end_field.edited.connect(self._end_edited)
        self.start_field.nudged.connect(lambda d: self._nudge("start", d))
        self.end_field.nudged.connect(lambda d: self._nudge("end", d))
        self.panel.optionsChanged.connect(self._options_changed)

    def _shortcuts(self) -> None:
        """(Re)create keyboard shortcuts from the keymap and refresh tooltips."""
        handlers = {
            "play_pause": self.player.toggle,
            "frame_back": lambda: self.player.step(-1),
            "frame_fwd": lambda: self.player.step(1),
            "back_1s": lambda: self._jump(-1.0),
            "fwd_1s": lambda: self._jump(1.0),
            "back_10s": lambda: self._jump(-10.0),
            "fwd_10s": lambda: self._jump(10.0),
            "go_start": lambda: self._goto(self.model.start_time),
            "go_end": lambda: self.model.loaded and self.player.seek_frame(self.model.end_idx),
            "loop": lambda: self.loop_btn.toggle(),
            "mark_in": self.mark_in,
            "mark_out": self.mark_out,
            "reset": self.reset_selection,
            "undo": self.undo,
            "redo": self.redo,
            "suggest": self.apply_suggested_trim,
            "snapshot": self.save_frame,
            "audio": self.toggle_preview_audio,
            "vol_up": lambda: self.audio_ctl.nudge_volume(5),
            "vol_down": lambda: self.audio_ctl.nudge_volume(-5),
            "open": self.open_dialog,
            "save": lambda: self.save(copy=False),
            "save_copy": lambda: self.save(copy=True),
            "save_animation": self.save_animation,
            "toggle_mode": lambda: self.mode.setIndex(1 - self.mode.index(), emit=True),
            "shortcuts": self.show_shortcuts,
            "zoom_in": lambda: self.timeline.zoom_in(),
            "zoom_out": lambda: self.timeline.zoom_out(),
            "zoom_fit": self.timeline.zoom_fit,
        }
        repeating = {"frame_back", "frame_fwd", "back_1s", "fwd_1s", "back_10s", "fwd_10s",
                     "vol_up", "vol_down"}
        for sc in self._shortcut_objs:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcut_objs = []
        for action_id, fn in handlers.items():
            for key in self.keymap.keys(action_id):
                sc = QShortcut(QKeySequence(key), self)
                sc.setContext(Qt.ShortcutContext.WindowShortcut)
                sc.setAutoRepeat(action_id in repeating)
                sc.activated.connect(fn)
                self._shortcut_objs.append(sc)
        k = self.keymap.hint
        tips = [
            (self.back_btn, "Previous frame", "frame_back"), (self.fwd_btn, "Next frame", "frame_fwd"),
            (self.in_btn, "Set start at playhead", "mark_in"), (self.out_btn, "Set end at playhead", "mark_out"),
            (self.loop_btn, "Loop the selection", "loop"), (self.reset_btn, "Select the whole video again", "reset"),
            (self.open_btn, "Open a video", "open"), (self.save_btn, "Save As\u2026", "save"),
            (self.keys_btn, "Keyboard shortcuts", "shortcuts"),
            (self.snap_btn, "Save this frame as PNG", "snapshot"),
            (self.zoom_in_btn, "Zoom the timeline in (or Ctrl + mouse wheel)", "zoom_in"),
            (self.zoom_out_btn, "Zoom the timeline out", "zoom_out"),
        ]
        for w, text, aid in tips:
            hint = k(aid)
            w.setToolTip(f"{text} ({hint})" if hint else text)
        self.audio_ctl.set_shortcut_hint(k("audio"))
        self._on_playing(self.player.playing)

    def show_settings(self) -> None:
        dlg = SettingsDialog(self.log_dir, self.keymap, self, hover_preview=self.hover.enabled)
        dlg.hoverPreviewChanged.connect(self.set_hover_preview)
        dlg.exec()

    def set_hover_preview(self, on: bool) -> None:
        self.hover.enabled = on
        self.settings.hover_preview = on
        if not on:
            self.hover.hide()

    def _timeline_hover(self, t: float, x: float) -> None:
        root = self.centralWidget()
        tl = self.timeline
        cx = tl.mapTo(root, QPoint(int(x), 0)).x()
        bottom = tl.mapTo(root, QPoint(0, 0)).y() + 6
        self.hover.hover(t, cx, bottom)

    def show_shortcuts(self) -> None:
        ShortcutsDialog(self.keymap, self).exec()

    # ======================================================================================
    # Loading
    # ======================================================================================
    def _check_ffmpeg(self) -> None:
        try:
            ffmpeg_tools.find_ffmpeg()
        except ffmpeg_tools.FFmpegNotFound as e:
            self.empty.show_error(str(e))
            self.empty.button.hide()

    def open_dialog(self) -> None:
        start = os.path.dirname(self.info.path) if self.info else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open video", start, VIDEO_FILTER)
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> None:
        if self.export_job is not None:
            self.toast.show_message("Please wait for the current export to finish.", "warn")
            return
        path = os.path.abspath(path)
        log.info("open %s", path)
        self._cancel_jobs()
        self.hover.set_media(-1, None, None, None)
        self.token += 1
        self.info = None
        self.model.clear()
        self.timeline.reset_thumbnails(0, 16 / 9)
        self._set_loaded(False)
        self.file_label.setText(Path(path).name)
        self.file_label.setToolTip(path)
        self.meta_label.setText("")
        self.setWindowTitle(f"{Path(path).name} – {APP_NAME}")
        self.empty.show_loading(Path(path).name)
        self.stack.setCurrentWidget(self.empty)
        self.player.open(path)
        job = LoadJob(self.token, path)
        job.probed.connect(self._on_probed)
        job.scanned.connect(self._on_scanned)
        job.failed.connect(self._on_load_failed)
        self.load_job = job
        job.start()

    def _cancel_jobs(self) -> None:
        for job in (self.load_job, self.thumb_job, self.still_job, self.estimate_job, self.zoom_job):
            if job is not None:
                job.cancel.set()
        self.load_job = self.thumb_job = self.still_job = self.estimate_job = self.zoom_job = None
        self._rates.clear()
        self.suggestion = None
        self.timeline.set_suggestions([])

    def _on_probed(self, token: int, info: MediaInfo) -> None:
        if token != self.token:
            return
        self.info = info
        self.model.load(FrameGrid.uniform(info.video.fps, info.duration - info.video_offset,
                                          origin=info.video_offset))
        self._apply_restore_selection()
        v = info.video
        w, h = v.display_size
        fps = f"{v.fps:.2f}".rstrip("0").rstrip(".")
        self.meta_label.setText(f"{w}×{h}  ·  {fps} fps  ·  {_human_size(info.size_bytes)}")
        self.total_label.setText(" / " + format_time(self.model.total))
        has_audio = info.has_audio
        self.audio_wrap.setVisible(has_audio)
        self.audio_ctl.set_has_audio(has_audio)
        self._fit_bottom_bar()
        self.panel.set_media(info, self.model.grid)
        self.panel.set_muted(not self.audio_switch.isChecked())
        self._set_loaded(True)
        if self.player.ready:
            self._show_video()
        dpr = self.devicePixelRatioF() or 1.0
        self.timeline.reset_thumbnails(THUMB_COUNT, w / h if h else 16 / 9)
        self.hover.set_media(token, info, self.model.grid, self.timeline.thumb_for_time)
        tj = ThumbJob(token, info, THUMB_COUNT, int(TRACK_H * dpr))
        tj.thumb.connect(self._on_thumb)
        self.thumb_job = tj
        tj.start()
        self._options_changed()

    def _on_scanned(self, token: int, grid: FrameGrid) -> None:
        if token != self.token:
            return
        self.model.replace_grid(grid)
        self.hover.set_media(token, self.info, grid, self.timeline.thumb_for_time)
        self._apply_restore_selection()
        self.total_label.setText(" / " + format_time(self.model.total))
        self.panel.set_media(self.info, grid)
        self._options_changed()            # exact timestamps are in: estimate the output size
        self._on_time(self.player.time)
        log.info("frame scan: %d frames, %d keyframes", len(grid), len(grid.keyframes))
        job = StillnessJob(token, self.info, grid)
        job.done.connect(self._on_suggestion)
        self.still_job = job
        job.start()

    def _on_suggestion(self, token: int, suggestion) -> None:
        if token != self.token:
            return
        self.suggestion = suggestion
        self.timeline.set_suggestions([suggestion.start, suggestion.end])

    def _apply_restore_selection(self) -> None:
        if self._restore_sel and self.model.loaded:
            st, en = self._restore_sel
            self.model.set_start_time(st)
            self.model.set_end_time(en)

    def _on_load_failed(self, token: int, message: str) -> None:
        if token != self.token:
            return
        log.warning("load failed: %s", message)
        self.player.release()
        self.info = None
        self.model.clear()
        self._set_loaded(False)
        self.file_label.setText("No video open")
        self.setWindowTitle(APP_NAME)
        self.empty.show_error(message)
        self.stack.setCurrentWidget(self.empty)

    def _on_media_ready(self) -> None:
        if self.info is not None:
            self._show_video()

    def _show_video(self) -> None:
        if self._shown_token == self.token:
            return
        self._shown_token = self.token
        self.stack.setCurrentWidget(self.video)
        self._disable_child_drops()
        target = self.model.start_time if self._restore_sel else 0.0
        self._restore_sel = None
        self.player.seek(target, immediate=True)

    def _on_media_failed(self, message: str) -> None:
        if self.info is None:
            return  # the probe error (if any) is more useful; it will be shown instead
        log.warning("preview unavailable: %s", message)
        self.toast.show_message("Preview unavailable for this file, but it can still be trimmed.", "warn",
                                msec=7000)

    def _on_thumb(self, token: int, i: int, img) -> None:
        if token == self.token:
            self.timeline.set_thumbnail(i, img)

    def _set_loaded(self, on: bool) -> None:
        for w in (self.play_btn, self.back_btn, self.fwd_btn, self.in_btn, self.out_btn, self.snap_btn,
                  self.reset_btn, self.start_field, self.end_field, self.save_btn, self.save_menu_btn):
            w.setEnabled(on)
        self.zoom_in_btn.setEnabled(on)
        self.zoom_out_btn.setEnabled(on and self.timeline.zoomed)
        if not on:
            self.time_label.setText("0:00.000")
            self.total_label.setText(" / 0:00.000")
            self.frame_label.setText("")
            self.dur_label.setText("0:00.000")
            self.audio_wrap.setVisible(False)
            self.panel.set_media(None, None)

    # ======================================================================================
    # Playback & selection
    # ======================================================================================
    def _on_time(self, t: float) -> None:
        moved = abs(t - self.timeline.playhead) > 1e-6
        self.timeline.set_playhead(t)
        # Follow the playhead only when it actually moves, so zooming into another part of the
        # video isn't undone by a routine update; and never while the user is dragging.
        if moved and self.timeline._drag is None:
            self.timeline.ensure_visible(t)
        self.time_label.setText(format_time(t))
        g = self.model.grid
        if g is not None:
            prefix = "" if g.exact else "~"
            self.frame_label.setText(f"frame {prefix}{g.index_at(t) + 1} / {prefix}{len(g)}")

    def _on_playing(self, playing: bool) -> None:
        self.play_btn.setIcon(icons.icon("pause" if playing else "play", theme.TEXT, 18))
        hint = self.keymap.hint("play_pause")
        self.play_btn.setToolTip(f"Pause ({hint})" if playing else f"Play the selection ({hint})")

    def _on_selection(self) -> None:
        m = self.model
        if not m.loaded:
            return
        self.start_field.setValue(m.start_time)
        self.end_field.setValue(m.end_time)
        self.dur_label.setText(format_time(m.duration))
        self.dur_label.setToolTip(f"{m.frame_count} frames")
        self._update_estimate()
        self.panel.set_selection(m.start_time, m.duration)

    def _scrub_start(self) -> None:
        self._was_playing_before_scrub = self.player.playing
        self.player.pause()

    def _scrub_end(self) -> None:
        if self._was_playing_before_scrub:
            self.player.play()

    def _handle_dragged(self, which: str, t: float) -> None:
        self.player.pause()
        if which == "start":
            self.player.seek(t)
        else:
            self.player.seek(self.model.grid.times[self.model.end_idx])

    def _start_edited(self, t: float) -> None:
        self._commit_now()
        self.model.set_start_time(t)
        self._commit_now()
        self.player.seek(self.model.start_time, immediate=True)

    def _end_edited(self, t: float) -> None:
        self._commit_now()
        self.model.set_end_time(t)
        self._commit_now()
        self.player.seek_frame(self.model.end_idx)

    def _nudge(self, which: str, d: int) -> None:
        if not self.model.loaded:
            return
        self.player.pause()
        if which == "start":
            self.model.set_start_index(self.model.start_idx + d)
            self.player.seek_frame(self.model.start_idx)
        else:
            self.model.set_end_index(self.model.end_idx + d)
            self.player.seek_frame(self.model.end_idx)

    def mark_in(self) -> None:
        if self.model.loaded:
            self._commit_now()                       # discrete actions are their own undo step
            self.model.set_start_index(self.player.current_index())
            self._commit_now()

    def mark_out(self) -> None:
        if self.model.loaded:
            self._commit_now()
            self.model.set_end_index(self.player.current_index())
            self._commit_now()

    def reset_selection(self) -> None:
        self._commit_now()
        self.model.reset()
        self._commit_now()

    # -- undo / redo ------------------------------------------------------------------------
    def _schedule_commit(self) -> None:
        if self.timeline._drag is None:          # a drag is one step, committed on release
            self._commit_timer.start()

    def _commit_now(self) -> None:
        self._commit_timer.stop()
        self.model.commit()

    def undo(self) -> None:
        self._commit_timer.stop()
        if self.model.undo():
            self._commit_timer.stop()
            self.player.seek(self.model.start_time)
            self.toast.show_message("Undo: selection restored", "info", msec=1400)

    def redo(self) -> None:
        self._commit_timer.stop()
        if self.model.redo():
            self._commit_timer.stop()
            self.player.seek(self.model.start_time)
            self.toast.show_message("Redo", "info", msec=1400)

    # -- snapshot ---------------------------------------------------------------------------
    def save_frame(self) -> None:
        if self.info is None or self.model.grid is None:
            return
        self.player.pause()
        index = self.player.current_index()
        suggested = os.path.join(os.path.dirname(self.info.path), snapshot.default_name(self.info, index))
        path, _ = QFileDialog.getSaveFileName(self, "Save frame as PNG", suggested, "PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        job = SnapshotJob(self.info, self.model.grid, index, path)
        job.done.connect(lambda p: self.toast.show_message(
            f"Saved frame {index + 1} as {Path(p).name}", "info", "Show in folder",
            lambda: open_in_explorer(p), msec=5000))
        job.failed.connect(lambda m: QMessageBox.warning(self, APP_NAME, m))
        self._snapshot_job = job
        job.start()

    # -- suggested trim (F1) ----------------------------------------------------------------
    def apply_suggested_trim(self) -> None:
        """Move the handles past still footage at the start and end (key: T)."""
        if not self.model.loaded:
            return
        sug = self.suggestion
        if sug is None:
            self.toast.show_message("Still checking the video for still footage…"
                                    if self.still_job is not None else "No suggestion available.", "warn", msec=2500)
            return
        if not sug.any:
            self.toast.show_message("No still footage found at the start or end.", "info", msec=3000)
            return
        self._commit_now()
        parts = []
        if sug.start is not None:
            self.model.set_start_time(sug.start)
            parts.append(f"{sug.start:.1f}s still start")
        if sug.end is not None:
            self.model.set_end_time(sug.end)
            parts.append(f"{self.model.total - sug.end:.1f}s still end")
        self._commit_now()
        self.player.seek(self.model.start_time, immediate=True)
        self.toast.show_message("Skipped " + " and ".join(parts) + "  ·  undo with "
                                + self.keymap.hint("undo"), "info", msec=4000)

    # -- single instance --------------------------------------------------------------------
    def handle_instance_message(self, msg: dict) -> None:
        """Another launch (e.g. Explorer's "Trim with SwiftCut") handed us a file."""
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        path = msg.get("open")
        if isinstance(path, str) and path:
            self.request_open(path)

    def _jump(self, secs: float) -> None:
        if self.model.loaded:
            self.player.seek(min(max(0.0, self.player.time + secs), self.model.total), immediate=True)

    def _goto(self, t: float) -> None:
        if self.model.loaded:
            self.player.seek(t, immediate=True)

    def _set_loop(self, on: bool) -> None:
        self.player.loop = on
        self.settings.loop = on

    def toggle_preview_audio(self) -> None:
        if self.info is not None and self.info.has_audio:
            self.audio_ctl.set_enabled_state(not self.audio_ctl.is_on(), emit=True)

    def _set_preview_audio(self, on: bool) -> None:
        self.player.set_muted(not on)
        self.settings.preview_muted = not on

    def _set_volume(self, v: float) -> None:
        self.player.set_volume(v)
        self.settings.volume = v

    def _set_device(self, device_id) -> None:
        self.player.set_device(device_id)
        self.settings.audio_device = device_id
        name = self.player.current_device().description() if device_id else "system default"
        self.toast.show_message(f"Audio output: {name}", "info", msec=2500)

    def _audio_toggled(self, on: bool) -> None:
        self.panel.set_muted(not on)
        self._options_changed()

    # ======================================================================================
    # Modes & options
    # ======================================================================================
    def _advanced(self) -> bool:
        return self.mode.index() == 1

    def _apply_mode(self) -> None:
        adv = self._advanced()
        self.panel.setVisible(adv)
        self.settings.advanced = adv
        self._options_changed()

    def _options_changed(self) -> None:
        if self._advanced():
            self.summary_label.setText(self.panel.summary())
            self.settings.save_export_options(self.panel.options(mute=False))
        else:
            if self.info:
                fmt = CONTAINERS[export_plan.resolve_container(self.info, ExportOptions())][2].split(" ")[0]
            else:
                fmt = "Original format"
            meta = " · No metadata" if self.panel.strip_meta.isChecked() else ""
            self.summary_label.setText(f"{fmt} · Precise trim · High quality{meta}")
        self._sync_preview_audio()
        self._update_estimate()
        if hasattr(self, "bottom_bar"):
            self._fit_bottom_bar()

    # -- size estimate ----------------------------------------------------------------------
    def _update_estimate(self) -> None:
        """Show a rough output size; measure the bitrate in the background when settings change."""
        if not hasattr(self, "_estimate_timer"):
            return
        if self.info is None or self.model.grid is None or not self.model.grid.exact:
            self.panel.set_estimate("")
            self._estimate_suffix = ""
            return
        opts = self.current_options()
        key = estimate.settings_key(self.info, opts)
        rate = self._rates.get(key)
        if rate is None:
            self.panel.set_estimate("Estimated size: measuring\u2026")
            self._estimate_suffix = ""
            self._estimate_timer.start()
        else:
            size = estimate.estimate_bytes(rate, self.info, opts, self.model.duration)
            text = _rough_size(size)
            self.panel.set_estimate(f"Estimated size: about {text}")
            self._estimate_suffix = f" \u00b7 ~{text}"
        self._refresh_summary_suffix()

    def _refresh_summary_suffix(self) -> None:
        base = self.summary_label.text().split(" \u00b7 ~")[0]
        limit = self.current_options().size_limit_mb
        self.summary_label.setText(base + ("" if limit else getattr(self, "_estimate_suffix", "")))

    def _run_estimate(self) -> None:
        if self.info is None or self.model.grid is None:
            return
        opts = self.current_options()
        key = estimate.settings_key(self.info, opts)
        if key in self._rates:
            self._update_estimate()
            return
        if self.estimate_job is not None:
            self.estimate_job.cancel.set()
        job = EstimateJob(key, self.info, self.model.grid, opts)
        job.done.connect(self._on_estimate)
        self.estimate_job = job
        job.start()

    def _on_estimate(self, key, rate: float) -> None:
        self._rates[key] = rate
        self._update_estimate()

    # -- timeline zoom ----------------------------------------------------------------------
    def _view_changed(self, _start: float, _length: float) -> None:
        tl = self.timeline
        self.zoom_in_btn.setEnabled(tl.can_zoom_in())
        self.zoom_out_btn.setEnabled(tl.zoomed)
        if tl.zoomed:
            self._zoom_thumb_timer.start()
        elif self.zoom_job is not None:
            self.zoom_job.cancel.set()

    def _fetch_zoom_thumbs(self) -> None:
        """Decode exact frames for the zoomed filmstrip slots that aren't cached yet."""
        tl, g = self.timeline, self.model.grid
        if self.info is None or g is None or not tl.zoomed:
            return
        wanted, seen = [], set()
        for t in tl.slot_times():
            i = g.index_at(t)
            if i not in tl.zoom_thumbs and i not in seen:
                seen.add(i)
                wanted.append((i, g.times[i]))
        if self.zoom_job is not None:
            self.zoom_job.cancel.set()
        if not wanted:
            return
        dpr = self.devicePixelRatioF() or 1.0
        job = ZoomThumbJob(self.token, self.info, wanted, int(TRACK_H * dpr), g.origin)
        job.thumb.connect(self._on_zoom_thumb)
        self.zoom_job = job
        job.start()

    def _on_zoom_thumb(self, token: int, index: int, img) -> None:
        if token == self.token:
            self.timeline.set_zoom_thumbnail(index, img)

    def _sync_preview_audio(self) -> None:
        """Make the preview play what the export will contain (first saved track, or silence)."""
        if self.info is None or not self.info.audio:
            return
        opts = self.current_options()
        if opts.mute:
            self.player.set_preview_track(None)
            return
        chosen = opts.audio_tracks if opts.audio_tracks is not None else [a.index for a in self.info.audio]
        positions = [n for n, a in enumerate(self.info.audio) if a.index in chosen]
        self.player.set_preview_track(positions[0] if positions else None)

    def current_options(self) -> ExportOptions:
        mute = not self.audio_switch.isChecked()
        if self._advanced():
            return self.panel.options(mute=mute)
        # Basic: fixed encoding defaults, but the privacy preference always applies.
        return ExportOptions(mute=mute, strip_metadata=self.panel.strip_meta.isChecked())

    def save(self, copy: bool) -> None:
        if not self.model.loaded or self.info is None or self.export_job is not None:
            return
        self.player.pause()
        info = self.info
        opts = self.current_options()
        container = export_plan.resolve_container(info, opts)
        suggested = paths.suggested_output_path(info.path, container)
        if copy:
            suggested = paths.trimmed_alternative(suggested)
        filters = [f"{CONTAINERS[container][2]} (*.{paths.output_extension(info.path, container)})"]
        for key, (_m, ext, label) in CONTAINERS.items():
            if key != container:
                filters.append(f"{label} (*.{ext})")
        dest, chosen_filter = QFileDialog.getSaveFileName(self, "Save trimmed video", suggested, ";;".join(filters))
        if not dest:
            return
        dest = os.path.abspath(dest)
        # The dialog may have changed the extension: follow the user's choice of format.
        chosen = paths.container_for_path(dest)
        if chosen is None:
            idx = filters.index(chosen_filter) if chosen_filter in filters else 0
            chosen = container if idx == 0 else [k for k in CONTAINERS if k != container][idx - 1]
            dest = paths.ensure_extension(dest, chosen)
        if chosen != container:
            opts.container = chosen
        problem = paths.check_destination(dest)
        if problem:
            QMessageBox.warning(self, APP_NAME, problem)
            return
        overwrite_source = paths.same_file(dest, info.path)
        try:
            plan = export_plan.build_plan(info, self.model.grid, self.model.start_time, self.model.end_time,
                                          opts, dest, ffmpeg_tools.find_ffmpeg().ffmpeg)
        except (PlanError, ffmpeg_tools.FFmpegNotFound) as e:
            QMessageBox.warning(self, APP_NAME, str(e))
            return
        if overwrite_source:
            # Release our own handles on the source so it can be replaced at the end.
            self._overwrite_source = info.path
            self._restore_sel = (self.model.start_time, self.model.end_time)
            if self.thumb_job:
                self.thumb_job.cancel.set()
            self.player.release()
            self.empty.show_loading("Replacing the original video…")
            self.stack.setCurrentWidget(self.empty)
        rebuild = None
        if plan.size_limit_bytes:
            st, en, grid, ff = self.model.start_time, self.model.end_time, self.model.grid, plan.args[0]
            rebuild = lambda scale, o=opts: export_plan.build_plan(  # noqa: E731
                info, grid, st, en, dataclasses.replace(o, size_scale=scale), dest, ff)
        job = ExportJob(plan, rebuild)
        job.progress.connect(self._on_export_progress)
        job.finished.connect(self._on_export_done)
        job.failed.connect(self._on_export_failed)
        job.cancelled.connect(self._on_export_cancelled)
        self.export_job = job
        self.progress.setValue(0)
        self.progress_label.setText("Exporting…")
        self.action_stack.setCurrentIndex(1)
        self.open_btn.setEnabled(False)
        self._export_plan = plan
        job.start()

    def save_animation(self) -> None:
        """Export the selection as an animated GIF or WebP (F9)."""
        if not self.model.loaded or self.info is None or self.export_job is not None:
            return
        self.player.pause()
        info, grid = self.info, self.model.grid
        dlg = AnimationDialog(info, grid, self.model.duration, self.settings.animation_options(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        opts = dlg.options()
        self.settings.save_animation_options(opts)
        ext = animation.FORMATS[opts.format][1]
        suggested = os.path.join(os.path.dirname(info.path), f"{Path(info.path).stem}.{ext}")
        label = animation.FORMATS[opts.format][0]
        dest, _ = QFileDialog.getSaveFileName(self, f"Save as {label}", suggested, f"{label} (*.{ext})")
        if not dest:
            return
        if not dest.lower().endswith("." + ext):
            dest += "." + ext
        dest = os.path.abspath(dest)
        if paths.same_file(dest, info.path):
            QMessageBox.warning(self, APP_NAME, "That would replace the original video. Choose another name.")
            return
        problem = paths.check_destination(dest)
        if problem:
            QMessageBox.warning(self, APP_NAME, problem)
            return
        try:
            plan = animation.build_animation_plan(info, grid, self.model.start_time, self.model.end_time,
                                                  opts, dest, ffmpeg_tools.find_ffmpeg().ffmpeg)
        except (PlanError, ffmpeg_tools.FFmpegNotFound) as e:
            QMessageBox.warning(self, APP_NAME, str(e))
            return
        self._start_export_job(ExportJob(plan), plan)

    def _start_export_job(self, job: ExportJob, plan) -> None:
        job.progress.connect(self._on_export_progress)
        job.finished.connect(self._on_export_done)
        job.failed.connect(self._on_export_failed)
        job.cancelled.connect(self._on_export_cancelled)
        self.export_job = job
        self.progress.setValue(0)
        self.progress_label.setText("Exporting\u2026")
        self.action_stack.setCurrentIndex(1)
        self.open_btn.setEnabled(False)
        self._export_plan = plan
        job.start()

    def cancel_export(self) -> None:
        if self.export_job is not None:
            self.progress_label.setText("Cancelling…")
            self.export_job.cancel()

    def _on_export_progress(self, f: float) -> None:
        self.progress.setValue(int(f * 1000))
        self.progress_label.setText(f"Exporting… {int(f * 100)}%")

    def _export_finished_ui(self) -> None:
        self.export_job = None
        self.action_stack.setCurrentIndex(0)
        self.open_btn.setEnabled(True)

    def _on_export_done(self, result) -> None:
        self._export_finished_ui()
        name = Path(result.output_path).name
        msg = f"Saved {name}  ·  {_human_size(result.size_bytes)}  ·  {result.elapsed:.1f}s"
        notes = getattr(self, "_export_plan", None)
        if notes and notes.notes:
            msg += "  ·  " + notes.notes[0]
        if self._overwrite_source:
            src = self._overwrite_source
            self._overwrite_source = None
            self._restore_sel = None
            self.open_file(src)
            msg = f"Replaced {name} with the trimmed clip  ·  {_human_size(result.size_bytes)}"
        self.toast.show_message(msg, "info", "Show in folder",
                                lambda p=result.output_path: open_in_explorer(p), msec=7000)

    def _reopen_after_abort(self) -> None:
        if self._overwrite_source:
            src = self._overwrite_source
            self._overwrite_source = None
            self.open_file(src)

    def _on_export_failed(self, message: str, detail: str) -> None:
        self._export_finished_ui()
        self._reopen_after_abort()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText(message)
        box.setInformativeText("Nothing was overwritten. The original video is unchanged.")
        if detail:
            box.setDetailedText(detail)
        box.exec()

    def _on_export_cancelled(self) -> None:
        self._export_finished_ui()
        self._reopen_after_abort()
        self.toast.show_message("Export cancelled. No file was written.", "warn")

    # ======================================================================================
    # Window events
    # ======================================================================================
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        urls = e.mimeData().urls() if e.mimeData().hasUrls() else []
        if urls and urls[0].isLocalFile() and self.export_job is None:
            e.acceptProposedAction()
            self._stack_before_drag = self.stack.currentWidget()
            self.empty.show_drop()
            self.stack.setCurrentWidget(self.empty)

    def dragLeaveEvent(self, _e) -> None:  # noqa: N802
        self._restore_after_drag()

    def _restore_after_drag(self) -> None:
        prev = getattr(self, "_stack_before_drag", None)
        if prev is not None:
            if prev is self.empty:
                if self.info is None:
                    self.empty.show_idle()
            self.stack.setCurrentWidget(prev)
            self._stack_before_drag = None

    def dropEvent(self, e) -> None:  # noqa: N802
        urls = e.mimeData().urls()
        if urls and urls[0].isLocalFile():
            e.acceptProposedAction()
            path = urls[0].toLocalFile()
            # Ask after the drop has finished, so Explorer isn't left waiting on our dialog.
            QTimer.singleShot(0, lambda: self.request_open(path) or self._restore_after_drag())
        else:
            self._restore_after_drag()

    def request_open(self, path: str, confirm: bool = True) -> bool:
        """Open `path` from a drop or another launch, asking first if work would be lost."""
        if self.export_job is not None:
            self.toast.show_message("Please wait for the current export to finish.", "warn")
            return False
        if confirm and self.model.loaded and not self.model.is_full:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle(APP_NAME)
            box.setText("Replace the current video?")
            box.setInformativeText(f"Open “{Path(path).name}” instead. "
                                   "Your current trim selection will be lost.")
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.Yes)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return False
        self._stack_before_drag = None
        self.open_file(path)
        return True

    def _disable_child_drops(self) -> None:
        # Qt's video widget contains an inner render surface that accepts drags and then
        # rejects them, which stopped drops ever reaching this window. Let them through.
        for w in [self.video, *self.video.findChildren(QWidget)]:
            w.setAcceptDrops(False)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._disable_child_drops()
        self._fit_bottom_bar()

    SAVE_TEXT = "  Save trimmed video"
    RESET_TEXT = " Reset"

    def _fit_bottom_bar(self) -> None:
        """Shrink the bottom bar in stages instead of letting Qt clip text:
        hide the summary, then the "Include audio" label, then show Reset and Save as icons."""
        steps = [
            lambda on: self.summary_label.setVisible(not on),
            lambda on: self.audio_label.setVisible(not on),
            lambda on: self.reset_btn.setText("" if on else self.RESET_TEXT),
            lambda on: self.save_btn.setText("" if on else self.SAVE_TEXT),
            lambda on: [f.caption.setVisible(not on) for f in (self.start_field, self.end_field)],
            lambda on: [b.setVisible(not on) for f in (self.start_field, self.end_field) for b in (f.back, f.fwd)],
        ]
        if not self.isVisible():
            return          # sizes aren't meaningful until the window is on screen
        lay = self.bottom_bar.layout()
        for step in steps:
            step(False)
        def bar_min() -> int:
            # Child layouts cache their sizes until an event loop pass; refresh them now.
            for child in self.bottom_bar.findChildren(QWidget):
                if child.layout() is not None:
                    child.layout().invalidate()
                    child.layout().activate()
            lay.invalidate()
            return lay.minimumSize().width()

        for step in steps:
            if bar_min() <= self.bottom_bar.width():
                break
            step(True)
        if self._compact_min_width is None:
            # Measure once with everything compacted: that's the narrowest usable window.
            for step in steps:
                step(True)
            self._compact_min_width = bar_min()
            self.setMinimumWidth(max(self.minimumWidth(), self._compact_min_width + 2))
            for step in steps:
                step(False)
            return self._fit_bottom_bar()
        compact = not self.save_btn.text()
        self.save_btn.setToolTip(("Save trimmed video" if compact else "Save As…") +
                                 f" ({self.keymap.hint('save')})")

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if ev.type() == QEvent.Type.MouseButtonPress and hasattr(self, "start_field"):
            for f in (self.start_field, self.end_field):
                if (f.edit.hasFocus() or f.editing) and obj is not f.edit:
                    f.cancel_edit()
        return False

    def changeEvent(self, e) -> None:  # noqa: N802
        super().changeEvent(e)
        if e.type() in (QEvent.Type.ActivationChange, QEvent.Type.WindowStateChange) and hasattr(self, "hover"):
            if not self.isActiveWindow() or self.isMinimized():
                self.hover.hide()                   # never leave the floating card behind

    def moveEvent(self, e) -> None:  # noqa: N802
        super().moveEvent(e)
        if hasattr(self, "hover"):
            self.hover.hide()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._fit_bottom_bar()
        if self.toast.isVisible():
            self.toast.reposition()

    def closeEvent(self, e: QCloseEvent) -> None:  # noqa: N802
        QApplication.instance().removeEventFilter(self)
        if self.export_job is not None:
            r = QMessageBox.question(self, APP_NAME, "An export is still running. Cancel it and quit?")
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self.export_job.cancel()
        self._cancel_jobs()
        self.hover.shutdown()
        self.hover.card.close()
        self.settings.geometry = self.saveGeometry()
        self.settings.sync()
        self.player.release()
        super().closeEvent(e)

    # ======================================================================================
    def _probe_encoders(self) -> None:
        probe = EncoderProbe()
        probe.done.connect(self.panel.set_encoders)
        probe.done.connect(lambda _d: self._options_changed())
        self._encoder_probe = probe
        probe.start()


def _rough_size(n: int) -> str:
    """'4.2 MB', '38 MB', '1.3 GB' - two significant figures; it's an estimate."""
    mb = n / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    if mb >= 10:
        return f"{mb:.0f} MB"
    if mb >= 1:
        return f"{mb:.1f} MB"
    return f"{max(1, round(n / 1024))} KB"
