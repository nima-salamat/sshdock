from __future__ import annotations

import asyncio
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


def main() -> None:
    configure_logging()
    install_exception_hooks()
    qInstallMessageHandler(_qt_message_handler)

    app = QApplication(sys.argv)
    app.setApplicationName("SSHDock")
    app.setOrganizationName("SSHDock")
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        try:
            loop.run_until_complete(async_main(app))
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            error = normalize_error(exc, operation="application.startup")
            error.log()
            raise


if __name__ == "__main__":
    main()
