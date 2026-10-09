import asyncio

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog
from qasync import QEventLoop

from app.main import _configure_application_lifecycle


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
