from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QPushButton, QApplication, QStyle

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
