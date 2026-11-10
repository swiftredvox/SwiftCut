"""Helpers for running SwiftCut windows unobtrusively during tests and screenshot checks.

Windows are shown on a secondary monitor (if there is one), without taking keyboard focus,
and pushed behind every other window. `capture()` uses PrintWindow with full-content rendering
so a covered window - including its GPU-rendered video - can still be photographed.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QKeySequence, QShortcut

SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
HWND_BOTTOM = 1
PW_RENDERFULLCONTENT = 0x00000002


def _target_screen():
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    others = [s for s in screens if s is not primary]
    return others[0] if others else primary


def show_in_background(w, width: int = 1280, height: int = 800) -> None:
    """Show `w` on the secondary monitor, unfocused, at the bottom of the z-order."""
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    geo = _target_screen().availableGeometry()
    w.resize(min(width, geo.width() - 80), min(height, geo.height() - 80))
    w.move(geo.x() + 40, geo.y() + 40)
    w.show()
    send_to_back(w)


def send_to_back(w) -> None:
    if sys.platform != "win32":
        return
    ctypes.windll.user32.SetWindowPos(int(w.winId()), HWND_BOTTOM, 0, 0, 0, 0,
                                      SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def capture(w, path: str) -> bool:
    """Screenshot a (possibly covered) top-level window, video surface included."""
    if sys.platform != "win32":
        return w.grab().save(path)
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hwnd = int(w.winId())
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    hdc_win = user32.GetWindowDC(hwnd)
    hdc = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, width, height)
    gdi32.SelectObject(hdc, bmp)
    ok = user32.PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = width, -height, 1, 32
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(hdc, bmp, 0, height, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc)
    user32.ReleaseDC(hwnd, hdc_win)
    img = QImage(buf.raw, width, height, width * 4, QImage.Format.Format_RGB32).copy()
    return bool(ok) and img.save(path)


def trigger_key(window, key: str) -> bool:
    """Fire the window's shortcut for `key` without needing keyboard focus."""
    want = QKeySequence(key)
    for sc in window.findChildren(QShortcut):
        if sc.isEnabled() and sc.key() == want:
            sc.activated.emit()
            return True
    return False


def clipped_buttons(root) -> list[str]:
    """Texts of push buttons under `root` whose label doesn't fit inside the button."""
    from PySide6.QtWidgets import QPushButton
    bad = []
    for b in root.findChildren(QPushButton):
        if not b.isVisibleTo(root) or not b.text().strip():
            continue
        need = b.fontMetrics().horizontalAdvance(b.text().strip())
        if not b.icon().isNull():
            need += b.iconSize().width() + 4
        if b.width() < need + 12:            # a little room for the stylesheet's padding
            bad.append(f"{b.text().strip()!r} ({b.width()}px < {need + 12}px)")
    return bad
