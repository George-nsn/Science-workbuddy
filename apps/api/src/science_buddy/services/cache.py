import asyncio
import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from science_buddy.infrastructure.models import CacheEntry


@dataclass(frozen=True, slots=True)
class CacheHit:
    value: dict[str, Any]
    level: str


class MemoryTTLCache:
    def __init__(self, *, max_entries: int, ttl_seconds: int) -> None:
        self._max_entries = max_entries
        self._ttl = timedelta(seconds=ttl_seconds)
        self._values: OrderedDict[str, tuple[datetime, dict[str, Any]]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> dict[str, Any] | None:
        async with self._lock:
            item = self._values.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= datetime.now(UTC):
                self._values.pop(key, None)
                return None
            self._values.move_to_end(key)
            return value

    async def set(self, key: str, value: dict[str, Any]) -> None:
        async with self._lock:
            self._values[key] = (datetime.now(UTC) + self._ttl, value)
            self._values.move_to_end(key)
            while len(self._values) > self._max_entries:
                self._values.popitem(last=False)

    async def delete_prefix(self, prefix: str) -> None:
        async with self._lock:
            for key in [key for key in self._values if key.startswith(prefix)]:
                self._values.pop(key, None)


class ThreeLevelCache:
    """L1 process memory → L2 Redis → L3 durable SQLite cache."""

    def __init__(
        self,
        *,
        redis: Redis,
        session_factory: async_sessionmaker[AsyncSession],
        namespace: str,
        l1_max_entries: int,
        l1_ttl_seconds: int,
        l2_ttl_seconds: int,
        l3_ttl_seconds: int,
    ) -> None:
        self._redis = redis
        self._sessions = session_factory
        self._namespace = namespace
        self._memory = MemoryTTLCache(
            max_entries=l1_max_entries,
            ttl_seconds=l1_ttl_seconds,
        )
        self._l2_ttl = l2_ttl_seconds
        self._l3_ttl = l3_ttl_seconds

    @staticmethod
    def digest(value: dict[str, Any]) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def scoped_key(self, namespace: str, key: str) -> str:
        return f"{self._namespace}:{namespace}:{key}"

    async def get(self, namespace: str, key: str) -> CacheHit | None:
        scoped = self.scoped_key(namespace, key)
        if value := await self._memory.get(scoped):
            return CacheHit(value=value, level="l1-memory")
        try:
            raw = await self._redis.get(scoped)
            if raw:
                decoded = json.loads(raw)
                if isinstance(decoded, dict):
                    await self._memory.set(scoped, decoded)
                    return CacheHit(value=decoded, level="l2-redis")
        except (RedisError, json.JSONDecodeError, TypeError):
            pass

        now = datetime.now(UTC)
        async with self._sessions() as session:
            entry = await session.scalar(
                select(CacheEntry).where(
                    CacheEntry.namespace == namespace,
                    CacheEntry.cache_key == key,
                    CacheEntry.expires_at > now,
                )
            )
            if entry is None:
                await session.execute(
                    delete(CacheEntry).where(
                        CacheEntry.namespace == namespace,
                        CacheEntry.cache_key == key,
                    )
                )
                await session.commit()
                return None
            value = dict(entry.value)
        await self._memory.set(scoped, value)
        try:
            await self._redis.setex(scoped, self._l2_ttl, json.dumps(value))
        except RedisError:
            pass
        return CacheHit(value=value, level="l3-sqlite")

    async def set(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        scoped = self.scoped_key(namespace, key)
        await self._memory.set(scoped, value)
        try:
            await self._redis.setex(scoped, self._l2_ttl, json.dumps(value))
        except RedisError:
            pass
        expires_at = datetime.now(UTC) + timedelta(seconds=self._l3_ttl)
        statement = sqlite_insert(CacheEntry).values(
            namespace=namespace,
            cache_key=key,
            value=value,
            expires_at=expires_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[CacheEntry.namespace, CacheEntry.cache_key],
            set_={"value": value, "expires_at": expires_at, "updated_at": datetime.now(UTC)},
        )
        async with self._sessions() as session:
            await session.execute(statement)
            await session.commit()

    async def invalidate_namespace(self, namespace: str) -> None:
        prefix = self.scoped_key(namespace, "")
        await self._memory.delete_prefix(prefix)
        try:
            async for key in self._redis.scan_iter(match=f"{prefix}*"):
                await self._redis.delete(key)
        except RedisError:
            pass
        async with self._sessions() as session:
            await session.execute(delete(CacheEntry).where(CacheEntry.namespace == namespace))
            await session.commit()

    async def cleanup_expired(self, now: datetime | None = None) -> int:
        async with self._sessions() as session:
            result = await session.execute(
                delete(CacheEntry).where(CacheEntry.expires_at <= (now or datetime.now(UTC)))
            )
            await session.commit()
            return int(result.rowcount or 0)
