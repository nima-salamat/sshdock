from __future__ import annotations

import os
import platform
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QRect, QSize, QTimer
from PySide6.QtGui import QIcon, QFont, QGuiApplication, QCursor, QPalette, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QTextEdit, QToolButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QApplication,
)

from app.core.models import AuthMethod, SSHProfile
from app.core.errors import normalize_error
from app.ssh.discovery import SSHAlias
from app.ssh.keypaste import looks_like_private_key, write_pasted_key_to_temp
from app.ssh.targets import is_alias_resolvable, parse_ssh_target, resolve_target

try:
    import qtawesome as qta
except ImportError:
    qta = None


def _field_help(tip: str) -> QLabel:
    """Tiny ? style help indicator with tooltip (shows quickly)."""
    lbl = QLabel("?")
    lbl.setFixedSize(18, 18)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setToolTip(tip)
    lbl.setToolTipDuration(1200)  # ms — much faster than Qt default (~700ms+ hold)
    lbl.setCursor(Qt.CursorShape.WhatsThisCursor)
    lbl.setStyleSheet(
        "background:#1d2530; color:#9fb0c3; border-radius:9px; font-size:11px; font-weight:700;"
    )
    return lbl


class ErrorDialog(QDialog):
    """Unified error dialog — always shows a copyable report block."""

    def __init__(self, title: str, message: str, details: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title or "Error")
        self.resize(560, 360)
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        icon = QLabel("⚠")
        icon.setStyleSheet("font-size:28px; color:#ff939b;")
        header.addWidget(icon)
        title_lbl = QLabel(title or "Something went wrong")
        title_lbl.setStyleSheet("font-size:18px; font-weight:700;")
        header.addWidget(title_lbl, 1)
        root.addLayout(header)

        msg = QLabel(message or "")
        msg.setWordWrap(True)
        msg.setStyleSheet("color:#e9edf2; font-size:13px;")
        root.addWidget(msg)

        # Always build a full copyable report (title + message + details)
        report_lines = [f"[{title or 'Error'}]", message or ""]
        if details:
            report_lines.extend(["", "--- details ---", str(details)])
        self._report = "\n".join(report_lines).strip() + "\n"

        frame = QFrame()
        frame.setObjectName("panel")
        frame.setStyleSheet(
            "#panel { background:#0d1014; border:1px solid #2a313a; border-radius:8px; }"
        )
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(10, 8, 10, 8)
        fl.setSpacing(6)
        lbl = QLabel("Details (selectable / copyable)")
        lbl.setStyleSheet("color:#8a95a4; font-size:11px; font-weight:600;")
        fl.addWidget(lbl)
        self.details_edit = QTextEdit()
        self.details_edit.setReadOnly(True)
        self.details_edit.setPlainText(self._report)
        self.details_edit.setMinimumHeight(140)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.details_edit.setFont(mono)
        fl.addWidget(self.details_edit)
        root.addWidget(frame, 1)

        buttons = QHBoxLayout()
        copy_btn = QPushButton("Copy")
        if qta:
            try:
                copy_btn.setIcon(qta.icon("fa5s.copy", color="#dbe7f5"))
            except Exception:
                pass
        copy_btn.clicked.connect(self._copy)
        buttons.addWidget(copy_btn)
        buttons.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setProperty("accent", True)
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

    def _copy(self):
        from PySide6.QtWidgets import QApplication
        text = self.details_edit.toPlainText() if self.details_edit else self._report
        QApplication.clipboard().setText(text)

    @staticmethod
    def show_error(parent, title: str, message: str, details: str | None = None):
        """Unified user-facing error entry point with a copyable diagnostic report."""
        if details:
            ErrorDialog(title, message, details, parent).exec()
            return
        ErrorDialog(title, message, message, parent).exec()

    @staticmethod
    def show_exception(parent, exc: BaseException, *, operation: str = "ui", title: str = "Operation failed"):
        error = normalize_error(exc, operation=operation, title=title)
        error.log()
        ErrorDialog(error.title, error.message, f"Error ID: {error.error_id}\nOperation: {error.operation}\n\n{error.details}", parent).exec()


class ConfirmRemoveDialog(QDialog):
    """Confirm container removal with optional force (-f) checkbox.

    By default force is OFF so a normal `docker rm` is used.
    User can tick the checkbox to force-remove running containers.
    """

    def __init__(self, names: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remove container(s)")
        self.setMinimumWidth(420)
        self.force = False
        self._names = names

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        if len(names) == 1:
            msg = QLabel(f"Remove container «{names[0]}»?")
        else:
            msg = QLabel(f"Remove {len(names)} selected container(s)?")
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size:14px; font-weight:600;")
        root.addWidget(msg)

        if len(names) > 1 and len(names) <= 12:
            listing = QLabel("\n".join(f"• {n}" for n in names))
            listing.setStyleSheet("color:#9ca7b5; font-size:12px;")
            listing.setWordWrap(True)
            root.addWidget(listing)
        elif len(names) > 12:
            listing = QLabel("\n".join(f"• {n}" for n in names[:10]) + f"\n… and {len(names) - 10} more")
            listing.setStyleSheet("color:#9ca7b5; font-size:12px;")
            listing.setWordWrap(True)
            root.addWidget(listing)

        hint = QLabel(
            "By default a normal remove is used (docker rm).\n"
            "If a container is still running, enable Force to stop and remove it."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8a95a4; font-size:12px;")
        root.addWidget(hint)

        self.force_cb = QCheckBox("Force remove (-f) — stop and remove even if running")
        self.force_cb.setChecked(False)
        self.force_cb.setStyleSheet("font-weight:600;")
        root.addWidget(self.force_cb)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Remove")
        ok.setProperty("danger", True)
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        root.addLayout(buttons)

    def _accept(self):
        self.force = self.force_cb.isChecked()
        self.accept()


def _screen_at(global_pos: QPoint):
    """Return QScreen under a global point (multi-monitor safe)."""
    screen = QGuiApplication.screenAt(global_pos)
    if screen is not None:
        return screen
    app = QGuiApplication.instance()
    if app is not None and app.primaryScreen() is not None:
        return app.primaryScreen()
    return None


def _available_rect_for_point(global_pos: QPoint) -> QRect:
    """Work area (excludes taskbar/docks) of the monitor under *global_pos*."""
    screen = _screen_at(global_pos)
    if screen is None:
        return QRect(0, 0, 1920, 1080)
    return screen.availableGeometry()


def clamp_window_to_screens(widget, *, prefer_pos: QPoint | None = None) -> None:
    """Keep *widget* fully inside a monitor work area (taskbar-safe, multi-monitor).

    - Uses availableGeometry (not fullGeometry) so the window stays above the taskbar.
    - If the window spans / is near another monitor, prefers the screen under the
      title-bar center (or *prefer_pos*).
    - Shrinks size if the target work area is smaller than the window.
    """
    if widget is None:
        return
    geo = widget.frameGeometry()
    # Anchor: center of title strip if possible, else window center
    if prefer_pos is not None:
        anchor = prefer_pos
    else:
        anchor = geo.center()
        # prefer top-center (title bar region)
        anchor = QPoint(geo.center().x(), geo.top() + min(24, geo.height() // 4))

    work = _available_rect_for_point(anchor)
    # Margin so border/shadow does not sit under OS chrome
    margin = 4
    work = work.adjusted(margin, margin, -margin, -margin)
    if work.width() < 200 or work.height() < 160:
        return

    w = min(geo.width(), work.width())
    h = min(geo.height(), work.height())
    # DPI-aware: if logical size was set fixed larger than this screen, shrink
    if hasattr(widget, "minimumSize") and widget.minimumSize().width() > 0:
        w = max(w, min(widget.minimumSize().width(), work.width()))
        h = max(h, min(widget.minimumSize().height(), work.height()))

    x = geo.x()
    y = geo.y()
    # Clamp so entire window stays in work area
    if x < work.left():
        x = work.left()
    if y < work.top():
        y = work.top()
    if x + w > work.right() + 1:
        x = work.right() + 1 - w
    if y + h > work.bottom() + 1:
        y = work.bottom() + 1 - h
    # Re-clamp after adjustments (tiny screens)
    x = max(work.left(), min(x, work.right() + 1 - w))
    y = max(work.top(), min(y, work.bottom() + 1 - h))

    widget.setGeometry(x, y, w, h)


def fit_dialog_size_for_screen(dialog: QDialog, ideal: QSize, *, min_size: QSize | None = None) -> QSize:
    """Scale *ideal* size down to fit the screen under the cursor / parent."""
    # Prefer screen of parent window, else cursor, else primary
    screen = None
    parent = dialog.parentWidget()
    if parent is not None and parent.screen() is not None:
        screen = parent.screen()
    if screen is None:
        screen = _screen_at(QCursor.pos())
    if screen is None:
        app = QGuiApplication.instance()
        screen = app.primaryScreen() if app else None
    if screen is None:
        return ideal

    work = screen.availableGeometry()
    # Leave room for taskbar + margins; respect device pixel ratio indirectly via availableGeometry
    max_w = max(480, work.width() - 48)
    max_h = max(360, work.height() - 48)
    w = min(ideal.width(), max_w)
    h = min(ideal.height(), max_h)
    if min_size is not None:
        w = max(min_size.width(), w) if min_size.width() <= max_w else max_w
        h = max(min_size.height(), h) if min_size.height() <= max_h else max_h
    return QSize(w, h)


def place_dialog_on_best_screen(dialog: QDialog) -> None:
    """Center dialog on the screen that contains the cursor (or parent), then clamp."""
    screen = _screen_at(QCursor.pos())
    parent = dialog.parentWidget()
    if parent is not None and parent.isVisible() and parent.screen() is not None:
        # If parent is on another monitor, prefer that unless cursor is elsewhere
        ps = parent.screen()
        if ps is not None and _screen_at(QCursor.pos()) is None:
            screen = ps
    if screen is None:
        app = QGuiApplication.instance()
        screen = app.primaryScreen() if app else None
    if screen is None:
        return
    work = screen.availableGeometry()
    geo = dialog.frameGeometry()
    geo.moveCenter(work.center())
    dialog.move(geo.topLeft())
    clamp_window_to_screens(dialog, prefer_pos=work.center())


class _TitleBar(QWidget):
    """Draggable custom title bar — multi-monitor + taskbar aware."""

    def __init__(self, parent: QDialog, title: str = "SSHDock"):
        super().__init__(parent)
        self._dialog = parent
        self._drag_offset: QPoint | None = None
        self.setObjectName("customTitleBar")
        self.setFixedHeight(44)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 8, 0)
        lay.setSpacing(10)

        brand = QLabel("SSHDock")
        brand.setObjectName("titleBrand")
        lay.addWidget(brand)
        sep = QLabel("·")
        sep.setObjectName("titleSep")
        lay.addWidget(sep)
        self._title = QLabel(title)
        self._title.setObjectName("titleText")
        lay.addWidget(self._title, 1)

        self.min_btn = QToolButton()
        self.min_btn.setObjectName("winBtn")
        self.min_btn.setText("–")
        self.min_btn.setFixedSize(32, 28)
        self.min_btn.setToolTip("Minimize")
        self.min_btn.clicked.connect(parent.showMinimized)

        self.close_btn = QToolButton()
        self.close_btn.setObjectName("winBtnClose")
        self.close_btn.setText("✕")
        self.close_btn.setFixedSize(32, 28)
        self.close_btn.setToolTip("Close")
        self.close_btn.clicked.connect(parent.reject)

        lay.addWidget(self.min_btn)
        lay.addWidget(self.close_btn)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # Offset from window top-left to cursor (global)
            self._drag_offset = event.globalPosition().toPoint() - self._dialog.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            target = event.globalPosition().toPoint() - self._drag_offset
            self._dialog.move(target)
            # Clamp so title bar never leaves the work area of the monitor under the cursor
            clamp_window_to_screens(
                self._dialog,
                prefer_pos=event.globalPosition().toPoint(),
            )
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        # Final snap into work area of current screen
        clamp_window_to_screens(self._dialog)
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        # Optional: re-center on current screen
        if event.button() == Qt.MouseButton.LeftButton:
            place_dialog_on_best_screen(self._dialog)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class _SecretLineEdit(QLineEdit):
    """QLineEdit with an embedded show/hide eye toggle.

    Used for password and passphrase fields. The eye icon lives in the
    right inset (``addAction`` with ``LineEditActionPosition.TrailingPosition``)
    so it stays pixel-aligned with the field's rounded corner.

    Behavior:
      * Starts hidden (EchoMode.Password).
      * First click reveals (EchoMode.Normal) and swaps the icon to "eye-slash".
      * Second click hides again.
      * When revealed, a 30s auto-hide timer re-masks the field so a briefly
        unattended screen doesn't leak the secret.
      * ``secret()`` returns the real text regardless of echo mode.
      * ``set_secret(text)`` sets text and re-masks.
    """

    def __init__(self, parent: QWidget | None = None, *, placeholder: str = ""):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        if placeholder:
            self.setPlaceholderText(placeholder)

        # Build a real QAction for the eye toggle. QLineEdit.addAction()
        # in PySide6 requires a QAction (Shiboken type-checks the argument
        # and rejects duck-typed wrappers), so we use QAction directly
        # and update its icon when the visibility changes. Qt renders the
        # action's icon inside the line edit's trailing inset and emits
        # ``triggered`` when the user clicks it.
        from PySide6.QtGui import QAction
        self._eye_action = QAction(self)
        self._eye_action.setToolTip("Show / hide (auto-hides in 30s)")
        self._eye_action.setObjectName("eyeToggle")
        self._set_eye_icon(visible=False)
        self._eye_action.triggered.connect(self._toggle_visibility)
        self.addAction(
            self._eye_action,
            QLineEdit.ActionPosition.TrailingPosition,
        )

        # Auto-hide timer: when revealed, re-mask after 30s of inactivity.
        self._auto_hide = QTimer(self)
        self._auto_hide.setSingleShot(True)
        self._auto_hide.setInterval(30_000)
        self._auto_hide.timeout.connect(self._mask)

    def _set_eye_icon(self, *, visible: bool) -> None:
        if qta is not None:
            try:
                self._eye_action.setIcon(
                    qta.icon("fa5s.eye-slash" if visible else "fa5s.eye",
                             color="#9fb0c3")
                )
                return
            except Exception:
                pass
        # Fallback: build a QIcon from a unicode glyph so the action
        # still has a visible icon even without qtawesome. We use a
        # QPixmap + QPainter because QIcon(str) treats the string as a
        # file path, not as text to render.
        try:
            from PySide6.QtGui import QPixmap, QPainter, QFont, QColor
            pm = QPixmap(16, 16)
            pm.fill(QColor(0, 0, 0, 0))
            painter = QPainter(pm)
            painter.setPen(QColor("#9fb0c3"))
            font = QFont()
            font.setPointSize(10)
            painter.setFont(font)
            painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter,
                             "🙈" if visible else "👁")
            painter.end()
            self._eye_action.setIcon(QIcon(pm))
        except Exception:
            pass

    def _toggle_visibility(self) -> None:
        if self.echoMode() == QLineEdit.EchoMode.Password:
            self.setEchoMode(QLineEdit.EchoMode.Normal)
            self._set_eye_icon(visible=True)
            self._auto_hide.start()
        else:
            self._mask()

    def _mask(self) -> None:
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self._set_eye_icon(visible=False)
        self._auto_hide.stop()

    # Convenience API ------------------------------------------------
    def secret(self) -> str:
        return self.text()

    def set_secret(self, value: str) -> None:
        self.setText(value or "")
        self._mask()

    def clear_secret(self) -> None:
        self.setText("")
        self._mask()


# ----------------------------------------------------------------------
# Screenshot protection helpers
# ----------------------------------------------------------------------
# These try to mark a top-level window as "do not capture" for OS-level
# screenshot / screen-recording tools. The implementation is best-effort:
#
#   * Windows: ``SetWindowDisplayAffinity(hwnd, WDA_MONITOR)`` makes the
#     window render as black in screenshots, screen-recording tools, and
#     remote-desktop sessions. Available on Windows 10+.
#   * macOS:   ``NSWindow.setSharingType(NSWindowSharingNone)`` opts the
#     window out of screen-capture APIs (so QuickTime / screencapture
#     cannot record it). Available on all macOS versions.
#   * Linux:   No reliable cross-toolkit mechanism exists on X11/Wayland.
#     We set the X11 window type to ``_NET_WM_WINDOW_TYPE_UTILITY`` and
#     add a visible "screen capture is not blocked on Linux" warning
#     banner so the user is not falsely reassured.
# ----------------------------------------------------------------------

def _enable_screenshot_protection(window: QWidget) -> tuple[bool, str]:
    """Try to mark *window* as non-capturable.

    Returns ``(succeeded, platform_note)`` where ``platform_note`` is a
    short human-readable string explaining what was done (or why not).
    """
    if window is None or not window.windowHandle():
        # No window handle yet — caller should call this in showEvent()
        # after the window has been realized.
        return False, "Window not realized yet — call from showEvent()."

    system = platform.system()

    if system == "Windows":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            # WDA_NONE = 0, WDA_MONITOR = 1, WDA_EXCLUDEFROMCAPTURE = 0x11
            # ``WDA_EXCLUDEFROMCAPTURE`` (Windows 10 2004+) is the strongest:
            # the window is excluded entirely from screenshots and recording.
            hwnd = int(window.winId())
            # Try ExcludeFromCapture first (Windows 10 2004+), fall back to
            # WDA_MONITOR which renders black instead.
            for affinity in (0x00000011, 0x00000001):
                try:
                    ok = user32.SetWindowDisplayAffinity(
                        wintypes.HWND(hwnd), wintypes.DWORD(affinity)
                    )
                    if ok:
                        return True, (
                            "Screenshot protection enabled (Windows "
                            "SetWindowDisplayAffinity). The window will be "
                            "excluded from screenshots and screen recording."
                        )
                except OSError:
                    continue
            return False, "SetWindowDisplayAffinity not available on this Windows build."
        except Exception as exc:  # pragma: no cover — platform-specific
            return False, f"Windows protection failed: {exc}"

    if system == "Darwin":
        try:
            # Use PyObjC if available; otherwise fall back gracefully.
            import AppKit  # type: ignore[import-not-found]
            from AppKit import NSWindow, NSWindowSharingNone  # noqa: F401

            ns_window = window.windowHandle()
            # PySide6 provides a native NSWindow via winId() on macOS.
            # We need to wrap the HWND/NSView back to NSWindow.
            # This is brittle; we attempt and report on failure.
            view_id = int(ns_window.winId())
            from AppKit import NSView  # type: ignore[import-not-found]
            ns_view = NSView.alloc().initWithFrame_(((0, 0), (0, 0)))
            ns_view = ns_view.__pointer_cast__(view_id) if hasattr(ns_view, "__pointer_cast__") else None
            if ns_view is not None:
                ns_win = ns_view.window()
                if ns_win is not None:
                    ns_win.setSharingType_(AppKit.NSWindowSharingNone)
                    return True, (
                        "Screenshot protection enabled (macOS "
                        "NSWindowSharingNone). Screen capture tools will "
                        "not record this window."
                    )
            return False, "Could not obtain NSWindow handle for protection."
        except ImportError:
            return False, (
                "PyObjC (AppKit) not installed — macOS screenshot "
                "protection unavailable. Install with: pip install pyobjc"
            )
        except Exception as exc:  # pragma: no cover — platform-specific
            return False, f"macOS protection failed: {exc}"

    # Linux / other: no reliable screenshot prevention.
    if system == "Linux":
        # Best-effort: set the X11 window type so compositors treat it as
        # a transient utility window. This does NOT prevent screenshots
        # but at least matches the visual semantics of a secure popup.
        try:
            window.setWindowFlag(Qt.WindowType.Tool, True)
            window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        except Exception:
            pass
        return False, (
            "Linux does not support reliable screenshot prevention. "
            "Treat this preview as visible to any screen-recording tool."
        )

    return False, f"Screenshot protection not implemented for {system}."


# ----------------------------------------------------------------------
# Secure private-key preview dialog
# ----------------------------------------------------------------------

class SecureKeyPreviewDialog(QDialog):
    """A separate, screenshot-protected window for previewing a private key.

    Features:
      * Opens as an independent top-level window (not embedded in the parent).
      * Calls :func:`_enable_screenshot_protection` from ``showEvent`` so the
        OS-level affinity is set as soon as the window is realized.
      * Shows the key contents in a read-only monospace ``QPlainTextEdit``.
      * A clear "Secure preview" banner tells the user what is and isn't
        protected on their platform.
      * Optional "Copy to clipboard" button that auto-clears the clipboard
        after 30 seconds.
      * On close, the clipboard is wiped if we put anything in it.
      * Cannot be minimized (so it can't be left open accidentally).
    """

    def __init__(self, key_text: str, *, source_label: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Private key preview — Secure")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self._clipboard_set_at: float | None = None
        self._clipboard_timer = QTimer(self)
        self._clipboard_timer.setSingleShot(True)
        self._clipboard_timer.setInterval(30_000)
        self._clipboard_timer.timeout.connect(self._wipe_clipboard)

        ideal = QSize(720, 540)
        fitted = fit_dialog_size_for_screen(self, ideal, min_size=QSize(480, 360))
        self.setMinimumSize(QSize(min(480, fitted.width()), min(360, fitted.height())))
        self.resize(fitted)

        self._key_text = key_text or ""
        self._source_label = source_label

        self._build()
        # Defer screenshot protection to showEvent (need a window handle).

    def _build(self) -> None:
        self.setStyleSheet(self._qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # ── Header ──
        header = QHBoxLayout()
        header.setSpacing(10)
        lock = QLabel("🔒")
        lock.setStyleSheet("font-size:22px;")
        header.addWidget(lock)
        title = QLabel("Private key preview")
        title.setStyleSheet("font-size:16px; font-weight:700; color:#f1f5f9;")
        header.addWidget(title)
        header.addStretch()
        if self._source_label:
            src = QLabel(self._source_label)
            src.setStyleSheet(
                "color:#9fb0c3; font-size:11px; padding:4px 10px;"
                "background:#1a222c; border:1px solid #2a3340; border-radius:10px;"
            )
            header.addWidget(src)
        root.addLayout(header)

        # ── Protection status banner ──
        # The text gets filled in by showEvent once we know the platform result.
        self._banner = QLabel("Verifying screenshot protection…")
        self._banner.setWordWrap(True)
        self._banner.setObjectName("secureBanner")
        self._banner.setStyleSheet(
            "#secureBanner {"
            "  padding:8px 12px; border-radius:8px;"
            "  background:#231b08; color:#ffca4b;"
            "  border:1px solid #4a3a14; font-size:12px;"
            "}"
        )
        root.addWidget(self._banner)

        # ── Key contents ──
        self._editor = QPlainTextEdit()
        self._editor.setReadOnly(True)
        self._editor.setPlainText(self._key_text)
        mono = QFont("JetBrains Mono")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(10)
        self._editor.setFont(mono)
        self._editor.setObjectName("keyPreview")
        self._editor.setStyleSheet(
            "#keyPreview {"
            "  background:#0a0d12; color:#c8d3e0;"
            "  border:1px solid #2a3340; border-radius:8px;"
            "  padding:10px;"
            "}"
        )
        root.addWidget(self._editor, 1)

        # ── Footer ──
        footer = QHBoxLayout()
        self._copy_btn = QPushButton("Copy to clipboard (auto-clears in 30s)")
        if qta:
            try:
                self._copy_btn.setIcon(qta.icon("fa5s.copy", color="#dbe7f5"))
            except Exception:
                pass
        self._copy_btn.clicked.connect(self._copy_with_autoclear)
        footer.addWidget(self._copy_btn)
        footer.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setProperty("accent", True)
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        root.addLayout(footer)

    @staticmethod
    def _qss() -> str:
        return (
            "QDialog { background:#0f1318; }"
            "QPushButton { background:#1a222c; border:1px solid #2c3542;"
            "              border-radius:8px; color:#dbe3ee; padding:7px 14px;"
            "              font-weight:600; }"
            "QPushButton:hover { background:#222b36; border-color:#3b4656; }"
            "QPushButton[accent=true] { background:#1d6fe8; border-color:#1a62ce;"
            "                           color:#fff; padding:8px 18px;"
            "                           font-weight:700; }"
            "QPushButton[accent=true]:hover { background:#2b7cf0; }"
        )

    # ── Screenshot protection ──
    def showEvent(self, event) -> None:
        super().showEvent(event)
        # First show: apply platform protection.
        if not getattr(self, "_protected", False):
            self._protected = True
            ok, note = _enable_screenshot_protection(self)
            if ok:
                self._banner.setText(
                    "✓ " + note + "  ·  Clipboard will auto-clear 30s after copy."
                )
                self._banner.setStyleSheet(
                    "#secureBanner {"
                    "  padding:8px 12px; border-radius:8px;"
                    "  background:#0d2818; color:#41d17d;"
                    "  border:1px solid #1d5a36; font-size:12px;"
                    "}"
                )
            else:
                # Could not enforce OS-level protection — warn the user.
                self._banner.setText(
                    "⚠ " + note + "  ·  Treat this preview as visible to any "
                    "screen-recording tool on this machine."
                )
                self._banner.setStyleSheet(
                    "#secureBanner {"
                    "  padding:8px 12px; border-radius:8px;"
                    "  background:#2a181c; color:#ff939b;"
                    "  border:1px solid #5a3036; font-size:12px;"
                    "}"
                )

    # ── Clipboard hygiene ──
    def _copy_with_autoclear(self) -> None:
        QApplication.clipboard().setText(self._key_text)
        self._clipboard_set_at = time.time()
        self._clipboard_timer.start()
        self._copy_btn.setText("Copied — will auto-clear in 30s")

    def _wipe_clipboard(self) -> None:
        # Only wipe if we still own the clipboard (best-effort).
        try:
            cb = QApplication.clipboard()
            if cb.text() == self._key_text:
                cb.setText("")
        except Exception:
            pass
        self._clipboard_set_at = None
        try:
            self._copy_btn.setText("Copy to clipboard (auto-clears in 30s)")
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        # Always wipe the clipboard when closing, even if the timer hasn't fired.
        self._wipe_clipboard()
        super().closeEvent(event)


# ----------------------------------------------------------------------
# Paste-private-key dialog
# ----------------------------------------------------------------------

class PastePrivateKeyDialog(QDialog):
    """A small dialog for pasting a private key directly.

    The pasted content is kept entirely inside this dialog and only
    returned to the caller via :attr:`result_text` when the user clicks
    Save. The main form never displays the content — it just shows a
    status chip ("✓ Key pasted (12 lines)").
    """

    def __init__(self, parent=None, *, existing: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Paste private key")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        ideal = QSize(640, 420)
        fitted = fit_dialog_size_for_screen(self, ideal, min_size=QSize(440, 320))
        self.setMinimumSize(QSize(min(440, fitted.width()), min(320, fitted.height())))
        self.resize(fitted)

        self.result_text: str | None = None
        self._build(existing)

    def _build(self, existing: str) -> None:
        self.setStyleSheet(SecureKeyPreviewDialog._qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel("Paste private key")
        title.setStyleSheet("font-size:15px; font-weight:700; color:#f1f5f9;")
        root.addWidget(title)

        hint = QLabel(
            "Paste an OpenSSH or PEM-format private key here. "
            "The content is kept in memory only — it will be written to a "
            "0600-permission temp file when you Save, and the main form "
            "will only show that a key has been pasted (never its contents)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#9fb0c3; font-size:12px;")
        root.addWidget(hint)

        # Editable area with show/hide toggle.
        edit_row = QHBoxLayout()
        edit_row.setContentsMargins(0, 0, 0, 0)
        edit_row.setSpacing(6)
        self._editor = QPlainTextEdit()
        self._editor.setPlaceholderText("-----BEGIN OPENSSH PRIVATE KEY-----\n…\n-----END OPENSSH PRIVATE KEY-----")
        # Default to masked: render as a password-style field by setting
        # the document to a custom colour. QPlainTextEdit has no EchoMode,
        # so we toggle visibility by swapping the text colour vs a placeholder
        # mask. We use a simpler trick: a "show" checkbox that toggles
        # whether the actual text or a row of • chars is shown.
        mono = QFont("JetBrains Mono")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(10)
        self._editor.setFont(mono)
        self._editor.setStyleSheet(
            "QPlainTextEdit {"
            "  background:#0a0d12; color:#c8d3e0;"
            "  border:1px solid #2a3340; border-radius:8px; padding:10px;"
            "}"
        )
        if existing:
            self._editor.setPlainText(existing)
        root.addWidget(self._editor, 1)

        # Show/hide row
        show_row = QHBoxLayout()
        self._show_cb = QCheckBox("Show characters (off by default)")
        self._show_cb.setChecked(False)
        self._show_cb.toggled.connect(self._toggle_mask)
        self._toggle_mask(False)  # initial masked state
        show_row.addWidget(self._show_cb)
        show_row.addStretch()
        self._line_count = QLabel("0 lines")
        self._line_count.setStyleSheet("color:#8a95a4; font-size:11px;")
        show_row.addWidget(self._line_count)
        self._editor.textChanged.connect(self._update_line_count)
        root.addLayout(show_row)

        # Footer
        footer = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setProperty("accent", True)
        save.setDefault(True)
        save.clicked.connect(self._save)
        footer.addStretch()
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)

        self._update_line_count()

    def _toggle_mask(self, show: bool) -> None:
        # QPlainTextEdit has no EchoMode. We emulate masking by swapping the
        # palette: when "show" is off, we keep the text but recolour it to
        # the background so it's not visible at a glance. (A determined
        # attacker with access to the running process can still read it;
        # this is a UI affordance, not a security boundary.)
        pal = self._editor.palette()
        if show:
            pal.setColor(QPalette.ColorRole.Text, QColor("#c8d3e0"))
        else:
            pal.setColor(QPalette.ColorRole.Text, QColor("#0a0d12"))
        self._editor.setPalette(pal)
        self._editor.viewport().update()

    def _update_line_count(self) -> None:
        text = self._editor.toPlainText()
        n = max(1, text.count("\n") + 1) if text.strip() else 0
        if n == 0:
            self._line_count.setText("0 lines")
        else:
            self._line_count.setText(f"{n} line{'s' if n != 1 else ''}")

    def _save(self) -> None:
        text = self._editor.toPlainText()
        if not text.strip():
            QMessageBox.warning(self, "Paste private key", "Paste some key content first.")
            return
        # Basic sanity: should contain a PEM-style header or SSH-format header.
        if not looks_like_private_key(text):
            reply = QMessageBox.question(
                self, "Paste private key",
                "This doesn't look like a recognizable private key format "
                "(expected '-----BEGIN … PRIVATE KEY-----' or an OpenSSH key).\n\n"
                "Save anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.result_text = text
        self.accept()


# ----------------------------------------------------------------------
# Connect-to-server dialog
# ----------------------------------------------------------------------

class ConnectionDialog(QDialog):
    """Modern frameless connect dialog with custom title bar."""

    def __init__(self, profiles: list[SSHProfile], aliases: list[SSHAlias], parent=None, store=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to SSH Server")
        self.profiles = list(profiles)
        self.aliases = list(aliases)
        self.store = store
        self.result_profile: SSHProfile | None = None
        self._extra_config_paths: list[Path] = []
        # Track the in-memory pasted key content separately from the file path
        # displayed in the form. When the user pastes a key, we keep the content
        # here, write it to a 0600 temp file, and use that path as the
        # ``private_key`` field of the resulting SSHProfile — so the existing
        # asyncssh-based client code needs no changes.
        self._pasted_key_text: str | None = None
        self._pasted_key_path: Path | None = None
        # Size for the monitor we will appear on (DPI / small laptop safe)
        ideal = QSize(940, 620)
        fitted = fit_dialog_size_for_screen(self, ideal, min_size=QSize(640, 480))
        self.setMinimumSize(QSize(min(640, fitted.width()), min(480, fitted.height())))
        self.resize(fitted)
        self._build()
        self._populate_list()

    def _build(self) -> None:
        self.setStyleSheet(self._dialog_qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)

        # ── Left rail ──
        left = QVBoxLayout()
        left.setSpacing(8)
        left_head = QHBoxLayout()
        left_label = QLabel("CONNECTIONS")
        left_label.setObjectName("railLabel")
        left_head.addWidget(left_label)
        left_head.addStretch()
        left.addLayout(left_head)

        self.list = QTreeWidget()
        self.list.setHeaderHidden(True)
        self.list.setMinimumWidth(260)
        self.list.setMaximumWidth(290)
        self.list.setIndentation(14)
        self.list.setRootIsDecorated(True)
        self.list.setAnimated(False)
        self.list.setUniformRowHeights(True)
        self.list.setExpandsOnDoubleClick(False)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setObjectName("connTree")
        left.addWidget(self.list, 1)

        left_btns = QHBoxLayout()
        left_btns.setSpacing(6)
        self.new_conn_btn = QPushButton("New")
        self.new_conn_btn.setObjectName("ghostBtn")
        self.new_conn_btn.setToolTip("Start a blank connection")
        self.new_conn_btn.clicked.connect(self._start_new_connection)
        self.load_config_btn = QPushButton("Config…")
        self.load_config_btn.setObjectName("ghostBtn")
        self.load_config_btn.setToolTip("Load an extra SSH config file")
        self.load_config_btn.clicked.connect(self._load_extra_config)
        self.delete_profile_btn = QPushButton("Delete")
        self.delete_profile_btn.setObjectName("ghostBtnDanger")
        self.delete_profile_btn.setVisible(False)
        self.delete_profile_btn.clicked.connect(self._delete_selected_profile)
        left_btns.addWidget(self.new_conn_btn)
        left_btns.addWidget(self.load_config_btn)
        left_btns.addWidget(self.delete_profile_btn)
        left.addLayout(left_btns)
        body.addLayout(left)

        # ── Right form ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("formScroll")

        right = QWidget()
        form = QVBoxLayout(right)
        form.setContentsMargins(4, 0, 8, 0)
        form.setSpacing(12)

        hero = QLabel("Server details")
        hero.setObjectName("formHero")
        form.addWidget(hero)
        sub = QLabel(
            "Pick a saved profile or SSH config alias, or fill the form. "
            "Type an alias and press Diagnose to auto-complete host, user, port and key."
        )
        sub.setWordWrap(True)
        sub.setObjectName("formSub")
        form.addWidget(sub)

        def _row(widget, tip: str):
            h = QHBoxLayout()
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(widget, 1)
            h.addWidget(_field_help(tip))
            return h

        # ── Hero banner (visual polish) ──
        # Replace the plain "Server details" hero with a richer banner that
        # includes a small icon and a gradient accent strip. The banner
        # lives above the form so it sets the visual tone for the dialog.
        hero_frame = QFrame()
        hero_frame.setObjectName("heroBanner")
        hero_layout = QHBoxLayout(hero_frame)
        hero_layout.setContentsMargins(16, 12, 16, 12)
        hero_layout.setSpacing(12)
        hero_icon = QLabel("🔐")
        hero_icon.setStyleSheet("font-size:24px;")
        hero_layout.addWidget(hero_icon)
        hero_text = QVBoxLayout()
        hero_text.setSpacing(2)
        hero_title = QLabel("Connect to SSH Server")
        hero_title.setObjectName("formHero")
        hero_text.addWidget(hero_title)
        hero_sub = QLabel(
            "Pick a saved profile or SSH config alias, or fill the form. "
            "Type an alias and press Diagnose to auto-complete host, user, port and key."
        )
        hero_sub.setWordWrap(True)
        hero_sub.setObjectName("formSub")
        hero_text.addWidget(hero_sub)
        hero_layout.addLayout(hero_text, 1)
        form.addWidget(hero_frame)

        def _row(widget, tip: str):
            h = QHBoxLayout()
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(widget, 1)
            h.addWidget(_field_help(tip))
            return h

        # Basic card
        basic_card = QFrame()
        basic_card.setObjectName("card")
        basic_layout = QFormLayout(basic_card)
        basic_layout.setContentsMargins(16, 14, 16, 14)
        basic_layout.setSpacing(10)
        basic_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.alias = QComboBox()
        self.alias.setEditable(True)
        self.alias.setPlaceholderText("e.g. vps — then Diagnose")
        self.alias.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

        self.diagnose_btn = QPushButton("Diagnose")
        self.diagnose_btn.setObjectName("secondaryBtn")
        self.diagnose_btn.setToolTip("Fill host / user / port / key from SSH config for this alias")
        self.diagnose_btn.setMinimumWidth(100)
        self.diagnose_btn.clicked.connect(self._diagnose_alias)
        alias_row = QHBoxLayout()
        alias_row.setContentsMargins(0, 0, 0, 0)
        alias_row.addWidget(self.alias, 1)
        alias_row.addWidget(self.diagnose_btn)
        alias_row.addWidget(_field_help("OpenSSH Host alias from your config."))

        self.host = QLineEdit()
        self.host.setPlaceholderText("hostname or IP")
        self.user = QLineEdit()
        self.user.setPlaceholderText("username")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(22)

        basic_layout.addRow("SSH alias", alias_row)
        basic_layout.addRow("Host", _row(self.host, "Required if no alias resolves."))
        basic_layout.addRow("Username", _row(self.user, "Remote SSH user."))
        port_row = QHBoxLayout()
        port_row.addWidget(self.port)
        port_row.addWidget(_field_help("Default 22"))
        port_row.addStretch()
        basic_layout.addRow("Port", port_row)
        form.addWidget(basic_card)

        # ── Auth card (modernized) ──
        # Adds:
        #   • _SecretLineEdit for password + passphrase (eye toggle, auto-hide 30s)
        #   • "Paste…" button next to "Browse…" for private key
        #   • "Preview" button that opens the SecureKeyPreviewDialog
        #   • A live "key status chip" showing the current key source
        auth_card = QFrame()
        auth_card.setObjectName("card")
        auth_layout = QFormLayout(auth_card)
        auth_layout.setContentsMargins(16, 14, 16, 14)
        auth_layout.setSpacing(10)

        self.auth_method = QComboBox()
        self.auth_method.addItems([m.value.title() for m in AuthMethod])

        self.key = QLineEdit()
        self.key.setPlaceholderText("Path to private key (optional)")
        self.browse_key_btn = QPushButton("Browse…")
        self.browse_key_btn.setObjectName("secondaryBtn")
        self.browse_key_btn.setMinimumWidth(88)
        self.browse_key_btn.clicked.connect(self._browse_private_key)
        self.paste_key_btn = QPushButton("Paste…")
        self.paste_key_btn.setObjectName("secondaryBtn")
        self.paste_key_btn.setMinimumWidth(78)
        self.paste_key_btn.setToolTip(
            "Paste a private key directly. The content is kept in memory "
            "and written to a 0600-permission temp file; the main form "
            "never displays the content."
        )
        self.paste_key_btn.clicked.connect(self._paste_private_key)
        self.preview_key_btn = QPushButton("Preview")
        self.preview_key_btn.setObjectName("secondaryBtn")
        self.preview_key_btn.setMinimumWidth(82)
        self.preview_key_btn.setToolTip(
            "Open a separate, screenshot-protected window to inspect the "
            "private key contents."
        )
        self.preview_key_btn.clicked.connect(self._preview_private_key)
        self.clear_key_btn = QPushButton("Clear")
        self.clear_key_btn.setObjectName("ghostBtn")
        self.clear_key_btn.setMinimumWidth(66)
        self.clear_key_btn.setToolTip("Remove the current key (file path or pasted content).")
        self.clear_key_btn.clicked.connect(self._clear_private_key)

        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(6)
        key_row.addWidget(self.key, 1)
        key_row.addWidget(self.browse_key_btn)
        key_row.addWidget(self.paste_key_btn)
        key_row.addWidget(self.preview_key_btn)
        key_row.addWidget(self.clear_key_btn)
        key_row.addWidget(_field_help("Browse for a key file, paste content directly, or Preview the current key."))

        # Key status chip — gives the user immediate feedback about the
        # currently selected key without showing the content.
        self.key_status = QLabel("No key set")
        self.key_status.setObjectName("keyStatusChip")
        self._update_key_status()

        # Use _SecretLineEdit for passphrase + password so they get the
        # show/hide eye toggle with auto-hide.
        self.passphrase = _SecretLineEdit(placeholder="Key passphrase if any")
        self.password = _SecretLineEdit(placeholder="Password (if not using key/agent)")
        self.agent = QCheckBox("Use SSH Agent")
        self.agent.setChecked(True)
        self.remember = QCheckBox("Remember secrets in OS keyring")

        auth_layout.addRow("Authentication", self.auth_method)
        auth_layout.addRow("Private key", key_row)
        auth_layout.addRow("", self.key_status)
        auth_layout.addRow("Passphrase", _row(self.passphrase, "Only if the key is encrypted."))
        auth_layout.addRow("Password", _row(self.password, "Password auth when not using a key."))
        auth_layout.addRow("", self.agent)
        auth_layout.addRow("", self.remember)
        form.addWidget(auth_card)

        # Advanced
        self.adv_toggle = QToolButton()
        self.adv_toggle.setText("▸  Advanced options")
        self.adv_toggle.setCheckable(True)
        self.adv_toggle.setObjectName("advToggle")
        self.adv_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.adv_toggle.toggled.connect(self._toggle_advanced)
        form.addWidget(self.adv_toggle)

        self.advanced_frame = QFrame()
        self.advanced_frame.setObjectName("cardMuted")
        self.advanced_frame.setVisible(False)
        adv_layout = QFormLayout(self.advanced_frame)
        adv_layout.setContentsMargins(16, 14, 16, 14)
        adv_layout.setSpacing(10)

        self.timeout = QSpinBox(); self.timeout.setRange(2, 120); self.timeout.setValue(10)
        self.keepalive = QSpinBox(); self.keepalive.setRange(0, 600); self.keepalive.setValue(30)
        self.retries = QSpinBox(); self.retries.setRange(0, 50); self.retries.setValue(5)
        self.retry_delay = QSpinBox(); self.retry_delay.setRange(1, 300); self.retry_delay.setValue(2)
        self.auto_reconnect = QCheckBox("Auto reconnect on drop")
        self.auto_reconnect.setChecked(True)
        self.proxy_jump = QLineEdit()
        self.proxy_jump.setPlaceholderText("bastion or user@bastion:22 (ProxyJump)")
        self.forward_agent_adv = QCheckBox("Forward SSH agent (ForwardAgent)")
        self.forward_agent_adv.setChecked(False)

        def _spin_row(spin, tip):
            h = QHBoxLayout(); h.addWidget(spin); h.addWidget(_field_help(tip)); h.addStretch(); return h

        adv_layout.addRow("Timeout (s)", _spin_row(self.timeout, "Handshake timeout."))
        adv_layout.addRow("Keepalive (s)", _spin_row(self.keepalive, "0 disables."))
        adv_layout.addRow("Max retries", _spin_row(self.retries, "After drop."))
        adv_layout.addRow("Retry delay (s)", _spin_row(self.retry_delay, "Between retries."))
        adv_layout.addRow("", self.auto_reconnect)
        adv_layout.addRow("ProxyJump", _row(self.proxy_jump, "Jump host from SSH config (ProxyJump)."))
        adv_layout.addRow("", self.forward_agent_adv)
        form.addWidget(self.advanced_frame)
        form.addStretch(1)

        scroll.setWidget(right)
        body.addWidget(scroll, 1)
        root.addLayout(body, 1)

        # Footer
        footer = QFrame()
        footer.setObjectName("connectFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(16, 10, 16, 12)
        self.status_hint = QLabel("")
        self.status_hint.setObjectName("statusHint")
        fl.addWidget(self.status_hint, 1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghostBtn")
        cancel.setMinimumWidth(100)
        cancel.clicked.connect(self.reject)
        connect_btn = QPushButton("Connect")
        connect_btn.setObjectName("primaryBtn")
        connect_btn.setDefault(True)
        connect_btn.setMinimumWidth(120)
        connect_btn.clicked.connect(self._accept)
        fl.addWidget(cancel)
        fl.addWidget(connect_btn)
        root.addWidget(footer)

        self.alias.currentTextChanged.connect(self._alias_changed)
        self.list.currentItemChanged.connect(self._profile_selected)
        self.list.itemDoubleClicked.connect(self._on_list_double_clicked)

    @staticmethod
    def _dialog_qss() -> str:
        return """
        QDialog { background: #0f1318; }
        #railLabel {
            color: #66717f; font-size: 10px; font-weight: 700; letter-spacing: 0.8px;
        }
        #connTree {
            background: #0e1218; border: 1px solid #252c36; border-radius: 10px;
            padding: 6px; outline: 0;
        }
        #connTree::item { padding: 6px 8px; border-radius: 6px; }
        #connTree::item:selected { background: #1d6fe8; color: #fff; }
        #connTree::item:hover { background: #1a222c; }

        /* Hero banner — gradient accent strip + icon for visual polish */
        #heroBanner {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #15243a, stop:1 #1a2230);
            border: 1px solid #243a5c;
            border-radius: 12px;
        }
        #formHero { font-size: 18px; font-weight: 700; color: #f1f5f9; }
        #formSub { color: #9fb0c3; font-size: 12px; }

        #card, #cardMuted {
            background: #151a21; border: 1px solid #252c36; border-radius: 12px;
        }
        #cardMuted { background: #10151b; }
        #ghostBtn, #ghostBtnDanger, #secondaryBtn {
            background: #1a222c; border: 1px solid #2c3542; border-radius: 8px;
            color: #dbe3ee; padding: 6px 12px; font-weight: 600;
        }
        #ghostBtn:hover, #secondaryBtn:hover { background: #222b36; border-color: #3b4656; }
        #ghostBtnDanger { color: #ffb4b9; }
        #ghostBtnDanger:hover { background: #2a181c; border-color: #5a3036; }
        #primaryBtn {
            background: #1d6fe8; border: 1px solid #1a62ce; border-radius: 8px;
            color: #fff; padding: 8px 18px; font-weight: 700;
        }
        #primaryBtn:hover { background: #2b7cf0; }
        #advToggle {
            background: transparent; border: 0; color: #8ab4ff;
            font-weight: 600; padding: 2px 0; font-size: 12px;
        }
        #advToggle:hover { color: #b3d0ff; }
        #connectFooter {
            background: #0c1015; border-top: 1px solid #252c36;
            border-radius: 8px;
        }
        #statusHint { color: #8a95a4; font-size: 11px; }
        QLineEdit, QSpinBox, QComboBox {
            background: #0e1218; border: 1px solid #2a3340; border-radius: 8px;
            padding: 7px 10px; color: #e8eef6; min-height: 18px;
        }
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #3b82f6; }
        QCheckBox { color: #c5d0de; spacing: 8px; }

        /* Eye toggle button inside _SecretLineEdit */
        QToolButton#eyeToggle {
            background: transparent; border: 0; border-radius: 4px;
            padding: 0; margin: 0 4px 0 0;
        }
        QToolButton#eyeToggle:hover { background: #1d2530; }

        /* Key status chip — gives feedback without revealing content */
        #keyStatusChip {
            background: #10151b; border: 1px solid #2a3340; border-radius: 10px;
            color: #8a95a4; font-size: 11px; font-weight: 600;
            padding: 5px 12px;
        }
        #keyStatusChip[state="file"] {
            background: #0d2818; border-color: #1d5a36; color: #41d17d;
        }
        #keyStatusChip[state="pasted"] {
            background: #0d2230; border-color: #1d4f6e; color: #5fb8ff;
        }
        #keyStatusChip[state="encrypted"] {
            background: #2a181c; border-color: #5a3036; color: #ff939b;
        }
        """

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # First show: place on the right monitor and keep above taskbar
        if not getattr(self, "_placed_once", False):
            self._placed_once = True
            place_dialog_on_best_screen(self)
        else:
            clamp_window_to_screens(self)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        # After restore from minimize, clamp again
        try:
            from PySide6.QtCore import QEvent
            if event.type() == QEvent.Type.WindowStateChange and not self.isMinimized():
                clamp_window_to_screens(self)
        except Exception:
            pass

    def _toggle_advanced(self, checked: bool):
        self.advanced_frame.setVisible(checked)
        self.adv_toggle.setText("▾  Advanced options" if checked else "▸  Advanced options")

    def _icon(self, name: str, color: str = "#c5d0de"):
        try:
            import qtawesome as qta
            return qta.icon(name, color=color)
        except Exception:
            return QIcon()

    def _clear_form(self) -> None:
        self.alias.blockSignals(True)
        self.alias.setCurrentText("")
        self.alias.blockSignals(False)
        self.host.clear()
        self.user.clear()
        self.port.setValue(22)
        self.key.clear()
        # Clear any pasted-key state as well — the temp file is left alone
        # (the user may paste again later); we only forget the in-memory
        # reference + path association.
        self._pasted_key_text = None
        self._pasted_key_path = None
        # _SecretLineEdit exposes clear_secret() which also re-masks the field.
        if hasattr(self.passphrase, "clear_secret"):
            self.passphrase.clear_secret()
        else:
            self.passphrase.clear()
        if hasattr(self.password, "clear_secret"):
            self.password.clear_secret()
        else:
            self.password.clear()
        self.agent.setChecked(True)
        self.auth_method.setCurrentIndex(0)
        self.timeout.setValue(10)
        self.keepalive.setValue(30)
        self.retries.setValue(5)
        self.retry_delay.setValue(2)
        self.auto_reconnect.setChecked(True)
        if hasattr(self, "proxy_jump"):
            self.proxy_jump.clear()
        if hasattr(self, "forward_agent_adv"):
            self.forward_agent_adv.setChecked(False)
        self.remember.setChecked(False)
        if hasattr(self, "_update_key_status"):
            self._update_key_status()
        self.status_hint.setText("New connection — fill in details or pick an alias.")

    def _start_new_connection(self) -> None:
        self.list.blockSignals(True)
        self.list.clearSelection()
        for i in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
            if isinstance(data, dict) and data.get("type") == "new":
                self.list.setCurrentItem(item)
                break
        self.list.blockSignals(False)
        self.delete_profile_btn.setVisible(False)
        self._clear_form()

    def _browse_private_key(self) -> None:
        start = str(Path.home() / ".ssh")
        path, _ = QFileDialog.getOpenFileName(
            self, "Select private key", start,
            "Private key files (id_rsa id_ed25519 id_ecdsa id_dsa *);;All files (*)",
        )
        if path:
            # Browsing for a file clears any previously pasted content.
            self._pasted_key_text = None
            self._pasted_key_path = None
            self.key.setText(path)
            for i in range(self.auth_method.count()):
                if "private" in self.auth_method.itemText(i).lower():
                    self.auth_method.setCurrentIndex(i)
                    break
            self._update_key_status()

    def _paste_private_key(self) -> None:
        """Open the paste-key dialog and store the result in memory."""
        dlg = PastePrivateKeyDialog(self, existing=self._pasted_key_text or "")
        if dlg.exec() == PastePrivateKeyDialog.DialogCode.Accepted and dlg.result_text:
            self._pasted_key_text = dlg.result_text
            # Write to a 0600 temp file so the existing SSH client code can
            # consume it as a path. We do this eagerly (rather than at
            # connect time) so the Preview button can re-read the file.
            try:
                self._pasted_key_path = write_pasted_key_to_temp(
                    dlg.result_text,
                    label=self.alias.currentText().strip() or "pasted",
                )
                self.key.setText(str(self._pasted_key_path))
            except OSError as exc:
                QMessageBox.warning(
                    self, "Paste private key",
                    f"Could not save pasted key to a temp file:\n{exc}",
                )
                return
            # Switch auth method to private_key so the user understands
            # this key will actually be used.
            for i in range(self.auth_method.count()):
                if "private" in self.auth_method.itemText(i).lower():
                    self.auth_method.setCurrentIndex(i)
                    break
            self._update_key_status()
            self.status_hint.setText(
                f"Private key pasted ({dlg.result_text.count(chr(10)) + 1} lines) — "
                "use Preview to inspect it in a secure window."
            )

    def _preview_private_key(self) -> None:
        """Open the screenshot-protected preview window for the current key."""
        text = self._current_key_text()
        if not text:
            QMessageBox.information(
                self, "Private key preview",
                "No private key is currently set. Browse for a file or "
                "paste content first.",
            )
            return
        # Source label for the preview header.
        if self._pasted_key_path and self.key.text().strip() == str(self._pasted_key_path):
            src = "pasted key"
        else:
            src = f"file: {Path(self.key.text().strip()).name}"
        dlg = SecureKeyPreviewDialog(text, source_label=src, parent=self)
        dlg.exec()

    def _clear_private_key(self) -> None:
        """Clear both the file path and any pasted content."""
        self._pasted_key_text = None
        # Best-effort: delete the temp file we created for the pasted key.
        if self._pasted_key_path is not None:
            try:
                self._pasted_key_path.unlink()
            except OSError:
                pass
            self._pasted_key_path = None
        self.key.clear()
        self._update_key_status()
        self.status_hint.setText("Private key cleared.")

    def _current_key_text(self) -> str:
        """Return the textual content of the current private key, or "".

        Used by the Preview button. Reads from the in-memory pasted
        content if available (faster + works even if the temp file was
        removed), otherwise falls back to reading the file path.
        """
        if self._pasted_key_text is not None:
            return self._pasted_key_text
        path_str = self.key.text().strip()
        if not path_str:
            return ""
        try:
            with open(path_str, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError as exc:
            return f"(could not read key file: {exc})"

    def _update_key_status(self) -> None:
        """Refresh the key status chip based on the current form state.

        The chip uses one of three states (rendered via QSS attribute
        selector on ``[state="…"]``):

          * ``"file"``      — green — key path set, looks like a real file
          * ``"pasted"``    — blue  — key content was pasted in this session
          * ``"encrypted"`` — red   — key file looks encrypted (heuristic only)
          * default (no state) — grey — no key set
        """
        chip = getattr(self, "key_status", None)
        if chip is None:
            return
        path_str = self.key.text().strip()
        if not path_str and self._pasted_key_text is None:
            chip.setText("◉  No key set")
            chip.setProperty("state", "")
        elif self._pasted_key_text is not None and self._pasted_key_path is not None:
            n = self._pasted_key_text.count("\n") + 1
            chip.setText(f"✓  Pasted key  ·  {n} lines  ·  stored as 0600 temp file")
            chip.setProperty("state", "pasted")
        elif path_str:
            # Try a quick file-existence + encryption sniff.
            from app.ssh.client import _looks_like_encrypted_openssh_key
            try:
                p = Path(path_str)
                if p.is_file():
                    if _looks_like_encrypted_openssh_key(path_str):
                        chip.setText(f"🔒  Encrypted OpenSSH key  ·  {p.name}")
                        chip.setProperty("state", "encrypted")
                    else:
                        chip.setText(f"✓  Key file  ·  {p.name}")
                        chip.setProperty("state", "file")
                else:
                    chip.setText(f"⚠  Path not found  ·  {p.name}")
                    chip.setProperty("state", "")
            except Exception:
                chip.setText(f"✓  Key file  ·  {path_str}")
                chip.setProperty("state", "file")
        else:
            chip.setText("◉  No key set")
            chip.setProperty("state", "")
        # Force a style refresh so the QSS [state="…"] selector re-applies.
        chip.style().unpolish(chip)
        chip.style().polish(chip)
        chip.update()

    def _load_extra_config(self) -> None:
        from app.ssh.discovery import discover_aliases, ssh_config_candidates
        start = str(Path.home() / ".ssh")
        path, _ = QFileDialog.getOpenFileName(
            self, "Open SSH config file", start, "SSH config (config *);;All files (*)",
        )
        if not path:
            return
        p = Path(path)
        if not p.is_file():
            QMessageBox.warning(self, "SSH config", "Selected path is not a file.")
            return
        if p not in self._extra_config_paths:
            self._extra_config_paths.append(p)
        paths = list(ssh_config_candidates()) + list(self._extra_config_paths)
        seen = set()
        unique = []
        for x in paths:
            try:
                key = x.resolve()
            except OSError:
                key = x
            if key not in seen:
                seen.add(key)
                unique.append(x)
        self.aliases = discover_aliases(unique)
        self._populate_list()
        self.status_hint.setText(f"Loaded config: {p.name}  ·  {len(self.aliases)} alias(es)")

    def _diagnose_alias(self) -> None:
        name = self.alias.currentText().strip()
        if not name:
            self.status_hint.setText("Type an alias name first, then Diagnose.")
            return

        # Try to match against discovered SSH config aliases (case-insensitive).
        alias = next((a for a in self.aliases if a.alias == name), None)
        if not alias:
            alias = next((a for a in self.aliases if a.alias.lower() == name.lower()), None)

        if alias:
            # Found in SSH config — load host/user/port/key from it.
            self._load_alias(alias)
            parts = [f"host={alias.host}", f"port={alias.port}"]
            if alias.user:
                parts.insert(0, f"user={alias.user}")
            if alias.identity_files:
                parts.append(f"key={alias.identity_files[0]}")
            src = f"  ({alias.source_file})" if getattr(alias, "source_file", "") else ""
            self.status_hint.setText(f"Diagnosed «{alias.alias}»: " + ", ".join(parts) + src)
            return

        # ----------------------------------------------------------------
        # Fallback: alias not in SSH config → treat the text as a direct
        # hostname / IP. Parse "user@host:port" so the user can type a
        # full target string in the alias field and we'll split it across
        # the right form fields automatically.
        # ----------------------------------------------------------------
        host, parsed_user, parsed_port = parse_ssh_target(name)
        if not host:
            # parse_ssh_target returns the input as-is when it can't parse
            # — still use it as the hostname so the user can connect.
            host = name

        self.host.setText(host)
        if parsed_user and not self.user.text().strip():
            self.user.setText(parsed_user)
        if parsed_port and parsed_port != 22:
            self.port.setValue(parsed_port)

        # Clear the alias field so _accept() doesn't pass it to asyncssh
        # as a config-lookup target (which would fail again). The original
        # text is preserved as the profile name via the status hint.
        self.alias.blockSignals(True)
        self.alias.setCurrentText("")
        self.alias.blockSignals(False)

        # Build a helpful status message that explains the fallback.
        detail_parts = [f"host={host}"]
        if parsed_user:
            detail_parts.insert(0, f"user={parsed_user}")
        if parsed_port and parsed_port != 22:
            detail_parts.append(f"port={parsed_port}")
        self.status_hint.setText(
            f"«{name}» not found in SSH config — treating it as a direct "
            "hostname/IP (" + ", ".join(detail_parts) + "). "
            "Adjust user/port/key if needed, then Connect."
        )

    def _populate_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self.alias.blockSignals(True)
        self.alias.clear()

        ic_new = self._icon("fa5s.plus-circle", "#74a7ff")
        ic_folder = self._icon("fa5s.folder", "#8a95a4")
        ic_folder_open = self._icon("fa5s.folder-open", "#74a7ff")
        ic_ssh = self._icon("fa5s.terminal", "#9fb0c3")
        ic_saved = self._icon("fa5s.bookmark", "#74a7ff")

        new_item = QTreeWidgetItem(["＋  New connection"])
        new_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "new"})
        new_item.setIcon(0, ic_new)
        new_item.setToolTip(0, "Clear the form and start a blank connection")
        self.list.addTopLevelItem(new_item)

        if self.aliases:
            cfg_root = QTreeWidgetItem([f"SSH config  ({len(self.aliases)})"])
            cfg_root.setData(0, Qt.ItemDataRole.UserRole, {"type": "group"})
            cfg_root.setIcon(0, ic_folder_open)
            cfg_root.setFlags(cfg_root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.list.addTopLevelItem(cfg_root)
            for alias in self.aliases:
                label = f"{alias.alias}  ·  {(alias.user + '@') if alias.user else ''}{alias.host}:{alias.port}"
                child = QTreeWidgetItem([label])
                child.setData(0, Qt.ItemDataRole.UserRole, {"type": "ssh_config", "id": alias.alias})
                child.setIcon(0, ic_ssh)
                tip = f"From SSH config\n{(alias.user or '')}@{alias.host}:{alias.port}"
                if getattr(alias, "source_file", ""):
                    tip += f"\n{alias.source_file}"
                child.setToolTip(0, tip)
                cfg_root.addChild(child)
                self.alias.addItem(alias.alias)
            cfg_root.setExpanded(True)
        else:
            empty = QTreeWidgetItem(["No aliases found — try Config…"])
            empty.setData(0, Qt.ItemDataRole.UserRole, {"type": "group"})
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.list.addTopLevelItem(empty)

        if self.profiles:
            saved_root = QTreeWidgetItem([f"Saved profiles  ({len(self.profiles)})"])
            saved_root.setData(0, Qt.ItemDataRole.UserRole, {"type": "group"})
            saved_root.setIcon(0, ic_folder_open)
            saved_root.setFlags(saved_root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.list.addTopLevelItem(saved_root)
            for profile in self.profiles:
                label = profile.name or profile.host or profile.id
                child = QTreeWidgetItem([label])
                child.setData(0, Qt.ItemDataRole.UserRole, {"type": "saved", "id": profile.id})
                child.setIcon(0, ic_saved)
                child.setToolTip(0, f"{profile.username}@{profile.host}:{profile.port}")
                saved_root.addChild(child)
            saved_root.setExpanded(True)

        self.list.blockSignals(False)
        self.alias.blockSignals(False)
        self.delete_profile_btn.setVisible(False)
        self._ic_folder = ic_folder
        self._ic_folder_open = ic_folder_open
        if not getattr(self, "_tree_expand_hooks", False):
            self.list.itemExpanded.connect(self._on_group_expanded)
            self.list.itemCollapsed.connect(self._on_group_collapsed)
            self._tree_expand_hooks = True
        self.list.setCurrentItem(new_item)
        self._clear_form()

    def _on_group_expanded(self, item: QTreeWidgetItem):
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if isinstance(data, dict) and data.get("type") == "group":
            item.setIcon(0, getattr(self, "_ic_folder_open", self._icon("fa5s.folder-open", "#74a7ff")))

    def _on_group_collapsed(self, item: QTreeWidgetItem):
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if isinstance(data, dict) and data.get("type") == "group":
            item.setIcon(0, getattr(self, "_ic_folder", self._icon("fa5s.folder", "#8a95a4")))

    def _on_list_double_clicked(self, item, column: int = 0) -> None:
        """Double-click a saved profile or SSH alias → fill form and Connect."""
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        kind = data.get("type")
        if kind in {"group", "new"}:
            return  # folders / blank template — not a connect target
        # Ensure form is loaded from this item
        self._profile_selected(item, None)
        self._accept()

    def _profile_selected(self, current, previous=None) -> None:
        self.delete_profile_btn.setVisible(False)
        if current is None:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        kind = data.get("type")
        key = data.get("id")
        if kind == "group":
            return
        if kind == "new":
            self._clear_form()
            return
        if kind == "saved":
            profile = next((p for p in self.profiles if p.id == key), None)
            if profile:
                self._load_profile(profile)
                self.delete_profile_btn.setVisible(True)
            return
        if kind == "ssh_config":
            alias = next((a for a in self.aliases if a.alias == key), None)
            if alias:
                self._load_alias(alias)
                self.status_hint.setText(f"Loaded alias «{alias.alias}» from SSH config")

    def _alias_changed(self, value: str) -> None:
        alias = next((a for a in self.aliases if a.alias == value.strip()), None)
        if alias:
            self._load_alias(alias)

    def _load_alias(self, alias: SSHAlias) -> None:
        self.alias.blockSignals(True)
        self.alias.setCurrentText(alias.alias)
        self.alias.blockSignals(False)
        self.host.setText(alias.host or "")
        self.user.setText(alias.user or "")
        self.port.setValue(alias.port or 22)
        # Loading from SSH config resets the pasted-key state — the alias
        # points to a file on disk, not pasted content.
        self._pasted_key_text = None
        self._pasted_key_path = None
        self.key.setText(alias.identity_files[0] if alias.identity_files else "")
        self.agent.setChecked(True)
        if hasattr(self, "keepalive"):
            self.keepalive.setValue(alias.server_alive_interval or 30)
        if hasattr(self, "retries"):
            self.retries.setValue(max(0, alias.server_alive_count_max or 3))
        if hasattr(self, "proxy_jump"):
            self.proxy_jump.setText(getattr(alias, "proxy_jump", None) or "")
        if hasattr(self, "forward_agent_adv"):
            self.forward_agent_adv.setChecked(bool(getattr(alias, "forward_agent", False)))
        if hasattr(self, "_update_key_status"):
            self._update_key_status()

    def _load_profile(self, profile: SSHProfile) -> None:
        self.alias.blockSignals(True)
        self.alias.setCurrentText(profile.alias or "")
        self.alias.blockSignals(False)
        self.host.setText(profile.host or "")
        self.user.setText(profile.username or "")
        self.port.setValue(profile.port or 22)
        # Same as _load_alias: loading a saved profile means the key comes
        # from a file path on disk, not from a paste.
        self._pasted_key_text = None
        self._pasted_key_path = None
        key_path = profile.private_key or (profile.identity_files[0] if profile.identity_files else "")
        self.key.setText(key_path or "")
        try:
            method = profile.auth_method.value.title() if profile.auth_method else "Auto"
            idx = self.auth_method.findText(method, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self.auth_method.setCurrentIndex(idx)
        except Exception:
            pass
        self.timeout.setValue(int(profile.connect_timeout or 10))
        self.keepalive.setValue(profile.keepalive or 30)
        self.retries.setValue(profile.max_retries or 5)
        self.retry_delay.setValue(int(profile.retry_delay or 2))
        self.auto_reconnect.setChecked(bool(profile.auto_reconnect))
        self.remember.setChecked(bool(profile.persist_secrets))
        if hasattr(self, "proxy_jump"):
            self.proxy_jump.setText(getattr(profile, "proxy_jump", None) or "")
        if hasattr(self, "forward_agent_adv"):
            self.forward_agent_adv.setChecked(bool(getattr(profile, "forward_agent", False)))
        if hasattr(self, "_update_key_status"):
            self._update_key_status()
        self.status_hint.setText(f"Loaded saved profile «{profile.name}»")

    def _delete_selected_profile(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or data.get("type") != "saved":
            return
        key = data.get("id")
        profile = next((p for p in self.profiles if p.id == key), None)
        if not profile:
            return
        reply = QMessageBox.question(
            self, "Delete profile",
            f"Delete saved profile «{profile.name}»?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.profiles = [p for p in self.profiles if p.id != profile.id]
        if self.store is not None:
            try:
                self.store.save_profiles(self.profiles)
            except Exception:
                pass
        self._populate_list()
        self.delete_profile_btn.setVisible(False)

    def _accept(self) -> None:
        alias_text = self.alias.currentText().strip() or None
        host = self.host.text().strip()
        user = self.user.text().strip()

        # ----------------------------------------------------------------
        # Alias resolution + direct-hostname fallback.
        #
        # If the user typed something in the alias field, we first check
        # whether it matches a known SSH config alias. If it does, we keep
        # it as ``alias`` so asyncssh resolves it from the config files.
        #
        # If it does NOT match any alias, we treat the text as a direct
        # hostname/IP — parse "user@host:port" if present, copy the host
        # into the host field, and clear the alias so asyncssh connects
        # directly without trying to look the text up in SSH config.
        # The original text is preserved as the profile display name.
        # ----------------------------------------------------------------
        alias = None
        name = host or "SSH Server"

        if alias_text:
            if is_alias_resolvable(alias_text, self.aliases):
                # Real alias — asyncssh will resolve it from config.
                alias = alias_text
                name = alias_text
            else:
                # Not a known alias — parse and treat as direct hostname/IP.
                parsed_host, parsed_user, parsed_port = parse_ssh_target(alias_text)
                if not host:
                    host = parsed_host or alias_text
                if parsed_user and not user:
                    user = parsed_user
                    self.user.setText(user)
                if parsed_port and parsed_port != 22 and self.port.value() == 22:
                    self.port.setValue(parsed_port)
                # Use the original alias text as the display name, but do
                # NOT set ``alias`` — we don't want asyncssh to receive
                # ``config=[...]`` and try to look it up.
                name = alias_text
                # Reflect the parsed host in the form so the user sees
                # what will actually be connected to.
                if not self.host.text().strip():
                    self.host.setText(host)

        if not alias and not host:
            QMessageBox.warning(
                self, "Connect",
                "Enter a Host, or type an SSH alias / IP and click Connect.",
            )
            return

        # Look up an existing saved profile to reuse its id (so re-connecting
        # to the same server doesn't create duplicates).
        existing = next((p for p in self.profiles if p.alias == alias and alias), None)
        if existing is None and host:
            existing = next(
                (p for p in self.profiles if p.host == host and p.username == user),
                None,
            )
        profile_id = existing.id if existing else name.lower().replace(" ", "-")
        key_text = self.key.text().strip()
        method_key = self.auth_method.currentText().lower().replace(" ", "_")
        method_map = {"auto": "auto", "password": "password", "private_key": "private_key", "agent": "agent"}
        self.result_profile = SSHProfile(
            id=profile_id,
            name=existing.name if existing else name,
            alias=alias,
            host=host,
            username=user,
            port=self.port.value(),
            auth_method=AuthMethod(method_map.get(method_key, "auto")),
            private_key=key_text or None,
            identity_files=[key_text] if key_text else [],
            connect_timeout=float(self.timeout.value()),
            keepalive=self.keepalive.value(),
            auto_reconnect=self.auto_reconnect.isChecked(),
            max_retries=self.retries.value(),
            retry_delay=float(self.retry_delay.value()),
            persist_secrets=self.remember.isChecked(),
            proxy_jump=(self.proxy_jump.text().strip() or None) if hasattr(self, "proxy_jump") else None,
            forward_agent=bool(self.forward_agent_adv.isChecked()) if hasattr(self, "forward_agent_adv") else False,
        )

        # If we fell back to direct-hostname mode, surface a friendly note
        # in the status bar so the user understands what just happened.
        if alias_text and alias is None:
            self.status_hint.setText(
                f"Connecting directly to «{host}» "
                f"(«{alias_text}» was not an SSH config alias)."
            )

        self.accept()
