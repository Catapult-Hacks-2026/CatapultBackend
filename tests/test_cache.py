import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.core.cache import (
    cache_delete,
    cache_get,
    cache_set,
    cache_set_if_absent,
    set_add,
    set_members,
    set_remove,
)


@pytest.fixture()
def no_redis():
    """Force all cache operations to use the in-memory fallback."""
    with patch("app.core.cache.get_redis", new_callable=AsyncMock, return_value=None):
        yield


class TestCacheSetGet:

    @pytest.mark.asyncio
    async def test_set_and_get_string(self, no_redis):
        await cache_set("key1", "hello")
        assert await cache_get("key1") == "hello"

    @pytest.mark.asyncio
    async def test_set_and_get_dict(self, no_redis):
        data = {"price": 42, "vendor": "Acme"}
        await cache_set("key2", data)
        assert await cache_get("key2") == data

    @pytest.mark.asyncio
    async def test_get_missing_key(self, no_redis):
        assert await cache_get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, no_redis):
        await cache_set("expiring", "value", ttl=1)
        assert await cache_get("expiring") == "value"
        await asyncio.sleep(1.1)
        assert await cache_get("expiring") is None

    @pytest.mark.asyncio
    async def test_no_ttl_persists(self, no_redis):
        await cache_set("persistent", "value")
        assert await cache_get("persistent") == "value"

    @pytest.mark.asyncio
    async def test_overwrite_value(self, no_redis):
        await cache_set("key", "v1")
        await cache_set("key", "v2")
        assert await cache_get("key") == "v2"


class TestCacheDelete:

    @pytest.mark.asyncio
    async def test_delete_existing(self, no_redis):
        await cache_set("to_delete", "val")
        await cache_delete("to_delete")
        assert await cache_get("to_delete") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, no_redis):
        await cache_delete("nope")  # should not raise


class TestCacheSetIfAbsent:

    @pytest.mark.asyncio
    async def test_set_when_absent(self, no_redis):
        result = await cache_set_if_absent("new_key", "val", ttl=60)
        assert result is True
        assert await cache_get("new_key") == "val"

    @pytest.mark.asyncio
    async def test_skip_when_present(self, no_redis):
        await cache_set("existing", "original")
        result = await cache_set_if_absent("existing", "new_value")
        assert result is False
        assert await cache_get("existing") == "original"


class TestSetOperations:

    @pytest.mark.asyncio
    async def test_add_and_members(self, no_redis):
        await set_add("myset", "a")
        await set_add("myset", "b")
        members = await set_members("myset")
        assert members == {"a", "b"}

    @pytest.mark.asyncio
    async def test_add_duplicate(self, no_redis):
        await set_add("myset", "a")
        await set_add("myset", "a")
        members = await set_members("myset")
        assert members == {"a"}

    @pytest.mark.asyncio
    async def test_remove_member(self, no_redis):
        await set_add("myset", "a")
        await set_add("myset", "b")
        await set_remove("myset", "a")
        members = await set_members("myset")
        assert members == {"b"}

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, no_redis):
        await set_remove("myset", "ghost")  # should not raise

    @pytest.mark.asyncio
    async def test_members_empty_set(self, no_redis):
        members = await set_members("empty_set")
        assert members == set()


class TestRedisIntegration:
    """Tests that run against a real Redis if available, otherwise skip."""

    @pytest_asyncio.fixture()
    async def live_redis(self):
        try:
            import redis.asyncio as aioredis
        except ImportError:
            pytest.skip("redis package not installed")
        client = aioredis.from_url("redis://localhost:6379/15", decode_responses=True)
        try:
            await client.ping()
        except Exception:
            pytest.skip("Redis not running on localhost:6379")
        await client.flushdb()
        with patch("app.core.cache.get_redis", new_callable=AsyncMock, return_value=client):
            yield client
        await client.flushdb()
        await client.aclose()

    @pytest.mark.asyncio
    async def test_set_get_roundtrip(self, live_redis):
        await cache_set("rk", {"data": 1})
        assert await cache_get("rk") == {"data": 1}

    @pytest.mark.asyncio
    async def test_set_if_absent_with_redis(self, live_redis):
        assert await cache_set_if_absent("once", "first") is True
        assert await cache_set_if_absent("once", "second") is False
        assert await cache_get("once") == "first"

    @pytest.mark.asyncio
    async def test_set_operations_with_redis(self, live_redis):
        await set_add("rs", "x")
        await set_add("rs", "y")
        assert await set_members("rs") == {"x", "y"}
        await set_remove("rs", "x")
        assert await set_members("rs") == {"y"}

    @pytest.mark.asyncio
    async def test_ttl_with_redis(self, live_redis):
        await cache_set("ttl_key", "val", ttl=1)
        assert await cache_get("ttl_key") == "val"
        await asyncio.sleep(1.1)
        assert await cache_get("ttl_key") is None

    @pytest.mark.asyncio
    async def test_delete_with_redis(self, live_redis):
        await cache_set("del_me", "gone")
        await cache_delete("del_me")
        assert await cache_get("del_me") is None
