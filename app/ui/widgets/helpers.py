from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QApplication, QStyle, QWidget

try:
    import qtawesome as qta
except ImportError:
    qta = None


def _icon_color(role: str = "normal") -> str:
    theme = str(QApplication.instance().property("sshdockTheme") or "dark").lower() if QApplication.instance() else "dark"
    if role == "danger":
        return "#b91c1c" if theme == "light" else "#ff939b"
    if role == "accent":
        return "#ffffff"
    return "#475569" if theme == "light" else "#dbe7f5"


def icon_button(text: str, icon_name: str, callback, tooltip: str | None = None):
    b = QPushButton(text)
    b.setProperty("sshdockIconName", icon_name)
    b.setProperty("sshdockIconRole", "normal")
    b.setMinimumHeight(32)
    if qta is not None:
        candidates = [icon_name.replace("fa5s.", "fa6s."), icon_name]
        for candidate in candidates:
            try:
                icon = qta.icon(candidate, color=_icon_color(str(b.property("sshdockIconRole") or "normal")))
                if not icon.isNull():
                    b.setIcon(icon)
                    break
            except Exception:
                continue
    if tooltip:
        b.setToolTip(tooltip)
    b.clicked.connect(callback)
    return b


AUTO_REFRESH_OPTIONS = (
    ("Off", 0),
    ("2 seconds", 2),
    ("5 seconds", 5),
    ("10 seconds", 10),
    ("30 seconds", 30),
)


class AutoRefreshControl(QWidget):
    """Shared compact auto-refresh selector used by all live data tables."""

    intervalChanged = Signal(int)

    def __init__(self, default_seconds: int = 5, parent=None):
        super().__init__(parent)
        self.setObjectName("autoRefreshControl")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel("Auto refresh")
        label.setObjectName("autoRefreshLabel")
        layout.addWidget(label)
        self.combo = QComboBox()
        self.combo.setObjectName("autoRefreshCombo")
        for text, seconds in AUTO_REFRESH_OPTIONS:
            self.combo.addItem(text, seconds)
        wanted = max(0, int(default_seconds))
        index = next(
            (i for i, (_, seconds) in enumerate(AUTO_REFRESH_OPTIONS) if seconds == wanted),
            2,
        )
        self.combo.setCurrentIndex(index)
        self.combo.currentIndexChanged.connect(
            lambda _index: self.intervalChanged.emit(self.seconds)
        )
        layout.addWidget(self.combo)

    @property
    def seconds(self) -> int:
        return int(self.combo.currentData() or 0)

    def set_seconds(self, seconds: int) -> None:
        wanted = max(0, int(seconds))
        for index in range(self.combo.count()):
            if int(self.combo.itemData(index) or 0) == wanted:
                self.combo.setCurrentIndex(index)
                return


class TableAutoRefresh(QObject):
    """Timer wrapper which skips hidden tables and overlapping refresh jobs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.CoarseTimer)
        self.timer.timeout.connect(self._tick)
        self._callback = None
        self._allowed = None
        self._running = False

    def configure(self, seconds: int, callback, allowed=None) -> None:
        self.timer.stop()
        self._callback = callback
        self._allowed = allowed
        value = max(0, int(seconds))
        if value:
            self.timer.start(value * 1000)

    def stop(self) -> None:
        self.timer.stop()

    def _tick(self) -> None:
        if self._running or self._callback is None:
            return
        if self._allowed is not None:
            try:
                if not self._allowed():
                    return
            except Exception:
                return
        self._running = True
        try:
            task = self._callback()
            if hasattr(task, "add_done_callback"):
                task.add_done_callback(self._finished)
            else:
                self._running = False
        except Exception:
            self._running = False

    def _finished(self, _task) -> None:
        self._running = False
