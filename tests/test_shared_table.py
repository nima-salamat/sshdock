import asyncio

from PySide6.QtWidgets import QApplication, QAbstractItemView, QHeaderView, QTableWidgetItem

from app.ui.widgets.table import ColumnSpec, StandardTableWidget, TableState


def _qapp():
    return QApplication.instance() or QApplication([])


def _table():
    _qapp()
    table = StandardTableWidget()
    table.configure_columns([
        ColumnSpec("Name", key="name", default_width=220, min_width=60),
        ColumnSpec("State", key="state", default_width=100, min_width=50),
    ])
    return table


def test_shared_table_columns_are_interactive():
    table = _table()
    assert table.columnCount() == 2
    assert table.columnWidth(0) == 220
    assert table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    assert table.horizontalHeader().sectionResizeMode(1) == QHeaderView.ResizeMode.Interactive


def test_shared_table_standard_selection_defaults_are_consistent():
    table = _table()
    table.configure_standard(
        selection_behavior=QAbstractItemView.SelectionBehavior.SelectRows,
        selection_mode=QAbstractItemView.SelectionMode.ExtendedSelection,
        sorting=True,
    )
    assert table.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows
    assert table.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    assert table.isSortingEnabled()


def test_shared_table_state_helpers():
    table = _table()
    table.set_loading("Loading containers…")
    assert table.state == TableState.LOADING
    assert table.rowCount() == 1

    table.set_error("SSH failed")
    assert table.state == TableState.ERROR
    assert table.item(0, 0).text() == "SSH failed"

    table.set_empty("Nothing here")
    assert table.state == TableState.EMPTY
    assert table.item(0, 0).text() == "Nothing here"


def test_shared_table_copy_operations():
    table = _table()
    table.setHorizontalHeaderLabels(["Name", "State"])
    table.setRowCount(2)
    table.setItem(0, 0, QTableWidgetItem("alpha"))
    table.setItem(0, 1, QTableWidgetItem("running"))
    table.setItem(1, 0, QTableWidgetItem("beta"))
    table.setItem(1, 1, QTableWidgetItem("stopped"))
    table.selectRow(0)
    table.copy_selection()
    assert "alpha" in QApplication.clipboard().text()
    table.copy_all()
    copied = QApplication.clipboard().text()
    assert "Name" in copied
    assert "beta" in copied


def test_shared_table_preserves_selection_and_scroll():
    table = _table()
    table.setRowCount(8)
    for row in range(8):
        table.setItem(row, 0, QTableWidgetItem(f"row-{row}"))
        table.setItem(row, 1, QTableWidgetItem("ready"))
    table.selectRow(5)
    with table.preserve_view((0,)):
        table.setRowCount(0)
        table.setRowCount(8)
        for row in range(8):
            table.setItem(row, 0, QTableWidgetItem(f"row-{row}"))
            table.setItem(row, 1, QTableWidgetItem("ready"))
    assert table.selectionModel().isRowSelected(5, table.rootIndex())


def test_shared_table_rejects_overlapping_refreshes():
    table = _table()
    loop = asyncio.new_event_loop()
    try:
        gate = loop.create_future()

        async def loader():
            await gate

        table.set_refresh_callback(lambda: loop.create_task(loader()))
        first = table.refresh()
        second = table.refresh()
        assert first is not None
        assert second is None
        gate.set_result(None)
        loop.run_until_complete(asyncio.sleep(0))
    finally:
        loop.close()


def test_standard_table_is_used_by_main_window_tables():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "from app.ui.widgets.table import StandardTableWidget" in src
    assert "StandardTableWidget(" in src
    assert "QTableWidget(" not in src
