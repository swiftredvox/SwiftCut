"""Dark theme: colour tokens, a Fusion palette and the application stylesheet."""
from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

BG = "#0d0e11"          # window
SURFACE = "#15171c"     # panels, header
SURFACE_2 = "#1c1f26"   # inputs, buttons
SURFACE_3 = "#252932"   # hover
BORDER = "#262a33"
BORDER_STRONG = "#353a46"
TEXT = "#e9ebf1"
TEXT_MUTED = "#8b91a1"
TEXT_FAINT = "#5c6273"
ACCENT = "#2ee6d6"
ACCENT_HOVER = "#5ff0e3"
ACCENT_PRESSED = "#1fc4b6"
ACCENT_TEXT = "#04211e"  # text on accent
ACCENT_DIM = "#123b3a"
DANGER = "#ff6b7a"
WARNING = "#ffc66b"
EXCLUDED_SHADE = QColor(5, 6, 8, 185)

FONT_FAMILY = "Segoe UI Variable Text" if sys.platform == "win32" else "Segoe UI"
MONO_FAMILY = "Cascadia Mono, Consolas"


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    c = QColor
    pal.setColor(QPalette.ColorRole.Window, c(BG))
    pal.setColor(QPalette.ColorRole.WindowText, c(TEXT))
    pal.setColor(QPalette.ColorRole.Base, c(SURFACE_2))
    pal.setColor(QPalette.ColorRole.AlternateBase, c(SURFACE))
    pal.setColor(QPalette.ColorRole.Text, c(TEXT))
    pal.setColor(QPalette.ColorRole.Button, c(SURFACE_2))
    pal.setColor(QPalette.ColorRole.ButtonText, c(TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, c(ACCENT))
    pal.setColor(QPalette.ColorRole.HighlightedText, c(ACCENT_TEXT))
    pal.setColor(QPalette.ColorRole.ToolTipBase, c(SURFACE_3))
    pal.setColor(QPalette.ColorRole.ToolTipText, c(TEXT))
    pal.setColor(QPalette.ColorRole.PlaceholderText, c(TEXT_FAINT))
    pal.setColor(QPalette.ColorRole.Link, c(ACCENT))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, c(TEXT_FAINT))
    app.setPalette(pal)
    font = QFont(FONT_FAMILY)
    font.setPointSizeF(9.5)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)
    from . import icons
    app.setStyleSheet(STYLESHEET
                      .replace("{CHECK}", icons.image_path("check", ACCENT_TEXT, 3.0))
                      .replace("{CHEV_OFF}", icons.image_path("chev-down", TEXT_FAINT, 2.2))
                      .replace("{CHEV}", icons.image_path("chev-down", TEXT_MUTED, 2.2)))


STYLESHEET = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER_STRONG};
            padding: 5px 8px; border-radius: 6px; }}
QLabel {{ color: {TEXT}; }}
QLabel[role="muted"] {{ color: {TEXT_MUTED}; }}
QLabel[role="faint"] {{ color: {TEXT_FAINT}; }}
QLabel[role="title"] {{ font-size: 11pt; font-weight: 600; }}
QLabel[role="section"] {{ color: {TEXT_MUTED}; font-size: 8pt; font-weight: 600;
                          letter-spacing: 0.6px; padding-top: 6px; }}
QLabel[role="note"] {{ color: {TEXT_MUTED}; font-size: 8.5pt; }}
QLabel[role="warn"] {{ color: {WARNING}; font-size: 8.5pt; }}
QLabel[role="mono"] {{ font-family: {MONO_FAMILY}; font-size: 10pt; }}

#Header {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
#BottomBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
#SidePanel {{ background: {SURFACE}; border-left: 1px solid {BORDER}; }}
#SidePanel QScrollArea, #SidePanel #SideContent {{ background: transparent; border: none; }}

QPushButton {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER};
               border-radius: 8px; padding: 6px 12px; }}
QPushButton:hover {{ background: {SURFACE_3}; border-color: {BORDER_STRONG}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; background: {SURFACE}; border-color: {BORDER}; }}
QPushButton:focus {{ outline: none; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; color: {ACCENT_TEXT}; border: none;
                               font-weight: 600; padding: 8px 18px; border-radius: 9px; }}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[kind="primary"]:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton[kind="primary"]:disabled {{ background: {ACCENT_DIM}; color: #3d6f6b; }}
QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid transparent; }}
QPushButton[kind="ghost"]:hover {{ background: {SURFACE_2}; border-color: {BORDER}; }}
QPushButton[kind="danger"] {{ background: transparent; color: {DANGER}; border: 1px solid #4a2a30; }}
QPushButton[kind="danger"]:hover {{ background: #2a1a1e; }}

QToolButton {{ background: transparent; color: {TEXT}; border: 1px solid transparent;
               border-radius: 8px; padding: 4px; }}
QToolButton:hover {{ background: {SURFACE_2}; border-color: {BORDER}; }}
QToolButton:pressed {{ background: {SURFACE_3}; }}
QToolButton:checked {{ background: {ACCENT_DIM}; border-color: #1d5754; }}
QToolButton:disabled {{ color: {TEXT_FAINT}; }}
QToolButton[kind="play"] {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: 19px; }}
QToolButton[kind="play"]:hover {{ background: {SURFACE_3}; border-color: {ACCENT}; }}

QLineEdit, QComboBox {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER};
                        border-radius: 7px; padding: 5px 8px; selection-background-color: {ACCENT};
                        selection-color: {ACCENT_TEXT}; }}
QLineEdit:hover, QComboBox:hover {{ border-color: {BORDER_STRONG}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {TEXT_FAINT}; background: {SURFACE}; }}
QLineEdit[invalid="true"] {{ border-color: {DANGER}; }}
QLineEdit[role="time"] {{ font-family: {MONO_FAMILY}; font-size: 10pt; padding: 4px 6px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({{CHEV}}); width: 12px; height: 12px; margin-right: 8px; }}
QComboBox::down-arrow:disabled {{ image: url({{CHEV_OFF}}); }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER_STRONG};
                               border-radius: 6px; padding: 4px; outline: none;
                               selection-background-color: {SURFACE_3}; selection-color: {TEXT}; }}

QCheckBox {{ color: {TEXT}; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER_STRONG};
                        background: {SURFACE_2}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({{CHECK}}); }}
QCheckBox:disabled {{ color: {TEXT_FAINT}; }}

QProgressBar {{ background: {SURFACE_2}; border: none; border-radius: 4px; height: 8px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; border-radius: 8px;
         padding: 5px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QMessageBox {{ background: {SURFACE}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""
