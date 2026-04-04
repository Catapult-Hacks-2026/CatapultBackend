from __future__ import annotations

import asyncio


class EmailSessionRegistry:
    def __init__(self) -> None:
        self._workers: dict[str, object] = {}
        self._processed_message_ids: set[str] = set()
        self._lock = asyncio.Lock()

    async def register_worker(self, session_id: str, worker: object) -> None:
        async with self._lock:
            self._workers[session_id] = worker

    async def unregister_worker(self, session_id: str) -> None:
        async with self._lock:
            self._workers.pop(session_id, None)

    async def get_worker(self, session_id: str) -> object | None:
        async with self._lock:
            return self._workers.get(session_id)

    async def mark_processed(self, message_id: str) -> bool:
        async with self._lock:
            if message_id and message_id in self._processed_message_ids:
                return False
            if message_id:
                self._processed_message_ids.add(message_id)
            return True


_registry: EmailSessionRegistry | None = None


def get_email_session_registry() -> EmailSessionRegistry:
    global _registry
    if _registry is None:
        _registry = EmailSessionRegistry()
    return _registry
