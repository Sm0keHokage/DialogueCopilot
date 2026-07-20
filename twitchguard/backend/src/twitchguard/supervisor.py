"""Task supervisor: crashed workers restart with backoff (NFR-Rel-01)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Coroutine
from typing import Any

log = logging.getLogger(__name__)

TaskFactory = Callable[[], Coroutine[Any, Any, None]]


class Supervisor:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, name: str, factory: TaskFactory) -> None:
        existing = self._tasks.get(name)
        if existing is not None and not existing.done():
            return
        self._tasks[name] = asyncio.create_task(self._run(name, factory), name=name)

    async def _run(self, name: str, factory: TaskFactory) -> None:
        backoff = 1.0
        while True:
            try:
                await factory()
                return  # factory finished normally
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("supervised task %s crashed, restarting in %.0fs", name, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    def is_running(self, name: str) -> bool:
        task = self._tasks.get(name)
        return task is not None and not task.done()

    def running_names(self, prefix: str = "") -> list[str]:
        return sorted(
            name
            for name, task in self._tasks.items()
            if name.startswith(prefix) and not task.done()
        )

    async def stop_prefix(self, prefix: str) -> None:
        for name in [n for n in list(self._tasks) if n.startswith(prefix)]:
            await self.stop(name)

    async def stop(self, name: str) -> None:
        task = self._tasks.pop(name, None)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def stop_all(self) -> None:
        for name in list(self._tasks):
            await self.stop(name)
