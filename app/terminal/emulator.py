from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal, QRect
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPen, QBrush, QMouseEvent, QClipboard, QFontMetrics, QCursor
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import pyte
except Exception:  # pragma: no cover
    pyte = None


class TerminalWidget(QWidget):
    """A real terminal viewport: pyte screen state + direct QPainter rendering."""

    input_bytes = Signal(bytes)
    resize_requested = Signal(int, int)

    ANSI = {
        "black": "#0b0f14", "red": "#ff5f56", "green": "#5cff8a", "yellow": "#ffd866",
        "blue": "#5aa9ff", "magenta": "#d68cff", "cyan": "#52e8ff", "white": "#e6edf3",
        "brightblack": "#66727e", "brightred": "#ff6b6b", "brightgreen": "#69f58f", "brightyellow": "#ffe58a",
        "brightblue": "#78b9ff", "brightmagenta": "#e7a8ff", "brightcyan": "#82f1ff", "brightwhite": "#ffffff",
        "default": "#e6edf3",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        if pyte:
            # HistoryScreen keeps scrollback and is less destructive on resize
            try:
                self._screen = pyte.HistoryScreen(120, 36, history=5000)
            except Exception:
                self._screen = pyte.Screen(120, 36)
            self._stream = pyte.Stream(self._screen)
        else:
            self._screen = None
            self._stream = None
        self._font = QFont("Cascadia Mono", 10)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._font.setFixedPitch(True)
        self.setFont(self._font)
        self._cell_w = 9
        self._cell_h = 18
        self._blink_on = True
        self._selection_start: tuple[int, int] | None = None
        self._selection_end: tuple[int, int] | None = None
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(530)
        self._cursor_timer.timeout.connect(self._blink_cursor)
        self._cursor_timer.start()
        self._recompute_metrics()

    def focusNextPrevChild(self, next: bool) -> bool:
        return False

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._blink_on = True
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._blink_on = True
        self.update()

    def _blink_cursor(self):
        if self._screen:
            self._blink_on = not self._blink_on
        else:
            self._blink_on = False
        self.update()

    def _recompute_metrics(self):
        fm = QFontMetrics(self._font)
        self._cell_w = max(1, fm.horizontalAdvance("M"))
        self._cell_h = max(1, fm.lineSpacing())

    def _grid_size(self):
        return max(20, self.width() // self._cell_w), max(5, self.height() // self._cell_h)

    def resizeEvent(self, event):
        self._recompute_metrics()
        cols, rows = self._grid_size()
        prev = getattr(self, "_last_grid", None)
        if prev != (cols, rows):
            self._last_grid = (cols, rows)
            if self._screen and hasattr(self._screen, "resize"):
                try:
                    self._screen.resize(rows, cols)
                except Exception:
                    pass
            # Debounce PTY resize — avoid a task on every splitter pixel
            self._schedule_resize_emit(cols, rows)
        self.update()
        super().resizeEvent(event)

    def _schedule_resize_emit(self, cols: int, rows: int):
        self._pending_resize = (cols, rows)
        timer = getattr(self, "_resize_emit_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(120)
            timer.timeout.connect(self._flush_resize_emit)
            self._resize_emit_timer = timer
        timer.start()

    def _flush_resize_emit(self):
        pending = getattr(self, "_pending_resize", None)
        if not pending:
            return
        cols, rows = pending
        last_sent = getattr(self, "_last_sent_resize", None)
        if last_sent == (cols, rows):
            return
        self._last_sent_resize = (cols, rows)
        self.resize_requested.emit(cols, rows)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#080d13"))
        painter.setFont(self._font)
        if not self._screen:
            painter.setPen(QColor("#9aa6b5"))
            painter.drawText(14, 24, "pyte is not installed")
            return

        lines = self._screen.buffer
        cursor = self._screen.cursor
        for y in range(self._screen.lines):
            row = lines[y]
            for x in range(self._screen.columns):
                char = row[x]
                value = self._cell_data(char)
                fg = self._color(char.fg)
                bg = self._color(char.bg)
                px, py = x * self._cell_w, y * self._cell_h
                if bg:
                    painter.fillRect(QRect(px, py, self._cell_w, self._cell_h), QColor(bg))
                selected = self._is_selected(x, y)
                if selected:
                    painter.fillRect(QRect(px, py, self._cell_w, self._cell_h), QColor("#245b91"))
                painter.setPen(QColor(fg or self.ANSI["default"]))
                font = QFont(self._font)
                if getattr(char, "bold", False):
                    font.setBold(True)
                painter.setFont(font)
                painter.drawText(px, py + self._cell_h - 3, value)
                if getattr(char, "underline", False):
                    painter.drawLine(px, py + self._cell_h - 1, px + self._cell_w, py + self._cell_h - 1)

        if cursor and self._blink_on:
            x = max(0, min(cursor.x, self._screen.columns - 1))
            y = max(0, min(cursor.y, self._screen.lines - 1))
            # High contrast block/outline cursor, always visible.
            rect = QRect(x * self._cell_w, y * self._cell_h, max(2, self._cell_w), max(2, self._cell_h))
            painter.fillRect(rect, QColor(240, 246, 252, 210))
            value = self._cell_data(self._screen.buffer[y][x])
            painter.setPen(QColor("#0a0f14"))
            painter.setFont(self._font)
            painter.drawText(rect.left(), rect.top() + self._cell_h - 3, value)

        painter.end()

    def _cell_data(self, cell: Any) -> str:
        value = getattr(cell, "data", None)
        if isinstance(value, str):
            return value[:1] or " "
        if isinstance(cell, int):
            try:
                ch = chr(cell)
                return ch if ch.isprintable() else " "
            except (TypeError, ValueError):
                return " "
        return " "

    def _color(self, value: Any) -> str | None:
        if isinstance(value, str):
            if value in ("default", "none"):
                return None
            return self.ANSI.get(value, value if value.startswith("#") else None)
        if isinstance(value, int):
            return self._ansi_256(value)
        return None

    @staticmethod
    def _ansi_256(value: int) -> str | None:
        if value < 16:
            palette = [
                "#0b0f14", "#ff5f56", "#5cff8a", "#ffd866", "#5aa9ff", "#d68cff", "#52e8ff", "#e6edf3",
                "#66727e", "#ff6b6b", "#69f58f", "#ffe58a", "#78b9ff", "#e7a8ff", "#82f1ff", "#ffffff",
            ]
            return palette[value]
        if value <= 231:
            v = value - 16
            r, g, b = v // 36, (v % 36) // 6, v % 6
            conv = lambda n: 0 if n == 0 else 55 + n * 40
            return f"#{conv(r):02x}{conv(g):02x}{conv(b):02x}"
        gray = 8 + (value - 232) * 10
        return f"#{gray:02x}{gray:02x}{gray:02x}"

    def _is_selected(self, x: int, y: int) -> bool:
        if not self._selection_start or not self._selection_end:
            return False
        a = self._selection_start[1] * self._screen.columns + self._selection_start[0]
        b = self._selection_end[1] * self._screen.columns + self._selection_end[0]
        lo, hi = sorted((a, b))
        cur = y * self._screen.columns + x
        return lo <= cur <= hi

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            pos = event.position().toPoint()
            self._selection_start = (max(0, pos.x() // self._cell_w), max(0, pos.y() // self._cell_h))
            self._selection_end = self._selection_start
            self.update()
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            menu = self._context_menu()
            menu.exec(self.mapToGlobal(event.position().toPoint()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.MouseButton.LeftButton and self._selection_start:
            pos = event.position().toPoint()
            self._selection_end = (max(0, pos.x() // self._cell_w), max(0, pos.y() // self._cell_h))
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _selected_text(self) -> str:
        if not self._screen or not self._selection_start or not self._selection_end:
            return ""
        a = self._selection_start[1] * self._screen.columns + self._selection_start[0]
        b = self._selection_end[1] * self._screen.columns + self._selection_end[0]
        lo, hi = sorted((a, b))
        lines: list[str] = []
        for idx in range(lo, hi + 1):
            y, x = divmod(idx, self._screen.columns)
            lines.append(self._cell_data(self._screen.buffer[y][x]))
        return "".join(lines).rstrip()

    def _context_menu(self):
        menu = QMenu(self)
        menu.setObjectName("terminalContextMenu")
        copy_action = menu.addAction("Copy selection")
        copy_all_action = menu.addAction("Copy all")
        paste_action = menu.addAction("Paste")
        menu.addSeparator()
        select_all_action = menu.addAction("Select all")
        clear_action = menu.addAction("Clear terminal")
        menu.addSeparator()
        reset_action = menu.addAction("Reset terminal")
        action = menu.exec(QCursor.pos())
        if action is copy_action:
            QApplication.clipboard().setText(self._selected_text())
        elif action is copy_all_action:
            QApplication.clipboard().setText(self.toPlainText())
        elif action is paste_action:
            self._emit_paste()
        elif action is select_all_action:
            self._selection_start = (0, 0)
            rows = max(1, self._screen.lines - 1) if self._screen else 1
            cols = max(1, self._screen.columns - 1) if self._screen else 1
            self._selection_end = (cols, rows)
            self.update()
        elif action is clear_action:
            self.clear()
        elif action is reset_action:
            if self._stream and self._screen:
                self._screen.reset()
                self._stream = pyte.Stream(self._screen)
            self._selection_start = self._selection_end = None
            self._blink_on = True
            self.update()
            self.setFocus(Qt.FocusReason.MouseFocusReason)

    def _emit_paste(self):
        text = QApplication.clipboard().text()
        if text:
            self.input_bytes.emit(text.replace("\r\n", "\n").replace("\n", "\r").encode("utf-8"))

    def set_font(self, font) -> None:
        from PySide6.QtGui import QFont
        if isinstance(font, QFont):
            self._font = QFont(font)
        else:
            self._font = QFont(str(font), 13)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._font.setFixedPitch(True)
        self.setFont(self._font)
        self.update()

    def feed(self, data: bytes):
        if self._stream:
            self._stream.feed(data.decode("utf-8", "replace"))
        self._blink_on = True
        self.update()

    def show_message(self, text: str):
        self.feed(text.encode("utf-8"))

    def clear(self, *, remote: bool = True):
        """Clear the terminal.

        Prefer sending Form-Feed (Ctrl+L) to the remote shell so it clears the
        screen *and* redraws the prompt. Local-only clear is a fallback.
        """
        self._selection_start = self._selection_end = None
        self._blink_on = True
        if remote:
            self.input_bytes.emit(b"\x0c")
            self.update()
            return
        if self._screen is not None:
            self._screen.reset()
        if self._stream is not None:
            self._stream.feed("[H[2J[3J")
        self.update()

    def toPlainText(self) -> str:
        if not self._screen:
            return ""
        display = getattr(self._screen, "display", None)
        if isinstance(display, list):
            return "\n".join(display)
        return "\n".join("".join(self._cell_data(c) for c in self._screen.buffer[y]) for y in range(self._screen.lines))

    def _update_cursor(self):
        # Compatibility hook used by the main window; cursor is painted in paintEvent.
        self._blink_on = True
        self.update()

    def copy(self):
        text = self._selected_text() or self.toPlainText()
        QApplication.clipboard().setText(text)

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        mods = event.modifiers()
        # Qt.Key.Key_Tab: b"\t" — Tab belongs to the remote PTY.
        if key == Qt.Key.Key_Tab:
            self.input_bytes.emit(b"\x1b[Z" if mods & Qt.KeyboardModifier.ShiftModifier else b"\t")
            event.accept(); return
        # Qt.Key.Key_Backtab: b"\x1b[Z" — Shift+Tab / Backtab.
        if key == Qt.Key.Key_Backtab:
            self.input_bytes.emit(b"\x1b[Z"); event.accept(); return
        if mods & Qt.KeyboardModifier.ControlModifier and mods & Qt.KeyboardModifier.ShiftModifier:
            if key == Qt.Key.Key_C:
                self.copy(); event.accept(); return
            if key == Qt.Key.Key_V:
                self._emit_paste(); event.accept(); return
        if mods & Qt.KeyboardModifier.ControlModifier:
            # Let Ctrl+` bubble to the main-window shortcut (toggle terminal)
            if key in (Qt.Key.Key_QuoteLeft, Qt.Key.Key_AsciiTilde):
                event.ignore()
                return
            mapping = {
                Qt.Key.Key_C: b"\x03", Qt.Key.Key_D: b"\x04", Qt.Key.Key_Z: b"\x1a", Qt.Key.Key_L: b"\x0c",
                Qt.Key.Key_A: b"\x01", Qt.Key.Key_E: b"\x05", Qt.Key.Key_K: b"\x0b", Qt.Key.Key_U: b"\x15",
                Qt.Key.Key_W: b"\x17",
            }
            if key in mapping:
                self.input_bytes.emit(mapping[key]); event.accept(); return
        arrows = {
            Qt.Key.Key_Left: b"\x1b[D", Qt.Key.Key_Right: b"\x1b[C", Qt.Key.Key_Up: b"\x1b[A", Qt.Key.Key_Down: b"\x1b[B",
            Qt.Key.Key_Home: b"\x1b[H", Qt.Key.Key_End: b"\x1b[F", Qt.Key.Key_Delete: b"\x1b[3~",
            Qt.Key.Key_PageUp: b"\x1b[5~", Qt.Key.Key_PageDown: b"\x1b[6~", Qt.Key.Key_Insert: b"\x1b[2~",
            Qt.Key.Key_Escape: b"\x1b", Qt.Key.Key_Backspace: b"\x7f", Qt.Key.Key_Return: b"\r", Qt.Key.Key_Enter: b"\r",
        }
        if key in arrows:
            self.input_bytes.emit(arrows[key]); event.accept(); return
        text = event.text()
        if text:
            self.input_bytes.emit(text.encode("utf-8")); event.accept(); return
        super().keyPressEvent(event)

class _TerminalCursor:
    """Compatibility marker for older terminal tests; cursor is now painted by TerminalWidget."""
    pass
