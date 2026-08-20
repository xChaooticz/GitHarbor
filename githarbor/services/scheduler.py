from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from githarbor.services.sync import SyncService

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, service: SyncService, interval_seconds: int, sync_on_startup: bool) -> None:
        self.service = service
        self.interval_seconds = interval_seconds
        self.sync_on_startup = sync_on_startup
        self.next_sync: datetime | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task

    async def _run(self) -> None:
        if self.sync_on_startup:
            self.service.start_global_sync("startup")
        self.next_sync = datetime.now(UTC) + timedelta(seconds=self.interval_seconds)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                self.service.start_global_sync("scheduled")
                self.next_sync = datetime.now(UTC) + timedelta(seconds=self.interval_seconds)
            except asyncio.CancelledError:
                break
        logger.info("Scheduler stopped")
