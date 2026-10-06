from __future__ import annotations

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

try:
    import qtawesome as qta
except ImportError:
    qta = None


class NavButton(QFrame):
    """Sidebar nav item: icon + label left, optional shortcut right."""

    clicked = Signal()
    toggled = Signal(bool)

    def __init__(
        self,
        text: str,
        icon_name: str | None = None,
        shortcut: str | None = None,
        checkable: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("navBtnFrame")
        self._checkable = checkable
        self._checked = False
        self._collapsed = False
        self._text = text
        self._icon_name = icon_name
        self._shortcut_text = shortcut or ""
        self._theme = "dark"
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)
        self.setToolTipDuration(800)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 0, 8, 0)
        root.setSpacing(8)

        self._accent = QFrame()
        self._accent.setObjectName("navAccent")
        self._accent.setFixedWidth(3)
        self._accent.setFixedHeight(18)
        root.addWidget(self._accent)

        self._icon_lbl = QLabel()
        self._icon_lbl.setObjectName("navIcon")
        self._icon_lbl.setFixedSize(20, 20)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_lbl.setStyleSheet("background: transparent; border: 0;")
        self._icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        root.addWidget(self._icon_lbl)

        self._label = QLabel(text)
        self._label.setObjectName("navBtnLabel")
        self._label.setStyleSheet("background: transparent;")
        root.addWidget(self._label, 1)

        self._shortcut = QLabel(self._shortcut_text)
        self._shortcut.setObjectName("navShortcut")
        self._shortcut.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if not shortcut:
            self._shortcut.hide()
        root.addWidget(self._shortcut)

        self._apply_style()
        self.setToolTip(text if not shortcut else f"{text}  ({shortcut})")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._checkable:
                self.setChecked(True)
            self.clicked.emit()
        super().mousePressEvent(event)

    def setChecked(self, checked: bool):
        if not self._checkable:
            return
        changed = self._checked != checked
        self._checked = checked
        self._apply_style()
        if changed:
            self.toggled.emit(checked)

    def isChecked(self) -> bool:
        return self._checked

    def setText(self, text: str):
        self._text = text
        self._label.setText(text)

    def text(self) -> str:
        return self._text

    def setCollapsed(self, collapsed: bool):
        self._collapsed = collapsed
        self._label.setVisible(not collapsed)
        self._shortcut.setVisible(not collapsed and bool(self._shortcut_text))
        self._accent.setVisible(not collapsed)
        if collapsed:
            self.setToolTip(self._text if not self._shortcut_text else f"{self._text}  ({self._shortcut_text})")
            root = self.layout()
            if root:
                root.setContentsMargins(0, 0, 0, 0)
                root.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            root = self.layout()
            if root:
                root.setContentsMargins(8, 0, 8, 0)
                root.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._apply_style()

    def _paint_icon(self, color: str):
        if not (qta and self._icon_name):
            return
        try:
            self._icon_lbl.setPixmap(qta.icon(self._icon_name, color=color).pixmap(QSize(16, 16)))
        except Exception:
            pass

    def set_theme(self, theme: str) -> None:
        self._theme = "light" if str(theme).lower() == "light" else "dark"
        self._apply_style()

    def _apply_style(self):
        self._icon_lbl.setStyleSheet("background: transparent; border: 0;")
        light = self._theme == "light"
        if light and self._checked:
            label_color, shortcut_color, accent_color, icon_color = "#1e40af", "#64748b", "#3b82f6", "#2563eb"
        elif light:
            label_color, shortcut_color, accent_color, icon_color = "#334155", "#94a3b8", "transparent", "#64748b"
        elif self._checked:
            label_color, shortcut_color, accent_color, icon_color = "#e8f0ff", "#8aa8d8", "#3b82f6", "#c7dbff"
        else:
            label_color, shortcut_color, accent_color, icon_color = "#9ca7b5", "#5c6775", "transparent", "#9aa6b5"

        self.setProperty("checked", bool(self._checked))
        self._label.setStyleSheet(
            f"color:{label_color}; font-weight:{600 if self._checked else 500}; background:transparent;"
        )
        self._shortcut.setStyleSheet(
            f"color:{shortcut_color}; font-size:11px; font-family:'Segoe UI',monospace; background:transparent;"
        )
        self._accent.setStyleSheet(f"background:{accent_color}; border-radius:2px;")
        self._paint_icon(icon_color)
        self.style().unpolish(self)
        self.style().polish(self)
