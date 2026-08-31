"""Desktop UI. Terminal chrome, native Qt window, same cooler controls."""

from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Callable, Optional, TypedDict

from PIL import Image
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPixmap,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, APP_SLUG, crate
from . import __version__
from .device import (
    FlashSlotStuck,
    Ryujin,
    RyujinError,
    enumerate_coolers,
)
from .media import composite_gif_rgb, view_rgb
from .protocol import LCD_HEIGHT as PREVIEW_H
from .protocol import LCD_WIDTH as PREVIEW_W
from .theme import (
    BG,
    DIM,
    DISABLED,
    FG,
    GREEN,
    INV_BG,
    INV_FG,
    RED,
    YELLOW,
    app_icon,
    configure_qt,
    pick_font,
    pil_to_qpixmap,
    theme_native_caption,
)

VIEW_ZOOM_MAX = 8.0
GALLERY_N = 4
THUMB_W = 77
THUMB_H = 58
THUMB_GAP = 4
ROW_CHARS = 44
LINE = 8
BTN_GAP = LINE * 2
HINT_IDLE = "HOVER A CONTROL FOR A SHORT EXPLANATION."
NO_FILE = "NO FILE SELECTED"
MIN_WIN_W = 880
MIN_WIN_H = 740


class GalleryEntry(TypedDict):
    path: str
    name: str
    zoom: float
    pan_x: float
    pan_y: float


def _short_product(name: str) -> str:
    s = " ".join(str(name).upper().split())
    if "ASUSTEK" in s:
        return "RYUJIN III"
    s = s.replace("ROG ", "").replace(" EDITION", "")
    return s.strip(" -")


def _state_file() -> Path:
    root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(root) / APP_SLUG / "state.json"


def _thumbs_dir() -> Path:
    return _state_file().parent / "thumbs"


def _thumb_file(source: str) -> Path:
    key = hashlib.sha256(_path_key(source).encode("utf-8", errors="replace")).hexdigest()[:16]
    return _thumbs_dir() / f"{key}.png"


def _path_key(path: Path | str) -> str:
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:
        return os.path.normcase(str(path))


class DragHandle(QWidget):
    """Header chrome that asks Qt for a native window move. Not a fake caption."""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            if win.isMaximized():
                win.showNormal()
            else:
                win.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class TermButton(QPushButton):
    """Plain text until hover/focus (1px box). inverted=True is a solid white CTA."""

    def __init__(
        self,
        text: str,
        command: Optional[Callable[[], None]] = None,
        font: QFont | None = None,
        padx: int = 8,
        pady: int = 2,
        inverted: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFlat(True)
        self.setAutoDefault(False)
        self.setDefault(False)
        if font is not None:
            self.setFont(font)
        pad = f"padding: {pady}px {padx}px;"
        disabled = (
            f"QPushButton:disabled {{ background: {BG}; color: {DISABLED}; "
            f"border: 1px solid {BG}; {pad} }}"
        )
        if inverted:
            self.setStyleSheet(
                f"QPushButton:enabled {{ background: {INV_BG}; color: {INV_FG}; border: 1px solid {INV_BG}; {pad} }}"
                + disabled
            )
        else:
            self.setStyleSheet(
                f"QPushButton:enabled {{ background: {BG}; color: {FG}; border: 1px solid {BG}; {pad} }}"
                f"QPushButton:enabled:hover, QPushButton:enabled:focus {{ border: 1px solid {FG}; }}"
                + disabled
            )
        self._hint = ""
        self._hint_host: Optional["App"] = None
        if command is not None:
            self.clicked.connect(command)

    def set_hint(self, text: str, host: "App") -> None:
        self._hint = text
        self._hint_host = host

    def enterEvent(self, event) -> None:
        if self._hint_host is not None:
            self._hint_host._hint_enter(self, self._hint)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hint_host is not None:
            self._hint_host._hint_leave(self)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:
        if self._hint_host is not None:
            self._hint_host._refresh_hint()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        if self._hint_host is not None:
            self._hint_host._refresh_hint()
        super().focusOutEvent(event)

    def set_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)
        self.setCursor(
            Qt.CursorShape.ArrowCursor if not enabled else Qt.CursorShape.PointingHandCursor
        )


class DottedRow(QWidget):
    """Label + leader dots + value. Width follows the text, not the window."""

    def __init__(self, label: str, font: QFont, value: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = label.upper()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._left = QLabel(self._label)
        self._dots = QLabel("")
        self._right = QLabel(value.upper())
        for widget in (self._left, self._dots, self._right):
            widget.setFont(font)
            widget.setStyleSheet("background: transparent;")
        self._dots.setStyleSheet(f"color: {DIM}; background: transparent;")
        lay.addWidget(self._left)
        lay.addWidget(self._dots)
        lay.addWidget(self._right)
        lay.addStretch(1)
        self._reflow()

    def set_value(self, value: str, *, dim: bool = False, alert: bool = False) -> None:
        color = RED if alert else (DIM if dim else FG)
        self._right.setText(str(value).upper())
        self._right.setStyleSheet(f"color: {color}; background: transparent;")
        self._reflow()

    def _reflow(self) -> None:
        val = self._right.text()
        n = ROW_CHARS - len(self._label) - len(val)
        self._dots.setText("." * max(3, n))


LED_DOT = 9
LED_GAP = 10
LED_GUTTER = LED_DOT + LED_GAP


class StatusLed(QWidget):
    """9px status dot. Drawn in paintEvent so the app-wide QWidget stylesheet cannot hide it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(LED_DOT, LED_DOT)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._ok = False

    def set_ok(self, ok: bool) -> None:
        self._ok = ok
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(BG))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(GREEN if self._ok else RED))
        painter.drawEllipse(1, 1, 7, 7)


class PreviewCanvas(QLabel):
    def __init__(self, owner: "App", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._owner = owner
        self.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setStyleSheet(f"background: {BG}; color: {DIM};")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._owner._preview_press(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._owner._preview_move(event.position())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._owner._preview_release()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._owner._reset_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._owner._on_preview_wheel(event)
        event.accept()

    def enterEvent(self, event) -> None:
        self._owner._hint_enter(
            self,
            "Wheel zoom and drag to pan the crop you will save. Double-click resets.",
        )
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._owner._hint_leave(self)
        super().leaveEvent(event)


class GalleryCell(QFrame):
    def __init__(
        self,
        selected: bool,
        on_click: Callable[[], None],
        pixmap: QPixmap | None,
        font: QFont,
        hint_host: "App | None" = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._selected = selected
        self._on_click = on_click
        self._hint_host = hint_host
        self._hint = "Load this recent crop into the preview."
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(THUMB_W + 2, THUMB_H + 2)
        self._paint_border()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        face = QLabel()
        face.setAlignment(Qt.AlignmentFlag.AlignCenter)
        face.setFixedSize(THUMB_W, THUMB_H)
        face.setStyleSheet(f"background: {BG}; color: {DIM};")
        face.setFont(font)
        if pixmap is not None:
            face.setPixmap(pixmap)
        else:
            face.setText("GIF")
        face.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(face)

    def _paint_border(self, hover: bool = False) -> None:
        edge = FG if (hover or self._selected) else DIM
        self.setStyleSheet(f"QFrame {{ background: {BG}; border: 1px solid {edge}; }}")

    def enterEvent(self, event) -> None:
        self._paint_border(True)
        if self._hint_host is not None:
            self._hint_host._hint_enter(self, self._hint)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._paint_border(False)
        if self._hint_host is not None:
            self._hint_host._hint_leave(self)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
            event.accept()
            return
        super().mousePressEvent(event)


class App(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())

        self.font = pick_font(11)
        self.font_sm = self.font

        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._busy = False
        self._path: Optional[Path] = None
        self._preview_pm: Optional[QPixmap] = None
        self._preview_frames: list[Image.Image] = []
        self._preview_durs: list[int] = []
        self._preview_i = 0
        self._view_zoom = 1.0
        self._view_pan_x = 0.0
        self._view_pan_y = 0.0
        self._pan_drag: tuple[float, float, float, float] | None = None
        self._focusables: list[TermButton] = []
        self._bright = 80
        self._connected = False
        self._gallery: list[GalleryEntry] = []
        self._gallery_pms: list[QPixmap] = []
        self._mouse_hint_src: object | None = None
        self._mouse_hint_text = ""
        self.hint_lbl: QLabel | None = None

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._tick_preview)
        self._state_timer = QTimer(self)
        self._state_timer.setSingleShot(True)
        self._state_timer.timeout.connect(self._flush_state)
        self._drain_timer = QTimer(self)
        self._drain_timer.timeout.connect(self._drain)

        self._build()
        hint = self.sizeHint()
        w = max(hint.width(), MIN_WIN_W)
        h = max(hint.height(), MIN_WIN_H)
        self.setMinimumSize(w, h)
        self.resize(w, h)
        self._bind_keys()
        self._sync_save()
        self._drain_timer.start(80)
        QTimer.singleShot(40, self._restore_source)
        self._refresh_status()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        theme_native_caption(self)
        QTimer.singleShot(0, lambda: theme_native_caption(self))

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.ActivationChange,
            QEvent.Type.WindowStateChange,
        ):
            theme_native_caption(self)
            QTimer.singleShot(0, lambda: theme_native_caption(self))

    def _build(self) -> None:
        shell = QWidget()
        self.setCentralWidget(shell)
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(16, LINE, 16, LINE)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setSpacing(24)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        left = QWidget()
        left.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self._left = QVBoxLayout(left)
        self._left.setContentsMargins(0, 0, 0, 0)
        self._left.setSpacing(LINE)
        self._left.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        right = QWidget()
        right.setFixedWidth(PREVIEW_W)
        self._right = QVBoxLayout(right)
        self._right.setContentsMargins(0, 0, 0, 0)
        self._right.setSpacing(LINE)
        self._right.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self._build_header(outer)
        self._build_status(self._left)
        self._build_crate(self._left)
        self._build_file(self._left)
        self._build_modes(self._left)
        self._build_bright(self._left)
        self._build_progress(self._left)
        self._left.addStretch(1)

        self._build_preview(self._right)
        self._right.addStretch(1)

        body.addWidget(left, 0)
        body.addWidget(right, 0)
        body.addStretch(1)
        outer.addLayout(body, 1)
        self._build_footer(outer)
        self._collect_focus()

    def _mute_mouse(self, widget: QWidget) -> None:
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def _label(self, text: str, font: QFont, color: str = FG, *, wrap: bool = False) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(font)
        lbl.setStyleSheet(f"color: {color}; background: transparent;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if wrap:
            lbl.setWordWrap(True)
            lbl.setMinimumWidth(0)
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return lbl

    def _build_header(self, parent: QVBoxLayout) -> None:
        hdr = DragHandle()
        lay = QVBoxLayout(hdr)
        lay.setContentsMargins(0, 0, 0, LINE * 2)
        lay.setSpacing(0)
        title = self._label(f"{APP_NAME.upper()} V{__version__}", self.font)
        nav = self._label("NAVIGATE WITH MOUSE OR ARROW & ENTER KEYS", self.font_sm)
        for widget in (title, nav):
            self._mute_mouse(widget)
            widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        lay.addWidget(title, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addSpacing(LINE)
        lay.addWidget(nav, 0, Qt.AlignmentFlag.AlignLeft)
        parent.addWidget(hdr, 0, Qt.AlignmentFlag.AlignLeft)

    def _build_status(self, parent: QVBoxLayout) -> None:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._led = StatusLed()
        self.device_lbl = self._label("LOOKING FOR COOLER...", self.font)
        self.refresh_btn = TermButton("REFRESH", self._refresh_status, font=self.font)
        lay.addWidget(self._led, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addSpacing(LED_GAP)
        lay.addWidget(self.device_lbl)
        lay.addSpacing(BTN_GAP)
        lay.addWidget(self.refresh_btn)
        lay.addStretch(1)
        parent.addWidget(row)

        self.liquid_row = DottedRow("LIQUID", self.font, "--")
        self.pump_row = DottedRow("PUMP", self.font, "--")
        self.liquid_row.setContentsMargins(LED_GUTTER, 0, 0, 0)
        self.pump_row.setContentsMargins(LED_GUTTER, 0, 0, 0)
        parent.addWidget(self.liquid_row, 0, Qt.AlignmentFlag.AlignLeft)
        parent.addWidget(self.pump_row, 0, Qt.AlignmentFlag.AlignLeft)

    def _led_dotted(
        self, label: str, value: str, *, top: int = 0, bottom: int = 0
    ) -> tuple[QWidget, StatusLed, DottedRow]:
        host = QWidget()
        lay = QHBoxLayout(host)
        lay.setContentsMargins(0, top, 0, bottom)
        lay.setSpacing(0)
        led = StatusLed()
        row = DottedRow(label, self.font, value)
        lay.addWidget(led, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addSpacing(LED_GAP)
        lay.addWidget(row, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        return host, led, row

    def _build_crate(self, parent: QVBoxLayout) -> None:
        crate_host, self._crate_led, self.crate_row = self._led_dotted(
            "ARMOURY CRATE", "UNKNOWN", top=LINE, bottom=0
        )
        admin_host, self._admin_led, self.admin_row = self._led_dotted(
            "ADMINISTRATOR", "UNKNOWN", top=0, bottom=0
        )
        parent.addWidget(crate_host, 0, Qt.AlignmentFlag.AlignLeft)
        parent.addWidget(admin_host, 0, Qt.AlignmentFlag.AlignLeft)
        btns = QWidget()
        lay = QHBoxLayout(btns)
        lay.setContentsMargins(LED_GUTTER, 0, 0, 0)
        lay.setSpacing(BTN_GAP)
        self.pause_btn = TermButton("PAUSE", lambda: self._run(self._pause_crate), font=self.font)
        self.resume_btn = TermButton("RESUME", lambda: self._run(self._resume_crate), font=self.font)
        self.admin_btn = TermButton("RELAUNCH AS ADMIN", self._relaunch_admin, font=self.font)
        lay.addWidget(self.pause_btn)
        lay.addWidget(self.resume_btn)
        lay.addWidget(self.admin_btn)
        lay.addStretch(1)
        parent.addWidget(btns, 0, Qt.AlignmentFlag.AlignLeft)

    def _build_file(self, parent: QVBoxLayout) -> None:
        self.file_row = DottedRow("SOURCE", self.font, NO_FILE)
        self.file_row.setContentsMargins(LED_GUTTER, LINE, 0, 0)
        parent.addWidget(self.file_row, 0, Qt.AlignmentFlag.AlignLeft)
        btns = QWidget()
        lay = QHBoxLayout(btns)
        lay.setContentsMargins(LED_GUTTER, 0, 0, 0)
        lay.setSpacing(BTN_GAP)
        self.choose_btn = TermButton("CHOOSE GIF OR IMAGE", self._browse, font=self.font)
        self.save_btn = TermButton("SAVE TO COOLER", self._start_save, font=self.font)
        self.wait_badge = QLabel("WAIT")
        self.wait_badge.setFont(self.font)
        self.wait_badge.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.wait_badge.setStyleSheet(
            f"QLabel {{ background: {YELLOW}; color: {INV_FG}; padding: 2px 8px; }}"
        )
        self.wait_badge.hide()
        lay.addWidget(self.choose_btn)
        lay.addWidget(self.save_btn)
        lay.addWidget(self.wait_badge)
        lay.addStretch(1)
        parent.addWidget(btns, 0, Qt.AlignmentFlag.AlignLeft)

    def _build_modes(self, parent: QVBoxLayout) -> None:
        heading = self._label("BUILT-IN MODES", self.font)
        heading.setContentsMargins(LED_GUTTER, LINE, 0, 0)
        parent.addWidget(heading, 0, Qt.AlignmentFlag.AlignLeft)
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(LED_GUTTER, 0, 0, 0)
        lay.setSpacing(BTN_GAP)
        self.clock_btn = TermButton("CLOCK", lambda: self._run(self._clock), font=self.font)
        self.rog_btn = TermButton("ROG ANIMATION", lambda: self._run(self._liquid), font=self.font)
        self.play_btn = TermButton("PLAY SAVED GIF", lambda: self._run(self._play_saved), font=self.font)
        lay.addWidget(self.clock_btn)
        lay.addWidget(self.rog_btn)
        lay.addWidget(self.play_btn)
        lay.addStretch(1)
        parent.addWidget(row, 0, Qt.AlignmentFlag.AlignLeft)

    def _build_bright(self, parent: QVBoxLayout) -> None:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(LED_GUTTER, LINE, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._label("BRIGHTNESS", self.font))
        self._bright_dots = QLabel("")
        self._bright_dots.setFont(self.font)
        self._bright_dots.setStyleSheet(f"color: {DIM}; background: transparent;")
        lay.addWidget(self._bright_dots)
        self.dim_btn = TermButton("<", lambda: self._nudge_bright(-5), font=self.font, padx=0, pady=0)
        self.bright_lbl = self._label(f" {self._bright:3d} % ", self.font)
        self.bright_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.bright_plus = TermButton(">", lambda: self._nudge_bright(5), font=self.font, padx=0, pady=0)
        ch = self.bright_lbl.fontMetrics().horizontalAdvance("0")
        self.dim_btn.setFixedWidth(ch)
        self.bright_plus.setFixedWidth(ch)
        self.bright_lbl.setFixedWidth(self.bright_lbl.fontMetrics().horizontalAdvance(" 100 % "))
        self.apply_btn = TermButton("APPLY", lambda: self._run(self._apply_bright), font=self.font)
        lay.addWidget(self.dim_btn)
        lay.addWidget(self.bright_lbl)
        lay.addWidget(self.bright_plus)
        lay.addSpacing(BTN_GAP)
        lay.addWidget(self.apply_btn)
        lay.addStretch(1)
        parent.addWidget(row, 0, Qt.AlignmentFlag.AlignLeft)
        self._reflow_bright()

    def _build_progress(self, parent: QVBoxLayout) -> None:
        self.progress_row = DottedRow("UPLOAD", self.font, "IDLE")
        self.progress_row.setContentsMargins(LED_GUTTER, LINE, 0, 0)
        parent.addWidget(self.progress_row, 0, Qt.AlignmentFlag.AlignLeft)
        self.bar_lbl = self._label("[" + "-" * 24 + "]", self.font, DIM)
        self.bar_lbl.setContentsMargins(LED_GUTTER, 0, 0, 0)
        parent.addWidget(self.bar_lbl, 0, Qt.AlignmentFlag.AlignLeft)

    def _build_preview(self, parent: QVBoxLayout) -> None:
        heading = self._label("> PREVIEW", self.font)
        heading.setContentsMargins(0, 0, 0, 0)
        parent.addWidget(heading, 0, Qt.AlignmentFlag.AlignLeft)

        frame = QFrame()
        frame.setFixedSize(PREVIEW_W + 2, PREVIEW_H + 2)
        frame.setStyleSheet(f"QFrame {{ background: {BG}; border: 1px solid {FG}; }}")
        flay = QVBoxLayout(frame)
        flay.setContentsMargins(1, 1, 1, 1)
        flay.setSpacing(0)
        self.preview_canvas = PreviewCanvas(self)
        flay.addWidget(self.preview_canvas)
        parent.addWidget(frame, 0, Qt.AlignmentFlag.AlignLeft)

        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.zoom_out_btn = TermButton("<", lambda: self._nudge_zoom(1 / 1.15), font=self.font, padx=6, pady=0)
        self.zoom_lbl = self._label("1.00 X", self.font)
        self.zoom_lbl.setFixedWidth(72)
        self.zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_in_btn = TermButton(">", lambda: self._nudge_zoom(1.15), font=self.font, padx=6, pady=0)
        self.reset_view_btn = TermButton("RESET", self._reset_view, font=self.font)
        lay.addWidget(self.zoom_out_btn)
        lay.addWidget(self.zoom_lbl)
        lay.addWidget(self.zoom_in_btn)
        lay.addSpacing(BTN_GAP)
        lay.addWidget(self.reset_view_btn)
        lay.addStretch(1)
        row.setFixedWidth(PREVIEW_W)
        parent.addWidget(row, 0, Qt.AlignmentFlag.AlignLeft)

        recent = self._label("> RECENT", self.font)
        recent.setContentsMargins(0, LINE, 0, 0)
        parent.addWidget(recent, 0, Qt.AlignmentFlag.AlignLeft)

        self.gallery_host = QWidget()
        self.gallery_host.setFixedWidth(PREVIEW_W)
        self.gallery_row = QHBoxLayout(self.gallery_host)
        self.gallery_row.setContentsMargins(0, 0, 0, 0)
        self.gallery_row.setSpacing(THUMB_GAP)
        self.gallery_row.addStretch(1)
        parent.addWidget(self.gallery_host, 0, Qt.AlignmentFlag.AlignLeft)

        self.gallery_hint = self._label("SAVE TO COOLER TO PIN HERE.", self.font_sm, DIM)
        parent.addWidget(self.gallery_hint, 0, Qt.AlignmentFlag.AlignLeft)
        self._draw_preview_empty()

    def _build_footer(self, parent: QVBoxLayout) -> None:
        foot = QWidget()
        foot.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay = QVBoxLayout(foot)
        lay.setContentsMargins(LED_GUTTER, QFontMetrics(self.font).lineSpacing() * 3, 0, 0)
        lay.setSpacing(LINE)
        self.hint_lbl = self._label("> " + HINT_IDLE, self.font, DIM, wrap=True)
        self.log_lbl = self._label("> READY.", self.font, wrap=True)
        self.log_lbl.setMinimumHeight(self.log_lbl.fontMetrics().height())
        pre = self._label("ALPHA", self.font_sm)
        note = self._label(
            "RUN AS ADMIN AND PAUSE CRATE BEFORE SAVE. CRATE STEALS THE FLASH HANDSHAKE.\n"
            "SAVE WRITES FLASH AND SURVIVES REBOOT. WHEEL ZOOM / DRAG PAN THE PREVIEW CROP.",
            self.font_sm,
            DIM,
            wrap=True,
        )
        lay.addWidget(self.hint_lbl)
        lay.addWidget(self.log_lbl)
        lay.addSpacing(LINE * 2)
        lay.addWidget(pre)
        lay.addWidget(note)
        parent.addWidget(foot)

    def _collect_focus(self) -> None:
        self._focusables = [
            self.refresh_btn,
            self.pause_btn,
            self.resume_btn,
            self.admin_btn,
            self.choose_btn,
            self.save_btn,
            self.clock_btn,
            self.rog_btn,
            self.play_btn,
            self.dim_btn,
            self.bright_plus,
            self.apply_btn,
            self.zoom_out_btn,
            self.zoom_in_btn,
            self.reset_view_btn,
        ]
        hints = {
            self.refresh_btn: "Re-scan for the cooler and update liquid, pump, crate, and admin status.",
            self.pause_btn: "Stop Armoury Crate first. A running Crate steals the flash handshake during Save.",
            self.resume_btn: "Start Armoury Crate services again when you finish saving.",
            self.admin_btn: "Restart this app elevated. Pause needs admin to stop Crate.",
            self.choose_btn: "Pick a GIF or still image to crop in the preview and save to the cooler.",
            self.save_btn: "Write the current crop to cooler flash. Survives reboot. Pause Crate first.",
            self.clock_btn: "Switch the LCD to the built-in clock.",
            self.rog_btn: "Switch the LCD to the built-in ROG animation.",
            self.play_btn: "Play the last GIF already stored on the cooler. Does not upload a new file.",
            self.dim_btn: "Lower brightness 5%. Click Apply to send the new value to the cooler.",
            self.bright_plus: "Raise brightness 5%. Click Apply to send the new value to the cooler.",
            self.apply_btn: "Send the brightness value to the cooler.",
            self.zoom_out_btn: "Zoom the preview crop out.",
            self.zoom_in_btn: "Zoom the preview crop in.",
            self.reset_view_btn: "Reset preview zoom and pan to the default crop.",
        }
        for btn, text in hints.items():
            btn.set_hint(text, self)

    def _hint_enter(self, src: object, text: str) -> None:
        self._mouse_hint_src = src
        self._mouse_hint_text = text
        self._refresh_hint()

    def _hint_leave(self, src: object) -> None:
        if self._mouse_hint_src is src:
            self._mouse_hint_src = None
            self._mouse_hint_text = ""
        self._refresh_hint()

    def _refresh_hint(self) -> None:
        if self.hint_lbl is None:
            return
        text = self._mouse_hint_text
        if not text:
            focused = QApplication.focusWidget()
            text = focused._hint if isinstance(focused, TermButton) else ""
        if text:
            self.hint_lbl.setText("> " + text.upper())
        else:
            self.hint_lbl.setText("> " + HINT_IDLE)

    def _bind_keys(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, activated=lambda: self._move_focus(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, activated=lambda: self._move_focus(1))
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self._move_focus(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self._move_focus(1))
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, activated=self._activate_focused)
        QShortcut(QKeySequence(Qt.Key.Key_Enter), self, activated=self._activate_focused)
        QShortcut(QKeySequence(Qt.Key.Key_F5), self, activated=self._refresh_status)

    def _move_focus(self, delta: int) -> None:
        if not self._focusables:
            return
        current = QApplication.focusWidget()
        if isinstance(current, TermButton) and current in self._focusables:
            idx = self._focusables.index(current)
        else:
            idx = 0 if delta > 0 else -1
        n = len(self._focusables)
        for _ in range(n):
            idx = (idx + delta) % n
            btn = self._focusables[idx]
            if btn.isEnabled():
                btn.setFocus()
                self._refresh_hint()
                return

    def _activate_focused(self) -> None:
        focused = QApplication.focusWidget()
        if isinstance(focused, TermButton) and focused.isEnabled():
            focused.click()

    def _nudge_bright(self, delta: int) -> None:
        self._bright = max(0, min(100, self._bright + delta))
        self.bright_lbl.setText(f" {self._bright:3d} % ")
        self._reflow_bright()

    def _reflow_bright(self) -> None:
        n = ROW_CHARS - len("BRIGHTNESS") - len("< 100 % >")
        self._bright_dots.setText("." * max(3, n))

    def _set_led(self, ok: bool) -> None:
        self._led.set_ok(ok)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for btn in self._focusables:
            if btn is self.refresh_btn or btn is self.save_btn:
                continue
            btn.set_enabled(not busy)
        if busy:
            self.wait_badge.show()
        else:
            self.wait_badge.hide()
        self._sync_save()

    def _has_source(self) -> bool:
        return self._path is not None and self._path.is_file()

    def _sync_save(self) -> None:
        self.save_btn.set_enabled((not self._busy) and self._has_source())

    def _log(self, msg: str) -> None:
        text = str(msg).strip()
        self.log_lbl.setText("> " + (text.upper() if text else "READY."))

    def _set_progress(self, cur: int, total: int) -> None:
        total = max(total, 1)
        frac = max(0.0, min(1.0, cur / total))
        width = 24
        filled = int(round(frac * width))
        bar = "#" * filled + "-" * (width - filled)
        self.bar_lbl.setText(f"[{bar}]")
        self.bar_lbl.setStyleSheet(f"color: {FG if cur else DIM}; background: transparent;")
        if cur <= 0:
            self.progress_row.set_value("IDLE", dim=True)
        elif cur >= total:
            self.progress_row.set_value("DONE")
        else:
            self.progress_row.set_value(f"{cur}/{total}")

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "device":
                    self._apply_device(payload)
                elif kind == "crate":
                    running, admin = payload
                    self._crate_led.set_ok(not running)
                    self.crate_row.set_value("STOPPED" if not running else "RUNNING", alert=running)
                    self._admin_led.set_ok(bool(admin))
                    self.admin_row.set_value("YES" if admin else "NEED ADMIN", alert=not admin)
                elif kind == "progress":
                    cur, total = payload
                    self._set_progress(cur, total)
                elif kind == "busy":
                    self._set_busy(bool(payload))
                elif kind == "error":
                    self._log(str(payload))
                    msg = str(payload)
                    QTimer.singleShot(0, lambda m=msg: self._dialog("ERROR", m))
                elif kind == "info":
                    msg = str(payload)
                    QTimer.singleShot(0, lambda m=msg: self._dialog("OK", m))
                elif kind == "gallery":
                    self._remember_upload()
        except queue.Empty:
            pass

    def _apply_device(self, payload) -> None:
        if not payload:
            self._connected = False
            self._set_led(False)
            self.device_lbl.setText("NO RYUJIN III FOUND")
            self.liquid_row.set_value("--", dim=True)
            self.pump_row.set_value("--", dim=True)
            return
        self._connected = True
        self._set_led(True)
        product = _short_product(str(payload.get("product") or "RYUJIN III"))
        fw = str(payload.get("firmware") or "").upper()
        self.device_lbl.setText(f"{product} - {fw}" if fw else product)
        self.liquid_row.set_value(f"{payload['liquid_c']:.1f} C")
        self.pump_row.set_value(f"{payload['pump_rpm']} RPM")

    def _dialog(self, title: str, body: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(APP_NAME)
        dlg.setModal(True)
        dlg.setMinimumWidth(380)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 14, 20, 14)
        heading = QLabel(title.upper())
        heading.setFont(self.font)
        if title == "ERROR":
            color = RED
        elif title == "WAIT":
            color = YELLOW
        else:
            color = GREEN
        heading.setStyleSheet(f"color: {color}; background: transparent;")
        text = QLabel(body.upper())
        text.setFont(self.font_sm)
        text.setWordWrap(True)
        text.setMaximumWidth(480)
        text.setStyleSheet(f"color: {FG}; background: transparent;")
        ok = TermButton("OK", dlg.accept, font=self.font, inverted=True)
        ok.setDefault(True)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(ok)
        lay.addWidget(heading)
        lay.addSpacing(10)
        lay.addWidget(text)
        lay.addSpacing(16)
        lay.addLayout(row)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), dlg, activated=dlg.reject)
        QShortcut(QKeySequence(Qt.Key.Key_Return), dlg, activated=dlg.accept)
        QShortcut(QKeySequence(Qt.Key.Key_Enter), dlg, activated=dlg.accept)
        dlg.createWinId()
        theme_native_caption(dlg)
        dlg.exec()

    def _emit(self, kind: str, payload) -> None:
        self._queue.put((kind, payload))

    def _run(self, fn: Callable[[], None]) -> None:
        if self._busy:
            return
        self._set_busy(True)
        threading.Thread(target=self._guarded, args=(fn,), name="killryujin-usb").start()

    def _guarded(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except FlashSlotStuck as exc:
            self._emit("error", str(exc))
        except (RyujinError, OSError) as exc:
            self._emit("error", str(exc))
        except Exception as exc:
            self._emit("error", f"unexpected error: {exc}")
        finally:
            self._emit("busy", False)

    def _refresh_status(self) -> None:
        self._run(self._update_status)

    def _update_status(self, announce: bool = True) -> None:
        found = enumerate_coolers()
        running = crate.is_running()
        self._emit("crate", (running, crate.is_admin()))
        if not found:
            self._emit("device", None)
            self._emit("log", "No Ryujin III found. Plug the AIO USB header into the motherboard.")
            return
        with Ryujin() as dev:
            st = dev.status()
        self._emit("device", st)
        if announce:
            self._emit("log", "Cooler connected.")

    def _relaunch_admin(self) -> None:
        if crate.is_admin():
            self._dialog("OK", "Already running as Administrator.")
            return
        if crate.relaunch_as_admin():
            QTimer.singleShot(300, self.close)
            return
        self._dialog(
            "ERROR",
            "Elevation was cancelled. Right-click this app and choose Run as administrator.",
        )

    def _crate_log(self, notes: list[str], *, paused: bool) -> None:
        blob = " ".join(notes).lower()
        if "administrator" in blob:
            self._emit("log", "Need Administrator to fully pause Armoury Crate.")
            return
        if paused:
            self._emit("log", "Armoury Crate paused.")
        else:
            self._emit("log", "Armoury Crate resumed.")

    def _pause_crate(self) -> None:
        notes = crate.pause()
        self._crate_log(notes, paused=True)
        self._update_status(announce=False)

    def _resume_crate(self) -> None:
        notes = crate.resume()
        self._crate_log(notes, paused=False)
        self._update_status(announce=False)

    def _browse(self) -> None:
        start = str(self._path.parent) if self._path and self._path.parent.is_dir() else ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "CHOOSE GIF OR IMAGE",
            start,
            "Images (*.gif *.png *.jpg *.jpeg *.webp *.bmp);;GIF (*.gif);;All files (*.*)",
        )
        if not path:
            return
        self._path = Path(path)
        self.file_row.set_value(self._path.name)
        try:
            self._load_preview(self._path)
            extra = f"{self._preview_frames[0].size[0]}x{self._preview_frames[0].size[1]}"
            if len(self._preview_frames) > 1:
                extra += f"  {len(self._preview_frames)} FRAMES"
            self._log(f"Loaded {self._path.name} ({extra})")
            self._save_state()
            self._sync_save()
        except Exception as exc:
            self._path = None
            self.file_row.set_value(NO_FILE)
            self._stop_preview_anim()
            self._preview_frames = []
            self._draw_preview_empty()
            self._sync_save()
            self._dialog("ERROR", f"Failed to read the file:\n{exc}")

    def _load_preview(
        self,
        path: Path,
        *,
        zoom: float | None = None,
        pan_x: float = 0.0,
        pan_y: float = 0.0,
    ) -> None:
        self._stop_preview_anim()
        if path.suffix.lower() == ".gif":
            frames, durs = composite_gif_rgb(path)
        else:
            with Image.open(path) as img:
                frames, durs = [img.convert("RGB").copy()], [80]
        self._preview_frames = frames
        self._preview_durs = durs
        self._preview_i = 0
        if zoom is None:
            self._reset_view(redraw=False)
        else:
            self._view_zoom = max(self._min_zoom(), min(VIEW_ZOOM_MAX, float(zoom)))
            self._view_pan_x = float(pan_x)
            self._view_pan_y = float(pan_y)
            self._clamp_view()
        self._render_preview()
        self._start_preview_anim()

    def _save_state(self) -> None:
        payload = {
            "source": str(self._path) if self._path else "",
            "zoom": self._view_zoom,
            "pan_x": self._view_pan_x,
            "pan_y": self._view_pan_y,
            "gallery": self._gallery,
            "gallery_newest_first": True,
        }
        try:
            dest = _state_file()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def _schedule_save_state(self) -> None:
        self._state_timer.start(400)

    def _flush_state(self) -> None:
        self._save_state()

    def _restore_source(self) -> None:
        try:
            data = json.loads(_state_file().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            self._redraw_gallery()
            self._sync_save()
            return
        raw = data.get("gallery") if isinstance(data, dict) else None
        migrated = not bool(data.get("gallery_newest_first"))
        self._gallery = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item.get("path"):
                    self._gallery.append(
                        {
                            "path": str(item["path"]),
                            "name": str(item.get("name") or Path(str(item["path"])).name),
                            "zoom": float(item.get("zoom") or 1.0),
                            "pan_x": float(item.get("pan_x") or 0.0),
                            "pan_y": float(item.get("pan_y") or 0.0),
                        }
                    )
            self._gallery = self._gallery[:GALLERY_N]
            if migrated:
                self._gallery.reverse()
        self._redraw_gallery()
        src = Path(str(data.get("source") or ""))
        if not src.is_file():
            if src.as_posix() not in {"", "."}:
                self._log("Last source is gone. Choose a GIF or image.")
            if migrated:
                self._save_state()
            self._sync_save()
            return
        self._path = src
        self.file_row.set_value(src.name)
        try:
            self._load_preview(
                src,
                zoom=float(data.get("zoom") or 1.0),
                pan_x=float(data.get("pan_x") or 0.0),
                pan_y=float(data.get("pan_y") or 0.0),
            )
        except Exception:
            self._path = None
            self.file_row.set_value(NO_FILE)
            self._draw_preview_empty()
        self._redraw_gallery()
        if migrated:
            self._save_state()
        self._sync_save()

    def _remember_upload(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        path = str(self._path.resolve())
        key = _path_key(path)
        entry: GalleryEntry = {
            "path": path,
            "name": self._path.name,
            "zoom": self._view_zoom,
            "pan_x": self._view_pan_x,
            "pan_y": self._view_pan_y,
        }
        self._write_thumb(entry)
        rest = [e for e in self._gallery if _path_key(e.get("path") or "") != key]
        gallery = [entry, *rest]
        dropped = gallery[GALLERY_N:]
        self._gallery = gallery[:GALLERY_N]
        for old in dropped:
            try:
                _thumb_file(str(old.get("path") or "")).unlink(missing_ok=True)
            except OSError:
                pass
        self._save_state()
        self._redraw_gallery()
        if dropped:
            self._log(f"Recent: {self._path.name}  dropped {dropped[-1].get('name')}")
        else:
            self._log(f"Recent: {self._path.name}")

    def _thumb_image(self, entry: GalleryEntry) -> Image.Image | None:
        src = Path(str(entry.get("path") or ""))
        zoom = float(entry.get("zoom") or 1.0)
        pan_x = float(entry.get("pan_x") or 0.0)
        pan_y = float(entry.get("pan_y") or 0.0)
        frame = None
        if (
            self._preview_frames
            and self._path is not None
            and _path_key(self._path) == _path_key(src)
        ):
            frame = self._preview_frames[0]
        if frame is None and src.is_file():
            try:
                if src.suffix.lower() == ".gif":
                    frames, _ = composite_gif_rgb(src, max_frames=1)
                    frame = frames[0]
                else:
                    with Image.open(src) as im:
                        frame = im.convert("RGB")
            except Exception:
                return None
        if frame is None:
            return None
        viewed = view_rgb(frame, PREVIEW_W, PREVIEW_H, zoom=zoom, pan_x=pan_x, pan_y=pan_y)
        return viewed.resize((THUMB_W, THUMB_H), Image.Resampling.LANCZOS)

    def _write_thumb(self, entry: GalleryEntry) -> None:
        img = self._thumb_image(entry)
        if img is None:
            return
        try:
            _thumbs_dir().mkdir(parents=True, exist_ok=True)
            img.save(_thumb_file(str(entry.get("path") or "")), format="PNG")
        except OSError:
            pass

    def _gallery_pixmap(self, entry: GalleryEntry) -> QPixmap | None:
        img = None
        cache = _thumb_file(str(entry.get("path") or ""))
        if cache.is_file():
            try:
                with Image.open(io.BytesIO(cache.read_bytes())) as im:
                    img = im.convert("RGB")
                    if img.size != (THUMB_W, THUMB_H):
                        img = img.resize((THUMB_W, THUMB_H), Image.Resampling.LANCZOS)
            except OSError:
                img = None
        if img is None:
            img = self._thumb_image(entry)
            if img is not None:
                try:
                    _thumbs_dir().mkdir(parents=True, exist_ok=True)
                    img.save(cache, format="PNG")
                except OSError:
                    pass
        if img is None:
            return None
        return pil_to_qpixmap(img)

    def _clear_layout(self, layout: QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _redraw_gallery(self) -> None:
        self._clear_layout(self.gallery_row)
        self._gallery_pms = []
        if not self._gallery:
            self.gallery_hint.show()
            self.gallery_row.addStretch(1)
            return
        self.gallery_hint.hide()
        current = _path_key(self._path) if self._path else ""
        for i, entry in enumerate(self._gallery):
            selected = current != "" and _path_key(entry.get("path") or "") == current
            pixmap = self._gallery_pixmap(entry)
            if pixmap is not None:
                self._gallery_pms.append(pixmap)
            cell = GalleryCell(
                selected,
                lambda idx=i: self._select_gallery(idx),
                pixmap,
                self.font_sm,
                hint_host=self,
            )
            self.gallery_row.addWidget(cell)
        self.gallery_row.addStretch(1)

    def _select_gallery(self, index: int) -> None:
        if index < 0 or index >= len(self._gallery):
            return
        entry = self._gallery[index]
        src = Path(str(entry.get("path") or ""))
        if not src.is_file():
            self._log("The file is gone.")
            self._gallery.pop(index)
            self._save_state()
            self._redraw_gallery()
            self._sync_save()
            return
        self._path = src
        self.file_row.set_value(src.name)
        try:
            self._load_preview(
                src,
                zoom=float(entry.get("zoom") or 1.0),
                pan_x=float(entry.get("pan_x") or 0.0),
                pan_y=float(entry.get("pan_y") or 0.0),
            )
        except Exception as exc:
            self._dialog("ERROR", f"Failed to read the file:\n{exc}")
            self._sync_save()
            return
        self._save_state()
        self._redraw_gallery()
        self._log(f"Ready: {src.name}")
        self._sync_save()

    def _draw_preview_empty(self) -> None:
        self._preview_pm = None
        self.preview_canvas.clear()
        self.preview_canvas.setText("NO FILE")
        self.zoom_lbl.setText("1.00 X")

    def _src_size(self) -> tuple[int, int] | None:
        if not self._preview_frames:
            return None
        return self._preview_frames[0].size

    def _min_zoom(self) -> float:
        size = self._src_size()
        if not size:
            return 1.0
        sw, sh = size
        cover = max(PREVIEW_W / sw, PREVIEW_H / sh)
        contain = min(PREVIEW_W / sw, PREVIEW_H / sh)
        return max(0.15, 0.5 * contain / cover)

    def _clamp_view(self) -> None:
        size = self._src_size()
        if not size:
            return
        sw, sh = size
        cover = max(PREVIEW_W / sw, PREVIEW_H / sh)
        scale = cover * self._view_zoom
        max_x = abs(sw * scale - PREVIEW_W) / (2 * scale)
        max_y = abs(sh * scale - PREVIEW_H) / (2 * scale)
        self._view_pan_x = max(-max_x, min(max_x, self._view_pan_x))
        self._view_pan_y = max(-max_y, min(max_y, self._view_pan_y))

    def _reset_view(self, redraw: bool = True) -> None:
        self._view_zoom = 1.0
        self._view_pan_x = 0.0
        self._view_pan_y = 0.0
        if redraw:
            self._render_preview()
        self._schedule_save_state()

    def _nudge_zoom(self, factor: float) -> None:
        self._zoom_at(factor, PREVIEW_W / 2, PREVIEW_H / 2)

    def _zoom_at(self, factor: float, canvas_x: float, canvas_y: float) -> None:
        size = self._src_size()
        if not size:
            return
        sw, sh = size
        cover = max(PREVIEW_W / sw, PREVIEW_H / sh)
        old_z = self._view_zoom
        new_z = max(self._min_zoom(), min(VIEW_ZOOM_MAX, old_z * factor))
        if abs(new_z - old_z) < 1e-6:
            return
        old_s = cover * old_z
        new_s = cover * new_z
        src_x = ((sw * old_s - PREVIEW_W) / 2 + self._view_pan_x * old_s + canvas_x) / old_s
        src_y = ((sh * old_s - PREVIEW_H) / 2 + self._view_pan_y * old_s + canvas_y) / old_s
        self._view_zoom = new_z
        self._view_pan_x = src_x - canvas_x / new_s - (sw - PREVIEW_W / new_s) / 2
        self._view_pan_y = src_y - canvas_y / new_s - (sh - PREVIEW_H / new_s) / 2
        self._clamp_view()
        self._render_preview()
        self._schedule_save_state()

    def _render_preview(self) -> None:
        if not self._preview_frames:
            self._draw_preview_empty()
            return
        frame = self._preview_frames[self._preview_i]
        viewed = view_rgb(
            frame,
            PREVIEW_W,
            PREVIEW_H,
            zoom=self._view_zoom,
            pan_x=self._view_pan_x,
            pan_y=self._view_pan_y,
        )
        self._preview_pm = pil_to_qpixmap(viewed)
        self.preview_canvas.setText("")
        self.preview_canvas.setPixmap(self._preview_pm)
        self.zoom_lbl.setText(f"{self._view_zoom:.2f} X")

    def _start_preview_anim(self) -> None:
        self._stop_preview_anim()
        if len(self._preview_frames) < 2:
            return
        delay = max(40, self._preview_durs[self._preview_i])
        self._preview_timer.start(delay)

    def _tick_preview(self) -> None:
        if len(self._preview_frames) < 2:
            return
        self._preview_i = (self._preview_i + 1) % len(self._preview_frames)
        self._render_preview()
        delay = max(40, self._preview_durs[self._preview_i])
        self._preview_timer.start(delay)

    def _stop_preview_anim(self) -> None:
        self._preview_timer.stop()

    def _on_preview_wheel(self, event: QWheelEvent) -> None:
        if not self._preview_frames:
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        pos = event.position()
        self._zoom_at(factor, float(pos.x()), float(pos.y()))

    def _preview_press(self, pos: QPointF) -> None:
        if not self._preview_frames:
            return
        self._pan_drag = (pos.x(), pos.y(), self._view_pan_x, self._view_pan_y)

    def _preview_move(self, pos: QPointF) -> None:
        if not self._pan_drag or not self._preview_frames:
            return
        x0, y0, pan0x, pan0y = self._pan_drag
        size = self._src_size()
        if not size:
            return
        sw, sh = size
        cover = max(PREVIEW_W / sw, PREVIEW_H / sh)
        scale = cover * self._view_zoom
        self._view_pan_x = pan0x - (pos.x() - x0) / scale
        self._view_pan_y = pan0y - (pos.y() - y0) / scale
        self._clamp_view()
        self._render_preview()

    def _preview_release(self) -> None:
        self._pan_drag = None
        self._schedule_save_state()

    def _require_file(self) -> Path:
        if self._path is None or not self._path.is_file():
            raise RyujinError("Choose a GIF or image first.")
        return self._path

    def _start_save(self) -> None:
        if self._path is None or not self._path.is_file():
            self._log("Choose a GIF or image first.")
            return
        self._run(self._save)

    def _save(self) -> None:
        path = self._require_file()
        animation = path.suffix.lower() == ".gif"
        if crate.is_running():
            notes = crate.pause()
            self._crate_log(notes, paused=True)
        if crate.is_running():
            if crate.is_admin():
                raise RyujinError(
                    "Armoury Crate is still running, so Save will fail mid-GIF. "
                    "Use Pause Crate, then retry."
                )
            raise RyujinError(
                "Armoury Crate is still running and this app is not Administrator, "
                "so Pause Crate cannot stop Crate. Crate steals the flash handshake "
                "and Save fails mid-GIF. Click Relaunch as Admin, Pause Crate until "
                "the status says STOPPED, then Save. If Save fails right after, "
                "power-cycle: PSU off, hold the case power button 30 seconds."
            )
        self._emit("log", "Encoding and writing to flash. Leave the cooler alone until this finishes.")
        self._emit("progress", (0, 1))

        def progress(cur: int, total: int) -> None:
            self._emit("progress", (cur, total))
            if cur == total or cur % 16 == 0:
                self._emit("log", f"Uploading {cur}/{total}...")

        with Ryujin() as dev:
            result = dev.persist(
                path,
                animation=animation,
                progress=progress,
                zoom=self._view_zoom,
                pan_x=self._view_pan_x,
                pan_y=self._view_pan_y,
            )
        self._emit("progress", (result["chunks"], result["chunks"]))
        self._emit(
            "info",
            "Saved to the cooler. The file stays after reboot.\n"
            f"{result['bytes']} bytes in slot {result['slot']}.",
        )
        self._emit("log", "Saved to cooler flash.")
        self._emit("gallery", True)

    def _clock(self) -> None:
        with Ryujin() as dev:
            dev.lcd_clock(True)
        self._emit("log", "Clock mode.")

    def _liquid(self) -> None:
        with Ryujin() as dev:
            dev.lcd_liquid()
        self._emit("log", "Built-in ROG animation.")

    def _play_saved(self) -> None:
        with Ryujin() as dev:
            dev.lcd_play_saved(3, True)
        self._emit("log", "Playing last saved GIF from flash.")

    def _apply_bright(self) -> None:
        with Ryujin() as dev:
            dev.lcd_brightness(self._bright)
        self._emit("log", f"Brightness {self._bright}%.")

    def closeEvent(self, event) -> None:
        if self._busy:
            event.ignore()
            self._log("Wait. Flash upload is still running.")
            self._dialog(
                "WAIT",
                "An upload is in progress. Closing now wedges the LCD until a power cycle.",
            )
            return
        self._stop_preview_anim()
        self._state_timer.stop()
        self._drain_timer.stop()
        self._save_state()
        event.accept()


def main() -> None:
    if sys.platform == "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "windows:darkmode=2")
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("killryujin.app")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    configure_qt(app)
    win = App()
    win.show()
    theme_native_caption(win)
    raise SystemExit(app.exec())
