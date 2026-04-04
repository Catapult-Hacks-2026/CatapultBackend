import asyncio
import json
import time
from collections import defaultdict
from typing import Any, Optional

try:
    import redis.asyncio as redis
except ImportError:  # pragma: no cover - optional dependency in some environments
    redis = None

from app.core.config import get_settings

settings = get_settings()
_redis_pool = None
_lock = asyncio.Lock()
_memory_store: dict[str, tuple[Any, Optional[float]]] = {}
_memory_sets: dict[str, set[str]] = defaultdict(set)


async def get_redis():
    global _redis_pool
    if _redis_pool is not None:
        return _redis_pool
    if redis is None or not settings.redis_url:
        return None
    async with _lock:
        if _redis_pool is None:
            _redis_pool = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_pool


def _is_expired(expires_at: Optional[float]) -> bool:
    return expires_at is not None and expires_at <= time.time()


async def cache_set(key: str, value: Any, ttl: Optional[int] = None) -> None:
    client = await get_redis()
    if client is not None:
        payload = json.dumps(value) if not isinstance(value, str) else value
        await client.set(key, payload, ex=ttl)
        return
    expires_at = time.time() + ttl if ttl else None
    _memory_store[key] = (value, expires_at)


async def cache_get(key: str) -> Any:
    client = await get_redis()
    if client is not None:
        value = await client.get(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    item = _memory_store.get(key)
    if item is None:
        return None
    value, expires_at = item
    if _is_expired(expires_at):
        _memory_store.pop(key, None)
        return None
    return value


async def cache_delete(key: str) -> None:
    client = await get_redis()
    if client is not None:
        await client.delete(key)
        return
    _memory_store.pop(key, None)


async def cache_set_if_absent(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    client = await get_redis()
    if client is not None:
        payload = json.dumps(value) if not isinstance(value, str) else value
        return bool(await client.set(key, payload, ex=ttl, nx=True))
    existing = await cache_get(key)
    if existing is not None:
        return False
    await cache_set(key, value, ttl=ttl)
    return True


async def set_add(key: str, value: str) -> None:
    client = await get_redis()
    if client is not None:
        await client.sadd(key, value)
        return
    _memory_sets[key].add(value)


async def set_remove(key: str, value: str) -> None:
    client = await get_redis()
    if client is not None:
        await client.srem(key, value)
        return
    _memory_sets[key].discard(value)


async def set_members(key: str) -> set[str]:
    client = await get_redis()
    if client is not None:
        return {str(item) for item in await client.smembers(key)}
    return set(_memory_sets.get(key, set()))
