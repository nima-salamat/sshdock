"""Browser-style multi-tab job dialog for bulk Docker/Compose actions."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTabWidget,
    QVBoxLayout, QWidget,
)


class MultiTabJobDialog(QDialog):
    """One non-modal window with a tab per target (project/container)."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 560)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        self.status = QLabel(title)
        self.status.setObjectName("muted")
        lay.addWidget(self.status)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(False)
        self.tabs.setMovable(True)
        lay.addWidget(self.tabs, 1)
        row = QHBoxLayout()
        row.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.close)
        row.addWidget(self.close_btn)
        lay.addLayout(row)
        self._views: dict[str, QPlainTextEdit] = {}
        self._done = 0
        self._total = 0

    def add_tab(self, name: str) -> QPlainTextEdit:
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        view.setFont(mono)
        pl.addWidget(view)
        self.tabs.addTab(page, name)
        self._views[name] = view
        self._total += 1
        return view

    def append(self, name: str, text: str) -> None:
        view = self._views.get(name)
        if view is None:
            return
        view.appendPlainText(text)
        cur = view.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        view.setTextCursor(cur)

    def mark_tab_done(self, name: str, ok: bool = True) -> None:
        idx = -1
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).lstrip("✓✗ ").startswith(name) or self.tabs.tabText(i) == name:
                idx = i
                break
        if idx >= 0:
            prefix = "✓ " if ok else "✗ "
            base = name
            self.tabs.setTabText(idx, prefix + base)
        self._done += 1
        self.status.setText(f"Completed {self._done}/{self._total}")
        if self._done >= self._total:
            self.close_btn.setEnabled(True)
            self.status.setText(f"All done ({self._total})")

    def set_running(self, name: str) -> None:
        self.status.setText(f"Running: {name}…")
