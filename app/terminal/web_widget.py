from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QScrollBar

WEB_DIR = Path(__file__).resolve().parent / "web"
HTML_PATH = WEB_DIR / "terminal.html"

# Default terminal chrome colors (match terminal.html xterm theme)
_BG_DARK = "#080d13"
_BG_LIGHT = "#f4f6f9"

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtGui import QColor, QPalette
    HAS_WEBENGINE = True
except Exception:  # pragma: no cover
    HAS_WEBENGINE = False
    QWebEngineView = None  # type: ignore


class _Bridge(QObject):
    """JS ↔ Python bridge for xterm.js."""

    input_received = Signal(str)
    resize_received = Signal(int, int)
    page_ready = Signal()
    find_requested = Signal()
    cwd_changed = Signal(str)
    magic_command = Signal(str)
    command_submitted = Signal(str)
    scroll_changed = Signal(int, int, int)

    def __init__(self):
        super().__init__()
        self.completion_provider = None

    @Slot(str, str, str, str, result=str)
    def predict_command(self, prefix: str, cwd: str, shell: str, server_id: str) -> str:
        provider = self.completion_provider
        if provider is None:
            return ""
        try:
            return str(provider(prefix or "", cwd or "/", shell or "", server_id or "") or "")
        except Exception:
            return ""

    @Slot(str)
    def report_command(self, command: str) -> None:
        if command:
            self.command_submitted.emit(command)

    @Slot()
    def request_find(self) -> None:
        self.find_requested.emit()

    @Slot(str)
    def send_input(self, data: str) -> None:
        if data:
            self.input_received.emit(data)

    @Slot(int, int)
    def report_size(self, cols: int, rows: int) -> None:
        if cols > 0 and rows > 0:
            self.resize_received.emit(int(cols), int(rows))

    @Slot(str)
    def report_cwd(self, path: str) -> None:
        if path:
            self.cwd_changed.emit(path)

    @Slot(str)
    def report_magic(self, payload: str) -> None:
        if payload:
            self.magic_command.emit(payload)

    @Slot(int, int, int)
    def report_scroll(self, max_line: int, viewport_line: int, rows: int) -> None:
        self.scroll_changed.emit(
            max(0, int(max_line)),
            max(0, int(viewport_line)),
            max(1, int(rows)),
        )

    @Slot()
    def js_ready(self) -> None:
        self.page_ready.emit()


class XtermTerminalWidget(QWidget):
    """Terminal viewport backed by xterm.js inside QWebEngineView."""

    input_bytes = Signal(bytes)
    resize_requested = Signal(int, int)
    cwd_changed = Signal(str)
    magic_command = Signal(str)

    def __init__(self, parent=None, theme: str = "dark"):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._ready = False
        self._pending_writes: list[str] = []
        self._last_size: tuple[int, int] | None = None
        self._pending_font: tuple[str, int] | None = None
        self._theme_name = "light" if str(theme).lower() == "light" else "dark"
        self._bg = _BG_LIGHT if self._theme_name == "light" else _BG_DARK
        self._bridge = _Bridge()
        self._bridge.input_received.connect(self._on_input)
        self._bridge.resize_received.connect(self._on_resize)
        self._bridge.page_ready.connect(self._on_page_ready)
        self._bridge.find_requested.connect(self._request_find)
        self._bridge.cwd_changed.connect(self._on_cwd_changed)
        self._bridge.magic_command.connect(self._on_magic_command)
        self._bridge.command_submitted.connect(self._on_command_submitted)
        self._bridge.scroll_changed.connect(self._on_scroll_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setStyleSheet(f"background: {self._bg};")

        self._load_placeholder = QLabel("Starting terminal…")
        self._load_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_placeholder.setStyleSheet(f"color:#7f8b99; background:{self._bg};")
        layout.addWidget(self._load_placeholder)

        if not HAS_WEBENGINE:
            lbl = QLabel(
                "Qt WebEngine is not installed.\n"
                "Install: pip install PySide6-WebEngine\n"
                "Falling back is handled by the app import layer."
            )
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color:#9aa6b5; background:{self._bg};")
            layout.addWidget(lbl)
            self._view = None
            return

        self._view = QWebEngineView(self)
        self._paint_chrome(self._bg)

        self._scrollbar = QScrollBar(Qt.Orientation.Vertical, self)
        self._scrollbar.setObjectName("terminalScrollBar")
        self._scrollbar.setRange(0, 0)
        self._scrollbar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scrollbar.setPageStep(1)
        self._scrollbar.setSingleStep(1)
        self._scrollbar.hide()
        self._scrollbar.valueChanged.connect(self._scrollbar_value_changed)
        self._scrollbar.setStyleSheet("""
            QScrollBar#terminalScrollBar {
                background: rgba(80, 94, 112, 0.18);
                width: 12px;
                border: 0;
                border-radius: 6px;
                margin: 3px 2px 3px 0;
            }
            QScrollBar#terminalScrollBar::handle:vertical {
                background: rgba(139, 154, 173, 0.84);
                min-height: 30px;
                border-radius: 5px;
                margin: 0;
            }
            QScrollBar#terminalScrollBar::handle:vertical:hover {
                background: rgba(168, 183, 201, 0.94);
            }
            QScrollBar#terminalScrollBar::add-line:vertical,
            QScrollBar#terminalScrollBar::sub-line:vertical,
            QScrollBar#terminalScrollBar::add-page:vertical,
            QScrollBar#terminalScrollBar::sub-page:vertical {
                background: transparent;
                border: 0;
                height: 0;
            }
        """)

        settings = self._view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FocusOnNavigationEnabled, True)

        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("bridge", self._bridge)
        self._view.page().setWebChannel(self._channel)

        layout.addWidget(self._view)
        self._view.hide()
        self._position_scrollbar()
        self._view.load(QUrl.fromLocalFile(str(HTML_PATH)))
        self._view.loadFinished.connect(self._on_load_finished)

    def _paint_chrome(self, bg: str) -> None:
        """Set Qt-side backgrounds so Chromium does not flash white on first paint."""
        if self._view is None:
            return
        try:
            color = QColor(bg)
            page = self._view.page()
            page.setBackgroundColor(color)
            pal = self._view.palette()
            pal.setColor(QPalette.ColorRole.Window, color)
            pal.setColor(QPalette.ColorRole.Base, color)
            self._view.setPalette(pal)
            self._view.setAutoFillBackground(True)
            self._view.setStyleSheet(f"QWebEngineView {{ background-color: {bg}; border: 0; }}")
            self.setStyleSheet(f"background-color: {bg};")
        except Exception:
            pass


    def _position_scrollbar(self) -> None:
        """Place the native Qt scrollbar in the reserved right gutter."""
        bar = getattr(self, "_scrollbar", None)
        if bar is None:
            return
        width = 14
        x = max(0, self.width() - width)
        bar.setGeometry(x, 0, width, max(0, self.height()))
        bar.raise_()

    def _on_scroll_changed(self, max_line: int, viewport_line: int, rows: int) -> None:
        bar = getattr(self, "_scrollbar", None)
        if bar is None:
            return
        value = max(0, min(int(viewport_line), int(max_line)))
        blocked = bar.blockSignals(True)
        try:
            bar.setRange(0, max(0, int(max_line)))
            bar.setPageStep(max(1, int(rows)))
            bar.setSingleStep(1)
            bar.setValue(value)
        finally:
            bar.blockSignals(blocked)
        bar.setVisible(int(max_line) > 0)
        self._position_scrollbar()

    def _scrollbar_value_changed(self, value: int) -> None:
        if self._scrollbar.signalsBlocked():
            return
        self._run_js(
            "window.sshdockTerm && window.sshdockTerm.scrollToLine && "
            f"window.sshdockTerm.scrollToLine({int(value)});"
        )


    def set_completion_provider(self, provider) -> None:
        """Set the lightweight Python-side predictor used by xterm.js."""
        self._bridge.completion_provider = provider

    def set_completion_context(self, *, server_id: str = "", cwd: str = "/", shell: str = "") -> None:
        import json
        payload = json.dumps({
            "serverId": str(server_id or ""),
            "cwd": str(cwd or "/"),
            "shell": str(shell or ""),
        })
        self._run_js(
            "window.sshdockTerm&&window.sshdockTerm.setCompletionContext&&"
            f"window.sshdockTerm.setCompletionContext({payload});"
        )

    def _on_command_submitted(self, command: str) -> None:
        """Forward commands observed by xterm without coupling transport to prediction."""
        try:
            parent = self.parentWidget()
            while parent is not None:
                handler = getattr(parent, "_terminal_command_submitted", None)
                if callable(handler):
                    handler(command, self)
                    return
                parent = parent.parentWidget()
        except Exception:
            pass

    def set_font(self, font) -> None:
        """Apply font family/size to xterm.js (queued until page ready)."""
        try:
            from PySide6.QtGui import QFont
            if isinstance(font, QFont):
                family = font.family() or "Consolas"
                size = int(font.pointSize() or 13)
            elif isinstance(font, (tuple, list)) and len(font) >= 2:
                family, size = str(font[0]), int(font[1])
            else:
                family, size = str(font), 13
        except Exception:
            family, size = "Consolas", 13
        if size < 8:
            size = 13
        self._pending_font = (family, size)
        self._push_font_to_js()

    def _push_font_to_js(self) -> None:
        if not self._pending_font:
            return
        family, size = self._pending_font
        import json
        fam = json.dumps(f"{family}, Cascadia Mono, Consolas, Menlo, monospace")
        # Prefer dedicated API; fall back to term.options if exposed later
        self._run_js(
            f"if(window.sshdockTerm&&window.sshdockTerm.setFont)"
            f"{{window.sshdockTerm.setFont({fam},{int(size)});}}"
        )

    def apply_theme(self, theme: str = "dark") -> None:
        """Update chrome + xterm color theme."""
        name = "light" if str(theme).lower() == "light" else "dark"
        self._theme_name = name
        bg = _BG_LIGHT if name == "light" else _BG_DARK
        self._bg = bg
        self._paint_chrome(bg)
        if name == "light":
            theme_js = {
                "background": "#f4f6f9",
                "foreground": "#1f2933",
                "cursor": "#1f2933",
                "selectionBackground": "#b6d0f7",
                "black": "#1f2933",
                "red": "#c0392b",
                "green": "#1e7a46",
                "yellow": "#a67c00",
                "blue": "#1a5fb4",
                "magenta": "#7d3c98",
                "cyan": "#0b7a75",
                "white": "#1f2933",
                "brightBlack": "#6b7280",
                "brightRed": "#e74c3c",
                "brightGreen": "#27ae60",
                "brightYellow": "#f1c40f",
                "brightBlue": "#3498db",
                "brightMagenta": "#9b59b6",
                "brightCyan": "#1abc9c",
                "brightWhite": "#111827",
            }
        else:
            theme_js = {
                "background": "#080d13",
                "foreground": "#e6edf3",
                "cursor": "#e6edf3",
                "selectionBackground": "#264f78",
                "black": "#0b0f14",
                "red": "#ff5f56",
                "green": "#5cff8a",
                "yellow": "#ffd866",
                "blue": "#5aa9ff",
                "magenta": "#d68cff",
                "cyan": "#52e8ff",
                "white": "#e6edf3",
                "brightBlack": "#66727e",
                "brightRed": "#ff6b6b",
                "brightGreen": "#69f58f",
                "brightYellow": "#ffe58a",
                "brightBlue": "#78b9ff",
                "brightMagenta": "#e7a8ff",
                "brightCyan": "#82f1ff",
                "brightWhite": "#ffffff",
            }
        import json
        payload = json.dumps(theme_js)
        self._run_js(
            f"document.documentElement.style.background='{bg}';"
            f"if(document.body)document.body.style.background='{bg}';"
            f"if(window.sshdockTerm&&window.sshdockTerm.setTheme)"
            f"{{window.sshdockTerm.setTheme({payload});}}"
        )
        # Re-apply font after theme (xterm can reset metrics)
        self._push_font_to_js()


    def _on_load_finished(self, ok: bool) -> None:
        # Re-assert background after navigation (Chromium may reset). Keep
        # the WebEngine surface hidden until it is actually ready so Chromium
        # cannot flash its default white page on first paint.
        self._paint_chrome(self._bg)
        if ok:
            self._view.show()
            self._load_placeholder.hide()
            QTimer.singleShot(80, self._flush_pending)
        else:
            self._load_placeholder.setText("Terminal engine failed to load")

    def _on_page_ready(self) -> None:
        self._ready = True
        if self._view is not None:
            self._view.show()
        self._load_placeholder.hide()
        self._paint_chrome(self._bg)
        self._flush_pending()
        # Apply any font/theme queued before JS was ready
        self.apply_theme(getattr(self, "_theme_name", "dark"))
        self._push_font_to_js()
        self._run_js("window.sshdockTerm && window.sshdockTerm.fit && window.sshdockTerm.fit();")

    def _request_find(self) -> None:
        try:
            self._view.setFocus(Qt.FocusReason.OtherFocusReason) if self._view is not None else None
        except Exception:
            pass
        parent = self.parentWidget()
        while parent is not None:
            handler = getattr(parent, "_focus_terminal_find", None)
            if callable(handler):
                handler()
                return
            parent = parent.parentWidget()

    def _on_cwd_changed(self, path: str) -> None:
        if path:
            self.cwd_changed.emit(path)

    def _on_magic_command(self, payload: str) -> None:
        if payload:
            self.magic_command.emit(payload)

    def _on_input(self, data: str) -> None:
        self.input_bytes.emit(data.encode("utf-8", "surrogatepass"))

    def _on_resize(self, cols: int, rows: int) -> None:
        size = (cols, rows)
        if size == self._last_size:
            return
        self._last_size = size
        self.resize_requested.emit(cols, rows)

    def _run_js(self, script: str) -> None:
        if self._view is None:
            return
        self._view.page().runJavaScript(script)

    def _flush_pending(self) -> None:
        if not self._pending_writes:
            return
        chunks = self._pending_writes
        self._pending_writes = []
        for chunk in chunks:
            self._write_js(chunk)
        self._ready = True

    def _write_js(self, text: str) -> None:
        import json
        payload = json.dumps(text)
        self._run_js(f"window.sshdockTerm && window.sshdockTerm.write({payload});")

    def feed(self, data: bytes) -> None:
        try:
            text = data.decode("utf-8", "replace")
        except Exception:
            text = data.decode("latin-1", "replace")
        if not self._ready and self._view is not None:
            self._pending_writes.append(text)
            return
        self._write_js(text)

    def show_table(self, title: str, columns: list, rows: list, command: str | None = None) -> None:
        """Show Qt/HTML result block inside the terminal page."""
        import json
        from PySide6.QtCore import QTimer
        payload = json.dumps(
            {"title": title, "columns": columns, "rows": rows, "command": command or ""},
            ensure_ascii=False,
        )
        script = (
            "(function(){"
            "try{"
            "if(window.sshdockTerm&&window.sshdockTerm.showTable){"
            f"window.sshdockTerm.showTable({payload});"
            "}else{console.error('sshdockTerm.showTable missing');}"
            "}catch(e){console.error('showTable',e);}"
            "})();"
        )
        def _do():
            self._run_js(script)
            self._run_js(
                "window.sshdockTerm&&window.sshdockTerm.focus&&window.sshdockTerm.focus();"
            )
            if self._view is not None:
                self._view.setFocus(Qt.FocusReason.OtherFocusReason)
        if self._ready:
            _do()
        else:
            # Page not ready yet — retry shortly
            QTimer.singleShot(200, _do)
            QTimer.singleShot(600, _do)

    def hide_table(self) -> None:
        self._run_js("window.sshdockTerm && window.sshdockTerm.hideTable && window.sshdockTerm.hideTable();")

    def clear(self, *, remote: bool = True) -> None:
        if remote:
            self.input_bytes.emit(b"\x0c")
        self._run_js("window.sshdockTerm && window.sshdockTerm.clear && window.sshdockTerm.clear();")

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._run_js("window.sshdockTerm && window.sshdockTerm.focus && window.sshdockTerm.focus();")
        if self._view is not None:
            self._view.setFocus(Qt.FocusReason.OtherFocusReason)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_scrollbar()
        QTimer.singleShot(50, lambda: self._run_js(
            "window.sshdockTerm && window.sshdockTerm.fit && window.sshdockTerm.fit();"
        ))

    def find_text(self, query: str) -> None:
        import json
        q = json.dumps(query or "")
        self._run_js(f"window.sshdockTerm&&window.sshdockTerm.find&&window.sshdockTerm.find({q});")

    def show_message(self, text: str) -> None:
        self.feed(text.encode("utf-8"))



def create_terminal_widget(parent=None, theme: str = "dark") -> QWidget:
    """Prefer xterm.js; fall back to pyte TerminalWidget."""
    if HAS_WEBENGINE and HTML_PATH.is_file():
        return XtermTerminalWidget(parent, theme=theme)
    from app.terminal.emulator import TerminalWidget
    return TerminalWidget(parent)
