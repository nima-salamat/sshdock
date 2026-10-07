"""Containers page UI (extracted from MainWindow shell)."""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.docker.models import ContainerInfo
from app.ui.widgets.table import ColumnSpec, StandardTableWidget


class ContainersPage(QWidget):
    """Owns containers table chrome; actions are delegated via callbacks."""

    refresh_requested = Signal()
    new_container_requested = Signal()
    details_requested = Signal()
    selection_count_changed = Signal(int)
    filter_changed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        make_columns_resizable: Callable | None = None,
        actions_cell: Callable | None = None,
        spawn_action: Callable[[str, str], None] | None = None,
        open_logs: Callable[[str], None] | None = None,
        open_shell: Callable[[str], None] | None = None,
        open_files: Callable[[str], None] | None = None,
        open_stats: Callable[[str], None] | None = None,
        open_inspect: Callable[[str], None] | None = None,
        remove_one: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._actions_cell = actions_cell
        self._spawn_action = spawn_action
        self._open_logs = open_logs
        self._open_shell = open_shell
        self._open_files = open_files
        self._open_stats = open_stats
        self._open_inspect = open_inspect
        self._remove_one = remove_one
        self._rows: list[dict[str, Any]] = []
        self._syncing_selection = False
        self.auto_refresh = None
        self._build(make_columns_resizable)

    def _build(self, make_columns_resizable: Callable | None) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Containers")
        title.setObjectName("heroTitle")
        header.addWidget(title)
        header.addStretch()
        from app.ui.widgets.table import TableToolbar
        self.table_toolbar = TableToolbar("Containers", auto_refresh_seconds=5, parent=self)
        self.auto_refresh = self.table_toolbar.auto_refresh
        self.table_toolbar.refreshRequested.connect(self.refresh_requested.emit)
        header.addWidget(self.auto_refresh)
        refresh = self.table_toolbar.refresh_button
        header.addWidget(refresh)
        new = QPushButton("New container")
        new.setProperty("accent", True)
        new.clicked.connect(self.new_container_requested.emit)
        header.addWidget(new)
        lay.addLayout(header)

        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, image, id…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _=None: self.filter_changed.emit())
        tools.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.addItems(["All", "Running", "Stopped", "Exited", "Restarting", "Unhealthy"])
        self.filter.currentIndexChanged.connect(lambda _=None: self.filter_changed.emit())
        tools.addWidget(self.filter)
        self.selected_label = QLabel("0 selected")
        self.selected_label.setObjectName("muted")
        tools.addWidget(self.selected_label)
        select_all = QPushButton("Select all")
        select_all.clicked.connect(self.select_all_visible)
        tools.addWidget(select_all)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear_selection)
        tools.addWidget(clear)
        lay.addLayout(tools)

        self.table = StandardTableWidget(0, 7)
        self.table.configure_columns([
            ColumnSpec("", key="selected", default_width=36, min_width=30, sortable=False),
            ColumnSpec("Status", key="status", default_width=58, min_width=48),
            ColumnSpec("Name", key="name", default_width=220),
            ColumnSpec("Image", key="image", default_width=220),
            ColumnSpec("Ports", key="ports", default_width=180),
            ColumnSpec("State", key="state", default_width=150),
            ColumnSpec("Actions", key="actions", default_width=120, sortable=False, copyable=False),
        ])
        self.table.configure_standard(
            selection_behavior=QAbstractItemView.SelectionBehavior.SelectRows,
            selection_mode=QAbstractItemView.SelectionMode.ExtendedSelection,
            sorting=False,
        )
        self.table.install_standard_context_menu()
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.verticalHeader().setMinimumSectionSize(32)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        if make_columns_resizable:
            make_columns_resizable(self.table, stretch_cols={3, 4})
        else:
            # Fallback: make all columns Interactive directly so the user
            # can resize every column even without the MainWindow helper.
            h = self.table.horizontalHeader()
            h.setStretchLastSection(False)
            for c in range(self.table.columnCount()):
                h.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            h.setMinimumSectionSize(40)
            self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemChanged.connect(self._on_check_changed)
        self.table.itemDoubleClicked.connect(lambda _: self.details_requested.emit())
        lay.addWidget(self.table, 1)

        # Compatibility aliases used by MainWindow
        self.container_table = self.table
        self.container_search = self.search
        self.container_filter = self.filter
        self.container_selected = self.selected_label

    def set_rows(self, rows: list[dict[str, Any]] | list[ContainerInfo]) -> None:
        normalized: list[dict[str, Any]] = []
        for x in rows:
            if isinstance(x, ContainerInfo):
                normalized.append({
                    "ID": x.id,
                    "Names": x.name,
                    "Name": x.name,
                    "Image": x.image,
                    "Status": x.status,
                    "State": x.state,
                    "Ports": x.ports,
                    **x.raw,
                })
            else:
                normalized.append(dict(x))
        self._rows = normalized
        self.render(normalized)

    def render(self, rows: list[dict[str, Any]] | None = None) -> None:
        rows = rows if rows is not None else self._rows
        preserve = set(self.checked_names())
        query = self.search.text().lower().strip()
        flt = self.filter.currentText()
        old_updates = self.table.updatesEnabled()
        old_signals = self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        try:
            self.table.setRowCount(0)
            for x in rows:
                status = (x.get("Status") or "")
                name = (x.get("Names") or x.get("Name") or "").lstrip("/")
                state = x.get("State", "") or (status.split()[0] if status else "")
                if "," in name: name = name.split(",", 1)[0].strip()
                image = x.get("Image", "") or ""
                cid = str(x.get("ID") or "")
                if query and query not in name.lower() and query not in image.lower() and query not in cid.lower(): continue
                state_l = state.lower()
                if flt == "Running" and state_l != "running": continue
                if flt == "Stopped" and state_l not in {"exited", "created", "dead"}: continue
                if flt == "Exited" and "exited" not in status.lower(): continue
                if flt == "Restarting" and "restart" not in status.lower(): continue
                if flt == "Unhealthy" and "unhealthy" not in status.lower(): continue
                r = self.table.rowCount(); self.table.insertRow(r)
                running = state_l == "running"
                check = QTableWidgetItem("")
                check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                check.setCheckState(Qt.CheckState.Checked if (name in preserve or cid in preserve) else Qt.CheckState.Unchecked)
                check.setData(Qt.ItemDataRole.UserRole, cid or name)
                check.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                check.setToolTip(f"Select {name}")
                self.table.setItem(r, 0, check)
                status_text = "● RUN" if running else "○ STOP"
                dot = QTableWidgetItem(status_text); dot.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                dot.setForeground(QColor("#39d47a" if running else "#ff5260"))
                f = dot.font(); f.setBold(True); dot.setFont(f)
                dot.setToolTip("Running · container is active" if running else "Stopped · container is not running")
                self.table.setItem(r, 1, dot)
                name_cell = QTableWidgetItem(name); name_cell.setData(Qt.ItemDataRole.UserRole, cid); self.table.setItem(r, 2, name_cell)
                self.table.setItem(r, 3, QTableWidgetItem(image or "—"))
                self.table.setItem(r, 4, QTableWidgetItem(x.get("Ports") or "—"))
                self.table.setItem(r, 5, QTableWidgetItem(status or state or "—"))
                if self._actions_cell and self._spawn_action:
                    entries = [
                        ("Stop" if running else "Start", "fa5s.stop" if running else "fa5s.play", lambda _=False, n=name, rn=running: self._spawn_action("stop" if rn else "start", n)),
                        ("Restart", "fa5s.sync", lambda _=False, n=name: self._spawn_action("restart", n)),
                        ("Logs", "fa5s.file-alt", lambda _=False, n=name: self._open_logs and self._open_logs(n)),
                        ("Shell", "fa5s.terminal", lambda _=False, n=name: self._open_shell and self._open_shell(n)),
                        ("Files", "fa5s.folder-open", lambda _=False, n=name: self._open_files and self._open_files(n)),
                        ("Stats", "fa5s.chart-bar", lambda _=False, n=name: self._open_stats and self._open_stats(n)),
                        ("Inspect", "fa5s.search", lambda _=False, n=name: self._open_inspect and self._open_inspect(n)),
                        ("Remove", "fa5s.trash", lambda _=False, n=name: self._remove_one and self._remove_one(n)),
                    ]
                    self.table.setCellWidget(r, 6, self._actions_cell(entries, f"Actions for {name}"))
                self.table.setRowHeight(r, 40)
            self._emit_selection_count()
        finally:
            self.table.setSortingEnabled(False)
            self.table.blockSignals(old_signals)
            self.table.setUpdatesEnabled(old_updates)
            self.table.viewport().update(); self.table.update()
            self._emit_selection_count()

    def select_all_visible(self) -> None:
        self._syncing_selection = True
        try:
            self.table.selectAll()
            self.table.blockSignals(True)
            for r in range(self.table.rowCount()):
                item = self.table.item(r, 0)
                if item is not None:
                    item.setCheckState(Qt.CheckState.Checked)
            self.table.blockSignals(False)
        finally:
            self._syncing_selection = False
        self._emit_selection_count()

    def clear_selection(self) -> None:
        self._syncing_selection = True
        try:
            self.table.clearSelection()
            self.table.blockSignals(True)
            for r in range(self.table.rowCount()):
                item = self.table.item(r, 0)
                if item is not None:
                    item.setCheckState(Qt.CheckState.Unchecked)
            self.table.blockSignals(False)
        finally:
            self._syncing_selection = False
        self._emit_selection_count()

    def _checkbox_for_row(self, row: int) -> QCheckBox | None:
        host = self.table.cellWidget(row, 0)
        if host is None:
            return None
        return host.findChild(QCheckBox)

    def _on_selection_changed(self) -> None:
        if self._syncing_selection:
            return
        self._syncing_selection = True
        try:
            selected = {idx.row() for idx in self.table.selectionModel().selectedRows()}
            for r in range(self.table.rowCount()):
                cb = self._checkbox_for_row(r)
                if cb is not None:
                    cb.blockSignals(True)
                    cb.setChecked(r in selected)
                    cb.blockSignals(False)
        finally:
            self._syncing_selection = False
        self._emit_selection_count()

    def _on_check_changed(self, item: QTableWidgetItem) -> None:
        if self._syncing_selection or item is None or item.column() != 0:
            return
        self._syncing_selection = True
        try:
            from PySide6.QtCore import QItemSelectionModel
            sm = self.table.selectionModel()
            index = self.table.model().index(item.row(), 0)
            flag = QItemSelectionModel.SelectionFlag.Rows | (
                QItemSelectionModel.SelectionFlag.Select
                if item.checkState() == Qt.CheckState.Checked
                else QItemSelectionModel.SelectionFlag.Deselect
            )
            sm.select(index, flag)
        finally:
            self._syncing_selection = False
        self._emit_selection_count()


    def _emit_selection_count(self) -> None:
        total = len(self.checked_names())
        self.selected_label.setText(f"{total} selected")
        self.selection_count_changed.emit(total)

    def checked_names(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            name_item = self.table.item(r, 2)
            name = name_item.text().strip() if name_item else ""
            if item and item.data(Qt.ItemDataRole.UserRole):
                selected_key = str(item.data(Qt.ItemDataRole.UserRole))
                if not name:
                    name = selected_key
            if not name:
                continue
            checked = item and item.checkState() == Qt.CheckState.Checked
            selected = False
            if self.table.selectionModel():
                selected = self.table.selectionModel().isRowSelected(r, self.table.rootIndex())
            if checked or selected:
                if name not in seen:
                    out.append(name)
                    seen.add(name)
        return out
