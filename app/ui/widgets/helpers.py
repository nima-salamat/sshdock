from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QApplication, QStyle

try:
    import qtawesome as qta
except ImportError:
    qta = None


def icon_button(text: str, icon_name: str, callback, tooltip: str | None = None):
    b = QPushButton(text)
    b.setMinimumHeight(32)
    if qta is not None:
        candidates = [icon_name.replace("fa5s.", "fa6s."), icon_name]
        for candidate in candidates:
            try:
                icon = qta.icon(candidate)
                if not icon.isNull():
                    b.setIcon(icon)
                    break
            except Exception:
                continue
    if tooltip:
        b.setToolTip(tooltip)
    b.clicked.connect(callback)
    return b
