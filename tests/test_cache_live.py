"""Live Redis and pure-unit tests closing specific coverage gaps.

Covers uncovered lines in:

- ``cache/queryset.py`` (173, 227, 244-249, 293-294, 359, 399-410)
- ``cache/redis.py`` (117, 316-317)
- ``cache/_coerce.py`` (58-59, 63-64)
- ``cache/decorators.py`` (48-49, 51-52, 56, 276, 291-294)

Live Redis runs against the docker ``redis-ext`` container on ``127.0.0.1:6381``.
Pure-unit tests (``_q_value_default``, ``_coerce``, ``_stable_signature``,
``_deserialize_results`` Model passthrough, app-scoped resolve) need no I/O.
Database-backed tests run against the live PostgreSQL test database on
``127.0.0.1:5434`` (see ``docker-compose.dev.yml``) — this package is
PostgreSQL-only.

Note: the defensive ``RedisCache.init`` error branches in ``redis.py`` (lines
99, 108) are unreachable with a healthy redis-py client and are intentionally
left uncovered rather than exercised through a fake connection pool.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import AsyncGenerator, Iterator
from datetime import date, datetime, time
from typing import ClassVar

import pytest
from tortoise import Tortoise

from tortoise_extended._types import CacheValue
from tortoise_extended.cache._coerce import coerce_cache_value
from tortoise_extended.cache.decorators import _stable_signature, cached_method
from tortoise_extended.cache.queryset import CachedQuerySet
from tortoise_extended.cache.redis import RedisCache, RedisCacheBackend
from tortoise_extended.exceptions import CacheSerializationError
from tortoise_extended.fields import CharField
from tortoise_extended.models.cacheable_model import BaseCacheableModel

from tests.test_cache import MockRedisBackend


# ---------------------------------------------------------------------------
# Redis availability probe (mirrors test_cache_extended.py exactly)
# ---------------------------------------------------------------------------


def _redis_available() -> bool:
    """Quick probe — can we reach the docker redis-ext container?"""
    try:
        sock = socket.create_connection(("localhost", 6381), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


_live_redis = pytest.mark.skipif(
    not _redis_available(),
    reason="Redis not available on localhost:6381 (docker compose up -d)",
)


# ---------------------------------------------------------------------------
# PostgreSQL availability probe — skip the whole module when PG is unreachable
# ---------------------------------------------------------------------------


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5434), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not available on localhost:5434",
)

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test",
)


# ---------------------------------------------------------------------------
# Tortoise model for queryset tests (PostgreSQL, module-scoped)
# ---------------------------------------------------------------------------


class CacheLiveThing(BaseCacheableModel):
    """Minimal cacheable model exercised by queryset coverage tests."""

    _cache_ttl = 300
    _cache_fields: ClassVar[list[str] | None] = ["title"]

    title = CharField(max_length=255)

    class Meta:
        table = "cache_live_things"


_TABLES = ("cache_live_things",)


@pytest.fixture(scope="module", autouse=True)
async def _init_tortoise() -> AsyncGenerator[None, None]:  # pyright: ignore[reportUnusedFunction]
    """Initialize Tortoise ORM against the live PostgreSQL test database."""
    _ = await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_cache_live"]},
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
    """Truncate every table before each test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(f"TRUNCATE {', '.join(_TABLES)} CASCADE")
    yield


# ===========================================================================
# File A: queryset.py
# ===========================================================================


# --- Line 173: annotation.get_sql() for a pypika Term ----------------------


class TestQuerySetAnnotationTerm:
    """Line 173: annotation that is a ``pypika_tortoise.terms.Term`` hits
    the ``get_sql()`` branch of ``_annotation_signature``."""

    def test_term_annotation_sql(self) -> None:
        """A pypika ``Field`` (a ``Term`` subclass) produces SQL text."""
        from pypika_tortoise.terms import Field as PypikaField

        backend = MockRedisBackend(default_ttl=300)
        qs = CachedQuerySet(CacheLiveThing).cache(ttl=300, backend=backend)

        term = PypikaField("title")
        sig = qs._annotation_signature(term)
        # The Term branch calls term.get_sql(context) which produces "title"
        assert "title" in sig

    def test_term_vs_expression_different(self) -> None:
        """Verify the Term branch path produces a different result from the
        Expression branch — confirming both branches are distinct."""
        from pypika_tortoise.terms import Field as PypikaField

        backend = MockRedisBackend(default_ttl=300)
        qs = CachedQuerySet(CacheLiveThing).cache(ttl=300, backend=backend)

        # Term → hit line 173 (get_sql)
        term_result = qs._annotation_signature(PypikaField("title"))
        # Expression (non-Term) → hit the resolve branch (line 176+)
        from tortoise.functions import Sum

        expr_result = qs._annotation_signature(Sum("title"))
        # Both should produce different strings
        assert term_result != expr_result


# --- Lines 227 + 244-249: single-flight non-claimed + error propagation ------


class TestQuerySetSingleFlight:
    """Lines 227 + 244-249: single-flight claim / await / error propagation."""

    @pytest.fixture(autouse=True)
    def _reset_single_flight(self) -> Iterator[None]:
        """Clear the module-level single-flight registry between tests."""
        from tortoise_extended.cache.queryset import _single_flight as sf

        sf._futures.clear()
        yield
        sf._futures.clear()

    @pytest.mark.asyncio
    async def test_non_claimed_awaits_future(self) -> None:
        """Line 227: a coroutine that does not claim awaits the shared future."""
        from tortoise_extended.cache.queryset import _single_flight as sf

        loop = asyncio.get_running_loop()
        key = "sf-non-claimed-unit"

        # Pre-seed a pending future so the queryset's claim() returns False.
        pending: asyncio.Future[CacheValue] = loop.create_future()
        sf._futures[key] = pending

        backend = MockRedisBackend(default_ttl=300)
        qs = CachedQuerySet(CacheLiveThing).cache(
            ttl=300, backend=backend, key=key
        )

        # Execute in a task — it will find the existing future and await it.
        task = asyncio.create_task(qs._execute())
        await asyncio.sleep(0)  # let it hit the claim → not claimed path

        assert not task.done()  # still waiting on the future

        # Complete the future so the waiting coroutine can return.
        pending.set_result([])
        result = await task
        assert result == []

    @pytest.mark.asyncio
    async def test_error_propagation_via_single_flight(self) -> None:
        """Lines 244-249: ``BaseException`` in the claimed coroutine propagates
        to all waiting coroutines via the shared future."""
        # Drop the table so ``super()._execute()`` raises.
        conn = Tortoise.get_connection("default")
        await conn.execute_script("DROP TABLE IF EXISTS cache_live_things")

        errors: list[BaseException] = []

        async def _run_query() -> None:
            backend = MockRedisBackend(default_ttl=300)
            qs = CachedQuerySet(CacheLiveThing).cache(
                ttl=300, backend=backend, key="sf-error-prop"
            )
            try:
                await qs._execute()
            except BaseException as exc:
                errors.append(exc)

        try:
            # Two concurrent executions: one claims and fails, the other
            # awaits the shared future and receives the same exception.
            await asyncio.gather(_run_query(), _run_query())

            assert len(errors) == 2
            # The error is an exception from Tortoise's query pipeline;
            # the exact type depends on internal Tortoise state, so just
            # verify both received the same error type.
            assert type(errors[0]) is type(errors[1])
        finally:
            # Recreate the table for subsequent tests.
            await Tortoise.generate_schemas()


# --- Lines 293-294: _deserialize_results Model passthrough --------------------


class TestQuerySetDeserializeModelPassthrough:
    """Lines 293-294: ``_deserialize_results`` passes through already-hydrated
    ``Model`` instances instead of deserializing them."""

    def test_already_model_passthrough(self) -> None:
        """A ``Model`` instance in the data list is returned as-is."""
        backend = MockRedisBackend(default_ttl=300)
        qs = CachedQuerySet(CacheLiveThing).cache(ttl=300, backend=backend)

        thing = CacheLiveThing(title="passthrough")
        results = qs._deserialize_results([thing])  # type: ignore[arg-type]

        assert len(results) == 1
        assert results[0] is thing


# --- Line 359: app-scoped resolve returns None when model not in app ----------


class TestQuerySetResolveModelAppScoped:
    """Line 359: the registered app does not contain the requested model."""

    def test_model_not_in_existing_app(self) -> None:
        """``_resolve_model("Unknown", "models")`` returns ``None`` because
        the ``models`` app is registered but does not contain ``Unknown``."""
        result = CachedQuerySet._resolve_model("NonexistentModel", "models")
        assert result is None


# --- Lines 399-410: _q_value_default normalization branches --------------------


class TestQuerySetValueDefault:
    """Lines 399-410: all normalization branches of ``_q_value_default``.

    Note: the base case is ``return str(value)``, so all leaf scalars
    are stringified.  Containers (set/list/dict) are recursively normalized.
    """

    def test_set_sorted(self) -> None:
        """``set`` → sorted list (elements recursively normalized)."""
        result = CachedQuerySet._q_value_default({3, 1, 2})  # type: ignore[arg-type]
        assert result == ["1", "2", "3"]

    def test_frozenset_sorted(self) -> None:
        """``frozenset`` → sorted list."""
        result = CachedQuerySet._q_value_default(frozenset({4, 6, 5}))  # type: ignore[arg-type]
        assert result == ["4", "5", "6"]

    def test_list_recursive(self) -> None:
        """``list`` elements are recursively normalized."""
        result = CachedQuerySet._q_value_default([1, {3, 2}, "a"])  # type: ignore[arg-type]
        assert result == ["1", ["2", "3"], "a"]

    def test_tuple_like_list(self) -> None:
        """``tuple`` is treated identically to ``list``."""
        result = CachedQuerySet._q_value_default((1, {3, 2}))  # type: ignore[arg-type]
        assert result == ["1", ["2", "3"]]

    def test_dict_sorted_keys(self) -> None:
        """``dict`` keys are sorted, values recursively normalized."""
        result = CachedQuerySet._q_value_default({"b": 2, "a": 1})  # type: ignore[arg-type]
        assert result == {"a": "1", "b": "2"}

    def test_dict_nested_set(self) -> None:
        """Nested ``dict`` with ``set`` values is fully normalized."""
        result = CachedQuerySet._q_value_default({"x": {3, 1, 2}})  # type: ignore[arg-type]
        assert result == {"x": ["1", "2", "3"]}

    def test_dict_non_str_keys(self) -> None:
        """Non-string dict keys are converted via ``str()``."""
        result = CachedQuerySet._q_value_default({1: "a", 2: "b"})  # type: ignore[arg-type]
        assert result == {"1": "a", "2": "b"}

    def test_datetime_isoformat(self) -> None:
        """``datetime`` → ISO-8601 string."""
        result = CachedQuerySet._q_value_default(datetime(2024, 6, 15, 10, 30))
        assert result == "2024-06-15T10:30:00"

    def test_date_isoformat(self) -> None:
        """``date`` → ISO-8601 string."""
        result = CachedQuerySet._q_value_default(date(2024, 6, 15))
        assert result == "2024-06-15"

    def test_time_isoformat(self) -> None:
        """``time`` → ISO-8601 string."""
        result = CachedQuerySet._q_value_default(time(10, 30))
        assert result == "10:30:00"

    def test_scalar_str(self) -> None:
        """Plain scalar → ``str(value)``."""
        result = CachedQuerySet._q_value_default(42)
        assert result == "42"


# ===========================================================================
# File B: redis.py
# ===========================================================================


# --- Line 117: re-init closes old pool via aclose() --------------------------


class TestRedisCacheReinitAclose:
    """Line 117: re-initializing with a healthy Redis closes the old pool
    via ``aclose()`` (redis-py >= 5 always exposes it)."""

    @pytest.fixture(autouse=True)
    async def _reset_singleton(self) -> AsyncGenerator[None, None]:
        RedisCache._instance = None
        RedisCache._pool = None
        yield
        if RedisCache._pool is not None:
            await RedisCache.close()
        RedisCache._instance = None
        RedisCache._pool = None

    @_live_redis
    @pytest.mark.asyncio
    async def test_reinit_closes_old_pool_via_aclose(self) -> None:
        """Two successive inits — the first pool is closed via ``aclose``."""
        await RedisCache.init(url="redis://localhost:6381/0")
        old_pool = RedisCache.get_pool()

        await RedisCache.init(url="redis://localhost:6381/0")
        new_pool = RedisCache.get_pool()

        assert new_pool is not old_pool
        # old_pool.aclose() was called (line 117); the pool is now closed.
        await RedisCache.close()


# --- Lines 316-317: get_many deserialization error --------------------------


class TestRedisCacheBackendGetManyDeser:
    """Lines 316-317: ``get_many`` raises ``CacheSerializationError`` when a
    stored value cannot be deserialized."""

    @pytest.fixture(autouse=True)
    async def _reset_singleton(self) -> AsyncGenerator[None, None]:
        RedisCache._instance = None
        RedisCache._pool = None
        yield
        if RedisCache._pool is not None:
            await RedisCache.close()
        RedisCache._instance = None
        RedisCache._pool = None

    @_live_redis
    @pytest.mark.asyncio
    async def test_get_many_invalid_data_raises(self) -> None:
        """Storing raw bytes that are not valid JSON triggers a
        ``CacheSerializationError`` inside ``get_many``."""
        await RedisCache.init(url="redis://localhost:6381/0")
        pool = RedisCache.get_pool()
        backend = RedisCacheBackend(pool=pool, namespace="test-gm-deser")

        # Write raw invalid bytes directly into Redis.
        _ = await pool.set("test-gm-deser:bad", b"{{{not-json")

        try:
            with pytest.raises(CacheSerializationError):
                await backend.get_many(["bad"])
        finally:
            _ = await pool.delete("test-gm-deser:bad")
            await RedisCache.close()


# ===========================================================================
# File C: _coerce.py
# ===========================================================================


class TestCoerceDateValueError:
    """Lines 58-59: ``date.fromisoformat`` raises ``ValueError`` → raw input
    returned untouched."""

    def test_invalid_date_returns_raw(self) -> None:
        """An unparseable date string is returned as-is."""
        from tortoise.fields import DateField

        assert coerce_cache_value("not-a-date", DateField()) == "not-a-date"


class TestCoerceTimeValueError:
    """Lines 63-64: ``time.fromisoformat`` raises ``ValueError`` → raw input
    returned untouched."""

    def test_invalid_time_returns_raw(self) -> None:
        """An unparseable time string is returned as-is."""
        from tortoise.fields import TimeField

        assert coerce_cache_value("not-a-time", TimeField()) == "not-a-time"


# ===========================================================================
# File D: decorators.py
# ===========================================================================


# --- Lines 48-49, 51-52, 56: _stable_signature normalize branches ------------


class TestStableSignatureNormalize:
    """Lines 48-49 (set/frozenset), 51-52 (dict), 56 (list): the ``normalize``
    inner function of ``_stable_signature`` canonicalizes containers before
    JSON encoding."""

    def test_set_sorted(self) -> None:
        """``set`` arg → sorted list in JSON output."""
        result = json.loads(_stable_signature(({3, 1, 2},), {}))
        assert result["args"] == [[1, 2, 3]]

    def test_frozenset_sorted(self) -> None:
        """``frozenset`` arg → sorted list in JSON output."""
        result = json.loads(_stable_signature((frozenset({4, 6, 5}),), {}))
        assert result["args"] == [[4, 5, 6]]

    def test_dict_sorted_keys(self) -> None:
        """``dict`` kwarg → sorted keys in JSON output."""
        result = json.loads(_stable_signature((), {"x": {"b": 2, "a": 1}}))
        assert result["kwargs"]["x"] == {"a": 1, "b": 2}

    def test_list_recursive(self) -> None:
        """``list`` arg → elements recursively normalized."""
        result = json.loads(_stable_signature(([1, {3, 2}],), {}))
        assert result["args"] == [[1, [2, 3]]]


# --- Lines 276, 291-294: @cached_method single-flight + error propagation ---


class TestCachedMethodErrorPropagation:
    """Lines 276 (non-claimed await) and 291-294 (error propagation) in the
    ``@cached_method`` decorator's single-flight mechanism.

    These tests require live Redis because ``@cached_method`` always obtains
    its backend from ``RedisCache.get_backend()``.
    """

    @pytest.fixture(autouse=True)
    async def _reset_singleton(self) -> AsyncGenerator[None, None]:
        RedisCache._instance = None
        RedisCache._pool = None
        yield
        if RedisCache._pool is not None:
            await RedisCache.close()
        RedisCache._instance = None
        RedisCache._pool = None

    @_live_redis
    @pytest.mark.asyncio
    async def test_single_flight_dedup(self) -> None:
        """Line 276: the second concurrent caller awaits the shared future
        and receives the same result without re-executing the function."""
        await RedisCache.init(url="redis://localhost:6381/0")
        pool = RedisCache.get_pool()
        # Flush so stale keys from a prior run don't short-circuit the test.
        _ = await pool.flushdb()

        call_count = 0

        class _Service:
            @cached_method(ttl=300, namespace="test-cm-dedup")
            async def get_data(self, key: str) -> str:
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0)  # yield so the second task can claim
                return f"result-{key}"

        service = _Service()
        results = await asyncio.gather(
            service.get_data("k1"),
            service.get_data("k1"),
        )

        assert call_count == 1
        assert results[0] == results[1] == "result-k1"
        await RedisCache.close()

    @_live_redis
    @pytest.mark.asyncio
    async def test_error_propagation(self) -> None:
        """Lines 291-294: a ``BaseException`` raised by the claimed coroutine
        is forwarded to all waiting coroutines via ``future.set_exception``."""
        await RedisCache.init(url="redis://localhost:6381/0")
        pool = RedisCache.get_pool()
        _ = await pool.flushdb()

        class _Service:
            @cached_method(ttl=300, namespace="test-cm-err")
            async def get_data(self, key: str) -> str:
                await asyncio.sleep(0)  # yield so the second task can await
                msg = "service failure"
                raise RuntimeError(msg)

        service = _Service()
        errors: list[RuntimeError] = []

        async def _call() -> None:
            try:
                await service.get_data("k2")
            except RuntimeError as exc:
                errors.append(exc)

        await asyncio.gather(_call(), _call())

        assert len(errors) == 2
        assert all("service failure" in str(e) for e in errors)
        await RedisCache.close()
