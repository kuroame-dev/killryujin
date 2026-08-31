"""Qt palette, fonts, and Windows caption colors."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QImage,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

BG = "#000000"
FG = "#FFFFFF"
DIM = "#7A7A7A"
DISABLED = "#4A4A4A"
GREEN = "#00FF00"
RED = "#FF2020"
YELLOW = "#FFFF00"
INV_BG = "#FFFFFF"
INV_FG = "#000000"

# Windows treats COLORREF 0 as "use the accent", so black is 1,1,1.
_CAPTION_BLACK = 0x00010101
_CAPTION_WHITE = 0x00FFFFFF
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_NONE = 1
GA_ROOT = 2


def app_icon() -> QIcon:
    ico = Path(__file__).with_name("icon.ico")
    png = Path(__file__).with_name("icon.png")
    path = ico if ico.is_file() else png
    return QIcon(str(path))


def pick_font(size: int, bold: bool = False) -> QFont:
    names = ("Cascadia Mono", "Consolas", "Lucida Console", "Courier New")
    available = {name.lower() for name in QFontDatabase.families()}
    chosen = "Courier New"
    for name in names:
        if name.lower() in available:
            chosen = name
            break
    font = QFont(chosen, size)
    font.setBold(bold)
    font.setStyleHint(QFont.StyleHint.TypeWriter)
    font.setFixedPitch(True)
    return font


def _dwm_set(hwnd: int, attr: int, value: int) -> None:
    try:
        v = ctypes.c_uint(value & 0xFFFFFFFF)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)
        )
    except (AttributeError, OSError):
        return


def _native_hwnd(widget: QWidget) -> int:
    handle = widget.windowHandle()
    hwnd = int(handle.winId()) if handle is not None else int(widget.effectiveWinId())
    if not hwnd:
        return 0
    try:
        root = ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT)
    except (AttributeError, OSError):
        return hwnd
    return int(root) if root else hwnd


def theme_native_caption(widget: QWidget) -> None:
    """Documented Win11 DWM colors. Windows resets the border on focus; we set it again."""
    if sys.platform != "win32":
        return
    hwnd = _native_hwnd(widget)
    if not hwnd:
        return
    _dwm_set(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
    _dwm_set(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_NONE)
    _dwm_set(hwnd, DWMWA_CAPTION_COLOR, _CAPTION_BLACK)
    _dwm_set(hwnd, DWMWA_TEXT_COLOR, _CAPTION_WHITE)
    _dwm_set(hwnd, DWMWA_BORDER_COLOR, _CAPTION_WHITE)


def pil_to_qpixmap(img: Image.Image) -> QPixmap:
    rgb = img.convert("RGB")
    w, h = rgb.size
    data = rgb.tobytes()
    qimg = QImage(data, w, h, w * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


def configure_qt(app: QApplication) -> None:
    app.setStyle("Fusion")
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except AttributeError:
        pass
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(FG))
    pal.setColor(QPalette.ColorRole.Base, QColor(BG))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(BG))
    pal.setColor(QPalette.ColorRole.Text, QColor(FG))
    pal.setColor(QPalette.ColorRole.Button, QColor(BG))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(FG))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(FG))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(BG))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(BG))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(FG))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(DISABLED))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(DISABLED))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor(BG))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(DISABLED))
    app.setPalette(pal)
    app.setStyleSheet(
        f"QMainWindow, QDialog, QWidget {{ background: {BG}; color: {FG}; }}"
        f"QPushButton:disabled {{ color: {DISABLED}; background: {BG}; }}"
        f"QToolTip {{ background: {BG}; color: {FG}; border: 1px solid {FG}; }}"
    )
