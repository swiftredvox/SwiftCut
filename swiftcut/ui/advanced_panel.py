"""Advanced-mode side panel: output format/codec/quality/encoder/size, privacy, audio tracks, file info."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QScrollArea, QVBoxLayout, QWidget)

from ..core.export_plan import (CONTAINER_VIDEO_CODECS, CONTAINERS, VIDEO_CODEC_TIPS, VIDEO_CODECS, Encoder,
                                ExportOptions, PlanError, Quality, TrimMethod, default_container,
                                size_budget)
from ..core.media_info import MediaInfo
from ..core.timecode import FrameGrid, format_time
from .widgets import ToggleSwitch

SIZE_CHOICES = [(0, "No limit"), (8.0, "8 MB"), (10.0, "10 MB"), (25.0, "25 MB"),
                (50.0, "50 MB"), (100.0, "100 MB"), ("custom", "Custom\u2026")]

QUALITY_LABELS = {Quality.HIGH: "High (visually lossless)", Quality.BALANCED: "Balanced",
                  Quality.SMALL: "Smaller file"}


def _section(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setProperty("role", "section")
    return lab


def _note(text: str = "", role: str = "note") -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", role)
    lab.setWordWrap(True)
    return lab


def _restyle(w: QWidget, role: str) -> None:
    if w.property("role") != role:
        w.setProperty("role", role)
        w.style().unpolish(w)
        w.style().polish(w)


class AdvancedPanel(QFrame):
    optionsChanged = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SidePanel")
        self.setFixedWidth(300)
        self.info: MediaInfo | None = None
        self.grid: FrameGrid | None = None
        self.sel_start = 0.0
        self.nvenc = {"h264_nvenc": False, "hevc_nvenc": False}
        self._building = False
        self._muted = False
        self.sel_duration = 0.0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        content = QWidget()
        content.setObjectName("SideContent")
        scroll.setWidget(content)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(18, 14, 18, 18)
        lay.setSpacing(8)

        # Output (every save is a frame-exact precise trim)
        lay.addWidget(_section("Output"))
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        self.container = QComboBox()
        self.codec = QComboBox()
        self.quality = QComboBox()
        self.encoder = QComboBox()
        self.size_limit = QComboBox()
        self.size_limit.setToolTip("Make the saved file fit under a size, e.g. for chat apps with upload limits.\n"
                                   "SwiftCut picks the best quality that fits (two-pass encode).")
        for value, label in SIZE_CHOICES:
            self.size_limit.addItem(label, value)
        self._custom_mb: float | None = None
        for q in Quality:
            self.quality.addItem(QUALITY_LABELS[q], q.value)
        rows = [("Format", self.container), ("Video codec", self.codec),
                ("Quality", self.quality), ("Encoder", self.encoder), ("Size limit", self.size_limit)]
        for r, (label, w) in enumerate(rows):
            lab = QLabel(label)
            lab.setProperty("role", "muted")
            form.addWidget(lab, r, 0)
            form.addWidget(w, r, 1)
            w.currentIndexChanged.connect(self._changed)
        form.setColumnStretch(1, 1)
        lay.addLayout(form)
        self.output_note = _note()
        lay.addWidget(self.output_note)
        self.size_note = _note()
        lay.addWidget(self.size_note)
        self.estimate_note = _note()
        self.estimate_note.setToolTip("A rough guide, measured by encoding short samples with these settings.\n"
                                      "Updates as you change the selection, format, quality or audio.")
        lay.addWidget(self.estimate_note)
        self._estimate_text = ""
        self.size_limit.activated.connect(self._size_chosen)

        # Privacy
        meta_row = QHBoxLayout()
        meta_label = QLabel("Remove metadata")
        tip = ("Strips titles, comments, creation dates, GPS location, chapters and\n"
               "encoder tags from the saved file. Orientation is kept.")
        meta_label.setToolTip(tip)
        self.strip_meta = ToggleSwitch()
        self.strip_meta.setToolTip(tip)
        self.strip_meta.toggled.connect(self._changed)
        meta_row.addWidget(meta_label, 1)
        meta_row.addWidget(self.strip_meta)
        lay.addSpacing(4)
        lay.addLayout(meta_row)
        self.meta_note = _note("Removes dates, GPS location, titles and encoder tags. "
                               "Applies in Basic mode too.")
        lay.addWidget(self.meta_note)

        # Audio
        lay.addSpacing(6)
        lay.addWidget(_section("Audio"))
        self.audio_box = QVBoxLayout()
        self.audio_box.setSpacing(6)
        lay.addLayout(self.audio_box)
        self.audio_note = _note()
        lay.addWidget(self.audio_note)
        self.track_checks: list[tuple[int, QCheckBox]] = []

        # File info
        lay.addSpacing(6)
        lay.addWidget(_section("File details"))
        self.info_grid = QGridLayout()
        self.info_grid.setHorizontalSpacing(12)
        self.info_grid.setVerticalSpacing(4)
        lay.addLayout(self.info_grid)
        lay.addStretch(1)

    # -- public -----------------------------------------------------------------------------
    def set_encoders(self, available: dict) -> None:
        self.nvenc = available
        self._refresh_encoders()

    def apply_saved(self, o: ExportOptions) -> None:
        self._building = True
        self.strip_meta.setChecked(o.strip_metadata)
        self._set_size_limit(o.size_limit_mb)
        self._saved = o
        self._building = False
        if self.info:
            self.set_media(self.info, self.grid)

    def set_media(self, info: MediaInfo | None, grid: FrameGrid | None) -> None:
        self.info, self.grid = info, grid
        self._building = True
        saved = getattr(self, "_saved", ExportOptions())
        cur_container = self.container.currentData() or saved.container
        self.container.clear()
        src = default_container(info, TrimMethod.PRECISE) if info else "mp4"
        self.container.addItem(f"Same as source ({CONTAINERS[src][2].split(' ')[0]})", "source")
        for key, (_m, _e, label) in CONTAINERS.items():
            self.container.addItem(label, key)
        self._select(self.container, cur_container)
        self._select(self.quality, (self.quality.currentData() or saved.quality.value))
        self._refresh_codecs(self.codec.currentData() or saved.video_codec)
        self._refresh_encoders(self.encoder.currentData() or saved.encoder.value)
        self._rebuild_tracks()
        self._rebuild_info()
        self._building = False
        self._update_notes()

    def set_estimate(self, text: str) -> None:
        """Show the estimated output size ("" hides it). Hidden while a size limit is set."""
        self._estimate_text = text
        self._update_estimate()

    def _update_estimate(self) -> None:
        show = bool(self._estimate_text) and self.size_limit_mb() is None
        self.estimate_note.setText(self._estimate_text)
        self.estimate_note.setVisible(show)

    def set_selection(self, start: float, duration: float) -> None:
        self.sel_start, self.sel_duration = start, duration
        self._update_notes()

    # -- size limit -------------------------------------------------------------------------
    def size_limit_mb(self) -> float | None:
        v = self.size_limit.currentData()
        return float(v) if isinstance(v, (int, float)) and v > 0 else None

    def _set_size_limit(self, mb: float | None) -> None:
        if mb and self.size_limit.findData(mb) < 0:
            self._add_custom(mb)
        self._select(self.size_limit, mb if mb else 0)

    def _add_custom(self, mb: float) -> None:
        i = self.size_limit.findData("custom")
        self.size_limit.insertItem(i, f"{mb:g} MB", float(mb))

    def _size_chosen(self, _i: int) -> None:
        if self.size_limit.currentData() == "custom":
            from PySide6.QtWidgets import QInputDialog
            mb, ok = QInputDialog.getDouble(self, "Size limit", "Keep the saved file under (MB):",
                                            self.size_limit_mb() or 10.0, 0.5, 4000.0, 1)
            self._building = True
            if ok:
                self._set_size_limit(round(mb, 1))
            else:
                self._select(self.size_limit, 0)
            self._building = False
        self._changed()

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        for _i, cb in self.track_checks:
            cb.setEnabled(not muted)
        self._update_notes(muted=muted)

    def options(self, mute: bool) -> ExportOptions:
        tracks = [i for i, cb in self.track_checks if cb.isChecked()]
        return ExportOptions(
            method=TrimMethod.PRECISE,
            container=self.container.currentData() or "source",
            video_codec=self.codec.currentData() or "h264",
            quality=Quality(self.quality.currentData() or Quality.HIGH.value),
            encoder=Encoder(self.encoder.currentData() or Encoder.CPU.value),
            audio_tracks=tracks if self.track_checks else None,
            mute=mute,
            strip_metadata=self.strip_meta.isChecked(),
            size_limit_mb=self.size_limit_mb(),
        )

    def resolved_container(self) -> str:
        c = self.container.currentData() or "source"
        if c == "source":
            return default_container(self.info, TrimMethod.PRECISE) if self.info else "mp4"
        return c

    def summary(self) -> str:
        o = self.options(mute=False)
        fmt = CONTAINERS[self.resolved_container()][2].split(" ")[0]
        meta = " · No metadata" if o.strip_metadata else ""
        codec = VIDEO_CODECS.get(self.codec.currentData() or "h264", "").split(" (")[0]
        if o.size_limit_mb:
            return f"{fmt} · {codec} · Under {o.size_limit_mb:g} MB{meta}"
        enc = " · GPU" if o.encoder == Encoder.NVENC else ""
        return f"{fmt} · {codec} · {QUALITY_LABELS[o.quality].split(' (')[0]}{enc}{meta}"

    # -- internals --------------------------------------------------------------------------
    @staticmethod
    def _select(combo: QComboBox, data) -> None:
        i = combo.findData(data)
        combo.setCurrentIndex(i if i >= 0 else 0)

    def _refresh_codecs(self, prefer: str | None = None) -> None:
        prefer = prefer or self.codec.currentData()
        container = self.resolved_container()
        was = self._building
        self._building = True
        self.codec.clear()
        for key in CONTAINER_VIDEO_CODECS[container]:
            self.codec.addItem(VIDEO_CODECS[key], key)
            self.codec.setItemData(self.codec.count() - 1, VIDEO_CODEC_TIPS[key], Qt.ItemDataRole.ToolTipRole)
        self._select(self.codec, prefer)
        self._building = was

    def _refresh_encoders(self, prefer: str | None = None) -> None:
        prefer = prefer or self.encoder.currentData()
        was = self._building
        self._building = True
        self.encoder.clear()
        self.encoder.addItem("CPU", Encoder.CPU.value)
        codec = self.codec.currentData() or "h264"
        if codec in ("h264", "hevc") and self.nvenc.get(f"{codec}_nvenc"):
            self.encoder.addItem("NVIDIA GPU (NVENC)", Encoder.NVENC.value)
        self._select(self.encoder, prefer)
        self._building = was

    def _rebuild_tracks(self) -> None:
        prev = {i: cb.isChecked() for i, cb in self.track_checks}
        while self.audio_box.count():
            w = self.audio_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.track_checks = []
        if not self.info or not self.info.audio:
            return
        for n, a in enumerate(self.info.audio, start=1):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            lab = QLabel(a.label(n))
            lab.setWordWrap(True)
            sw = ToggleSwitch()
            sw.setChecked(prev.get(a.index, True))
            sw.setEnabled(not self._muted)
            sw.toggled.connect(self._changed)
            h.addWidget(lab, 1)
            h.addWidget(sw)
            self.audio_box.addWidget(row)
            self.track_checks.append((a.index, sw))  # type: ignore[arg-type]

    def _rebuild_info(self) -> None:
        while self.info_grid.count():
            w = self.info_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not self.info:
            return
        i, v = self.info, self.info.video
        w, h = v.display_size
        fps = f"{v.fps:.3f}".rstrip("0").rstrip(".")
        vfr = self.grid is not None and self.grid.exact and self._looks_vfr()
        rows = [
            ("Resolution", f"{w} × {h}" + (f"  (rotated {v.rotation}°)" if v.rotation else "")),
            ("Frame rate", f"{fps} fps" + ("  · variable" if vfr else "")),
            ("Video", f"{v.codec.upper()} {v.profile}".strip() + (f"  · {v.pix_fmt}" if v.pix_fmt else "")),
            ("Audio", ", ".join(f"{a.codec.upper()} {a.channels}ch" for a in i.audio) or "None"),
            ("Duration", format_time(i.duration)),
            ("Frames", str(len(self.grid)) if self.grid and self.grid.exact else "scanning…"),
            ("Keyframes", self._keyframe_text()),
            ("Size", _human_size(i.size_bytes)),
        ]
        for r, (k, val) in enumerate(rows):
            kl = QLabel(k)
            kl.setProperty("role", "faint")
            vl = QLabel(val)
            vl.setWordWrap(True)
            vl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.info_grid.addWidget(kl, r, 0, Qt.AlignmentFlag.AlignTop)
            self.info_grid.addWidget(vl, r, 1)
        self.info_grid.setColumnStretch(1, 1)

    def _looks_vfr(self) -> bool:
        g = self.grid
        if not g or len(g) < 3:
            return False
        ivals = [b - a for a, b in zip(g.times, g.times[1:])]
        mean = sum(ivals) / len(ivals)
        return max(ivals) > mean * 1.5 or min(ivals) < mean * 0.5

    def _keyframe_text(self) -> str:
        g = self.grid
        if not g or not g.exact:
            return "scanning…"
        n = len(g.keyframes)
        if n <= 1:
            return "1 (only the first frame)"
        avg = g.end_time / n
        return f"{n} (about every {avg:.1f}s)"

    def _changed(self, *_a) -> None:
        if self._building:
            return
        self._building = True
        self._refresh_codecs()
        self._refresh_encoders()
        self._building = False
        self._update_notes()
        self.optionsChanged.emit()

    def _update_notes(self, muted: bool | None = None) -> None:
        muted = self._muted if muted is None else muted
        sized = self.size_limit_mb() is not None
        for w in (self.quality, self.encoder):      # a size limit decides quality, on the CPU
            w.setEnabled(not sized)
        self._update_size_note()
        self._update_estimate()
        # Output note
        note = ""
        if sized:
            note = ""
        elif self.encoder.currentData() == Encoder.NVENC.value:
            note = "GPU encoding is much faster; files are slightly larger for the same quality."
        elif self.codec.currentData() == "vp9":
            note = "VP9 encoding is noticeably slower than H.264."
        self.output_note.setText(note)
        self.output_note.setVisible(bool(note))
        # Audio note
        if not self.info:
            self.audio_note.setText("")
        elif not self.info.audio:
            self.audio_note.setText("This video has no audio.")
        elif muted:
            self.audio_note.setText("“Include audio” is off, so the saved file and the preview are silent.")
        else:
            on = [n for n, (_i, cb) in enumerate(self.track_checks, start=1) if cb.isChecked()]
            if not on:
                self.audio_note.setText("All tracks are off: the saved file and the preview are silent.")
            elif len(on) > 1:
                self.audio_note.setText(f"Previewing Track {on[0]} only (playback is one track at a time). "
                                        f"All {len(on)} selected tracks will be saved.")
            else:
                self.audio_note.setText("Turn off tracks you don't want. The preview plays what will be saved.")

    def _update_size_note(self) -> None:
        mb = self.size_limit_mb()
        if not mb:
            self.size_note.hide()
            return
        self.size_note.show()
        if not self.info or self.sel_duration <= 0:
            self.size_note.setText(f"Fits the file under {mb:g} MB. Saving takes about twice as long (two passes).")
            _restyle(self.size_note, "note")
            return
        n_audio = 0 if self._muted else len([1 for _i, cb in self.track_checks if cb.isChecked()])
        try:
            b = size_budget(self.info, self.sel_duration, n_audio, mb)
        except PlanError:
            self.size_note.setText(f"This {format_time(self.sel_duration, show_ms=False)} selection is too long "
                                   f"to fit in {mb:g} MB. Shorten it or raise the limit.")
            _restyle(self.size_note, "warn")
            return
        verdict = {"good": "good quality", "ok": "decent quality",
                   "low": "low quality: consider a shorter clip or bigger limit"}[b.quality]
        rate = f"{b.video_kbps / 1000:.1f} Mbit/s" if b.video_kbps >= 1000 else f"{b.video_kbps} kbit/s"
        self.size_note.setText(f"About {rate} video for this {format_time(self.sel_duration, show_ms=False)} "
                               f"clip, {verdict}. Saving takes about twice as long (two passes).")
        _restyle(self.size_note, "warn" if b.quality == "low" else "note")

    def refresh_info(self) -> None:
        self._rebuild_info()
        self._update_notes()


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} bytes"
