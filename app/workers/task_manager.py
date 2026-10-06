from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable


@dataclass
class TaskRecord:
    task: asyncio.Task
    name: str
    started_at: float
    status: str = "running"
    error: str = ""
    finished_at: float | None = None
    task_id: int = 0

    def live_status(self) -> str:
        """Status reflecting the real asyncio.Task state right now."""
        if not self.task.done():
            age = time.monotonic() - self.started_at
            if age > 60:
                return "running (long)"
            return "running"
        # Prefer recorded terminal state, but never leave a done task as "running"
        if self.status == "running":
            # _done callback may not have run yet
            if self.task.cancelled():
                return "cancelled"
            try:
                exc = self.task.exception()
            except asyncio.CancelledError:
                return "cancelled"
            except Exception:
                return "failed"
            if exc is not None:
                return "failed"
            return "completed"
        return self.status


class TaskManager:
    def __init__(self):
        self.records: dict[int, TaskRecord] = {}
        self._seq = 0

    def create(self, coro: Awaitable, name: str = "Background task") -> asyncio.Task | None:
        try:
            task = asyncio.create_task(coro, name=name)
        except RuntimeError:
            try:
                coro.close()  # type: ignore[attr-defined]
            except Exception:
                pass
            return None
        self._seq += 1
        key = self._seq
        rec = TaskRecord(task=task, name=name, started_at=time.monotonic(), task_id=key)
        self.records[key] = rec
        task._sshdock_task_id = key  # type: ignore[attr-defined]
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task):
        key = getattr(task, "_sshdock_task_id", None)
        rec = self.records.get(key)
        if rec is None:
            return
        rec.finished_at = time.monotonic()
        try:
            task.result()
            rec.status = "completed"
        except asyncio.CancelledError:
            rec.status = "cancelled"
        except Exception as exc:
            rec.status = "failed"
            rec.error = str(exc)

    @property
    def active(self):
        return [r for r in self.records.values() if not r.task.done()]

    def snapshot(self):
        self.purge(max_age=300)
        # Refresh status for any done tasks whose callback lagged
        for rec in self.records.values():
            if rec.task.done() and rec.status == "running":
                self._done(rec.task)
        return sorted(self.records.values(), key=lambda r: r.started_at, reverse=True)

    def cancel(self, task_id: int) -> bool:
        rec = self.records.get(task_id)
        if rec is None or rec.task.done():
            return False
        rec.task.cancel()
        rec.status = "cancelling"
        return True

    def force_drop(self, task_id: int) -> bool:
        """Cancel if running and remove from the board immediately."""
        rec = self.records.get(task_id)
        if rec is None:
            return False
        if not rec.task.done():
            rec.task.cancel()
        self.records.pop(task_id, None)
        return True

    def purge(self, max_age: float = 300):
        now = time.monotonic()
        for key, rec in list(self.records.items()):
            if rec.task.done() and rec.finished_at is not None and now - rec.finished_at > max_age:
                self.records.pop(key, None)

    async def cancel_all(self, timeout: float = 2.0):
        current = asyncio.current_task()
        tasks = [r.task for r in self.active if r.task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
            except asyncio.TimeoutError:
                pass
