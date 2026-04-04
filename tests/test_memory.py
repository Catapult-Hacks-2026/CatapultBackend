import json
import unittest
from unittest.mock import patch

from app.services import memory


class FakePipeline:
    def __init__(self, redis_client) -> None:
        self.redis_client = redis_client
        self.ops: list[tuple] = []

    def delete(self, key: str):
        self.ops.append(("delete", key))
        return self

    def rpush(self, key: str, value: str):
        self.ops.append(("rpush", key, value))
        return self

    def expire(self, key: str, ttl: int):
        self.ops.append(("expire", key, ttl))
        return self

    def execute(self):
        for op in self.ops:
            name = op[0]
            if name == "delete":
                self.redis_client.store.pop(op[1], None)
            elif name == "rpush":
                self.redis_client.store.setdefault(op[1], []).append(op[2])
            elif name == "expire":
                self.redis_client.expirations[op[1]] = op[2]
        return True


class FakeRedis:
    def __init__(self, store: dict[str, list[str]] | None = None) -> None:
        self.store = store or {}
        self.expirations: dict[str, int] = {}

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        values = self.store.get(key, [])
        normalized_end = None if end == -1 else end + 1
        return values[start:normalized_end]

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    def rpush(self, key: str, value: str) -> None:
        self.store.setdefault(key, []).append(value)

    def expire(self, key: str, ttl: int) -> None:
        self.expirations[key] = ttl


class MemoryTests(unittest.TestCase):
    def test_load_negotiation_working_memory_uses_redis_cache(self) -> None:
        redis_client = FakeRedis(
            {
                memory._redis_key("neg-1"): [
                    json.dumps(
                        {
                            "role": "assistant",
                            "content": "cached",
                            "structured_data": "{\"unit_price\": 99}",
                            "created_at": "2026-04-04T00:00:00+00:00",
                        }
                    )
                ]
            }
        )

        with patch("app.services.memory.get_redis", return_value=redis_client):
            result = memory.load_negotiation_working_memory("neg-1", limit=8)

        self.assertEqual(result[0]["content"], "cached")
        self.assertEqual(result[0]["structured_data"]["unit_price"], 99)

    def test_load_negotiation_working_memory_falls_back_and_backfills_redis(self) -> None:
        redis_client = FakeRedis()
        sqlite_memory = [
            {
                "role": "user",
                "content": "Need better pricing",
                "structured_data": {"unit_price": 120},
                "created_at": "2026-04-04T00:00:00+00:00",
            }
        ]

        with (
            patch("app.services.memory.get_redis", return_value=redis_client),
            patch("app.services.memory._load_from_sqlite", return_value=sqlite_memory) as load_sqlite,
        ):
            result = memory.load_negotiation_working_memory("neg-2", limit=8)

        self.assertEqual(result, sqlite_memory)
        load_sqlite.assert_called_once_with("neg-2", 8)
        cached = redis_client.store[memory._redis_key("neg-2")]
        self.assertEqual(json.loads(cached[0])["content"], "Need better pricing")
        self.assertEqual(redis_client.expirations[memory._redis_key("neg-2")], 3600)

    def test_refresh_negotiation_working_memory_rewrites_cache(self) -> None:
        redis_client = FakeRedis(
            {memory._redis_key("neg-3"): [json.dumps({"content": "stale"})]}
        )
        refreshed = [
            {
                "role": "assistant",
                "content": "fresh",
                "structured_data": None,
                "created_at": "2026-04-04T00:00:00+00:00",
            }
        ]

        with (
            patch("app.services.memory.get_redis", return_value=redis_client),
            patch("app.services.memory._load_from_sqlite", return_value=refreshed),
        ):
            result = memory.refresh_negotiation_working_memory("neg-3")

        self.assertEqual(result, refreshed)
        cached = redis_client.store[memory._redis_key("neg-3")]
        self.assertEqual(json.loads(cached[0])["content"], "fresh")

    def test_store_memory_candidates_persists_to_redis(self) -> None:
        redis_client = FakeRedis()
        candidates = [
            {"vendor_name": "acme", "summary": "summary", "latest_offer": {"unit_price": 90}}
        ]

        with patch("app.services.memory.get_redis", return_value=redis_client):
            memory.store_memory_candidates(candidates)

        key = "negotiation_memory:candidates:acme"
        self.assertEqual(len(redis_client.store[key]), 1)
        self.assertEqual(redis_client.expirations[key], 86400)


if __name__ == "__main__":
    unittest.main()
