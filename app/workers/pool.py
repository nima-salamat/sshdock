from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from app.core.runtime import create_task


class AsyncTaskPool:
    """Bounded application task pool backed by the global task registry."""

    def __init__(self, limit: int = 8):
        self.sem = asyncio.Semaphore(max(1, int(limit)))
        self.tasks: set[asyncio.Task] = set()

    def create(self, coro: Awaitable[Any], *, name: str = "worker-pool-task") -> asyncio.Task | None:
        async def wrapped() -> Any:
            async with self.sem:
                return await coro

        task = create_task(wrapped(), name=name, owner="task-pool")
        if task is not None:
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        return task

    async def close(self) -> None:
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
