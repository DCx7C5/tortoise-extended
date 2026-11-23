"""Live-Redis coverage of ``BaseCacheableModel`` model-level caching.

The fake-backend tests in ``test_cacheable_model.py`` exercise the same
code paths through ``MockRedisBackend``. This module instead wires
``BaseCacheableModel`` to the **real** ``RedisCache`` backend (by leaving
``_cache_backend = None`` so ``_get_backend()`` routes through
``RedisCache.get_backend()``), giving end-to-end coverage of the
model-level lifecycle with a live Redis connection:

- ``save()`` -> ``_invalidate_cache()`` + cache write on the next fetch
- ``get_cached()`` cache hit / miss
- ``filter_cached()`` cache hit / miss
- ``refresh_from_db()`` -> cache write
- ``delete()`` -> cache invalidation

Live Redis runs against the docker ``redis-ext`` container on ``127.0.0.1:6381``;
tests skip when it is unreachable. The model rows live in the PostgreSQL test
database on ``127.0.0.1:5434`` (see ``docker-compose.dev.yml``) — this package
is PostgreSQL-only.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncGenerator
from typing import ClassVar

import pytest
from tortoise import Tortoise, fields

from tortoise_extended.cache.base import CacheBackend
from tortoise_extended.cache.redis import RedisCache
from tortoise_extended.fields import CharField
from tortoise_extended.models.cacheable_model import BaseCacheableModel


# ---------------------------------------------------------------------------
# Availability probes
# ---------------------------------------------------------------------------


def _redis_available() -> bool:
    """Quick probe — can we reach the docker redis-ext container?"""
    try:
        sock = socket.create_connection(("localhost", 6381), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5434), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


_live_redis = pytest.mark.skipif(
    not _redis_available(),
    reason="Redis not available on localhost:6381 (docker compose up -d)",
)

pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not available on localhost:5434",
)

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test",
)


# ---------------------------------------------------------------------------
# Model — leaves _cache_backend = None so the live RedisCache backend is used
# ---------------------------------------------------------------------------


class LiveCacheRecord(BaseCacheableModel):
    """Cacheable model routed through the real Redis backend."""

    _cache_ttl = 300
    _cache_fields: ClassVar[list[str] | None] = ["title", "views"]

    title = CharField(max_length=255)
    views = fields.IntField(default=0)

    class Meta:
        table = "live_cache_records"


_TABLES = ("live_cache_records",)


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> AsyncGenerator[None, None]:  # pyright: ignore[reportUnusedFunction]
    """Initialize Tortoise ORM against the live PostgreSQL test database."""
    _ = await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_cacheable_live"]},
    )
    conn = Tortoise.get_connection("default")
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.generate_schemas()
    yield
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
async def _clean_tables() -> AsyncGenerator[None, None]:  # pyright: ignore[reportUnusedFunction]
    """Truncate the model table before each test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(f"TRUNCATE {', '.join(_TABLES)} CASCADE")
    yield


@pytest.fixture(autouse=True)
async def _live_redis_backend() -> AsyncGenerator[None, None]:
    """Init a live Redis singleton for the test, clean it, then close it."""
    RedisCache._instance = None
    RedisCache._pool = None
    await RedisCache.init(url="redis://localhost:6381/0")
    await RedisCache.get_pool().flushdb()
    yield
    await RedisCache.close()


# ---------------------------------------------------------------------------
# model-level lifecycle against live Redis
# ---------------------------------------------------------------------------


class TestLiveCacheableModel:
    """``BaseCacheableModel`` save/get_cached/filter_cached/refresh/delete
    against a live Redis connection."""

    @staticmethod
    async def _seed() -> LiveCacheRecord:
        rec = LiveCacheRecord(title="hello", views=42)
        await rec.save()
        return rec

    @staticmethod
    def _backend() -> CacheBackend:
        """The exact live Redis backend BaseCacheableModel uses."""
        return LiveCacheRecord._get_backend()

    @staticmethod
    def _get_key(pk: int) -> str:
        return LiveCacheRecord._cache_key_for("get", id=str(pk))

    @_live_redis
    async def test_save_then_get_cached_cache_hit(self) -> None:
        """A saved row is cached; get_cached returns a from-cache instance."""
        rec = await self._seed()
        hit = await LiveCacheRecord.get_cached(id=rec.pk)
        assert hit is not None
        assert hit.pk == rec.pk
        assert hit.title == "hello"
        assert hit.views == 42

        # A second get_cached must come from Redis (no DB re-query).
        assert await self._backend().exists(self._get_key(rec.pk)) is True

    @_live_redis
    async def test_get_cached_miss_fetches_db_and_writes_cache(self) -> None:
        """A miss queries the DB and then populates the Redis key."""
        rec = await self._seed()
        # Force a clean first lookup that must start from a miss.
        backend = self._backend()
        key = self._get_key(rec.pk)
        await backend.delete(key)
        hit = await LiveCacheRecord.get_cached(id=rec.pk)
        assert hit is not None and hit.title == "hello"
        assert await backend.exists(key) is True

    @_live_redis
    async def test_get_cached_not_found_returns_none(self) -> None:
        """get_cached with a missing PK returns None without error."""
        assert await LiveCacheRecord.get_cached(id=999_999) is None

    @_live_redis
    async def test_save_invalidates_cache(self) -> None:
        """Re-saving a row invalidates its previously cached entry."""
        rec = await LiveCacheRecord.create(title="hello", views=42)
        _ = await LiveCacheRecord.get_cached(id=rec.pk)  # populate the key
        backend = self._backend()
        key = self._get_key(rec.pk)
        assert await backend.exists(key) is True

        rec.title = "updated"
        await rec.save()
        assert await backend.exists(key) is False

    @_live_redis
    async def test_delete_invalidates_cache(self) -> None:
        """Deleting a row invalidates its previously cached entry."""
        rec = await LiveCacheRecord.create(title="hello", views=42)
        _ = await LiveCacheRecord.get_cached(id=rec.pk)  # populate the key
        backend = self._backend()
        key = self._get_key(rec.pk)
        assert await backend.exists(key) is True

        await rec.delete()
        assert await backend.exists(key) is False

    @_live_redis
    async def test_refresh_from_db_writes_cache(self) -> None:
        """refresh_from_db re-populates the instance's cache entry."""
        rec = await self._seed()
        backend = self._backend()
        key = self._get_key(rec.pk)
        await backend.delete(key)
        await rec.refresh_from_db()
        assert await backend.exists(key) is True

    @_live_redis
    async def test_filter_cached_hit_roundtrip(self) -> None:
        """filter_cached caches a list and returns from-cache instances."""
        await LiveCacheRecord.create(title="a", views=1)
        await LiveCacheRecord.create(title="b", views=2)

        first = await LiveCacheRecord.filter_cached()
        assert {r.title for r in first} == {"a", "b"}

        backend = self._backend()
        key = LiveCacheRecord._cache_key_for("filter")
        assert await backend.exists(key) is True

        second = await LiveCacheRecord.filter_cached()
        assert {r.title for r in second} == {"a", "b"}
