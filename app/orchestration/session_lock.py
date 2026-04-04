from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class SessionLockManager:
    """In-process asyncio lock keyed by hotel_id.

    For multi-process deployment, replace with Redis or DB-level locks.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._owners: dict[str, str] = {}  # hotel_id -> session_id

    def _get_lock(self, hotel_id: str) -> asyncio.Lock:
        if hotel_id not in self._locks:
            self._locks[hotel_id] = asyncio.Lock()
        return self._locks[hotel_id]

    async def acquire(self, hotel_id: str, session_id: str) -> bool:
        lock = self._get_lock(hotel_id)
        if lock.locked():
            logger.debug(
                "Lock busy for hotel %s (held by %s)",
                hotel_id,
                self._owners.get(hotel_id),
            )
            return False
        await lock.acquire()
        self._owners[hotel_id] = session_id
        logger.debug("Lock acquired for hotel %s by session %s", hotel_id, session_id)
        return True

    def release(self, hotel_id: str, session_id: str) -> None:
        lock = self._get_lock(hotel_id)
        owner = self._owners.get(hotel_id)
        if owner != session_id:
            logger.warning(
                "Session %s tried to release lock for hotel %s held by %s",
                session_id,
                hotel_id,
                owner,
            )
            return
        if lock.locked():
            lock.release()
            self._owners.pop(hotel_id, None)
            logger.debug("Lock released for hotel %s by session %s", hotel_id, session_id)

    def is_locked(self, hotel_id: str) -> bool:
        lock = self._locks.get(hotel_id)
        return lock is not None and lock.locked()


_manager: SessionLockManager | None = None


def get_lock_manager() -> SessionLockManager:
    global _manager
    if _manager is None:
        _manager = SessionLockManager()
    return _manager
