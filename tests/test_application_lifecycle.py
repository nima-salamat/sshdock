import asyncio
import signal

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog
from qasync import QEventLoop

from app.main import (
    _configure_application_lifecycle,
    _install_process_signal_handlers,
)


def test_closing_last_dialog_does_not_stop_qasync_before_coroutine_finishes():
    app = QApplication.instance() or QApplication([])
    previous_quit_on_last_window_closed = app.quitOnLastWindowClosed()
    _configure_application_lifecycle(app)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    async def scenario():
        dialog = QDialog()
        dialog.show()
        QTimer.singleShot(0, dialog.close)

        # This await must resume after Qt closes the final visible window.
        await asyncio.sleep(0.02)
        assert not dialog.isVisible()
        return "startup finished"

    try:
        with loop:
            result = loop.run_until_complete(scenario())
        assert result == "startup finished"
    finally:
        app.setQuitOnLastWindowClosed(previous_quit_on_last_window_closed)
        asyncio.set_event_loop(None)


def test_termination_signal_cancels_startup_task_instead_of_quitting_qt():
    app = QApplication.instance() or QApplication([])
    app.closeAllWindows()
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    async def scenario():
        async def wait_forever():
            await asyncio.Event().wait()

        main_task = asyncio.create_task(wait_forever(), name="signal-test-main")
        _, restore = _install_process_signal_handlers(app, main_task)
        try:
            handler = signal.getsignal(signal.SIGINT)
            handler(signal.SIGINT, None)
            # Give the queued shutdown callback and cancelled task time to run.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert main_task.cancelled()
            return "event-loop-kept-running"
        finally:
            restore()

    try:
        with loop:
            assert loop.run_until_complete(scenario()) == "event-loop-kept-running"
    finally:
        asyncio.set_event_loop(None)
