from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.infrastructure.models import Base
from science_buddy.services.cache import ThreeLevelCache


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value.encode()

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def scan_iter(self, match: str) -> AsyncIterator[str]:
        prefix = match.removesuffix("*")
        for key in list(self.values):
            if key.startswith(prefix):
                yield key


def make_cache(
    redis: FakeRedis,
    sessions: async_sessionmaker[Any],
) -> ThreeLevelCache:
    return ThreeLevelCache(
        redis=redis,  # type: ignore[arg-type]
        session_factory=sessions,  # type: ignore[arg-type]
        namespace="test",
        l1_max_entries=16,
        l1_ttl_seconds=60,
        l2_ttl_seconds=60,
        l3_ttl_seconds=300,
    )


@pytest.mark.asyncio
async def test_three_level_cache_promotes_redis_and_sqlite(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cache.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    redis = FakeRedis()
    cache1 = make_cache(redis, sessions)
    await cache1.set("retrieval:project", "key", {"value": 42})

    assert (await cache1.get("retrieval:project", "key")).level == "l1-memory"  # type: ignore[union-attr]

    cache2 = make_cache(redis, sessions)
    assert (await cache2.get("retrieval:project", "key")).level == "l2-redis"  # type: ignore[union-attr]

    redis.values.clear()
    cache3 = make_cache(redis, sessions)
    hit = await cache3.get("retrieval:project", "key")
    assert hit is not None
    assert hit.level == "l3-sqlite"
    assert hit.value == {"value": 42}
    await engine.dispose()
