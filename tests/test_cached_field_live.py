"""Live PostgreSQL + Redis tests for the cache-backed field layer (row 3230).

Every test here runs against the real docker containers: PostgreSQL on
``127.0.0.1:5434`` (``TORTOISE_TEST_DB`` overrides the DSN) and Redis (AUTH) on
``127.0.0.1:6381``.  The cache side is
the real :class:`RedisCacheBackend` in the private ``cfl`` namespace on database
15; the database side is a real PostgreSQL row.  Nothing is mocked.

Coverage map (the dispatched cases):

1. ``save()`` write-through is a mixin behaviour; a bare cache-aside model
   populates nothing on save
2. a cache hit never reads the database
3. a miss fills the key from the column
4. ``ttl=1`` expires, ``ttl=0`` has no expiry at all (Redis TTL ``-1``)
5. ``delete()`` clears every cache field on the model
6. ``CachedField.cached_set`` deletes the key; the column stays authoritative
7. ``RedisPrimaryField.cached_set`` writes before save; eviction loses it
8. an omitted ``update_fields`` entry invalidates instead of caching
9. ``QuerySet.update()`` leaves a stale key until ``cached_invalidate``
10. the failure matrix: not-initialised raises, Redis-down fails open
11. ``prefetch_cached`` issues exactly one MGET and one pipelined set
12. a partial instance raises ``ValueError`` and never caches an unloaded field
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import cast, override

import pytest
import redis.asyncio as aioredis
from tortoise import Tortoise, models
from tortoise.fields import CharField, DatetimeField, IntField, JSONField
from tortoise.models import Model

from tortoise_extended import (
    CachedField,
    CachedFieldsMixin,
    RedisPrimaryField,
    cached_get,
    cached_invalidate,
    cached_set,
    prefetch_cached,
)
from tortoise_extended._types import CacheScalar, CacheValue
from tortoise_extended.cache.redis import RedisCache, RedisCacheBackend
from tortoise_extended.exceptions import (
    CacheBackendNotInitializedError,
    RedisCacheError,
)
from tortoise_extended.fields._cache_codec import CacheCodec
from tortoise_extended.fields._cached_base import _CachedFieldBase

from tests._pg_env import database_url, pg_skip_reason
from tests._redis_env import redis_skip_reason, redis_url


# ---------------------------------------------------------------------------
# Availability probes — the whole module skips without PG, each class without
# AUTH-enabled Redis (the containers require a password).
# ---------------------------------------------------------------------------


_REDIS_SKIP_REASON = redis_skip_reason()
_live_redis = pytest.mark.skipif(
    _REDIS_SKIP_REASON is not None, reason=_REDIS_SKIP_REASON or ""
)


_PG_SKIP_REASON = pg_skip_reason()
pytestmark = pytest.mark.skipif(
    _PG_SKIP_REASON is not None,
    reason=_PG_SKIP_REASON or "",
)

DB_URL = database_url()


# ---------------------------------------------------------------------------
# Models — importable as the Tortoise models module
# ---------------------------------------------------------------------------


class LiveItem(models.Model):
    """Cache-aside column on a bare model: no save hook, the read fills."""

    id = IntField(primary_key=True)
    title = CachedField(CharField(max_length=64), ttl=60, namespace="cfl")

    class Meta:
        table = "cfl_items"


class LiveProfile(CachedFieldsMixin, models.Model):
    """Redis-first column behind the write-through mixin."""

    id = IntField(primary_key=True)
    nickname = RedisPrimaryField(JSONField(), ttl=60, namespace="cfl")

    class Meta:
        table = "cfl_profiles"


class LiveMixed(CachedFieldsMixin, models.Model):
    """Two cache fields (same policy) plus a plain column to update alone."""

    id = IntField(primary_key=True)
    title = CachedField(CharField(max_length=64), ttl=60, namespace="cfl")
    subtitle = CachedField(CharField(max_length=64), ttl=60, namespace="cfl")
    note = CharField(max_length=64, null=True)

    class Meta:
        table = "cfl_mixed"


class LiveStamp(CachedFieldsMixin, models.Model):
    """Mixin model whose cached column needs a tagged codec payload."""

    id = IntField(primary_key=True)
    at = CachedField(DatetimeField(), ttl=60, namespace="cfl")

    class Meta:
        table = "cfl_stamps"


_TABLES = ("cfl_items", "cfl_profiles", "cfl_mixed", "cfl_stamps")

_CODEC = CacheCodec()
"""Codec the field layer wraps values in before handing them to the backend."""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
async def _init_stack() -> AsyncGenerator[None, None]:  # pyright: ignore[reportUnusedFunction]
    """Initialise Tortoise and the live Redis singleton, then build schemas."""
    _ = await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_cached_field_live"]},
    )
    await RedisCache.init(url=redis_url(db=15))
    conn = Tortoise.get_connection("default")
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.generate_schemas()
    yield
    await RedisCache.close()
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
async def _clean_state() -> AsyncGenerator[None, None]:  # pyright: ignore[reportUnusedFunction]
    """Truncate every table and flush the private ``cfl`` namespace."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(f"TRUNCATE {', '.join(_TABLES)} CASCADE")
    await RedisCache.get_backend(namespace="cfl").delete_pattern("*")
    yield
    await RedisCache.get_backend(namespace="cfl").delete_pattern("*")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _backend() -> RedisCacheBackend:
    """The live backend for the private ``cfl`` namespace."""
    return RedisCache.get_backend(namespace="cfl", default_ttl=60)


def _field(model: type[Model], name: str) -> _CachedFieldBase[CacheScalar]:
    """Return the cache-field wrapper declared under *name* on *model*."""
    return cast(_CachedFieldBase[CacheScalar], model._meta.fields_map[name])


async def _cached_value(key: str) -> CacheScalar:
    """Read *key* through the live backend and decode the stored payload."""
    raw = await _backend().get(key)
    assert raw is not None, f"expected a cached value at {key!r}"
    return _CODEC.decode(raw)


async def _redis_ttl(key: str) -> int:
    """Raw Redis TTL for an unprefixed cache key (``-1`` means no expiry)."""
    return await RedisCache.get_pool().ttl(f"{_backend().namespace}:{key}")


class _CountingBackend(RedisCacheBackend):
    """Live namespace backend that counts the batched calls prefetch issues."""

    def __init__(self, *, namespace: str = "cfl", default_ttl: int = 60) -> None:
        super().__init__(
            pool=RedisCache.get_pool(),
            namespace=namespace,
            default_ttl=default_ttl,
        )
        self.get_many_calls = 0
        self.set_many_calls = 0

    @override
    async def get_many(self, keys: list[str]) -> dict[str, CacheValue]:
        self.get_many_calls += 1
        return await super().get_many(keys)

    @override
    async def set_many(
        self, mapping: dict[str, CacheValue], ttl: int | None = None
    ) -> None:
        self.set_many_calls += 1
        await super().set_many(mapping, ttl=ttl)


# ---------------------------------------------------------------------------
# Case 1 — save() write-through
# ---------------------------------------------------------------------------


@_live_redis
class TestSaveHooks:
    """The write-through hook belongs to ``CachedFieldsMixin``, not the field."""

    async def test_mixed_create_populates_both_keys(self) -> None:
        mixed = await LiveMixed.create(title="m", subtitle="s")
        title_key = _field(LiveMixed, "title").cached_key(mixed)
        subtitle_key = _field(LiveMixed, "subtitle").cached_key(mixed)
        assert await _cached_value(title_key) == "m"
        assert await _cached_value(subtitle_key) == "s"

    async def test_mixed_save_rewrites_the_keys(self) -> None:
        mixed = await LiveMixed.create(title="m", subtitle="s")
        mixed.title = "m2"
        mixed.subtitle = "s2"
        await mixed.save()
        title_key = _field(LiveMixed, "title").cached_key(mixed)
        subtitle_key = _field(LiveMixed, "subtitle").cached_key(mixed)
        assert await _cached_value(title_key) == "m2"
        assert await _cached_value(subtitle_key) == "s2"

    async def test_profile_save_populates_the_write_through_key(self) -> None:
        profile = await LiveProfile.create(nickname={"display": "p"})
        key = _field(LiveProfile, "nickname").cached_key(profile)
        assert await _cached_value(key) == {"display": "p"}
        profile.nickname = {"display": "q"}
        await profile.save()
        assert await _cached_value(key) == {"display": "q"}

    async def test_plain_model_save_does_not_populate(self) -> None:
        item = await LiveItem.create(title="plain")
        key = _field(LiveItem, "title").cached_key(item)
        assert await _backend().get(key) is None

    async def test_mixin_uses_the_codec_for_tagged_types(self) -> None:
        moment = datetime(2026, 11, 24, 9, 30, tzinfo=UTC)
        stamp = await LiveStamp.create(at=moment)
        key = _field(LiveStamp, "at").cached_key(stamp)
        cached = await _cached_value(key)
        assert isinstance(cached, datetime)
        assert cached == moment


# ---------------------------------------------------------------------------
# Cases 2 and 3 — cached_get hit / miss against the real database
# ---------------------------------------------------------------------------


@_live_redis
class TestCachedGetLive:
    """A hit must not read the row; a miss must fill the key from the column."""

    async def test_hit_never_reads_the_database(self) -> None:
        item = await LiveItem.create(title="cached")
        assert await cached_get(item, "title") == "cached"
        await LiveItem.filter(pk=item.pk).update(title="db")
        fresh = await LiveItem.get(pk=item.pk)
        assert fresh.title == "db"
        # A DB read would return "db"; the cache hit must win.
        assert await cached_get(fresh, "title") == "cached"

    async def test_miss_fills_the_key_from_the_column(self) -> None:
        item = await LiveItem.create(title="column")
        key = _field(LiveItem, "title").cached_key(item)
        assert await _backend().get(key) is None
        assert await cached_get(item, "title") == "column"
        assert await _cached_value(key) == "column"


# ---------------------------------------------------------------------------
# Case 4 — TTL semantics against real Redis expiry
# ---------------------------------------------------------------------------


@_live_redis
class TestTtlLive:
    """``ttl=1`` expires; ``ttl=0`` is a real "no expiry", not a default."""

    async def test_ttl_one_expires(self) -> None:
        field = _field(LiveItem, "title")
        original = field.ttl
        item = await LiveItem.create(title="short")
        key = field.cached_key(item)
        field.ttl = 1
        try:
            assert await cached_get(item, "title") == "short"
            assert await _backend().get(key) is not None
            assert 0 <= await _redis_ttl(key) <= 1
            await asyncio.sleep(1.2)
            assert await _backend().get(key) is None
        finally:
            field.ttl = original

    async def test_ttl_zero_never_expires(self) -> None:
        field = _field(LiveItem, "title")
        original = field.ttl
        item = await LiveItem.create(title="forever")
        key = field.cached_key(item)
        field.ttl = 0
        try:
            assert await cached_get(item, "title") == "forever"
            assert await _redis_ttl(key) == -1
            await asyncio.sleep(1.2)
            assert await _cached_value(key) == "forever"
        finally:
            field.ttl = original


# ---------------------------------------------------------------------------
# Case 5 — delete() clears every cache field
# ---------------------------------------------------------------------------


@_live_redis
class TestDeleteLive:
    """``delete()`` removes the keys of every cache field on the model."""

    async def test_delete_removes_every_cache_field_key(self) -> None:
        mixed = await LiveMixed.create(title="t", subtitle="s")
        title_key = _field(LiveMixed, "title").cached_key(mixed)
        subtitle_key = _field(LiveMixed, "subtitle").cached_key(mixed)
        assert await _backend().get(title_key) is not None
        assert await _backend().get(subtitle_key) is not None
        await mixed.delete()
        assert await _backend().get(title_key) is None
        assert await _backend().get(subtitle_key) is None

    async def test_delete_removes_the_write_through_key(self) -> None:
        profile = await LiveProfile.create(nickname={"display": "p"})
        key = _field(LiveProfile, "nickname").cached_key(profile)
        assert await _backend().get(key) is not None
        await profile.delete()
        assert await _backend().get(key) is None


# ---------------------------------------------------------------------------
# Case 6 — cache-aside cached_set
# ---------------------------------------------------------------------------


@_live_redis
class TestCacheAsideCachedSetLive:
    """``CachedField.cached_set`` deletes the key; the column stays authority."""

    async def test_set_deletes_key_and_the_column_stays_authoritative(self) -> None:
        item = await LiveItem.create(title="old")
        assert await cached_get(item, "title") == "old"
        key = _field(LiveItem, "title").cached_key(item)
        await cached_set(item, "title", "new")
        # Ordinary Python attribute semantics for the mutated instance.
        assert item.title == "new"
        assert await _backend().get(key) is None
        # Any other instance still sees the column, and the key refills from it.
        fresh = await LiveItem.get(pk=item.pk)
        assert fresh.title == "old"
        assert await cached_get(fresh, "title") == "old"
        assert await _cached_value(key) == "old"
        # ``refresh_from_db()`` re-reads the row but must leave the key alone:
        # the reload is not a cache refresh (CachedField docstring contract).
        reloaded = await LiveItem.get(pk=item.pk)
        await LiveItem.filter(pk=item.pk).update(title="db-after-refresh")
        await reloaded.refresh_from_db()
        assert reloaded.title == "db-after-refresh"
        assert await _cached_value(key) == "old"


# ---------------------------------------------------------------------------
# Case 7 — write-through cached_set
# ---------------------------------------------------------------------------


@_live_redis
class TestWriteThroughCachedSetLive:
    """``cached_set`` writes the key before save; eviction loses the copy."""

    async def test_set_writes_key_before_save_and_eviction_loses_it(self) -> None:
        profile = await LiveProfile.create(nickname={"display": "saved"})
        key = _field(LiveProfile, "nickname").cached_key(profile)
        assert await _cached_value(key) == {"display": "saved"}
        await cached_set(profile, "nickname", {"display": "unsaved"})
        assert await _cached_value(key) == {"display": "unsaved"}
        # Simulated eviction: the unsaved value was never durable.
        assert await _backend().delete(key) is True
        fresh = await LiveProfile.get(pk=profile.pk)
        assert fresh.nickname == {"display": "saved"}
        assert await cached_get(fresh, "nickname") == {"display": "saved"}


# ---------------------------------------------------------------------------
# Case 8 — save(update_fields=...) omitting a cache field
# ---------------------------------------------------------------------------


@_live_redis
class TestUpdateFieldsLive:
    """An omitted cache field is invalidated, never encoded from memory."""

    async def test_omitted_update_fields_invalidates_not_caches(self) -> None:
        mixed = await LiveMixed.create(title="saved", subtitle="sub", note="n")
        title_key = _field(LiveMixed, "title").cached_key(mixed)
        subtitle_key = _field(LiveMixed, "subtitle").cached_key(mixed)
        assert await _cached_value(title_key) == "saved"
        assert await _cached_value(subtitle_key) == "sub"
        mixed.title = "unsaved"
        mixed.subtitle = "unsaved-sub"
        await mixed.save(update_fields=["note"])
        assert await _backend().get(title_key) is None
        assert await _backend().get(subtitle_key) is None
        assert mixed.title == "unsaved"
        fresh = await LiveMixed.get(pk=mixed.pk)
        assert fresh.title == "saved"
        assert fresh.subtitle == "sub"


# ---------------------------------------------------------------------------
# Case 9 — QuerySet.update() bypass
# ---------------------------------------------------------------------------


@_live_redis
class TestQuerySetUpdateLive:
    """``QuerySet.update()`` bypasses the hook; the key stays stale."""

    async def test_update_leaves_stale_key_until_invalidate(self) -> None:
        item = await LiveItem.create(title="v1")
        assert await cached_get(item, "title") == "v1"
        key = _field(LiveItem, "title").cached_key(item)
        await LiveItem.filter(pk=item.pk).update(title="v2")
        assert await _cached_value(key) == "v1"
        fresh = await LiveItem.get(pk=item.pk)
        assert fresh.title == "v2"
        assert await cached_invalidate(item, "title") is True
        assert await _backend().get(key) is None
        assert await cached_invalidate(item, "title") is False


# ---------------------------------------------------------------------------
# Case 10 — failure matrix
# ---------------------------------------------------------------------------


@_live_redis
class TestFailureMatrixLive:
    """Not-initialised raises; a real failing Redis fails open and is swallowed."""

    async def test_helpers_raise_when_redis_is_not_initialised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        item = await LiveItem.create(title="row")
        # ``init()`` stores the pool on the singleton *instance*
        # (``cache/redis.py``: ``instance._pool = new_pool``), so a class-level
        # patch is shadowed by the instance attribute and would prove nothing.
        # Patching the instance keeps the process-wide singleton intact and is
        # undone by monkeypatch; the close/init path is covered in
        # ``tests/test_cache_live.py``.
        monkeypatch.setattr(RedisCache(), "_pool", None)
        with pytest.raises(CacheBackendNotInitializedError):
            await cached_get(item, "title")
        with pytest.raises(CacheBackendNotInitializedError):
            await cached_set(item, "title", "other")
        with pytest.raises(CacheBackendNotInitializedError):
            await cached_invalidate(item, "title")
        with pytest.raises(CacheBackendNotInitializedError):
            await prefetch_cached([item], "title")

    async def test_mixin_swallows_not_initialised_on_save_and_delete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mixed = await LiveMixed.create(title="t", subtitle="s", note="n")
        title_key = _field(LiveMixed, "title").cached_key(mixed)
        assert await _cached_value(title_key) == "t"
        # An uninitialised backend raises ``CacheBackendNotInitializedError``,
        # which is a ``CacheError`` but NOT a ``RedisCacheError``: narrowing the
        # mixin's ``except CacheError`` lets it escape from save/delete.
        monkeypatch.setattr(RedisCache(), "_pool", None)
        mixed.title = "after"
        await mixed.save()  # must not raise
        row = await LiveMixed.get(pk=mixed.pk)
        assert row.title == "after"  # the column write committed
        assert row.note == "n"
        await mixed.delete()  # must not raise either
        assert await LiveMixed.filter(pk=mixed.pk).exists() is False
        monkeypatch.undo()
        # Both hooks hit the uninitialised backend and swallowed the error, so
        # the create-time key is untouched — not rewritten, not deleted.
        assert await _cached_value(title_key) == "t"

    async def test_redis_down_fails_open_in_get_and_is_swallowed_in_save(
        self,
    ) -> None:
        pool = aioredis.from_url(
            "redis://127.0.0.1:1/0",
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        down = RedisCacheBackend(pool=pool, namespace="cfl", default_ttl=60)
        try:
            # Prove the injected backend really raises the documented error.
            with pytest.raises(RedisCacheError):
                await down.get("probe")

            item = await LiveItem.create(title="db-value")
            assert await cached_get(item, "title", backend=down) == "db-value"

            mixed = await LiveMixed.create(title="before", subtitle="s", note="n")
            field = _field(LiveMixed, "title")
            key = field.cached_key(mixed)
            assert await _cached_value(key) == "before"
            setattr(field, "_backend", down)
            try:
                mixed.title = "after"
                await mixed.save()
            finally:
                setattr(field, "_backend", None)
            # The save really went through the failing backend: had the mixin
            # ignored the injected backend, the key would now hold "after".
            assert await _cached_value(key) == "before"
            fresh = await LiveMixed.get(pk=mixed.pk)
            assert fresh.title == "after"
        finally:
            aclose = getattr(pool, "aclose", None)
            if aclose is None:
                await pool.close()
            else:
                await aclose()


# ---------------------------------------------------------------------------
# Case 11 — prefetch_cached batching
# ---------------------------------------------------------------------------


@_live_redis
class TestPrefetchLive:
    """One MGET and one pipelined set for N instances, hits left alone."""

    async def test_one_mget_and_one_pipelined_set_for_n_instances(self) -> None:
        items = [await LiveItem.create(title=f"item-{index}") for index in range(5)]
        field = _field(LiveItem, "title")
        # Warm one key with a payload that deliberately differs from the column:
        # a validated hit is left exactly as written, so a prefetch that
        # re-encodes the loaded attribute would overwrite it.
        warm_key = field.cached_key(items[0])
        warm_payload = _CODEC.encode("warm-hit")
        await _backend().set(warm_key, warm_payload, ttl=60)

        backend = _CountingBackend()
        await prefetch_cached(items, "title", backend=backend)

        assert backend.get_many_calls == 1
        assert backend.set_many_calls == 1
        # The warm hit survives untouched; only the misses are filled.
        assert await _backend().get(warm_key) == warm_payload
        for item in items[1:]:
            assert await _cached_value(field.cached_key(item)) == item.title

    async def test_empty_sequence_issues_no_batched_calls(self) -> None:
        backend = _CountingBackend()
        await prefetch_cached([], "title", backend=backend)
        assert backend.get_many_calls == 0
        assert backend.set_many_calls == 0


# ---------------------------------------------------------------------------
# Case 12 — partial/deferred instances (N2 delta)
# ---------------------------------------------------------------------------


@_live_redis
class TestPartialInstancesLive:
    """A partial instance raises a named ``ValueError`` and caches nothing."""

    async def test_cached_get_on_partial_raises_value_error_naming_the_field(
        self,
    ) -> None:
        item = await LiveItem.create(title="loaded")
        partial = await LiveItem.filter(pk=item.pk).only("id").get()
        assert not hasattr(partial, "title")
        with pytest.raises(ValueError, match=r"LiveItem\.title") as excinfo:
            await cached_get(partial, "title")
        assert "not loaded" in str(excinfo.value)

    async def test_save_on_partial_does_not_cache_the_unwritten_field(self) -> None:
        mixed = await LiveMixed.create(title="t", subtitle="s", note="n")
        title_key = _field(LiveMixed, "title").cached_key(mixed)
        subtitle_key = _field(LiveMixed, "subtitle").cached_key(mixed)
        partial = await LiveMixed.filter(pk=mixed.pk).only("id", "note").get()
        assert not hasattr(partial, "title")
        assert not hasattr(partial, "subtitle")
        # A partial instance can only be saved with update_fields; the mixin must
        # skip the unloaded cache fields instead of encoding a missing attribute.
        await partial.save(update_fields=["note"])
        # The unloaded fields are SKIPPED, not invalidated: the keys keep the
        # values the create wrote. Deleting the guard would drop them.
        assert await _cached_value(title_key) == "t"
        assert await _cached_value(subtitle_key) == "s"
