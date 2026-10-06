"""In-app notifications: history bus + footer bell (no floating toast spam)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque

from PySide6.QtCore import QObject, Qt, QEvent, QPoint, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QToolButton, QVBoxLayout, QWidget, QApplication, QStyle,
)

try:
    import qtawesome as qta
except ImportError:
    qta = None


@dataclass
class NotificationItem:
    level: str
    title: str
    message: str
    at: datetime


class NotificationBus(QObject):
    notified = Signal(str, str, str)  # level, title, message

    _instance: "NotificationBus | None" = None

    def __init__(self) -> None:
        super().__init__()
        self.history: Deque[NotificationItem] = deque(maxlen=100)
        self.unread: int = 0

    @classmethod
    def instance(cls) -> "NotificationBus":
        if cls._instance is None:
            cls._instance = NotificationBus()
        return cls._instance

    def push(self, level: str, title: str, message: str = "") -> None:
        level = (level or "info").lower()
        item = NotificationItem(level, title or "", message or "", datetime.now())
        self.history.appendleft(item)
        self.unread = min(99, self.unread + 1)
        self.notified.emit(level, title or "", message or "")

    def info(self, title: str, message: str = "") -> None:
        self.push("info", title, message)

    def success(self, title: str, message: str = "") -> None:
        self.push("success", title, message)

    def warning(self, title: str, message: str = "") -> None:
        self.push("warning", title, message)

    def error(self, title: str, message: str = "") -> None:
        self.push("error", title, message)

    def clear_unread(self) -> None:
        self.unread = 0

    def clear_history(self) -> None:
        self.history.clear()
        self.unread = 0


# Back-compat: keep ToastHost name but make it a no-op host (bell replaces toasts)
class ToastHost(QWidget):
    """Deprecated floating toasts — kept so old imports don't break; does nothing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hide()

    def _on_notified(self, *_args) -> None:
        pass


class NotificationBell(QWidget):
    """Footer bell: badge + popup history list."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._popup: QFrame | None = None
        self._outside_filter_installed = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.btn = QToolButton()
        self.btn.setObjectName("notifyBell")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setToolTip("Notifications")
        self.btn.setAutoRaise(True)
        self.btn.setFixedSize(32, 28)
        try:
            if qta:
                self.btn.setIcon(qta.icon("fa5s.bell", color="#c5d0de"))
            else:
                self.btn.setIcon(QApplication.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        except Exception:
            self.btn.setText("🔔")
        self.btn.clicked.connect(self.toggle_popup)
        lay.addWidget(self.btn)

        self.badge = QLabel("")
        self.badge.setObjectName("notifyBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFixedHeight(16)
        self.badge.setMinimumWidth(16)
        self.badge.setStyleSheet(
            "QLabel#notifyBadge {"
            "  background:#e5484d; color:#fff; border-radius:8px;"
            "  font-size:10px; font-weight:700; padding:0 4px;"
            "}"
        )
        self.badge.hide()
        # Overlay badge on top-right of button via parent layout is awkward —
        # put badge next to button compactly
        lay.addWidget(self.badge)

        bus = NotificationBus.instance()
        bus.notified.connect(self._on_notified)
        self._refresh_badge()

    def _on_notified(self, level: str, title: str, message: str) -> None:
        self._refresh_badge()
        # If popup is open, refresh list
        if self._popup is not None and self._popup.isVisible():
            self._rebuild_list()

    def _refresh_badge(self) -> None:
        n = NotificationBus.instance().unread
        if n <= 0:
            self.badge.hide()
            self.badge.setText("")
        else:
            self.badge.setText(str(n) if n < 100 else "99+")
            self.badge.show()

    def toggle_popup(self) -> None:
        if self._popup is not None and self._popup.isVisible():
            self._popup.hide()
            return
        self._open_popup()

    def _open_popup(self) -> None:
        if self._popup is None:
            # Do not use Qt.Popup here: Qt.Popup auto-hides on the button click
            # that opened it, so clicking the bell a second time cannot toggle it
            # closed reliably. Tool behaves like a small floating panel instead.
            self._popup = QFrame(
                self.window(),
                Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint,
            )
            self._popup.setObjectName("notifyPopup")
            self._popup.setStyleSheet(
                "QFrame#notifyPopup {"
                "  background:#121820; border:1px solid #2a3340; border-radius:10px;"
                "}"
                "QListWidget { background:transparent; border:none; color:#e8eef6; outline:0; }"
                "QListWidget::item { padding:8px 10px; border-bottom:1px solid #1e252e; }"
                "QListWidget::item:selected { background:#1c2430; }"
                "QLabel#notifyTitle { font-weight:700; font-size:13px; color:#e8eef6; }"
                "QPushButton#notifyClear {"
                "  background:#161c24; border:1px solid #2a3340; border-radius:6px;"
                "  color:#c5d0de; padding:4px 10px;"
                "}"
                "QPushButton#notifyClear:hover { background:#1c2430; }"
            )
            self._popup.setFixedWidth(360)
            self._popup.setMinimumHeight(280)
            pl = QVBoxLayout(self._popup)
            pl.setContentsMargins(10, 10, 10, 10)
            pl.setSpacing(8)
            head = QHBoxLayout()
            title = QLabel("Notifications")
            title.setObjectName("notifyTitle")
            head.addWidget(title)
            head.addStretch()
            clear = QPushButton("Clear")
            clear.setObjectName("notifyClear")
            clear.clicked.connect(self._clear_all)
            head.addWidget(clear)
            pl.addLayout(head)
            self._list = QListWidget()
            self._list.setWordWrap(True)
            pl.addWidget(self._list, 1)
            empty = QLabel("No notifications yet")
            empty.setObjectName("muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color:#7a8694; padding:24px;")
            self._empty = empty
            pl.addWidget(empty)
            app = QApplication.instance()
            if app is not None and not self._outside_filter_installed:
                app.installEventFilter(self)
                self._outside_filter_installed = True

        NotificationBus.instance().clear_unread()
        self._refresh_badge()
        self._rebuild_list()

        # Position above the bell, aligned to the right
        self._popup.adjustSize()
        global_pos = self.btn.mapToGlobal(QPoint(0, 0))
        x = global_pos.x() + self.btn.width() - self._popup.width()
        y = global_pos.y() - self._popup.height() - 6
        if x < 8:
            x = 8
        if y < 8:
            y = global_pos.y() + self.btn.height() + 6
        self._popup.move(x, y)
        self._popup.show()
        self._popup.raise_()

    def eventFilter(self, obj, event) -> bool:
        if (
            self._popup is not None
            and self._popup.isVisible()
            and event.type() == QEvent.Type.MouseButtonPress
        ):
            pos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else None
            if pos is not None and not self._popup.geometry().contains(pos) and not self.btn.geometry().contains(self.btn.mapFromGlobal(pos)):
                self._popup.hide()
        return super().eventFilter(obj, event)

    def _rebuild_list(self) -> None:
        if not hasattr(self, "_list"):
            return
        self._list.clear()
        items = list(NotificationBus.instance().history)
        self._empty.setVisible(len(items) == 0)
        self._list.setVisible(len(items) > 0)
        colors = {
            "success": "#39d47a",
            "error": "#ff6b6b",
            "warning": "#ffd866",
            "info": "#74a7ff",
        }
        for item in items:
            when = item.at.strftime("%H:%M:%S")
            color = colors.get(item.level, "#9aa6b5")
            body = item.message.strip()
            text = f"●  {item.title}"
            if body:
                text += f"\n{body}"
            text += f"\n{when}"
            li = QListWidgetItem(text)
            li.setToolTip(f"{item.level}: {item.title}\n{item.message}")
            # store color via foreground
            from PySide6.QtGui import QColor, QBrush
            li.setForeground(QBrush(QColor(color)))
            self._list.addItem(li)

    def _clear_all(self) -> None:
        NotificationBus.instance().clear_history()
        self._refresh_badge()
        self._rebuild_list()
