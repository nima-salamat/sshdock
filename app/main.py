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


def _install_process_signal_handlers(
    app: QApplication,
    main_task: asyncio.Task | None = None,
) -> tuple[dict[int, object], callable]:
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

        # Never call app.quit() while run_until_complete() is waiting for
        # async_main: that stops Qt before the asyncio Future can finish.
        # With no visible window to close, cancel the application task and let
        # run_until_complete unwind normally instead.
        if main_task is not None and not main_task.done():
            main_task.cancel()

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


def _configure_application_lifecycle(app: QApplication) -> None:
    """Let async_main own shutdown instead of Qt stopping qasync on last-window close.

    Startup and message dialogs are opened non-blockingly, so Qt would otherwise
    emit quit as soon as the last visible dialog closes. That can stop QEventLoop
    before the awaiting coroutine resumes, raising "Event loop stopped before
    Future completed". MainWindow already performs an asynchronous, awaited
    shutdown and signals async_main when cleanup is complete.
    """
    app.setQuitOnLastWindowClosed(False)


def main() -> None:
    configure_logging()
    install_exception_hooks()
    qInstallMessageHandler(_qt_message_handler)

    app = QApplication(sys.argv)
    app.setApplicationName("SSHDock")
    app.setOrganizationName("SSHDock")
    _configure_application_lifecycle(app)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    # Keep an explicit Task so a termination signal never stops the Qt event
    # loop underneath run_until_complete() while async_main is still pending.
    main_task = loop.create_task(async_main(app), name="sshdock-application")
    _previous_signal_handlers, restore_signal_handlers = _install_process_signal_handlers(app, main_task)
    with loop:
        try:
            loop.run_until_complete(main_task)
        except asyncio.CancelledError:
            # A termination signal arrived during a window-less startup/teardown
            # transition. The task was cancelled intentionally; exit normally.
            pass
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
