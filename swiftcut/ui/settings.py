"""Persisted preferences (HKCU\\Software\\SwiftCut via QSettings). No file history is stored."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings

from ..core.export_plan import Encoder, ExportOptions, Quality, TrimMethod


class Settings:
    def __init__(self, application: str = "SwiftCut") -> None:
        self._s = QSettings("SwiftCut", application)

    def clear(self) -> None:
        self._s.clear()

    def _get(self, key: str, default, typ=str):
        try:
            v = self._s.value(key, default, type=typ)
        except (TypeError, ValueError):
            return default
        return default if v is None else v

    # window ---------------------------------------------------------------------------------
    @property
    def geometry(self) -> QByteArray | None:
        v = self._s.value("window/geometry")
        return v if isinstance(v, QByteArray) else None

    @geometry.setter
    def geometry(self, v: QByteArray) -> None:
        self._s.setValue("window/geometry", v)

    @property
    def advanced(self) -> bool:
        return bool(self._get("ui/advanced", False, bool))

    @advanced.setter
    def advanced(self, v: bool) -> None:
        self._s.setValue("ui/advanced", v)

    # playback -------------------------------------------------------------------------------
    @property
    def loop(self) -> bool:
        return bool(self._get("playback/loop", False, bool))

    @loop.setter
    def loop(self, v: bool) -> None:
        self._s.setValue("playback/loop", v)

    @property
    def preview_muted(self) -> bool:
        return bool(self._get("playback/muted", False, bool))

    @preview_muted.setter
    def preview_muted(self, v: bool) -> None:
        self._s.setValue("playback/muted", v)

    @property
    def volume(self) -> float:
        try:
            return min(1.0, max(0.0, float(self._get("playback/volume", 0.8, float))))
        except (TypeError, ValueError):
            return 0.8

    @volume.setter
    def volume(self, v: float) -> None:
        self._s.setValue("playback/volume", round(v, 3))

    @property
    def audio_device(self) -> bytes | None:
        v = str(self._get("playback/device", ""))
        try:
            return bytes.fromhex(v) if v else None
        except ValueError:
            return None

    @audio_device.setter
    def audio_device(self, device_id: bytes | None) -> None:
        self._s.setValue("playback/device", device_id.hex() if device_id else "")

    @property
    def hover_preview(self) -> bool:
        return bool(self._get("ui/hover_preview", True, bool))

    @hover_preview.setter
    def hover_preview(self, v: bool) -> None:
        self._s.setValue("ui/hover_preview", v)

    def animation_options(self):
        from ..core.animation import AnimationOptions
        d = AnimationOptions()
        fmt = str(self._get("animation/format", d.format))
        return AnimationOptions(
            format=fmt if fmt in ("gif", "webp") else d.format,
            width=int(self._get("animation/width", d.width or 0, int)) or None,
            fps=float(self._get("animation/fps", d.fps or 0, float)) or None,
            loop=bool(self._get("animation/loop", d.loop, bool)),
            quality=str(self._get("animation/quality", d.quality)),
        )

    def save_animation_options(self, o) -> None:
        self._s.setValue("animation/format", o.format)
        self._s.setValue("animation/width", int(o.width or 0))
        self._s.setValue("animation/fps", float(o.fps or 0))
        self._s.setValue("animation/loop", o.loop)
        self._s.setValue("animation/quality", o.quality)

    @property
    def qsettings(self) -> QSettings:
        return self._s

    # export (Advanced mode) -----------------------------------------------------------------
    def export_options(self) -> ExportOptions:
        def enum(cls, key, default):
            try:
                return cls(self._get(key, default.value))
            except ValueError:
                return default
        return ExportOptions(
            container=str(self._get("export/container", "source")),
            video_codec=str(self._get("export/codec", "h264")),
            quality=enum(Quality, "export/quality", Quality.HIGH),
            encoder=enum(Encoder, "export/encoder", Encoder.CPU),
            strip_metadata=bool(self._get("export/strip_metadata", False, bool)),
            size_limit_mb=(float(self._get("export/size_limit_mb", 0.0, float)) or None),
        )

    def save_export_options(self, o: ExportOptions) -> None:
        self._s.setValue("export/container", o.container)
        self._s.setValue("export/codec", o.video_codec)
        self._s.setValue("export/quality", o.quality.value)
        self._s.setValue("export/encoder", o.encoder.value)
        self._s.setValue("export/strip_metadata", o.strip_metadata)
        self._s.setValue("export/size_limit_mb", float(o.size_limit_mb or 0.0))

    def sync(self) -> None:
        self._s.sync()
