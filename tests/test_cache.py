"""Tests for tortoise_extended.cache module.

Tests cache backend, decorators, queryset caching, and model caching.
"""

from uuid import uuid4

import pytest

from tortoise_extended.cache.base import (
    CacheBackend,
    CacheKey,
    CacheNamespace,
    JSONSerializer,
    NullSerializer,
    PickleSerializer,
    Serializer,
)
from tortoise_extended.cache.decorators import (
    _build_cache_key,
    cached,
    cached_method,
    invalidate,
)
from tortoise_extended.models.cacheable_model import BaseCacheableModel
from tortoise_extended.exceptions import CacheError

# Concrete subclasses with the ABC machinery disabled so the *abstract*
# method bodies (which raise NotImplementedError) can be invoked directly.
_RawSerializer = Serializer
setattr(_RawSerializer, "__abstractmethods__", frozenset())

_RawBackend = CacheBackend
setattr(_RawBackend, "__abstractmethods__", frozenset())


# ---------------------------------------------------------------------------
# Abstract method bodies — base coverage
# ---------------------------------------------------------------------------


class TestAbstractMethodBodies:
    """The ``NotImplementedError`` bodies of abstract methods are reachable."""

    def test_serializer_bodies_raise(self) -> None:
        s = _RawSerializer()
        with pytest.raises(NotImplementedError):
            s.dumps(None)
        with pytest.raises(NotImplementedError):
            s.loads(b"")

    @pytest.mark.asyncio
    async def test_backend_bodies_raise(self) -> None:
        b = _RawBackend()
        with pytest.raises(NotImplementedError):
            await b.get("k")
        with pytest.raises(NotImplementedError):
            await b.set("k", 1)
        with pytest.raises(NotImplementedError):
            await b.delete("k")
        with pytest.raises(NotImplementedError):
            await b.exists("k")
        with pytest.raises(NotImplementedError):
            await b.expire("k", 1)
        with pytest.raises(NotImplementedError):
            await b.keys("k")
        with pytest.raises(NotImplementedError):
            await b.delete_pattern("k")
        with pytest.raises(NotImplementedError):
            await b.flush()


# ---------------------------------------------------------------------------
# CacheKey tests
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_basic_build(self):
        key = CacheKey("prefix").add("user", "123").build()
        assert key == "6:prefix:4:user:3:123"

    def test_empty_prefix(self):
        key = CacheKey().add("user", "123").build()
        assert key == "4:user:3:123"

    def test_custom_separator(self):
        key = CacheKey("prefix", separator="/").add("user", "123").build()
        assert key == "6:prefix/4:user/3:123"

    def test_from_dict(self):
        key = CacheKey.from_dict("user", {"id": "123", "name": "alice"})
        assert key.build() == "4:user:2:id:3:123:4:name:5:alice"

    def test_hash(self):
        h = CacheKey.hash("hello world")
        assert len(h) == 16
        assert h == CacheKey.hash("hello world")  # deterministic

    def test_int_parts(self):
        key = CacheKey("test").add(42, 3.14).build()
        assert key == "4:test:2:42:4:3.14"

    def test_separator_inside_value_is_unambiguous(self):
        """Length-prefixing means a ':' inside a value can never be confused
        with a component boundary."""
        one_part = CacheKey("p").add("1:2").build()
        two_parts = CacheKey("p").add("1", "2").build()
        assert one_part == "1:p:3:1:2"
        assert two_parts == "1:p:1:1:1:2"
        assert one_part != two_parts


# ---------------------------------------------------------------------------
# CacheNamespace tests
# ---------------------------------------------------------------------------


class TestCacheNamespace:
    def test_key(self):
        ns = CacheNamespace("entity")
        assert ns.key("get", "123") == "entity:get:123"

    def test_pattern(self):
        ns = CacheNamespace("entity")
        assert ns.pattern("get") == "entity:get:*"


# ---------------------------------------------------------------------------
# Serializer tests
# ---------------------------------------------------------------------------


class TestJSONSerializer:
    def test_roundtrip(self):
        s = JSONSerializer()
        data = {"key": "value", "num": 42, "nested": [1, 2, 3]}
        assert s.loads(s.dumps(data)) == data

    def test_uuid_serialization(self):
        s = JSONSerializer()
        uid = uuid4()
        result = s.loads(s.dumps({"id": uid}))
        assert result["id"] == str(uid)


class TestPickleSerializer:
    def test_roundtrip(self):
        s = PickleSerializer()
        data = {"key": "value", "num": 42}
        assert s.loads(s.dumps(data)) == data


class TestNullSerializer:
    def test_bytes_passthrough(self):
        s = NullSerializer()
        data = b"raw bytes"
        assert s.loads(s.dumps(data)) == data

    def test_string_to_bytes(self):
        s = NullSerializer()
        result = s.dumps("hello")
        assert result == b"hello"


# ---------------------------------------------------------------------------
# RedisCacheBackend tests (mocked)
# ---------------------------------------------------------------------------


class MockRedisBackend(CacheBackend):
    """In-memory cache backend for testing."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._store: dict[str, bytes] = {}
        self._ttls: dict[str, int] = {}

    async def get(self, key: str):
        if key in self._store:
            return self.deserialize(self._store[key])
        return None

    async def set(self, key: str, value, ttl=None):
        ttl = ttl if ttl is not None else self.default_ttl
        self._store[key] = self.serialize(value)
        self._ttls[key] = ttl

    async def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            self._ttls.pop(key, None)
            return True
        return False

    async def exists(self, key: str) -> bool:
        return key in self._store

    async def expire(self, key: str, ttl: int) -> bool:
        if key in self._store:
            self._ttls[key] = ttl
            return True
        return False

    async def keys(self, pattern: str = "*") -> list[str]:
        import fnmatch

        # For flush("*"), return all keys
        if pattern == "*":
            return list(self._store.keys())
        return [k for k in self._store if fnmatch.fnmatch(k, pattern)]

    async def delete_pattern(self, pattern: str = "*") -> int:
        keys = await self.keys(pattern)
        for key in keys:
            del self._store[key]
            self._ttls.pop(key, None)
        return len(keys)

    async def flush(self) -> None:
        """Flush all keys."""
        self._store.clear()
        self._ttls.clear()


class TestMockBackend:
    def setup_method(self):
        self.backend = MockRedisBackend(default_ttl=300)

    @pytest.mark.asyncio
    async def test_get_set(self):
        await self.backend.set("key1", {"data": "value"})
        result = await self.backend.get("key1")
        assert result == {"data": "value"}

    @pytest.mark.asyncio
    async def test_get_missing(self):
        result = await self.backend.get("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self):
        await self.backend.set("key1", "value")
        assert await self.backend.delete("key1") is True
        assert await self.backend.get("key1") is None

    @pytest.mark.asyncio
    async def test_delete_missing(self):
        assert await self.backend.delete("missing") is False

    @pytest.mark.asyncio
    async def test_exists(self):
        await self.backend.set("key1", "value")
        assert await self.backend.exists("key1") is True
        assert await self.backend.exists("missing") is False

    @pytest.mark.asyncio
    async def test_keys(self):
        await self.backend.set("user:1", "a")
        await self.backend.set("user:2", "b")
        await self.backend.set("entity:1", "c")
        keys = await self.backend.keys("user:*")
        assert len(keys) == 2

    @pytest.mark.asyncio
    async def test_delete_pattern(self):
        await self.backend.set("user:1", "a")
        await self.backend.set("user:2", "b")
        await self.backend.set("entity:1", "c")
        count = await self.backend.delete_pattern("user:*")
        assert count == 2
        assert await self.backend.get("entity:1") == "c"

    @pytest.mark.asyncio
    async def test_get_many(self):
        await self.backend.set("a", 1)
        await self.backend.set("b", 2)
        result = await self.backend.get_many(["a", "b", "c"])
        assert result == {"a": 1, "b": 2}

    @pytest.mark.asyncio
    async def test_set_many(self):
        await self.backend.set_many({"x": 10, "y": 20})
        assert await self.backend.get("x") == 10
        assert await self.backend.get("y") == 20

    @pytest.mark.asyncio
    async def test_incr(self):
        assert await self.backend.incr("counter") == 1
        assert await self.backend.incr("counter") == 2
        assert await self.backend.incr("counter", 5) == 7

    @pytest.mark.asyncio
    async def test_incr_non_integer_raises_cache_error(self):
        """Base incr must surface a domain CacheError, not a raw ValueError."""
        await self.backend.set("counter", "not-a-number")
        with pytest.raises(CacheError, match="non-integer"):
            await self.backend.incr("counter")

    @pytest.mark.asyncio
    async def test_flush(self):
        await self.backend.set("a", 1)
        await self.backend.set("b", 2)
        await self.backend.flush()
        # Flush uses delete_pattern("*") which should delete all keys
        assert len(self.backend._store) == 0


# ---------------------------------------------------------------------------
# Decorator tests
# ---------------------------------------------------------------------------


class TestCachedDecorator:
    def setup_method(self):
        self.backend = MockRedisBackend(default_ttl=300)

    @pytest.mark.asyncio
    async def test_cached_basic(self):
        call_count = 0
        backend = self.backend

        @cached(ttl=60, backend=backend)
        async def expensive_func(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 2

        result1 = await expensive_func(5)
        assert result1 == 10
        assert call_count == 1

        result2 = await expensive_func(5)
        assert result2 == 10
        assert call_count == 1  # Cached

    @pytest.mark.asyncio
    async def test_cached_different_args(self):
        call_count = 0
        backend = self.backend

        @cached(ttl=60, backend=backend, key_builder=lambda x: f"func:{x}")
        async def func(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 2

        await func(1)
        await func(2)
        assert call_count == 2

        await func(1)
        assert call_count == 2  # Cached

    @pytest.mark.asyncio
    async def test_cached_returns_none(self):
        call_count = 0
        backend = self.backend

        @cached(ttl=60, backend=backend)
        async def func() -> None:
            nonlocal call_count
            call_count += 1
            return None

        await func()
        await func()
        assert call_count == 2  # None not cached

    @pytest.mark.asyncio
    async def test_cached_method(self):
        call_count = 0
        backend = self.backend

        class Service:
            @cached_method(ttl=60, namespace="test")
            async def get_data(self, item_id: str) -> dict:
                nonlocal call_count
                call_count += 1
                return {"id": item_id}

        # We need to mock RedisCache at the module level where it's imported
        import tortoise_extended.cache.redis as redis_module

        original_cls = redis_module.RedisCache

        class MockRedisCache:
            _pool = True

            @classmethod
            def get_backend(cls, **kwargs):
                return backend

        redis_module.RedisCache = MockRedisCache
        try:
            service = Service()
            result1 = await service.get_data("123")
            assert result1 == {"id": "123"}
            assert call_count == 1

            result2 = await service.get_data("123")
            assert result2 == {"id": "123"}
            assert call_count == 1  # Cached
        finally:
            redis_module.RedisCache = original_cls


class TestInvalidateDecorator:
    def setup_method(self):
        self.backend = MockRedisBackend(default_ttl=300)

    @pytest.mark.asyncio
    async def test_invalidate_on_call(self):
        backend = self.backend
        await backend.set("entity:*", ["cached_data"])

        @invalidate("entity:*", namespace="test")
        async def update_entity(entity_id: str):
            return {"updated": entity_id}

        # Mock RedisCache at the module level where it's imported in invalidate
        import tortoise_extended.cache.redis as redis_module

        original_cls = redis_module.RedisCache

        class MockRedisCache:
            _pool = True

            @classmethod
            def get_backend(cls, **kwargs):
                return backend

        redis_module.RedisCache = MockRedisCache
        try:
            await update_entity("123")
            # Pattern deleted
            assert await backend.get("entity:*") is None
        finally:
            redis_module.RedisCache = original_cls


# ---------------------------------------------------------------------------
# Decorator branch coverage — default backend, error paths, helpers
# ---------------------------------------------------------------------------


class TestCachedDecoratorBranches:
    def setup_method(self):
        self.backend = MockRedisBackend(default_ttl=300)

    def _mock_redis_cache(self, monkeypatch, backend=None):
        """Replace redis.RedisCache.get_backend so decorators use a fake."""
        import tortoise_extended.cache.redis as redis_module

        target = backend or self.backend

        class MockRedisCache:
            _pool = True

            @classmethod
            def get_backend(cls, **kwargs):
                return target

        monkeypatch.setattr(redis_module, "RedisCache", MockRedisCache)

    @pytest.mark.asyncio
    async def test_cached_default_backend(self, monkeypatch):
        """With no backend passed, the decorator pulls one from RedisCache."""
        self._mock_redis_cache(monkeypatch)
        call_count = 0

        @cached(ttl=60)
        async def func(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 2

        assert await func(3) == 6
        assert await func(3) == 6
        assert call_count == 1  # second call served from cache

    @pytest.mark.asyncio
    async def test_cached_read_error_is_suppressed(self):
        """A CacheError on read falls through to the function."""

        class ErrorBackend(MockRedisBackend):
            async def get(self, key):
                raise CacheError("boom")

        call_count = 0

        @cached(ttl=60, backend=ErrorBackend())
        async def func() -> int:
            nonlocal call_count
            call_count += 1
            return 7

        assert await func() == 7
        assert await func() == 7
        assert call_count == 2  # never cached — read always fails

    @pytest.mark.asyncio
    async def test_cached_write_error_is_suppressed(self):
        """A CacheError on set does not break the call."""

        class ErrorBackend(MockRedisBackend):
            async def set(self, key, value, ttl=None):
                raise CacheError("boom")

        call_count = 0

        @cached(ttl=60, backend=ErrorBackend())
        async def func() -> int:
            nonlocal call_count
            call_count += 1
            return 7

        assert await func() == 7
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_wrapper_helpers_invalidate_and_cache_key(self, monkeypatch):
        """wrapper.invalidate() and wrapper.cache_key() are exposed."""
        self._mock_redis_cache(monkeypatch)

        @cached(ttl=60, backend=self.backend)
        async def func(x: int) -> int:
            return x

        await func(1)
        key = func.cache_key(1)
        assert isinstance(key, str)
        assert "func" in key
        assert await self.backend.exists(key) is True

        await func.invalidate(1)
        assert await self.backend.exists(key) is False

    @pytest.mark.asyncio
    async def test_invalidate_uses_decorator_backend(self, monkeypatch):
        """wrapper.invalidate() must delete from the @cached backend, not the
        default Redis 'decorators' namespace backend."""
        custom_backend = MockRedisBackend(default_ttl=300)
        default_backend = MockRedisBackend(default_ttl=300)
        self._mock_redis_cache(monkeypatch, backend=default_backend)

        @cached(ttl=60, backend=custom_backend)
        async def func(x: int) -> int:
            return x

        await func(1)
        key = func.cache_key(1)
        assert await custom_backend.exists(key) is True
        assert await default_backend.exists(key) is False

        await func.invalidate(1)
        assert await custom_backend.exists(key) is False

    @pytest.mark.asyncio
    async def test_single_flight_concurrent_miss_one_execution(self):
        """Concurrent misses for the same key share a single execution."""
        import asyncio

        backend = MockRedisBackend(default_ttl=300)
        calls = 0
        started = asyncio.Event()
        release = asyncio.Event()

        @cached(ttl=60, backend=backend)
        async def expensive() -> int:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return 42

        t1 = asyncio.create_task(expensive())
        await started.wait()
        t2 = asyncio.create_task(expensive())
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(t1, t2)

        assert results == [42, 42]
        assert calls == 1

    @pytest.mark.asyncio
    async def test_single_flight_error_propagates_to_awaiters(self):
        """A failing execution must not hang concurrent waiters."""
        import asyncio

        class ErrorBackend(MockRedisBackend):
            async def set(self, key, value, ttl=None):
                raise CacheError("boom")

        backend = ErrorBackend(default_ttl=300)
        calls = 0
        started = asyncio.Event()
        release = asyncio.Event()

        @cached(ttl=60, backend=backend)
        async def failing() -> int:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            msg = "db down"
            raise RuntimeError(msg)

        t1 = asyncio.create_task(failing())
        await started.wait()
        t2 = asyncio.create_task(failing())
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(RuntimeError, match="db down"):
            await asyncio.gather(t1, t2)
        assert calls == 1

    @pytest.mark.asyncio
    async def test_cached_method_helpers(self, monkeypatch):
        """cached_method exposes cache_key()/invalidate() like @cached."""
        self._mock_redis_cache(monkeypatch)
        call_count = 0

        class Service:
            @cached_method(ttl=60, namespace="test")
            async def get_data(self, item_id: str) -> dict:
                nonlocal call_count
                call_count += 1
                return {"id": item_id}

        service = Service()
        assert await service.get_data("7") == {"id": "7"}
        key = service.get_data.cache_key("7")
        assert isinstance(key, str)
        assert "get_data" in key
        assert await self.backend.exists(key) is True

        await service.get_data.invalidate("7")
        assert await self.backend.exists(key) is False

    @pytest.mark.asyncio
    async def test_cached_method_read_error(self, monkeypatch):
        """cached_method swallows CacheError on read."""

        class ErrorBackend(MockRedisBackend):
            async def get(self, key):
                raise CacheError("boom")

        backend = ErrorBackend()

        class MockRedisCache:
            _pool = True

            @classmethod
            def get_backend(cls, **kwargs):
                return backend

        import tortoise_extended.cache.redis as redis_module

        monkeypatch.setattr(redis_module, "RedisCache", MockRedisCache)

        call_count = 0

        class Service:
            @cached_method(ttl=60, namespace="test")
            async def get_data(self, item_id: str) -> dict:
                nonlocal call_count
                call_count += 1
                return {"id": item_id}

        service = Service()
        assert await service.get_data("1") == {"id": "1"}
        assert await service.get_data("1") == {"id": "1"}
        assert call_count == 2


class TestInvalidateDecoratorBranches:
    def setup_method(self):
        self.backend = MockRedisBackend(default_ttl=300)

    def _mock_redis_cache(self, monkeypatch, backend=None):
        import tortoise_extended.cache.redis as redis_module

        target = backend or self.backend

        class MockRedisCache:
            _pool = True

            @classmethod
            def get_backend(cls, **kwargs):
                return target

        monkeypatch.setattr(redis_module, "RedisCache", MockRedisCache)

    @pytest.mark.asyncio
    async def test_invalidate_with_key_func(self, monkeypatch):
        """key_func generates the exact key to delete."""
        self._mock_redis_cache(monkeypatch)
        await self.backend.set("custom:1", "v")

        @invalidate(key_func=lambda *a, **kw: "custom:1", namespace="test")
        async def update(entity_id: str) -> str:
            return entity_id

        await update("1")
        assert await self.backend.get("custom:1") is None

    @pytest.mark.asyncio
    async def test_invalidate_error_is_suppressed(self, monkeypatch):
        """A CacheError during invalidation does not break the call."""

        class ErrorBackend(MockRedisBackend):
            async def delete_pattern(self, pattern):
                raise CacheError("boom")

        self._mock_redis_cache(monkeypatch, backend=ErrorBackend())

        @invalidate("entity:*", namespace="test")
        async def update(entity_id: str) -> str:
            return entity_id

        assert await update("1") == "1"


# ---------------------------------------------------------------------------
# _build_cache_key tests
# ---------------------------------------------------------------------------


class TestBuildCacheKey:
    def test_basic(self):
        async def dummy():
            pass

        key = _build_cache_key(dummy, (None, "arg1"), {"kwarg": "val"})
        assert "dummy" in key

    def test_custom_prefix(self):
        async def dummy():
            pass

        key = _build_cache_key(dummy, (None,), {}, prefix="custom")
        assert key.startswith("6:custom")


class TestCachedKeyCollision:
    """Regression: first positional arg must be part of the plain-function key.

    The old implementation keyed on ``args[1:]`` for *all* callables, so
    ``f(1, "a")`` and ``f(2, "a")`` produced the same key and the second call
    returned stale data for the wrong argument.
    """

    @pytest.mark.asyncio
    async def test_distinct_first_args_do_not_collide(self):
        call_count = 0
        backend = MockRedisBackend(default_ttl=300)

        @cached(ttl=60, backend=backend)
        async def func(x: int, y: str) -> list[int | str]:
            nonlocal call_count
            call_count += 1
            return [x, y]

        r1 = await func(1, "a")
        assert r1 == [1, "a"]
        assert call_count == 1

        r2 = await func(2, "a")  # same second arg, different first arg
        assert r2 == [2, "a"]  # must NOT return [1, "a"] from cache
        assert call_count == 2

        r3 = await func(1, "a")
        assert r3 == [1, "a"]
        assert call_count == 2  # cached

    def test_build_cache_key_distinct(self):
        async def dummy():
            pass

        k1 = _build_cache_key(dummy, (1, "a"), {})
        k2 = _build_cache_key(dummy, (2, "a"), {})
        k3 = _build_cache_key(dummy, (1, "a"), {})
        assert k1 != k2
        assert k1 == k3


class TestCacheableModelKeyNamespace:
    """Regression: get_cached/filter_cached must not share a key space, and
    invalidation must key on the real pk field name."""

    def test_get_and_filter_keys_are_distinct(self):
        key_get = BaseCacheableModel._cache_key_for("get", id="1")
        key_filter = BaseCacheableModel._cache_key_for("filter", id="1")
        assert key_get != key_filter
        assert key_get == "18:BaseCacheableModel:3:get:2:id:1:1"
        assert key_filter == "18:BaseCacheableModel:6:filter:2:id:1:1"

    def test_key_uses_given_pk_field_name(self):
        # Invalidation now passes the model's pk_attr instead of hardcoded "id"
        key = BaseCacheableModel._cache_key_for("get", uid="abc")
        assert key == "18:BaseCacheableModel:3:get:3:uid:3:abc"
        assert key != BaseCacheableModel._cache_key_for("get", id="abc")


class TestSingleFlight:
    """Unit coverage for the cache/base.py SingleFlight deduplicator."""

    @pytest.mark.asyncio
    async def test_claim_deduplicates_and_release_clears(self):
        from tortoise_extended.cache.base import SingleFlight

        sf = SingleFlight()
        claimed1, fut1 = sf.claim("k")
        claimed2, fut2 = sf.claim("k")
        assert claimed1 is True
        assert claimed2 is False
        assert fut2 is fut1

        fut1.set_result("v")
        assert await fut2 == "v"

        sf.release("k", fut1)
        claimed3, _ = sf.claim("k")
        assert claimed3 is True  # released slot can be claimed again

    @pytest.mark.asyncio
    async def test_release_ignores_stale_future(self):
        from tortoise_extended.cache.base import SingleFlight

        sf = SingleFlight()
        _, fut1 = sf.claim("k")
        sf.release("k", fut1)
        # A new cycle starts after the first completed.
        _, fut2 = sf.claim("k")
        assert fut1 is not fut2
        sf.release("k", fut1)  # stale release must not clear the new slot
        claimed3, fut3 = sf.claim("k")
        assert claimed3 is False
        assert fut3 is fut2
