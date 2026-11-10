"""Small line-icon set (24x24, stroke-based), rendered to crisp, tinted QIcons at any DPI."""
from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

RESOURCES = Path(__file__).resolve().parents[1] / "resources"

_STROKE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" '
           'stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')

PATHS: dict[str, str] = {
    "play": '<path d="M7.5 4.8v14.4a1 1 0 0 0 1.5.86l11.2-7.2a1 1 0 0 0 0-1.72L9 3.94a1 1 0 0 0-1.5.86z" fill="{c}" stroke="none"/>',
    "pause": '<rect x="6" y="4.5" width="4" height="15" rx="1.2" fill="{c}" stroke="none"/>'
             '<rect x="14" y="4.5" width="4" height="15" rx="1.2" fill="{c}" stroke="none"/>',
    "frame-back": '<path d="M6 5v14"/><path d="M18 6.5v11a.8.8 0 0 1-1.2.7L9.5 12.7a.8.8 0 0 1 0-1.4l7.3-5.5a.8.8 0 0 1 1.2.7z" fill="{c}"/>',
    "frame-fwd": '<path d="M18 5v14"/><path d="M6 6.5v11a.8.8 0 0 0 1.2.7l7.3-5.5a.8.8 0 0 0 0-1.4L7.2 5.8a.8.8 0 0 0-1.2.7z" fill="{c}"/>',
    "loop": '<path d="M17 2.5l3.5 3.5L17 9.5"/><path d="M3.5 11.5V10a4 4 0 0 1 4-4h13"/>'
            '<path d="M7 21.5L3.5 18 7 14.5"/><path d="M20.5 12.5V14a4 4 0 0 1-4 4h-13"/>',
    "volume": '<path d="M11 5L6 9H3v6h3l5 4z" fill="{c}"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9.5 9.5 0 0 1 0 13"/>',
    "volume-off": '<path d="M11 5L6 9H3v6h3l5 4z" fill="{c}"/><path d="M22 9l-6 6"/><path d="M16 9l6 6"/>',
    "open": '<path d="M3.5 7.5v10a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-7l-2-2.5h-4a2 2 0 0 0-2 2z"/>',
    "settings": '<path d="M4 6h9"/><path d="M17 6h3"/><circle cx="15" cy="6" r="2"/><path d="M4 12h3"/><path d="M11 12h9"/>'
                '<circle cx="9" cy="12" r="2"/><path d="M4 18h11"/><path d="M19 18h1"/><circle cx="17" cy="18" r="2"/>',
    "reset": '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5"/><path d="M3.5 3.5v5h5"/>',
    "save": '<path d="M12 3.5v11"/><path d="M7.5 10l4.5 4.5 4.5-4.5"/><path d="M4.5 20.5h15"/>',
    "scissors": '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.1 15.9"/>'
                '<path d="M14.5 14.5L20 20"/><path d="M8.1 8.1L12 12"/>',
    "film": '<rect x="3" y="3.5" width="18" height="17" rx="2.5"/><path d="M7.5 3.5v17"/><path d="M16.5 3.5v17"/>'
            '<path d="M3 8h4.5"/><path d="M3 12h18"/><path d="M3 16h4.5"/><path d="M16.5 8H21"/><path d="M16.5 16H21"/>',
    "keyboard": '<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6.5 10h.01"/><path d="M10 10h.01"/>'
                '<path d="M13.5 10h.01"/><path d="M17 10h.01"/><path d="M8 14h8"/>',
    "camera": '<path d="M4 8h3l1.8-2.5h6.4L17 8h3a1.5 1.5 0 0 1 1.5 1.5v8.5A1.5 1.5 0 0 1 20 19.5H4'
              'A1.5 1.5 0 0 1 2.5 18V9.5A1.5 1.5 0 0 1 4 8z"/><circle cx="12" cy="13.5" r="3.5"/>',
    "zoom-in": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L20.5 20.5"/><path d="M8 10.5h5"/><path d="M10.5 8v5"/>',
    "zoom-out": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L20.5 20.5"/><path d="M8 10.5h5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "chev-down": '<path d="M6 9.5l6 6 6-6"/>',
    "chev-left": '<path d="M14.5 6l-6 6 6 6"/>',
    "chev-right": '<path d="M9.5 6l6 6-6 6"/>',
    "mark-in": '<path d="M10 4.5H6v15h4"/><path d="M14 12h6"/><path d="M17 9l3 3-3 3"/>',
    "mark-out": '<path d="M14 4.5h4v15h-4"/><path d="M10 12H4"/><path d="M7 9l-3 3 3 3"/>',
    "close": '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
    "folder": '<path d="M3.5 7.5v10a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-7l-2-2.5h-4a2 2 0 0 0-2 2z"/><path d="M12 11v5"/><path d="M9.5 13.5L12 16l2.5-2.5"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5"/><path d="M12 7.8v.2"/>',
    "alert": '<path d="M12 4l9 15.5H3z"/><path d="M12 10v4"/><path d="M12 17v.2"/>',
    "drop": '<path d="M12 3.5v11"/><path d="M7.5 10l4.5 4.5 4.5-4.5"/><rect x="3.5" y="16.5" width="17" height="4" rx="1.5"/>',
}


def svg(name: str, color: str, width: float = 1.8) -> bytes:
    body = PATHS[name].replace("{c}", color)
    return _STROKE.format(c=color, w=width, body=body).encode()


@lru_cache(maxsize=256)
def pixmap(name: str, color: str = theme.TEXT, size: int = 18, dpr: float = 2.0, width: float = 1.8) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg(name, color, width)))
    px = int(round(size * dpr))
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, px, px))
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str = theme.TEXT, size: int = 18, disabled_color: str = theme.TEXT_FAINT) -> QIcon:
    ic = QIcon()
    for dpr in (1.0, 1.5, 2.0):
        ic.addPixmap(pixmap(name, color, size, dpr), QIcon.Mode.Normal)
        ic.addPixmap(pixmap(name, disabled_color, size, dpr), QIcon.Mode.Disabled)
    return ic


def app_icon() -> QIcon:
    ico = RESOURCES / "swiftcut.ico"
    return QIcon(str(ico)) if ico.exists() else QIcon(str(RESOURCES / "swiftcut.png"))


@lru_cache(maxsize=None)
def image_path(name: str, color: str, width: float = 1.8) -> str:
    """Write an icon to a temp .svg for use in stylesheets (QSS can only reference files)."""
    path = Path(tempfile.gettempdir()) / f"swiftcut-{name}-{color.lstrip('#')}.svg"
    data = svg(name, color, width)
    try:
        if not path.exists() or path.read_bytes() != data:
            path.write_bytes(data)
    except OSError:
        return ""
    return path.as_posix()
