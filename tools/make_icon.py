"""Render the SwiftCut "cut S" icon (concept D2) into .ico / .png files.

The geometry mirrors swiftcut/resources/swiftcut.svg (100x100 design grid). It is drawn
with QPainter rather than QSvgRenderer because Qt's SVG renderer ignores <clipPath>,
which the split-colour design relies on.

Usage:  .venv/Scripts/python tools/make_icon.py
"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt
from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QImage, QPainter, QPainterPath,
                           QPen, QPolygonF)

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "swiftcut" / "resources"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

BG = QColor("#15171c")
WHITE = QColor("#ffffff")
CYAN = QColor("#2ee6d6")


def s_path() -> QPainterPath:
    p = QPainterPath(QPointF(65, 31))
    p.cubicTo(58, 22, 36, 23, 36, 37)
    p.cubicTo(36, 50, 64, 48, 64, 62)
    p.cubicTo(64, 77, 40, 77, 34, 68)
    return p


def poly(*pts) -> QPainterPath:
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
    path.closeSubpath()
    return path


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 100.0, size / 100.0)
    # Slightly heavier strokes at tiny sizes so the S survives 16-24 px.
    stroke = 11 if size >= 32 else 13
    gap = 6 if size >= 32 else 7
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(BG)
    p.drawRoundedRect(0, 0, 100, 100, 22, 22)
    for color, clip in ((WHITE, poly((0, 0), (100, 0), (100, 10.7), (0, 89.3))),
                        (CYAN, poly((0, 89.3), (100, 10.7), (100, 100), (0, 100)))):
        p.save()
        p.setClipPath(clip)
        p.setPen(QPen(color, stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(s_path())
        p.restore()
    p.setPen(QPen(BG, gap, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
    p.drawLine(QPointF(18, 75), QPointF(82, 25))
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba.data())


def main() -> None:
    QGuiApplication(sys.argv)
    blobs = [png_bytes(render(s)) for s in SIZES]
    out = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + 16 * len(SIZES)
    data = b""
    for size, blob in zip(SIZES, blobs):
        dim = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        data += blob
        offset += len(blob)
    (RES / "swiftcut.ico").write_bytes(out + data)
    render(512).save(str(RES / "swiftcut.png"))
    print(f"Wrote swiftcut.ico ({len(SIZES)} sizes) and swiftcut.png to {RES}")


if __name__ == "__main__":
    main()
