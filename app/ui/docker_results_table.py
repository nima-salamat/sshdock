"""Interactive Docker results table using the shared SSHDock table foundation."""
from __future__ import annotations

from typing import Any, Callable, Awaitable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.widgets.helpers import AutoRefreshControl
from app.ui.widgets.table import ColumnSpec, StandardTableWidget, TableState


class DockerResultsTable(QWidget):
    """Top-level non-modal Docker results window with standard table behavior."""

    def __init__(self, parent=None):
        super().__init__(None)
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
        self.title_lbl.setObjectName("dockerResultsTitle")
        bar.addWidget(self.title_lbl, 1)

        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("dockerResultsCount")
        bar.addWidget(self.count_lbl)

        self.auto_refresh = AutoRefreshControl(5, self)
        bar.addWidget(self.auto_refresh)

        self.copy_btn = QPushButton("Copy table")
        self.copy_btn.setToolTip("Copy all rows as TSV")
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

        self.table = StandardTableWidget(0, 0, self)
        self.table.setObjectName("dockerResultsTable")
        self.table.configure_standard(
            selection_behavior=QAbstractItemView.SelectionBehavior.SelectItems,
            selection_mode=QAbstractItemView.SelectionMode.ExtendedSelection,
            sorting=True,
        )
        self.table.install_standard_context_menu()
        self.table.refreshStarted.connect(lambda: self._set_refresh_ui(True))
        self.table.refreshFinished.connect(lambda ok: self._set_refresh_ui(False, ok))
        self.auto_refresh.intervalChanged.connect(self.table.configure_auto_refresh)
        root.addWidget(self.table, 1)

        tip = QLabel(
            "Drag column borders to resize · double-click header edge to auto-fit · Ctrl+C copy"
        )
        tip.setObjectName("dockerResultsHint")
        root.addWidget(tip)

    def _set_refresh_ui(self, active: bool, ok: bool = True) -> None:
        enabled = not active and self._refresh_cb is not None
        self.refresh_btn.setEnabled(enabled)
        self.copy_btn.setEnabled(not active)
        self.copy_sel_btn.setEnabled(not active)
        self.refresh_btn.setText("Refreshing…" if active else "Refresh")

    def set_refresh(self, cb: Callable[[], Awaitable[None]] | None):
        self._refresh_cb = cb
        self.refresh_btn.setVisible(cb is not None)
        self.table.set_refresh_callback(
            self._run_refresh if cb is not None else None,
            allowed=lambda: self.isVisible(),
            auto_refresh_seconds=self.auto_refresh.seconds if cb is not None else 0,
        )

    def _run_refresh(self):
        if self._refresh_cb is None:
            return None
        from app.core.runtime import create_task
        return create_task(self._refresh_cb(), name="docker-table-refresh")

    def _on_refresh(self):
        self.table.refresh()

    def show_data(self, title: str, columns: list[str], rows: list[list[Any]]):
        self._title = title
        self._columns = [str(x) for x in columns]
        self._rows = [[("" if c is None else str(c)) for c in row] for row in rows]

        self.setWindowTitle(title)
        self.title_lbl.setText(title)
        self.count_lbl.setText(f"{len(self._rows)} row(s)")

        specs: list[ColumnSpec] = []
        for name in self._columns:
            key = name.strip().lower()
            width = 130
            if key in {"id", "image id"}:
                width = 150
            elif key in {"name", "names", "repository", "image"}:
                width = 190
            elif key in {"status", "ports", "command"}:
                width = 220
            elif key in {"size", "tag", "driver", "scope", "mode", "replicas"}:
                width = 100
            specs.append(ColumnSpec(name, key=key, default_width=width, min_width=60))

        self.table.configure_columns(specs)
        self.table.configure_standard(
            selection_behavior=QAbstractItemView.SelectionBehavior.SelectItems,
            selection_mode=QAbstractItemView.SelectionMode.ExtendedSelection,
            sorting=True,
        )
        with self.table.preserve_view((0,)):
            self.table.setRowCount(0)
            for values in self._rows:
                row = self.table.rowCount()
                self.table.insertRow(row)
                for col, value in enumerate(values):
                    self.table.setItem(row, col, QTableWidgetItem(value))

        self.table.set_state(TableState.READY if self._rows else TableState.EMPTY)
        self.show()
        self.raise_()
        self.activateWindow()

    def copy_selection(self):
        self.table.copy_selection()

    def copy_all(self):
        self.table.copy_all()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
