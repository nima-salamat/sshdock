from __future__ import annotations

import asyncio
import signal
import sys

from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from app.core.errors import normalize_error
from app.core.logging import configure_logging
from app.core.runtime import install_exception_hooks
from app.ui.app import async_main


def _qt_message_handler(mode: QtMsgType, context, message: str) -> None:
    import logging
    level = logging.ERROR if mode in {QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg} else logging.WARNING if mode == QtMsgType.QtWarningMsg else logging.INFO
    logging.getLogger("qt").log(level, "Qt message: %s", message)


def _install_process_signal_handlers(app: QApplication) -> tuple[dict[int, object], callable]:
    """Route process termination signals through the normal Qt shutdown path.

    SIGINT (Ctrl+C from the console) must not bypass MainWindow.closeEvent:
    that path owns terminal/SSH/task cleanup. Keep the previous handlers so
    they can be restored after the qasync loop exits.
    """
    previous: dict[int, object] = {}
    shutdown_requested = False

    def request_shutdown() -> None:
        nonlocal shutdown_requested
        if shutdown_requested:
            return
        shutdown_requested = True

        # Prefer closing MainWindow so its closeEvent starts the existing
        # asynchronous cleanup sequence. During startup, close any modal
        # connection dialog instead so async_main can return normally.
        windows = list(QApplication.topLevelWidgets())
        main_window = next(
            (
                w for w in windows
                if w.__class__.__name__ == "MainWindow"
                and w.isVisible()
            ),
            None,
        )
        if main_window is not None:
            main_window.close()
            return

        for widget in windows:
            if widget.isVisible() and widget.__class__.__name__ == "ConnectionDialog":
                widget.close()
                return

        app.quit()

    def handler(signum, frame) -> None:
        del frame
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            request_shutdown()
            return
        loop.call_soon_threadsafe(request_shutdown)

    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, handler)
        except (OSError, RuntimeError, ValueError):
            continue

    def restore() -> None:
        for sig, old_handler in previous.items():
            try:
                signal.signal(sig, old_handler)
            except (OSError, RuntimeError, ValueError):
                pass

    return previous, restore


def main() -> None:
    configure_logging()
    install_exception_hooks()
    qInstallMessageHandler(_qt_message_handler)

    app = QApplication(sys.argv)
    app.setApplicationName("SSHDock")
    app.setOrganizationName("SSHDock")
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    _previous_signal_handlers, restore_signal_handlers = _install_process_signal_handlers(app)
    with loop:
        try:
            loop.run_until_complete(async_main(app))
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            error = normalize_error(exc, operation="application.startup")
            error.log()
            raise
        finally:
            restore_signal_handlers()


if __name__ == "__main__":
    main()
