from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.core.database import get_db

logger = logging.getLogger(__name__)


class SessionLockManager:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._owners: dict[str, str] = {}

    def _get_lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def acquire(self, lock_key: str, session_id: str) -> bool:
        conn = get_db()
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=get_settings().session_lock_ttl)
        conn.execute("DELETE FROM session_locks WHERE expires_at <= ?", (now.isoformat(),))
        existing = conn.execute(
            "SELECT session_id FROM session_locks WHERE lock_key = ?",
            (lock_key,),
        ).fetchone()
        if existing and existing["session_id"] != session_id:
            conn.commit()
            conn.close()
            logger.debug("Session lock busy for %s, held by %s", lock_key, existing["session_id"])
            return False

        lock = self._get_lock(lock_key)
        if lock.locked() and self._owners.get(lock_key) != session_id:
            conn.commit()
            conn.close()
            logger.debug("Async lock busy for %s, held by %s", lock_key, self._owners.get(lock_key))
            return False

        if not lock.locked():
            await lock.acquire()
        self._owners[lock_key] = session_id
        conn.execute(
            """
            INSERT INTO session_locks (lock_key, session_id, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(lock_key) DO UPDATE SET
                session_id = excluded.session_id,
                expires_at = excluded.expires_at
            """,
            (lock_key, session_id, expires_at.isoformat()),
        )
        conn.commit()
        conn.close()
        logger.debug("Session lock acquired for %s by %s", lock_key, session_id)
        return True

    def release(self, lock_key: str, session_id: str) -> None:
        conn = get_db()
        owner = self._owners.get(lock_key)
        if owner and owner != session_id:
            logger.warning("Session %s tried to release %s owned by %s", session_id, lock_key, owner)
            conn.close()
            return
        conn.execute(
            "DELETE FROM session_locks WHERE lock_key = ? AND session_id = ?",
            (lock_key, session_id),
        )
        conn.commit()
        conn.close()
        lock = self._locks.get(lock_key)
        if lock and lock.locked():
            lock.release()
        self._owners.pop(lock_key, None)
        logger.debug("Session lock released for %s by %s", lock_key, session_id)


_manager: SessionLockManager | None = None


def get_lock_manager() -> SessionLockManager:
    global _manager
    if _manager is None:
        _manager = SessionLockManager()
    return _manager
