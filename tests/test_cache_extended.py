"""Extended cache tests covering edge cases, serializers, CacheBackend,
CacheKey, CacheNamespace, MockRedisBackend advanced operations.

No Redis connection required.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime

import pytest
from tortoise import Tortoise, fields
from tortoise import models

from tests.test_cache import MockRedisBackend

from tortoise_extended.cache.base import (
    CacheKey,
    CacheNamespace,
    JSONSerializer,
    NullSerializer,
    PickleSerializer,
)
from tortoise_extended.cache.model import CacheableModel
from tortoise_extended.cache.queryset import CachedQuerySet
from tortoise_extended.cache.redis import RedisCache, RedisCacheBackend
from tortoise_extended.exceptions import (
    CacheBackendNotInitializedError,
    CacheError,
    CacheKeyError,
    CacheSerializationError,
    RedisCacheError,
)


# ---------------------------------------------------------------------------
# CacheKey edge cases
# ---------------------------------------------------------------------------


class TestCacheKeyEdgeCases:
    """Test CacheKey error handling and advanced patterns."""

    def test_valueerror_no_prefix_no_parts(self) -> None:
        """CacheKey.build() should raise ValueError when empty."""
        key = CacheKey()
        with pytest.raises(CacheKeyError, match="CacheKey requires a prefix or at least one part"):
            key.build()

    def test_hash_different_inputs(self) -> None:
        """Different inputs should produce different hashes."""
        h1 = CacheKey.hash("hello")
        h2 = CacheKey.hash("world")
        assert h1 != h2

    def test_hash_same_input_deterministic(self) -> None:
        """Same input should produce the same hash every time."""
        h1 = CacheKey.hash("test_value")
        h2 = CacheKey.hash("test_value")
        assert h1 == h2

    def test_hash_length(self) -> None:
        """Hash should be exactly 16 characters."""
        h = CacheKey.hash("anything")
        assert len(h) == 16


# ---------------------------------------------------------------------------
# CacheNamespace edge cases
# ---------------------------------------------------------------------------


class TestCacheNamespaceEdgeCases:
    """Test CacheNamespace with multiple parts and patterns."""

    def test_key_single_part(self) -> None:
        """Key with single part should use separator."""
        ns = CacheNamespace("app")
        assert ns.key("get") == "app:get"

    def test_key_multiple_parts(self) -> None:
        """Key with multiple parts should join all."""
        ns = CacheNamespace("app")
        assert ns.key("user", "123", "profile") == "app:user:123:profile"

    def test_pattern_single_part(self) -> None:
        """Pattern with single part should append wildcard."""
        ns = CacheNamespace("app")
        assert ns.pattern("user") == "app:user:*"

    def test_pattern_multiple_parts(self) -> None:
        """Pattern with multiple parts should join all and append wildcard."""
        ns = CacheNamespace("app")
        assert ns.pattern("user", "123") == "app:user:123:*"

    def test_custom_separator(self) -> None:
        """Custom separator should be used."""
        ns = CacheNamespace("app", separator="/")
        assert ns.key("user", "123") == "app/user/123"


# ---------------------------------------------------------------------------
# JSONSerializer edge cases
# ---------------------------------------------------------------------------


class TestJSONSerializerEdgeCases:
    """Test JSONSerializer with various types."""

    def test_custom_default_handler(self) -> None:
        """Custom default handler should be called for non-serializable types."""

        def default_handler(obj):
            if isinstance(obj, set):
                return sorted(obj)
            return str(obj)

        s = JSONSerializer(default=default_handler)
        data = {"tags": {3, 1, 2}}
        result = s.loads(s.dumps(data))
        assert result["tags"] == [1, 2, 3]

    def test_nested_structures(self) -> None:
        """Deeply nested structures should roundtrip correctly."""
        s = JSONSerializer()
        data = {"a": {"b": {"c": [1, 2, {"d": "e"}]}}}
        assert s.loads(s.dumps(data)) == data

    def test_empty_dict(self) -> None:
        """Empty dict should roundtrip correctly."""
        s = JSONSerializer()
        assert s.loads(s.dumps({})) == {}


# ---------------------------------------------------------------------------
# PickleSerializer edge cases
# ---------------------------------------------------------------------------


class TestPickleSerializerEdgeCases:
    """Test PickleSerializer with various types."""

    def test_set_roundtrip(self) -> None:
        """Set should survive pickle roundtrip."""
        s = PickleSerializer()
        data = {1, 2, 3}
        assert s.loads(s.dumps(data)) == data

    def test_tuple_roundtrip(self) -> None:
        """Tuple should survive pickle roundtrip."""
        s = PickleSerializer()
        data = (1, "two", 3.0)
        assert s.loads(s.dumps(data)) == data

    def test_bytes_roundtrip(self) -> None:
        """Bytes should survive pickle roundtrip."""
        s = PickleSerializer()
        data = b"\x00\x01\x02"
        assert s.loads(s.dumps(data)) == data


# ---------------------------------------------------------------------------
# NullSerializer edge cases
# ---------------------------------------------------------------------------


class TestNullSerializerEdgeCases:
    """Test NullSerializer edge cases."""

    def test_int_value(self) -> None:
        """Int value should be converted to string bytes."""
        s = NullSerializer()
        result = s.dumps(42)
        assert result == b"42"

    def test_none_value(self) -> None:
        """None value should be converted to string bytes."""
        s = NullSerializer()
        result = s.dumps(None)
        assert result == b"None"

    def test_bytes_passthrough(self) -> None:
        """Bytes should pass through unchanged."""
        s = NullSerializer()
        data = b"raw"
        assert s.dumps(data) == data
        assert s.loads(data) == data


# ---------------------------------------------------------------------------
# MockRedisBackend advanced tests
# ---------------------------------------------------------------------------


class TestMockRedisBackendAdvanced:
    """Advanced MockRedisBackend tests for expire, incr, set_many with ttl."""

    def setup_method(self) -> None:
        """Initialize fresh backend."""
        self.backend = MockRedisBackend(default_ttl=300)

    @pytest.mark.asyncio
    async def test_expire_existing_key(self) -> None:
        """expire on existing key should update TTL."""
        await self.backend.set("k1", "v1", ttl=100)
        result = await self.backend.expire("k1", 200)
        assert result is True
        assert self.backend._ttls["k1"] == 200

    @pytest.mark.asyncio
    async def test_expire_missing_key(self) -> None:
        """expire on missing key should return False."""
        result = await self.backend.expire("missing", 100)
        assert result is False

    @pytest.mark.asyncio
    async def test_incr_custom_amount(self) -> None:
        """incr with custom amount should increment correctly."""
        await self.backend.set("counter", 10)
        result = await self.backend.incr("counter", 5)
        assert result == 15

    @pytest.mark.asyncio
    async def test_incr_missing_key(self) -> None:
        """incr on missing key should start from 0."""
        result = await self.backend.incr("new_counter")
        assert result == 1

    @pytest.mark.asyncio
    async def test_set_many_with_ttl(self) -> None:
        """set_many with TTL should store all values with TTL."""
        await self.backend.set_many({"a": 1, "b": 2}, ttl=60)
        assert await self.backend.get("a") == 1
        assert await self.backend.get("b") == 2
        assert self.backend._ttls["a"] == 60
        assert self.backend._ttls["b"] == 60

    @pytest.mark.asyncio
    async def test_set_many_empty(self) -> None:
        """set_many with empty dict should be a no-op."""
        await self.backend.set_many({})
        assert len(self.backend._store) == 0

    @pytest.mark.asyncio
    async def test_get_many_empty(self) -> None:
        """get_many with empty list should return empty dict."""
        result = await self.backend.get_many([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_many_partial_missing(self) -> None:
        """get_many should only return existing keys."""
        await self.backend.set("a", 1)
        result = await self.backend.get_many(["a", "missing1", "missing2"])
        assert result == {"a": 1}

    @pytest.mark.asyncio
    async def test_flush_all(self) -> None:
        """flush should remove all stored data."""
        await self.backend.set("a", 1)
        await self.backend.set("b", 2)
        await self.backend.flush()
        assert len(self.backend._store) == 0
        assert len(self.backend._ttls) == 0

    @pytest.mark.asyncio
    async def test_default_ttl(self) -> None:
        """Values should use default_ttl when not specified."""
        backend = MockRedisBackend(default_ttl=600)
        await backend.set("k", "v")
        assert backend._ttls["k"] == 600


# ---------------------------------------------------------------------------
# RedisCache + RedisCacheBackend — fake-pool tests (no Redis connection)
# ---------------------------------------------------------------------------


class FakeRedisPool:
    """In-memory stand-in for ``redis.asyncio.Redis`` (decode_responses=False).

    Stores raw bytes under ``str`` keys; ``scan_iter`` yields ``bytes`` keys
    to mirror real redis-asyncio behaviour.
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.ttls: dict[str, int] = {}

    @staticmethod
    def _norm(key: str | bytes) -> str:
        return key.decode() if isinstance(key, bytes) else key

    async def get(self, key: str | bytes) -> bytes | None:
        return self.store.get(self._norm(key))

    async def setex(self, key: str | bytes, ttl: int, value: bytes) -> None:
        k = self._norm(key)
        self.store[k] = value
        self.ttls[k] = ttl

    async def set(self, key: str | bytes, value: bytes) -> None:
        k = self._norm(key)
        self.store[k] = value
        self.ttls.pop(k, None)

    async def delete(self, key: str | bytes) -> int:
        return int(self.store.pop(self._norm(key), None) is not None)

    async def exists(self, key: str | bytes) -> int:
        return int(self._norm(key) in self.store)

    async def expire(self, key: str | bytes, ttl: int) -> int:
        k = self._norm(key)
        if k in self.store:
            self.ttls[k] = ttl
            return 1
        return 0

    async def scan_iter(self, match: str | None = None, count: int = 100):
        import fnmatch

        for key in list(self.store):
            if match is None or fnmatch.fnmatch(key, match):
                yield key.encode()

    async def mget(self, keys: list[str | bytes]) -> list[bytes | None]:
        return [self.store.get(self._norm(k)) for k in keys]

    async def incrby(self, key: str | bytes, amount: int) -> int:
        k = self._norm(key)
        current = int(self.store.get(k) or 0)
        new = current + amount
        self.store[k] = str(new).encode()
        return new

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self.store.clear()
        self.ttls.clear()

    def pipeline(self, transaction: bool = False) -> "FakeRedisPipeline":
        return FakeRedisPipeline(self)


class FakeRedisPipeline:
    """Recorded pipelined writes, executed atomically on ``execute``."""

    def __init__(self, pool: FakeRedisPool) -> None:
        self._pool = pool
        self._ops: list[Callable[[], Awaitable[None]]] = []

    def setex(self, key: str, ttl: int, value: bytes) -> None:
        async def run() -> None:
            await self._pool.setex(key, ttl, value)

        self._ops.append(run)

    def set(self, key: str, value: bytes) -> None:
        async def run() -> None:
            await self._pool.set(key, value)

        self._ops.append(run)

    async def execute(self) -> list[object]:
        for op in self._ops:
            await op()
        return []


class RaisingRedisPool:
    """Pool whose every operation raises an infrastructure Redis error."""

    def __init__(self, exc: type[Exception] = ConnectionError) -> None:
        self.exc = exc

    async def _boom(self, *args: object, **kwargs: object) -> None:
        raise self.exc("boom")

    async def get(self, *args: object, **kwargs: object) -> None:
        await self._boom()

    async def setex(self, *args: object, **kwargs: object) -> None:
        await self._boom()

    async def set(self, *args: object, **kwargs: object) -> None:
        await self._boom()

    async def delete(self, *args: object, **kwargs: object) -> None:
        await self._boom()

    async def exists(self, *args: object, **kwargs: object) -> None:
        await self._boom()

    async def expire(self, *args: object, **kwargs: object) -> None:
        await self._boom()

    async def mget(self, *args: object, **kwargs: object) -> None:
        await self._boom()

    async def incrby(self, *args: object, **kwargs: object) -> None:
        await self._boom()

    async def scan_iter(self, *args: object, **kwargs: object):
        raise self.exc("boom")
        yield  # pragma: no cover

    def pipeline(self, transaction: bool = False) -> "RaisingRedisPipeline":
        return RaisingRedisPipeline(self)


class RaisingRedisPipeline:
    def __init__(self, pool: RaisingRedisPool) -> None:
        self._pool = pool

    def setex(self, key: str, ttl: int, value: bytes) -> None:
        pass

    def set(self, key: str, value: bytes) -> None:
        pass

    async def execute(self) -> list[object]:
        raise self._pool.exc("boom")


class TestRedisCacheSingleton:
    """RedisCache singleton lifecycle against a fake aioredis module."""

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        RedisCache._instance = None
        RedisCache._pool = None
        yield
        RedisCache._instance = None
        RedisCache._pool = None

    def test_singleton_identity(self) -> None:
        """RedisCache() always returns the same instance."""
        assert RedisCache() is RedisCache()

    @pytest.mark.asyncio
    async def test_init_requires_redis_package(self, monkeypatch) -> None:
        """Missing redis package raises ImportError."""
        import tortoise_extended.cache.redis as redis_module

        monkeypatch.setattr(redis_module, "aioredis", None)
        with pytest.raises(ImportError, match="redis package not installed"):
            await RedisCache.init()

    @pytest.mark.asyncio
    async def test_init_and_close(self, monkeypatch) -> None:
        """init() pings the pool and close() tears it down."""
        import tortoise_extended.cache.redis as redis_module

        class FakeAIORedis:
            _pool: FakeRedisPool | None = None

            @classmethod
            def from_url(cls, url, max_connections=None, decode_responses=None, **kwargs):
                if cls._pool is None:
                    cls._pool = FakeRedisPool()
                return cls._pool

        FakeAIORedis._pool = FakeRedisPool()
        monkeypatch.setattr(redis_module, "aioredis", FakeAIORedis)
        await RedisCache.init(url="redis://localhost:6379/0")
        assert RedisCache.get_pool() is FakeAIORedis._pool
        await RedisCache.close()
        with pytest.raises(CacheBackendNotInitializedError):
            RedisCache.get_pool()

    @pytest.mark.asyncio
    async def test_init_closes_existing_pool(self, monkeypatch) -> None:
        """Re-initializing closes the previous pool first."""
        import tortoise_extended.cache.redis as redis_module

        class FakeAIORedis:
            _pool: FakeRedisPool | None = None

            @classmethod
            def from_url(cls, url, max_connections=None, decode_responses=None, **kwargs):
                return cls._pool

        first = FakeRedisPool()
        FakeAIORedis._pool = first
        monkeypatch.setattr(redis_module, "aioredis", FakeAIORedis)
        await RedisCache.init(url="redis://a/0")
        assert RedisCache.get_pool() is first
        second = FakeRedisPool()
        FakeAIORedis._pool = second
        await RedisCache.init(url="redis://b/0")
        assert RedisCache.get_pool() is second
        assert first.store == {}  # closed before replacement

    def test_get_pool_uninitialized_raises(self) -> None:
        """get_pool() before init() raises CacheBackendNotInitializedError."""
        with pytest.raises(CacheBackendNotInitializedError):
            RedisCache.get_pool()

    @pytest.mark.asyncio
    async def test_get_backend(self, monkeypatch) -> None:
        """get_backend() returns a namespaced backend over the pool."""
        import tortoise_extended.cache.redis as redis_module

        class FakeAIORedis:
            _pool: FakeRedisPool | None = None

            @classmethod
            def from_url(cls, url, max_connections=None, decode_responses=None, **kwargs):
                return cls._pool

        FakeAIORedis._pool = FakeRedisPool()
        monkeypatch.setattr(redis_module, "aioredis", FakeAIORedis)
        await RedisCache.init(url="redis://localhost:6379/0")
        backend = RedisCache.get_backend(namespace="ns", default_ttl=42)
        assert isinstance(backend, RedisCacheBackend)
        assert backend.namespace == "ns"
        assert backend.default_ttl == 42


class TestRedisCacheBackend:
    """RedisCacheBackend operations against the fake pool."""

    def setup_method(self) -> None:
        self.pool = FakeRedisPool()
        self.backend = RedisCacheBackend(
            pool=self.pool, namespace="ns", default_ttl=60
        )

    @pytest.mark.asyncio
    async def test_get_miss(self) -> None:
        assert await self.backend.get("k") is None

    @pytest.mark.asyncio
    async def test_get_hit(self) -> None:
        await self.backend.set("k", {"a": 1})
        assert await self.backend.get("k") == {"a": 1}

    @pytest.mark.asyncio
    async def test_get_bad_data_raises_serialization_error(self) -> None:
        self.pool.store["ns:k"] = b"{not json"
        with pytest.raises(CacheSerializationError):
            await self.backend.get("k")

    @pytest.mark.asyncio
    async def test_set_with_ttl(self) -> None:
        await self.backend.set("k", "v", ttl=10)
        assert self.pool.store["ns:k"] == b'"v"'
        assert self.pool.ttls["ns:k"] == 10

    @pytest.mark.asyncio
    async def test_set_without_ttl(self) -> None:
        """With default_ttl=0 the plain ``set`` path is used (no TTL)."""
        backend = RedisCacheBackend(pool=self.pool, namespace="ns", default_ttl=0)
        await backend.set("k", "v", ttl=0)
        assert "ns:k" in self.pool.store
        assert self.pool.ttls.get("ns:k") is None

    @pytest.mark.asyncio
    async def test_set_unserializable_raises(self) -> None:
        class Bad:
            def __str__(self) -> str:
                raise ValueError("bad str")

        with pytest.raises(CacheSerializationError):
            await self.backend.set("k", Bad())

    @pytest.mark.asyncio
    async def test_delete(self) -> None:
        await self.backend.set("k", "v")
        assert await self.backend.delete("k") is True
        assert await self.backend.delete("k") is False

    @pytest.mark.asyncio
    async def test_exists(self) -> None:
        await self.backend.set("k", "v")
        assert await self.backend.exists("k") is True
        assert await self.backend.exists("missing") is False

    @pytest.mark.asyncio
    async def test_expire(self) -> None:
        await self.backend.set("k", "v", ttl=10)
        assert await self.backend.expire("k", 200) is True
        assert self.pool.ttls["ns:k"] == 200
        assert await self.backend.expire("missing", 200) is False

    @pytest.mark.asyncio
    async def test_keys(self) -> None:
        await self.backend.set("a", "1")
        await self.backend.set("b", "2")
        assert sorted(await self.backend.keys("*")) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_delete_pattern(self) -> None:
        await self.backend.set("a", "1")
        await self.backend.set("b", "2")
        assert await self.backend.delete_pattern("*") == 2
        assert self.pool.store == {}

    @pytest.mark.asyncio
    async def test_get_many(self) -> None:
        await self.backend.set("a", {"x": 1})
        result = await self.backend.get_many(["a", "missing"])
        assert result == {"a": {"x": 1}}

    @pytest.mark.asyncio
    async def test_get_many_empty(self) -> None:
        assert await self.backend.get_many([]) == {}

    @pytest.mark.asyncio
    async def test_set_many(self) -> None:
        await self.backend.set_many({"a": 1, "b": 2}, ttl=10)
        assert self.pool.ttls["ns:a"] == 10
        assert self.pool.ttls["ns:b"] == 10
        assert await self.backend.get("a") == 1

    @pytest.mark.asyncio
    async def test_set_many_no_ttl(self) -> None:
        """With default_ttl=0 the plain ``set`` path is used (no TTL)."""
        backend = RedisCacheBackend(pool=self.pool, namespace="ns", default_ttl=0)
        await backend.set_many({"a": 1}, ttl=0)
        assert self.pool.ttls.get("ns:a") is None

    @pytest.mark.asyncio
    async def test_set_many_empty(self) -> None:
        await self.backend.set_many({})
        assert self.pool.store == {}

    @pytest.mark.asyncio
    async def test_set_many_unserializable(self) -> None:
        class Bad:
            def __str__(self) -> str:
                raise ValueError("bad str")

        with pytest.raises(CacheSerializationError):
            await self.backend.set_many({"a": Bad()})

    @pytest.mark.asyncio
    async def test_incr(self) -> None:
        assert await self.backend.incr("c") == 1
        assert await self.backend.incr("c", 5) == 6

    @pytest.mark.asyncio
    async def test_flush(self) -> None:
        await self.backend.set("a", "1")
        await self.backend.flush()
        assert self.pool.store == {}

    def test_pipeline(self) -> None:
        pipe = self.backend.pipeline()
        assert isinstance(pipe, FakeRedisPipeline)

    def test_raise_redis_translates(self) -> None:
        with pytest.raises(RedisCacheError):
            RedisCacheBackend._raise_redis(ConnectionError("boom"))

    @pytest.mark.asyncio
    async def test_infra_errors_become_redis_cache_error(self) -> None:
        """Driver-level infra failures translate to RedisCacheError."""
        backend = RedisCacheBackend(pool=RaisingRedisPool(), namespace="ns")
        for coro in [
            backend.get("k"),
            backend.set("k", 1),
            backend.delete("k"),
            backend.exists("k"),
            backend.expire("k", 1),
            backend.keys(),
            backend.delete_pattern(),
            backend.get_many(["a"]),
            backend.set_many({"a": 1}),
            backend.incr("c"),
        ]:
            with pytest.raises(RedisCacheError):
                await coro


# ---------------------------------------------------------------------------
# CacheableModel — fake-backend tests (SQLite in-memory, no Redis)
# ---------------------------------------------------------------------------


class CacheThing(CacheableModel):
    """Cacheable model used by the fake-backend tests."""

    _cache_ttl = 300
    _cache_fields = ["title", "created_at"]

    title = fields.CharField(max_length=255)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "cache_things"


class CacheCategory(models.Model):
    """Category parent used to exercise relation serialization."""

    name = fields.CharField(max_length=50)

    class Meta:
        table = "cache_categories"


class CacheArticle(CacheableModel):
    """Cacheable model with a ForeignKey to exercise the pk-reduction branch."""

    _cache_ttl = 300

    title = fields.CharField(max_length=255)
    category = fields.ForeignKeyField(
        "models.CacheCategory",
        null=True,
        related_name="articles",
        on_delete=fields.SET_NULL,
    )

    class Meta:
        table = "cache_articles"


@pytest.fixture(scope="module", autouse=True)
async def _init_cache_db():
    """Initialize Tortoise with a shared in-memory SQLite DB."""
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["tests.test_cache_extended"]},
    )
    await Tortoise.generate_schemas()
    yield
    await Tortoise.close_connections()


class TestCacheableModel:
    """CacheableModel get_cached / filter_cached / invalidation paths."""

    def setup_method(self) -> None:
        self.backend = MockRedisBackend(default_ttl=300)
        # Reset class-level knobs mutated by previous tests.
        CacheThing._cache_ttl = 300
        CacheThing._cache_fields = ["title", "created_at"]
        CacheThing._cache_backend = None  # type: ignore[assignment]

    def _model(self) -> type[CacheThing]:
        """Return a CacheThing with this test's backend injected."""
        cls = CacheThing
        cls._cache_backend = self.backend  # type: ignore[assignment]
        return cls

    @pytest.mark.asyncio
    async def test_get_backend_custom(self) -> None:
        """A custom _cache_backend is returned as-is."""
        cls = self._model()
        assert cls._get_backend() is self.backend

    @pytest.mark.asyncio
    async def test_get_cached_miss_then_hit(self) -> None:
        """First call queries DB and caches; second call hits cache."""
        cls = self._model()
        thing = await cls.create(title="alpha")
        cached = await cls.get_cached(id=thing.pk)
        assert cached is not None
        assert cached.title == "alpha"

        # Second call returns a reconstructed (cache-only) instance.
        hit = await cls.get_cached(id=thing.pk)
        assert hit is not None
        assert hit.title == "alpha"
        assert await self.backend.exists(cls._cache_key_for(id=str(thing.pk)))

    @pytest.mark.asyncio
    async def test_get_cached_disabled(self) -> None:
        """ttl<=0 bypasses the cache entirely."""
        cls = self._model()
        cls._cache_ttl = 0
        thing = await cls.create(title="uncached")
        cached = await cls.get_cached(id=thing.pk)
        assert cached is not None
        assert cached.title == "uncached"
        assert self.backend._store == {}

    @pytest.mark.asyncio
    async def test_get_cached_not_found_returns_none(self) -> None:
        cls = self._model()
        assert await cls.get_cached(id=9_999_999) is None

    @pytest.mark.asyncio
    async def test_get_cached_bad_data_type_falls_back(self) -> None:
        """Non-dict cached data raises CacheDataError, caught by CacheError,
        and falls back to the database."""
        cls = self._model()
        thing = await cls.create(title="bad")
        await self.backend.set(cls._cache_key_for(id=str(thing.pk)), "not-a-dict")
        cached = await cls.get_cached(id=thing.pk)
        assert cached is not None
        assert cached.title == "bad"

    @pytest.mark.asyncio
    async def test_get_cached_read_error_falls_back_to_db(self) -> None:
        """CacheError on read is logged and the DB is queried."""
        cls = self._model()

        class BrokenBackend(MockRedisBackend):
            async def get(self, key: str) -> object:
                raise CacheError("boom")

        cls._cache_backend = BrokenBackend(default_ttl=300)  # type: ignore[assignment]
        thing = await cls.create(title="fallback")
        cached = await cls.get_cached(id=thing.pk)
        assert cached is not None
        assert cached.title == "fallback"

    @pytest.mark.asyncio
    async def test_get_cached_write_error_suppressed(self) -> None:
        """CacheError on write is suppressed; the instance is still returned."""
        cls = self._model()

        class BrokenBackend(MockRedisBackend):
            async def set(self, key: str, value: object, ttl: int | None = None) -> None:
                raise CacheError("boom")

        cls._cache_backend = BrokenBackend(default_ttl=300)  # type: ignore[assignment]
        thing = await cls.create(title="write-fail")
        cached = await cls.get_cached(id=thing.pk)
        assert cached is not None
        assert cached.title == "write-fail"

    @pytest.mark.asyncio
    async def test_filter_cached_miss_then_hit(self) -> None:
        """First call queries DB and caches a list; second call hits cache."""
        cls = self._model()
        await cls.create(title="f1", created_at=datetime.now())
        await cls.create(title="f2", created_at=datetime.now())
        results = await cls.filter_cached(title="f1")
        assert len(results) == 1
        assert results[0].title == "f1"

        hits = await cls.filter_cached(title="f1")
        assert len(hits) == 1
        assert hits[0].title == "f1"

    @pytest.mark.asyncio
    async def test_filter_cached_disabled(self) -> None:
        cls = self._model()
        cls._cache_ttl = 0
        await cls.create(title="nc1")
        results = await cls.filter_cached(title="nc1")
        assert len(results) == 1
        assert self.backend._store == {}

    @pytest.mark.asyncio
    async def test_filter_cached_bad_data_type_falls_back(self) -> None:
        """Non-list cached data is caught by CacheError and falls back to DB."""
        cls = self._model()
        await cls.create(title="x1")
        await self.backend.set(cls._cache_key_for(title="x1"), "not-a-list")
        results = await cls.filter_cached(title="x1")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_filter_cached_write_error_logged(self) -> None:
        """CacheError on write is logged; DB results still returned."""
        cls = self._model()

        class BrokenBackend(MockRedisBackend):
            async def set(self, key: str, value: object, ttl: int | None = None) -> None:
                raise CacheError("boom")

        cls._cache_backend = BrokenBackend(default_ttl=300)  # type: ignore[assignment]
        await cls.create(title="w1")
        results = await cls.filter_cached(title="w1")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_cache_key_for_sorted_kwargs(self) -> None:
        """Cache keys are deterministic regardless of kwarg order."""
        cls = self._model()
        assert cls._cache_key_for(a="1", b="2") == cls._cache_key_for(b="2", a="1")

    @pytest.mark.asyncio
    async def test_to_cache_datetime_and_pk(self) -> None:
        """Datetime fields are ISO-formatted and PK stored under _pk."""
        cls = self._model()
        thing = await cls.create(title="ser")
        data = cls._to_cache(thing)
        assert data["_model"] == "CacheThing"
        assert data["_pk"] == str(thing.pk)
        assert data["title"] == "ser"
        assert data["created_at"] == thing.created_at.isoformat()

    @pytest.mark.asyncio
    async def test_to_cache_relation_stored_as_pk(self) -> None:
        """Values with a pk attribute are reduced to their primary key."""
        cls = self._model()

        class FakeRelation:
            pk = 7

        cls._cache_fields = ["title", "rel"]
        thing = await cls.create(title="ser")
        thing.rel = FakeRelation()  # type: ignore[attr-defined]
        data = cls._to_cache(thing)
        assert data["rel"] == "7"

    @pytest.mark.asyncio
    async def test_from_cache_restores_pk_and_fields(self) -> None:
        cls = self._model()
        thing = await cls.create(title="restore")
        data = cls._to_cache(thing)
        restored = cls._from_cache(data)
        assert str(restored.pk) == str(thing.pk)
        assert restored.title == "restore"
        assert restored.created_at == thing.created_at.isoformat()

    @pytest.mark.asyncio
    async def test_save_invalidates_cache(self) -> None:
        cls = self._model()
        thing = await cls.create(title="before")
        key = cls._cache_key_for(id=str(thing.pk))
        await self.backend.set(key, "stale")
        thing.title = "after"
        await thing.save()
        assert await self.backend.get(key) is None

    @pytest.mark.asyncio
    async def test_delete_invalidates_cache(self) -> None:
        cls = self._model()
        thing = await cls.create(title="del")
        key = cls._cache_key_for(id=str(thing.pk))
        await self.backend.set(key, "stale")
        await thing.delete()
        assert await self.backend.get(key) is None

    @pytest.mark.asyncio
    async def test_refresh_from_db_updates_cache(self) -> None:
        cls = self._model()
        thing = await cls.create(title="refresh")
        key = cls._cache_key_for(id=str(thing.pk))
        assert await self.backend.get(key) is None
        await thing.refresh_from_db()
        data = await self.backend.get(key)
        assert data is not None
        assert data["title"] == "refresh"

    @pytest.mark.asyncio
    async def test_invalidate_cache_disabled_when_ttl_zero(self) -> None:
        cls = self._model()
        cls._cache_ttl = 0
        thing = await cls.create(title="no-inv")
        # _invalidate_cache is a no-op when ttl<=0
        assert await thing._invalidate_cache() is None


# ---------------------------------------------------------------------------
# CachedQuerySet — fake-backend tests (SQLite in-memory, no Redis)
# ---------------------------------------------------------------------------


class TestCachedQuerySet:
    """CachedQuerySet caching, key building, serialize/deserialize paths."""

    def setup_method(self) -> None:
        self.backend = MockRedisBackend(default_ttl=300)

    def _qs(self, **kwargs: object) -> CachedQuerySet:
        qs = CachedQuerySet(CacheThing).cache(  # type: ignore[arg-type]
            backend=self.backend,  # type: ignore[arg-type]
            **kwargs,
        )
        return qs

    def test_cache_returns_clone(self) -> None:
        qs = CachedQuerySet(CacheThing)
        clone = qs.cache(ttl=120, key="k", namespace="custom")
        assert clone is not qs
        assert clone._cache_ttl == 120
        assert clone._cache_key == "k"
        assert clone._cache_namespace == "custom"

    def test_build_cache_key_custom(self) -> None:
        qs = self._qs(key="custom-key")
        assert qs._build_cache_key() == "custom-key"

    def test_build_cache_key_default_hash(self) -> None:
        qs = self._qs()
        key = qs._build_cache_key()
        assert key.startswith("CacheThing:hash:")

    def test_build_cache_key_includes_query_parts(self) -> None:
        qs = self._qs()
        qs._q_objects = ["a==1", "b==2"]
        qs._annotations = {"cnt": object()}
        qs._orderings = ["-id"]
        qs._limit = 10
        qs._offset = 5
        qs._distinct = True
        qs._fields_for_select = ("title",)
        key1 = qs._build_cache_key()

        qs2 = self._qs()
        qs2._q_objects = ["b==2", "a==1"]
        qs2._annotations = {"cnt": object()}
        qs2._orderings = ["-id"]
        qs2._limit = 10
        qs2._offset = 5
        qs2._distinct = True
        qs2._fields_for_select = ("title",)
        key2 = qs2._build_cache_key()
        # Reordering filters changes the serialized payload, so keys differ
        # only when the underlying filter list differs; same list stays same.
        qs3 = self._qs()
        qs3._q_objects = ["a==1", "b==2"]
        qs3._annotations = {"cnt": object()}
        qs3._orderings = ["-id"]
        qs3._limit = 10
        qs3._offset = 5
        qs3._distinct = True
        qs3._fields_for_select = ("title",)
        assert qs3._build_cache_key() == key1
        assert key1 != key2

    @pytest.mark.asyncio
    async def test_execute_miss_then_hit(self) -> None:
        qs = self._qs(ttl=300).filter(title="miss-unique")
        results = await qs
        assert len(results) == 0
        assert self.backend._store != {}

        cached = await qs
        assert isinstance(cached, list)

    @pytest.mark.asyncio
    async def test_execute_cache_disabled(self) -> None:
        qs = CachedQuerySet(CacheThing).filter(title="none")
        results = await qs
        assert results == []
        assert self.backend._store == {}

    @pytest.mark.asyncio
    async def test_execute_single_bypasses_cache(self) -> None:
        qs = self._qs(ttl=300).filter(title="single-missing-unique")
        qs._single = True
        results = await qs
        assert results is None  # single queries return the instance directly
        assert self.backend._store == {}

    @pytest.mark.asyncio
    async def test_execute_cache_hit_deserializes(self) -> None:
        await CacheThing.create(title="hit")
        qs = self._qs(ttl=300).filter(title="hit")
        await qs  # populate cache
        cached = await qs
        assert isinstance(cached, list)
        assert cached[0].title == "hit"

    @pytest.mark.asyncio
    async def test_execute_bad_cache_data_falls_back(self) -> None:
        qs = self._qs(ttl=300).filter(title="bad")
        key = qs._build_cache_key()
        await self.backend.set(key, "not-a-list")
        results = await qs
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_execute_read_error_falls_back_to_db(self) -> None:
        class BrokenBackend(MockRedisBackend):
            async def get(self, key: str) -> object:
                raise CacheError("boom")

        qs = CachedQuerySet(CacheThing).cache(
            ttl=300,
            backend=BrokenBackend(default_ttl=300),  # type: ignore[arg-type]
        )
        results = await qs
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_execute_write_error_suppressed(self) -> None:
        class BrokenBackend(MockRedisBackend):
            async def set(self, key: str, value: object, ttl: int | None = None) -> None:
                raise CacheError("boom")

        qs = CachedQuerySet(CacheThing).cache(
            ttl=300,
            backend=BrokenBackend(default_ttl=300),  # type: ignore[arg-type]
        )
        results = await qs
        assert isinstance(results, list)

    def test_serialize_results_datetime(self) -> None:
        raw = [CacheThing(title="t", created_at=datetime(2024, 1, 1))]
        serialized = CachedQuerySet._serialize_results(raw)  # type: ignore[arg-type]
        assert serialized[0]["title"] == "t"
        assert serialized[0]["created_at"] == "2024-01-01T00:00:00+00:00"
        assert serialized[0]["_model"] == "CacheThing"

    def test_serialize_results_non_model(self) -> None:
        serialized = CachedQuerySet._serialize_results([{"raw": 1}])  # type: ignore[arg-type]
        assert serialized == [{"raw": 1}]

    def test_deserialize_results_missing_model(self) -> None:
        qs = self._qs()
        results = qs._deserialize_results([{"title": "x"}])  # type: ignore[arg-type]
        assert results == [{"title": "x"}]

    def test_deserialize_results_unresolved_model(self) -> None:
        qs = self._qs()
        results = qs._deserialize_results([{"_model": "NoSuchModel", "title": "x"}])  # type: ignore[arg-type]
        assert results == [{"_model": "NoSuchModel", "title": "x"}]

    def test_deserialize_results_construct(self) -> None:
        qs = self._qs()
        results = qs._deserialize_results(  # type: ignore[arg-type]
            [{"_model": "CacheThing", "title": "x", "created_at": "2024-01-01T00:00:00"}]
        )
        assert len(results) == 1
        assert results[0].title == "x"

    def test_coerce_value_int(self) -> None:
        from tortoise.fields import IntField

        assert CachedQuerySet._coerce_value("42", IntField()) == 42

    def test_coerce_value_int_invalid(self) -> None:
        from tortoise.fields import IntField

        assert CachedQuerySet._coerce_value("nope", IntField()) == "nope"

    def test_coerce_value_float(self) -> None:
        from tortoise.fields import FloatField

        assert CachedQuerySet._coerce_value("1.5", FloatField()) == 1.5

    def test_coerce_value_float_invalid(self) -> None:
        from tortoise.fields import FloatField

        assert CachedQuerySet._coerce_value("nope", FloatField()) == "nope"

    def test_coerce_value_bool(self) -> None:
        from tortoise.fields import BooleanField

        assert CachedQuerySet._coerce_value("true", BooleanField()) is True
        assert CachedQuerySet._coerce_value("no", BooleanField()) is False

    def test_coerce_value_non_string(self) -> None:
        from tortoise.fields import IntField

        assert CachedQuerySet._coerce_value(42, IntField()) == 42

    def test_resolve_model_found(self) -> None:
        resolved = CachedQuerySet._resolve_model("CacheThing")
        assert resolved is CacheThing

    def test_resolve_model_missing(self) -> None:
        assert CachedQuerySet._resolve_model("NoSuchModel") is None

    @pytest.mark.asyncio
    async def test_invalidate_cache(self) -> None:
        qs = self._qs(ttl=300).filter(title="inv")
        await qs
        assert await qs.invalidate_cache() == 1
        assert await qs.invalidate_cache() == 0


# ---------------------------------------------------------------------------
# Default-backend paths (RedisCache singleton with fake pool)
# ---------------------------------------------------------------------------


class TestCacheDefaultBackend:
    """CacheableModel/CachedQuerySet default-backend branches."""

    @pytest.fixture(autouse=True)
    def _fake_redis(self, monkeypatch):
        """Route RedisCache.get_backend to a FakeRedisPool-backed backend."""
        import tortoise_extended.cache.redis as redis_module

        class FakeAIORedis:
            _pool: FakeRedisPool | None = None

            @classmethod
            def from_url(cls, url, max_connections=None, decode_responses=None, **kwargs):
                if cls._pool is None:
                    cls._pool = FakeRedisPool()
                return cls._pool

        FakeAIORedis._pool = FakeRedisPool()
        monkeypatch.setattr(redis_module, "aioredis", FakeAIORedis)
        RedisCache._instance = None
        RedisCache._pool = None
        yield FakeAIORedis._pool
        RedisCache._instance = None
        RedisCache._pool = None

    @pytest.mark.asyncio
    async def test_model_get_backend_default(self, _fake_redis) -> None:
        """_get_backend() falls back to RedisCache when no custom backend."""
        await RedisCache.init(url="redis://localhost:6379/0")
        CacheThing._cache_backend = None  # type: ignore[assignment]
        CacheThing._cache_ttl = 300
        backend = CacheThing._get_backend()
        from tortoise_extended.cache.redis import RedisCacheBackend

        assert isinstance(backend, RedisCacheBackend)
        assert backend.namespace == "model:CacheThing"
        assert backend.default_ttl == 300

    @pytest.mark.asyncio
    async def test_queryset_execute_default_backend(self, _fake_redis) -> None:
        """_execute() falls back to RedisCache.get_backend() when none set."""
        await RedisCache.init(url="redis://localhost:6379/0")
        qs = CachedQuerySet(CacheThing).cache(ttl=300).filter(title="db-default")
        results = await qs
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_queryset_invalidate_default_backend(self, _fake_redis) -> None:
        """invalidate_cache() falls back to RedisCache.get_backend()."""
        await RedisCache.init(url="redis://localhost:6379/0")
        qs = CachedQuerySet(CacheThing).cache(ttl=300).filter(title="inv-default")
        assert await qs.invalidate_cache() in (0, 1)

    @pytest.mark.asyncio
    async def test_serialize_results_relation_pk(self) -> None:
        """Values exposing a pk attribute are reduced to that pk."""
        CacheArticle._cache_backend = MockRedisBackend(default_ttl=300)  # type: ignore[assignment]
        category = await CacheCategory.create(name="tech")
        article = await CacheArticle.create(title="rel", category=category)
        serialized = CachedQuerySet._serialize_results([article])  # type: ignore[arg-type]
        assert serialized[0]["category"] == str(category.pk)
        assert serialized[0]["_model"] == "CacheArticle"

    def test_resolve_model_no_apps(self, monkeypatch) -> None:
        """Empty Tortoise.apps resolves nothing."""
        from tortoise.context import get_current_context

        ctx = get_current_context()
        assert ctx is not None
        monkeypatch.setattr(ctx, "_apps", {})
        assert CachedQuerySet._resolve_model("CacheThing") is None

    def test_resolve_model_non_dict_app(self, monkeypatch) -> None:
        """Non-dict app configs are skipped."""
        from tortoise.context import get_current_context

        ctx = get_current_context()
        assert ctx is not None
        monkeypatch.setattr(ctx, "_apps", {"bad": 42})
        assert CachedQuerySet._resolve_model("CacheThing") is None
