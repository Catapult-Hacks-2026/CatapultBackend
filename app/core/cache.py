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


def vendor_working_memory_key(vendor_name: str, product_category: str) -> str:
    return f"wm:vendor:{vendor_name}:{product_category}"


def negotiation_working_memory_key(negotiation_id: str) -> str:
    return f"wm:negotiation:{negotiation_id}"


def category_working_memory_key(product_category: str) -> str:
    return f"wm:category:{product_category}"


def campaign_working_memory_key(campaign_id: str) -> str:
    return f"wm:campaign:{campaign_id}"


async def get_working_memory(key: str) -> Any:
    return await cache_get(key)


async def put_working_memory(key: str, value: Any, ttl: Optional[int] = None) -> None:
    await cache_set(key, value, ttl=ttl)


async def delete_working_memory(key: str) -> None:
    await cache_delete(key)


async def get_vendor_working_memory(vendor_name: str, product_category: str) -> Any:
    return await get_working_memory(vendor_working_memory_key(vendor_name, product_category))


async def put_vendor_working_memory(
    vendor_name: str,
    product_category: str,
    value: Any,
    ttl: Optional[int] = None,
) -> None:
    await put_working_memory(
        vendor_working_memory_key(vendor_name, product_category),
        value,
        ttl=ttl,
    )


async def get_negotiation_working_memory(negotiation_id: str) -> Any:
    return await get_working_memory(negotiation_working_memory_key(negotiation_id))


async def put_negotiation_working_memory(
    negotiation_id: str,
    value: Any,
    ttl: Optional[int] = None,
) -> None:
    await put_working_memory(
        negotiation_working_memory_key(negotiation_id),
        value,
        ttl=ttl,
    )


async def get_category_working_memory(product_category: str) -> Any:
    return await get_working_memory(category_working_memory_key(product_category))


async def put_category_working_memory(
    product_category: str,
    value: Any,
    ttl: Optional[int] = None,
) -> None:
    await put_working_memory(
        category_working_memory_key(product_category),
        value,
        ttl=ttl,
    )


async def get_campaign_working_memory(campaign_id: str) -> Any:
    return await get_working_memory(campaign_working_memory_key(campaign_id))


async def put_campaign_working_memory(
    campaign_id: str,
    value: Any,
    ttl: Optional[int] = None,
) -> None:
    await put_working_memory(
        campaign_working_memory_key(campaign_id),
        value,
        ttl=ttl,
    )
