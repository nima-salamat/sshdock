from __future__ import annotations

import asyncio
import logging
import threading
import traceback
from dataclasses import dataclass
from time import monotonic
from typing import Any, Awaitable, Coroutine

log = logging.getLogger(__name__)


@dataclass(slots=True)
class TrackedTask:
    task: asyncio.Task
    name: str
    created_at: float
    owner: str = "app"


class TaskRegistry:
    """Process-wide asyncio task registry and leak watchdog.

    Every application-owned task should be created through :meth:`create`.
    The registry keeps a weak-ish lifecycle record, logs unhandled exceptions,
    and can audit the running event loop for tasks which were created outside
    the registry.
    """

    _instance: "TaskRegistry | None" = None

    def __init__(self) -> None:
        self.tasks: dict[int, TrackedTask] = {}
        self._next_id = 0
        self._lock = threading.RLock()

    @classmethod
    def instance(cls) -> "TaskRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def create(self, coro: Awaitable[Any], *, name: str = "Background task", owner: str = "app") -> asyncio.Task | None:
        try:
            task = asyncio.create_task(coro, name=name)
        except RuntimeError:
            close = getattr(coro, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            return None
        with self._lock:
            self._next_id += 1
            key = self._next_id
            self.tasks[key] = TrackedTask(task, name, monotonic(), owner)
        task._sshdock_registry_id = key  # type: ignore[attr-defined]
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task) -> None:
        key = getattr(task, "_sshdock_registry_id", None)
        with self._lock:
            self.tasks.pop(key, None)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Could not inspect completed task %r", task)
            return
        if exc is not None:
            from app.core.errors import normalize_error
            error = normalize_error(exc, operation=f"task:{task.get_name()}", title="Background task failed")
            error.log()

    def audit(self, *, include_internal: bool = False) -> list[asyncio.Task]:
        """Return running tasks which are not registered by the application."""
        try:
            current = asyncio.current_task()
            live = asyncio.all_tasks()
        except RuntimeError:
            return []
        known = {entry.task for entry in self.tasks.values()}
        leaked: list[asyncio.Task] = []
        for task in live:
            if task is current or task.done() or task in known:
                continue
            name = task.get_name()
            if not include_internal and (name.startswith("Task-") or name.startswith("qasync")):
                continue
            leaked.append(task)
        return leaked

    def snapshot(self) -> list[TrackedTask]:
        with self._lock:
            return list(self.tasks.values())


registry = TaskRegistry.instance()


def create_task(coro: Awaitable[Any], *, name: str = "Background task", owner: str = "app") -> asyncio.Task | None:
    return registry.create(coro, name=name, owner=owner)


def _show_uncaught_dialog(exc: BaseException, *, title: str = "SSHDock crashed") -> None:
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance()
        if app is None:
            return
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(title)
        box.setText("An unexpected error occurred. SSHDock recovered the error report instead of silently failing.")
        box.setDetailedText(details)
        box.setInformativeText(f"{type(exc).__name__}: {exc}")
        box.exec()
    except Exception:
        log.exception("Could not display uncaught-exception dialog")


def install_exception_hooks() -> None:
    """Route uncaught Python/thread exceptions to the unified logger and UI."""
    import sys

    def excepthook(exc_type, exc_value, exc_tb):
        log.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
        if exc_value is not None:
            _show_uncaught_dialog(exc_value)

    def thread_hook(args: threading.ExceptHookArgs):
        log.critical("Uncaught exception in thread %s", args.thread.name if args.thread else "unknown", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
