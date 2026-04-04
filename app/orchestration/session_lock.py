from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.core.database import get_db

logger = logging.getLogger(__name__)


class SessionLockManager:
    """DB-backed session lock with in-process asyncio guard.

    Cross-process: the DB row (with TTL) is the source of truth.
    Within a process: the asyncio.Lock prevents concurrent coroutines from
    overlapping.  We never force-release a live asyncio lock — if a local
    coroutine still holds it, the acquire is refused even when the DB row
    has expired, because that coroutine is still running its critical section.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._owners: dict[str, str] = {}

    def _get_lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _clear_local(self, lock_key: str) -> None:
        """Release the in-process lock and clear ownership."""
        lock = self._locks.get(lock_key)
        if lock and lock.locked():
            lock.release()
        self._owners.pop(lock_key, None)

    def _db_acquire(self, lock_key: str, session_id: str) -> bool:
        """Atomically try to claim the DB lock. Returns True on success."""
        conn = get_db()
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=get_settings().session_lock_ttl)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM session_locks WHERE expires_at <= ?", (now.isoformat(),))
            existing = conn.execute(
                "SELECT session_id FROM session_locks WHERE lock_key = ?",
                (lock_key,),
            ).fetchone()
            if existing and existing["session_id"] != session_id:
                conn.rollback()
                return False

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
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _db_release(self, lock_key: str, session_id: str) -> None:
        conn = get_db()
        try:
            conn.execute(
                "DELETE FROM session_locks WHERE lock_key = ? AND session_id = ?",
                (lock_key, session_id),
            )
            conn.commit()
        finally:
            conn.close()

    async def acquire(self, lock_key: str, session_id: str) -> bool:
        lock = self._get_lock(lock_key)
        prev_owner = self._owners.get(lock_key)

        # If a different coroutine in this process still holds the local lock,
        # refuse — that coroutine is still running regardless of DB TTL state.
        if lock.locked() and prev_owner != session_id:
            logger.debug(
                "Session lock busy for %s (local lock held by %s)", lock_key, prev_owner,
            )
            return False

        if not self._db_acquire(lock_key, session_id):
            logger.debug("Session lock busy for %s (DB lock held by another process)", lock_key)
            return False

        # Both DB and local are clear — take the local lock
        if not lock.locked():
            await lock.acquire()
        self._owners[lock_key] = session_id
        logger.debug("Session lock acquired for %s by %s", lock_key, session_id)
        return True

    def release(self, lock_key: str, session_id: str) -> None:
        owner = self._owners.get(lock_key)
        if owner and owner != session_id:
            logger.warning("Session %s tried to release %s owned by %s", session_id, lock_key, owner)
            return
        self._db_release(lock_key, session_id)
        self._clear_local(lock_key)
        logger.debug("Session lock released for %s by %s", lock_key, session_id)

    def renew(self, lock_key: str, session_id: str) -> bool:
        conn = get_db()
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=get_settings().session_lock_ttl)
        try:
            # Only renew if the row still belongs to this session AND hasn't expired.
            # An expired row means the TTL contract was already broken — another
            # process may have legitimately acquired this lock.
            result = conn.execute(
                "UPDATE session_locks SET expires_at = ? "
                "WHERE lock_key = ? AND session_id = ? AND expires_at > ?",
                (expires_at.isoformat(), lock_key, session_id, now.isoformat()),
            )
            conn.commit()
            renewed = result.rowcount > 0
        finally:
            conn.close()
        if renewed:
            logger.debug("Session lock renewed for %s by %s", lock_key, session_id)
        else:
            logger.warning("Session lock renewal failed for %s by %s (expired or not owner)", lock_key, session_id)
        return renewed

    def is_locked(self, lock_key: str) -> bool:
        # Check the in-process lock first — a local coroutine may still be
        # running even if the DB row has expired.
        lock = self._locks.get(lock_key)
        if lock is not None and lock.locked():
            return True

        conn = get_db()
        try:
            now = datetime.now(UTC)
            conn.execute("DELETE FROM session_locks WHERE expires_at <= ?", (now.isoformat(),))
            row = conn.execute(
                "SELECT 1 FROM session_locks WHERE lock_key = ?",
                (lock_key,),
            ).fetchone()
            conn.commit()
            return row is not None
        finally:
            conn.close()


_manager: SessionLockManager | None = None


def get_lock_manager() -> SessionLockManager:
    global _manager
    if _manager is None:
        _manager = SessionLockManager()
    return _manager
