"""Interactive Docker results table — resizable columns, copy-friendly, non-modal."""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut, QAction
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QHBoxLayout, QHeaderView, QLabel,
    QMenu, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


def _status_color(text: str) -> QColor | None:
    low = (text or "").lower()
    if any(k in low for k in ("up ", "running", "healthy", "online")):
        return QColor("#3fb950")
    if any(k in low for k in ("exit", "dead", "unhealthy", "stopped")):
        return QColor("#f85149")
    if any(k in low for k in ("created", "paused", "restarting")):
        return QColor("#d29922")
    return None


class DockerResultsTable(QWidget):
    """Top-level non-modal window showing docker list data in a resizable table."""

    def __init__(self, parent=None):
        super().__init__(None)  # independent window
        self.setWindowTitle("Docker results")
        self.resize(960, 480)
        self.setMinimumSize(420, 240)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinMaxButtonsHint
        )
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._columns: list[str] = []
        self._rows: list[list[str]] = []
        self._title = "Docker results"
        self._refresh_cb: Callable[[], Awaitable[None]] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        bar = QHBoxLayout()
        self.title_lbl = QLabel(self._title)
        self.title_lbl.setStyleSheet("font-weight:700; font-size:13px; color:#e6edf3;")
        bar.addWidget(self.title_lbl, 1)
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet("color:#8b949e;")
        bar.addWidget(self.count_lbl)

        self.copy_btn = QPushButton("Copy table")
        self.copy_btn.setToolTip("Copy all rows as TSV (paste into Excel / sheets)")
        self.copy_btn.clicked.connect(self.copy_all)
        bar.addWidget(self.copy_btn)

        self.copy_sel_btn = QPushButton("Copy selection")
        self.copy_sel_btn.setToolTip("Copy selected cells (Ctrl+C)")
        self.copy_sel_btn.clicked.connect(self.copy_selection)
        bar.addWidget(self.copy_sel_btn)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._on_refresh)
        bar.addWidget(self.refresh_btn)
        root.addLayout(bar)

        self.table = QTableWidget(0, 0)
        self.table.setObjectName("dockerResultsTable")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        hdr = self.table.horizontalHeader()
        hdr.setSectionsMovable(True)
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setMinimumSectionSize(60)
        hdr.setDefaultSectionSize(140)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        root.addWidget(self.table, 1)

        tip = QLabel("Drag column borders to resize · double-click header edge to auto-fit · Ctrl+C copy")
        tip.setStyleSheet("color:#6e7681; font-size:11px;")
        root.addWidget(tip)

        self.setStyleSheet(
            """
            QWidget { background: #0d1117; color: #e6edf3; }
            QPushButton {
                background: #21262d; color: #e6edf3; border: 1px solid #30363d;
                border-radius: 6px; padding: 5px 12px;
            }
            QPushButton:hover { background: #30363d; }
            QTableWidget#dockerResultsTable {
                background: #0d1117; gridline-color: #21262d;
                border: 1px solid #30363d; border-radius: 6px;
                alternate-background-color: #161b22;
            }
            QHeaderView::section {
                background: #161b22; color: #79c0ff; font-weight: 600;
                padding: 6px 8px; border: none;
                border-right: 1px solid #30363d; border-bottom: 1px solid #30363d;
            }
            QTableWidget::item:selected { background: #1f6feb; color: white; }
            """
        )

        QShortcut(QKeySequence.StandardKey.Copy, self, self.copy_selection)
        QShortcut(QKeySequence("Ctrl+A"), self, self.table.selectAll)

    def set_refresh(self, cb: Callable[[], Awaitable[None]] | None):
        self._refresh_cb = cb
        self.refresh_btn.setVisible(cb is not None)

    def _on_refresh(self):
        if self._refresh_cb is None:
            return
        try:
            from app.core.runtime import create_task
            create_task(self._refresh_cb(), name="docker-table-refresh")
        except Exception:
            pass

    def show_data(self, title: str, columns: list[str], rows: list[list[Any]]):
        self._title = title
        self._columns = list(columns)
        self._rows = [[("" if c is None else str(c)) for c in row] for row in rows]
        self.setWindowTitle(title)
        self.title_lbl.setText(title)
        self.count_lbl.setText(f"{len(self._rows)} row(s)")

        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.clear()
        self.table.setColumnCount(len(self._columns))
        self.table.setHorizontalHeaderLabels(self._columns)
        self.table.setRowCount(len(self._rows))

        mono = QFont("Cascadia Mono")
        if not mono.exactMatch():
            mono = QFont("Consolas")
        mono.setPointSize(12)

        status_cols = {
            i for i, name in enumerate(self._columns)
            if name.lower() in {"status", "state"}
        }

        for r, row in enumerate(self._rows):
            for c, val in enumerate(row):
                item = QTableWidgetItem(val)
                item.setFont(mono)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c in status_cols:
                    color = _status_color(val)
                    if color is not None:
                        item.setForeground(color)
                self.table.setItem(r, c, item)

        self.table.setSortingEnabled(sorting)
        # Sensible initial widths; user can drag freely afterwards
        for c, name in enumerate(self._columns):
            key = name.lower()
            if key in {"id", "image id"}:
                self.table.setColumnWidth(c, 120)
            elif key in {"name", "names", "repository", "image"}:
                self.table.setColumnWidth(c, 180)
            elif key in {"status", "ports", "command"}:
                self.table.setColumnWidth(c, 200)
            elif key in {"size", "tag", "driver", "scope", "mode", "replicas"}:
                self.table.setColumnWidth(c, 100)
            else:
                self.table.setColumnWidth(c, 130)

        self.show()
        self.raise_()
        self.activateWindow()

    def copy_selection(self):
        ranges = self.table.selectedRanges()
        if not ranges:
            # fallback: current cell
            item = self.table.currentItem()
            if item is not None:
                QApplication.clipboard().setText(item.text())
            return
        lines: list[str] = []
        for rg in ranges:
            for r in range(rg.topRow(), rg.bottomRow() + 1):
                cells = []
                for c in range(rg.leftColumn(), rg.rightColumn() + 1):
                    it = self.table.item(r, c)
                    cells.append(it.text() if it else "")
                lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))

    def copy_all(self):
        lines = ["\t".join(self._columns)]
        for row in self._rows:
            lines.append("\t".join(row))
        QApplication.clipboard().setText("\n".join(lines))

    def _context_menu(self, pos: QPoint):
        menu = QMenu(self)
        act_copy = menu.addAction("Copy selection")
        act_all = menu.addAction("Copy entire table (TSV)")
        act_fit = menu.addAction("Auto-fit columns")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is act_copy:
            self.copy_selection()
        elif chosen is act_all:
            self.copy_all()
        elif chosen is act_fit:
            self.table.resizeColumnsToContents()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
