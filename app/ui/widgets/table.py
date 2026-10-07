"""Shared SSHDock table primitives.

This module is intentionally UI-focused: it owns table chrome, state, refresh
coordination, selection/scroll preservation, and copy behavior while allowing
pages to provide their own row rendering and actions.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Iterable

from PySide6.QtCore import QTimer, Qt, QPoint, Signal, QDateTime
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.widgets.helpers import AutoRefreshControl


class TableState(str, Enum):
    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    EMPTY = "empty"
    REFRESHING = "refreshing"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    label: str
    key: str | None = None
    default_width: int = 130
    min_width: int = 40
    alignment: Qt.AlignmentFlag | None = None
    sortable: bool = True
    copyable: bool = True
    hidden: bool = False


class TableToolbar(QWidget):
    """Reusable toolbar for a StandardTableWidget."""

    refreshRequested = Signal()
    autoRefreshChanged = Signal(int)

    def __init__(
        self,
        title: str = "",
        *,
        show_auto_refresh: bool = True,
        auto_refresh_seconds: int = 5,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("tableToolbar")
        self._callback: Callable[[], Any] | None = None
        self._allowed: Callable[[], bool] | None = None
        self._running = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("tableToolbarTitle")
        layout.addWidget(self.title_label)

        self.count_label = QLabel("")
        self.count_label.setObjectName("tableToolbarCount")
        layout.addWidget(self.count_label)

        self.status_label = QLabel("")
        self.status_label.setObjectName("tableToolbarStatus")
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        self.auto_refresh = None
        if show_auto_refresh:
            self.auto_refresh = AutoRefreshControl(auto_refresh_seconds, self)
            self.auto_refresh.intervalChanged.connect(self.autoRefreshChanged.emit)
            layout.addWidget(self.auto_refresh)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("tableRefreshButton")
        self.refresh_button.clicked.connect(self.refreshRequested.emit)
        layout.addWidget(self.refresh_button)

    def set_title(self, title: str) -> None:
        self.title_label.setText(str(title))

    def set_count(self, count: int) -> None:
        self.count_label.setText(f"{int(count)} row(s)")

    def set_refresh_callback(self, callback: Callable[[], Any] | None, allowed: Callable[[], bool] | None = None) -> None:
        self._callback = callback
        self._allowed = allowed
        if callback is None:
            self.refresh_button.setEnabled(False)
            return
        self.refresh_button.setEnabled(True)

    def trigger_refresh(self) -> Any:
        callback = self._callback
        if callback is None or self._running:
            return None
        if self._allowed is not None:
            try:
                if not self._allowed():
                    return None
            except Exception:
                return None
        self._running = True
        self.status_label.setText("Refreshing…")
        try:
            result = callback()
        except Exception as exc:
            self._running = False
            self.status_label.setText(f"Refresh failed: {str(exc)[:100]}")
            return None
        if hasattr(result, "add_done_callback"):
            result.add_done_callback(self._finish_refresh)
        else:
            self._running = False
            self.status_label.setText("Updated")
        return result

    def _finish_refresh(self, task) -> None:
        self._running = False
        try:
            task.result()
        except asyncio.CancelledError:
            self.status_label.setText("Cancelled")
        except Exception as exc:
            self.status_label.setText(f"Refresh failed: {str(exc)[:100]}")
        else:
            self.status_label.setText(
                f"Updated {QDateTime.currentDateTime().toString('HH:mm:ss')}"
            )

    def set_refreshing(self, active: bool, message: str | None = None) -> None:
        self._running = bool(active)
        self.refresh_button.setEnabled(not self._running and self._callback is not None)
        if message is not None:
            self.status_label.setText(message)


class StandardTableWidget(QTableWidget):
    """QTableWidget with standardized SSHDock behavior."""

    stateChanged = Signal(object)
    refreshStarted = Signal()
    refreshFinished = Signal(bool)

    def __init__(self, rows: int = 0, columns: int = 0, parent=None) -> None:
        super().__init__(rows, columns, parent)
        self._state = TableState.IDLE
        self._columns: list[ColumnSpec] = []
        self._refresh_callback: Callable[[], Awaitable[Any] | Any] | None = None
        self._refresh_allowed: Callable[[], bool] | None = None
        self._refresh_task = None
        self._refresh_generation = 0
        self._auto_timer = QTimer(self)
        self._auto_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._auto_timer.timeout.connect(self._auto_tick)
        self._empty_message = "No data"
        self._error_message = "Could not load data"

        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSortingEnabled(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(38)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        header = self.horizontalHeader()
        header.setSectionsMovable(False)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(40)
        for column in range(self.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._copy_shortcut = QAction(self)
        self._copy_shortcut.setShortcut(QKeySequence.StandardKey.Copy)
        self._copy_shortcut.triggered.connect(self.copy_selection)
        self.addAction(self._copy_shortcut)

    @property
    def state(self) -> TableState:
        return self._state

    def set_state(self, state: TableState) -> None:
        state = TableState(state)
        if self._state == state:
            return
        self._state = state
        self.stateChanged.emit(state)

    def configure_columns(self, columns: Iterable[ColumnSpec]) -> None:
        self._columns = list(columns)
        self.setSortingEnabled(False)
        self.setColumnCount(len(self._columns))
        self.setHorizontalHeaderLabels([column.label for column in self._columns])
        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        for index, spec in enumerate(self._columns):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
            header.setMinimumSectionSize(max(20, int(spec.min_width)))
            self.setColumnWidth(index, max(spec.min_width, int(spec.default_width)))
            self.setColumnHidden(index, bool(spec.hidden))
        self.setSortingEnabled(any(spec.sortable for spec in self._columns))

    def configure_standard(
        self,
        *,
        selection_behavior: QAbstractItemView.SelectionBehavior = QAbstractItemView.SelectionBehavior.SelectRows,
        selection_mode: QAbstractItemView.SelectionMode = QAbstractItemView.SelectionMode.ExtendedSelection,
        sorting: bool = True,
        alternating: bool = True,
    ) -> None:
        self.setSelectionBehavior(selection_behavior)
        self.setSelectionMode(selection_mode)
        self.setAlternatingRowColors(alternating)
        self.setSortingEnabled(sorting and bool(self._columns))
        self._install_header_defaults()

    def _install_header_defaults(self) -> None:
        header = self.horizontalHeader()
        header.setSectionsMovable(False)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(40)
        for index, spec in enumerate(self._columns):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
            self.setColumnWidth(index, max(spec.min_width, int(spec.default_width)))

    @contextmanager
    def preserve_view(self, key_columns: tuple[int, ...] = (0,)):
        selected: set[tuple[str, ...]] = set()
        model = self.selectionModel()
        if model is not None:
            for index in model.selectedRows():
                selected.add(tuple(
                    self.item(index.row(), col).text() if self.item(index.row(), col) else ""
                    for col in key_columns
                ))
        scroll = self.verticalScrollBar().value()
        old_updates = self.updatesEnabled()
        old_sorting = self.isSortingEnabled()
        old_signals = self.blockSignals(True)
        self.setUpdatesEnabled(False)
        self.setSortingEnabled(False)
        try:
            yield
            if selected:
                for row in range(self.rowCount()):
                    key = tuple(
                        self.item(row, col).text() if self.item(row, col) else ""
                        for col in key_columns
                    )
                    if key in selected:
                        self.selectRow(row)
            self.verticalScrollBar().setValue(
                min(scroll, self.verticalScrollBar().maximum())
            )
        finally:
            self.blockSignals(old_signals)
            self.setSortingEnabled(old_sorting)
            self.setUpdatesEnabled(old_updates)
            self.viewport().update()

    def set_loading(self, message: str = "Loading…") -> None:
        self._empty_message = message
        self.clearContents()
        self.setRowCount(1)
        self._set_message_row(message)
        self.set_state(TableState.LOADING)

    def set_empty(self, message: str = "No data") -> None:
        self._empty_message = message
        self.clearContents()
        self.setRowCount(1)
        self._set_message_row(message)
        self.set_state(TableState.EMPTY)

    def set_error(self, message: str | None = None) -> None:
        text = message or self._error_message
        self._error_message = text
        self.clearContents()
        self.setRowCount(1)
        self._set_message_row(text)
        self.set_state(TableState.ERROR)

    def _set_message_row(self, message: str) -> None:
        if self.columnCount() <= 0:
            return
        item = QTableWidgetItem(str(message))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.setItem(0, 0, item)
        if self.columnCount() > 1:
            self.setSpan(0, 0, 1, self.columnCount())

    def set_ready(self, rows: int | None = None) -> None:
        count = self.rowCount() if rows is None else int(rows)
        if count <= 0:
            self.set_empty(self._empty_message)
        else:
            self.set_state(TableState.READY)

    def set_refresh_callback(
        self,
        callback: Callable[[], Awaitable[Any] | Any] | None,
        *,
        allowed: Callable[[], bool] | None = None,
        auto_refresh_seconds: int = 0,
    ) -> None:
        self._refresh_callback = callback
        self._refresh_allowed = allowed
        self.configure_auto_refresh(auto_refresh_seconds)

    def configure_auto_refresh(self, seconds: int) -> None:
        self._auto_timer.stop()
        value = max(0, int(seconds))
        if value:
            self._auto_timer.start(value * 1000)

    def stop_auto_refresh(self) -> None:
        self._auto_timer.stop()

    def _auto_tick(self) -> None:
        self.refresh()

    def refresh(self) -> Any:
        if self._refresh_callback is None or self._refresh_task is not None:
            return None
        if self._refresh_allowed is not None:
            try:
                if not self._refresh_allowed():
                    return None
            except Exception:
                return None

        self._refresh_generation += 1
        generation = self._refresh_generation
        self.set_state(TableState.REFRESHING if self.rowCount() > 0 else TableState.LOADING)
        self.refreshStarted.emit()
        try:
            result = self._refresh_callback()
        except Exception:
            self.refreshFinished.emit(False)
            self.set_state(TableState.ERROR)
            return None

        if hasattr(result, "add_done_callback"):
            self._refresh_task = result
            result.add_done_callback(lambda task, gen=generation: self._finish_refresh(task, gen))
        else:
            self._finish_refresh(result, generation)
        return result

    def _finish_refresh(self, task, generation: int) -> None:
        if generation != self._refresh_generation:
            return
        self._refresh_task = None
        ok = True
        try:
            if hasattr(task, "result"):
                task.result()
        except asyncio.CancelledError:
            ok = False
        except Exception:
            ok = False
        self.refreshFinished.emit(ok)
        if not ok:
            # Keep existing data on refresh failure; only initial load becomes Error.
            if self.rowCount() > 0 and self._state != TableState.LOADING:
                self.set_state(TableState.READY)
            else:
                self.set_state(TableState.ERROR)

    def copy_selection(self) -> None:
        ranges = self.selectedRanges()
        if not ranges:
            item = self.currentItem()
            QApplication.clipboard().setText(item.text() if item else "")
            return
        lines: list[str] = []
        seen: set[tuple[int, int, int, int]] = set()
        for rg in ranges:
            key = (rg.topRow(), rg.bottomRow(), rg.leftColumn(), rg.rightColumn())
            if key in seen:
                continue
            seen.add(key)
            for row in range(rg.topRow(), rg.bottomRow() + 1):
                cells = []
                for col in range(rg.leftColumn(), rg.rightColumn() + 1):
                    item = self.item(row, col)
                    cells.append(item.text() if item else "")
                lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))

    def copy_all(self) -> None:
        headers = [self.horizontalHeaderItem(i).text() if self.horizontalHeaderItem(i) else "" for i in range(self.columnCount())]
        lines = ["\t".join(headers)]
        for row in range(self.rowCount()):
            lines.append("\t".join(
                self.item(row, col).text() if self.item(row, col) else ""
                for col in range(self.columnCount())
            ))
        QApplication.clipboard().setText("\n".join(lines))

    def _context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("Copy selection", self.copy_selection)
        menu.addAction("Copy entire table (TSV)", self.copy_all)
        menu.addAction("Select all", self.selectAll)
        menu.addAction("Clear selection", self.clearSelection)
        menu.addSeparator()
        menu.addAction("Auto-fit columns", self.resizeColumnsToContents)
        menu.exec(self.viewport().mapToGlobal(pos))


class StandardTable(QWidget):
    """Optional page-level composition: toolbar + StandardTableWidget."""

    refreshRequested = Signal()
    autoRefreshChanged = Signal(int)

    def __init__(
        self,
        title: str,
        columns: Iterable[ColumnSpec],
        *,
        auto_refresh_seconds: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.toolbar = TableToolbar(
            title,
            show_auto_refresh=True,
            auto_refresh_seconds=auto_refresh_seconds,
            parent=self,
        )
        self.columns = list(columns)
        self.table = StandardTableWidget(0, len(self.columns), self)
        self.table.configure_columns(self.columns)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.table, 1)
        self.toolbar.refreshRequested.connect(self._toolbar_refresh)
        self.toolbar.autoRefreshChanged.connect(self._auto_changed)

    def _toolbar_refresh(self) -> None:
        self.refreshRequested.emit()
        self.table.refresh()

    def _auto_changed(self, seconds: int) -> None:
        self.autoRefreshChanged.emit(seconds)
        self.table.configure_auto_refresh(seconds)
