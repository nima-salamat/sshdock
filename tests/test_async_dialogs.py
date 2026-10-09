import asyncio

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QVBoxLayout
from qasync import QEventLoop

from app.ui.async_dialogs import await_dialog


def _qt_app():
    return QApplication.instance() or QApplication([])


def test_await_dialog_does_not_reenter_qasync_tasks():
    app = _qt_app()
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    async def scenario():
        sibling_ran = asyncio.Event()

        async def sibling_task():
            await asyncio.sleep(0)
            sibling_ran.set()

        sibling = asyncio.create_task(sibling_task(), name="dialog-sibling-task")
        dialog = QDialog()
        layout = QVBoxLayout(dialog)
        button = QPushButton("Accept")
        button.clicked.connect(dialog.accept)
        layout.addWidget(button)
        QTimer.singleShot(0, button.click)

        result = await await_dialog(dialog)
        await sibling

        assert result == int(QDialog.DialogCode.Accepted)
        assert sibling_ran.is_set()
        assert not dialog.isVisible()

    try:
        with loop:
            loop.run_until_complete(scenario())
    finally:
        asyncio.set_event_loop(None)


def test_cancelling_awaited_dialog_closes_it_during_shutdown():
    app = _qt_app()
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    async def scenario():
        dialog = QDialog()
        task = asyncio.create_task(await_dialog(dialog), name="cancelled-dialog-task")
        await asyncio.sleep(0)
        assert dialog.isVisible()

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("dialog waiter should propagate cancellation")

        assert not dialog.isVisible()

    try:
        with loop:
            loop.run_until_complete(scenario())
    finally:
        asyncio.set_event_loop(None)


def test_container_actions_no_longer_call_nested_dialog_exec():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app/ui/main_window.py").read_text(encoding="utf-8")
    start = source.index("async def container_action_exec(")
    end = source.index("async def _bulk_selected(", start)
    assert ".exec(" not in source[start:end]
    assert "await await_dialog(dlg)" in source[start:end]


def test_startup_flow_awaits_connection_dialog_without_exec():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app/ui/app.py").read_text(encoding="utf-8")
    assert "await await_dialog(dialog)" in source
    assert "dialog.exec()" not in source
