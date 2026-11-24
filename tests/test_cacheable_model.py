"""Tests for tortoise_extended.models.cacheable_model.

Covers BaseCacheableModel ClassVar wiring, serialization round-trips,
and the full cache lifecycle using a MockRedisBackend (no live Redis needed).
The models are registered against the live PostgreSQL test database
(see ``docker-compose.dev.yml``) — this package is PostgreSQL-only.
"""

from collections.abc import AsyncGenerator
from typing import ClassVar, cast
from unittest.mock import AsyncMock, Mock

import pytest
from tortoise import Tortoise, fields, models
from tortoise.exceptions import DoesNotExist

from tortoise_extended.cache.base import CacheBackend, CacheValue
from tortoise_extended.cache.redis import RedisCache
from tortoise_extended.exceptions import CacheError
from tortoise_extended.models import BaseCacheableModel
from tests._pg_env import database_url, pg_skip_reason


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------


class MockRedisBackend(CacheBackend):
    """In-memory fake cache backend for testing."""

    def __init__(self, **kwargs: CacheValue) -> None:
        super().__init__(**kwargs)
        self._store: dict[str, CacheValue] = {}

    async def get(self, key: str) -> CacheValue | None:
        return self._store.get(key)

    async def set(self, key: str, value: CacheValue, ttl: int | None = None) -> None:
        self._store[key] = value

    async def delete(self, key: str) -> bool:
        existed = key in self._store
        self._store.pop(key, None)
        return existed

    async def exists(self, key: str) -> bool:
        return key in self._store

    async def expire(self, key: str, ttl: int) -> bool:
        return key in self._store

    async def keys(self, pattern: str) -> list[str]:
        import fnmatch

        return [k for k in self._store if fnmatch.fnmatch(k, pattern)]

    async def delete_pattern(self, pattern: str) -> int:
        import fnmatch

        to_delete = [k for k in self._store if fnmatch.fnmatch(k, pattern)]
        for k in to_delete:
            del self._store[k]
        return len(to_delete)

    async def flush(self) -> None:
        self._store.clear()


# ---------------------------------------------------------------------------
# Concrete models
# ---------------------------------------------------------------------------


class TaggedItem(BaseCacheableModel):
    """Test model with default ClassVar wiring."""

    name = fields.CharField(max_length=255)
    category = fields.CharField(max_length=100, default="general")

    class Meta:
        table = "tagged_items"


class ShortLivedItem(BaseCacheableModel):
    """TTL=0 disables caching (pass-through to DB)."""

    _cache_ttl = 0
    name = fields.CharField(max_length=255)

    class Meta:
        table = "short_lived_items"


class SelectiveFieldsItem(BaseCacheableModel):
    """Only cache name, not category."""

    _cache_fields: ClassVar[list[str] | None] = ["name"]
    _cache_ttl = 600
    name = fields.CharField(max_length=255)
    category = fields.CharField(max_length=100, default="general")

    class Meta:
        table = "selective_fields_items"


class CustomBackendItem(BaseCacheableModel):
    """Model with a custom cache backend."""

    _cache_backend: ClassVar[CacheBackend | None] = MockRedisBackend(default_ttl=120)
    name = fields.CharField(max_length=255)

    class Meta:
        table = "custom_backend_items"


class CustomNamespaceItem(BaseCacheableModel):
    """Model with a custom cache namespace."""

    _cache_namespace = "custom"
    name = fields.CharField(max_length=255)

    class Meta:
        table = "custom_namespace_items"


# ---------------------------------------------------------------------------
# Config — skip entire module if PG is not available
# ---------------------------------------------------------------------------


DB_URL = database_url()


_PG_SKIP_REASON = pg_skip_reason()
pytestmark = pytest.mark.skipif(
    _PG_SKIP_REASON is not None,
    reason=_PG_SKIP_REASON or "",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TABLES = (
    "tagged_items",
    "short_lived_items",
    "selective_fields_items",
    "custom_backend_items",
    "custom_namespace_items",
)


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> AsyncGenerator[None, None]:  # pyright: ignore[reportUnusedFunction]
    """Initialize Tortoise ORM against the live PostgreSQL test database."""
    _ = await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_cacheable_model"]},
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
    """Truncate every table and reset class-level backends around each test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(f"TRUNCATE {', '.join(_TABLES)} CASCADE")
    yield
    # Reset class-level backend overrides to avoid leaking across tests
    TaggedItem._cache_backend = None
    ShortLivedItem._cache_backend = None
    SelectiveFieldsItem._cache_backend = None
    CustomBackendItem._cache_backend = MockRedisBackend(default_ttl=120)
    CustomNamespaceItem._cache_backend = None


# ---------------------------------------------------------------------------
# 1. ClassVar defaults and overrides
# ---


class TestClassVarDefaults:
    """Default ClassVar wiring on BaseCacheableModel subclasses."""

    def test_default_classvars(self) -> None:
        assert TaggedItem._cache_ttl == 300
        assert TaggedItem._cache_fields is None
        assert TaggedItem._cache_namespace == "model"

    def test_ttl_zero_disables_caching(self) -> None:
        assert ShortLivedItem._cache_ttl == 0

    def test_selective_fields(self) -> None:
        assert SelectiveFieldsItem._cache_fields == ["name"]

    def test_custom_backend_stored(self) -> None:
        backend = getattr(CustomBackendItem, "_cache_backend")
        assert isinstance(backend, MockRedisBackend)
        assert backend.default_ttl == 120

    def test_custom_namespace(self) -> None:
        assert CustomNamespaceItem._cache_namespace == "custom"


# ---------------------------------------------------------------------------
# 2. _get_backend()
# ---


class TestGetBackend:
    """_get_backend() dispatching."""

    def test_get_backend_returns_custom(self) -> None:
        backend = CustomBackendItem._get_backend()
        assert backend is CustomBackendItem._cache_backend

    def test_get_backend_falls_back_to_redis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_backend = MockRedisBackend(default_ttl=300)
        calls: dict[str, CacheValue] = {}

        def fake_get_backend(**kwargs: CacheValue) -> MockRedisBackend:
            calls.update(kwargs)
            return mock_backend

        monkeypatch.setattr(RedisCache, "get_backend", fake_get_backend)
        result = TaggedItem._get_backend()

        assert result is mock_backend
        assert calls["namespace"] == "model:TaggedItem"
        assert calls["default_ttl"] == 300

    def test_get_backend_custom_namespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: dict[str, CacheValue] = {}

        def fake_get_backend(**kwargs: CacheValue) -> MockRedisBackend:
            calls.update(kwargs)
            return MockRedisBackend(default_ttl=300)

        monkeypatch.setattr(RedisCache, "get_backend", fake_get_backend)
        CustomNamespaceItem._get_backend()

        assert calls["namespace"] == "custom:CustomNamespaceItem"


# ---------------------------------------------------------------------------
# 3. _cache_key_for()
# ---


class TestCacheKeyFor:
    """Cache key construction."""

    def test_cache_key_for_get(self) -> None:
        key = TaggedItem._cache_key_for("get", id="123")
        assert "get" in key
        assert "123" in key

    def test_cache_key_for_filter(self) -> None:
        key_get = TaggedItem._cache_key_for("get", category="tech")
        key_filter = TaggedItem._cache_key_for("filter", category="tech")
        assert key_get != key_filter

    def test_cache_keys_sorted_kwargs(self) -> None:
        key1 = TaggedItem._cache_key_for("get", b="2", a="1")
        key2 = TaggedItem._cache_key_for("get", a="1", b="2")
        assert key1 == key2


# ---------------------------------------------------------------------------
# 4. _to_cache() and _from_cache() round-trip
# ---


class TestSerialization:
    """Serialization and deserialization round-trips."""

    def test_to_cache_includes_pk_and_model_name(self) -> None:
        inst = TaggedItem.construct(id=42, name="t", category="c")
        data = TaggedItem._to_cache(inst)

        assert data["_model"] == "TaggedItem"
        assert data["_pk"] == "42"

    def test_to_cache_only_cached_fields(self) -> None:
        inst = SelectiveFieldsItem.construct(id=1, name="t", category="c")
        data = SelectiveFieldsItem._to_cache(inst)

        assert "name" in data
        assert "category" not in data

    def test_to_cache_all_fields_when_none(self) -> None:
        inst = TaggedItem.construct(id=1, name="t", category="c")
        data = TaggedItem._to_cache(inst)

        assert "name" in data
        assert "category" in data

    def test_from_cache_restores_fields(self) -> None:
        inst = TaggedItem.construct(id=7, name="alpha", category="beta")
        data = TaggedItem._to_cache(inst)
        restored = TaggedItem._from_cache(data)

        assert restored.pk == 7
        assert restored.name == "alpha"
        assert restored.category == "beta"


# ---------------------------------------------------------------------------
# 5. get_cached() lifecycle
# ---


class TestGetCached:
    """get_cached() cache hit, miss, and error paths."""

    @pytest.mark.asyncio
    async def test_get_cached_cache_hit(self) -> None:
        backend = MockRedisBackend(default_ttl=300)
        TaggedItem._cache_backend = backend

        cache_key = TaggedItem._cache_key_for("get", id="99")
        cached_data: CacheValue = {
            "_model": "TaggedItem",
            "_pk": "99",
            "name": "cached",
            "category": "gen",
        }
        await backend.set(cache_key, cached_data)

        # A CacheError here would mean the cache wasn't hit
        get_mock = AsyncMock(side_effect=CacheError("should not be called"))

        # Patch TaggedItem.get so we can verify it's not called
        original_get = TaggedItem.get
        TaggedItem.get = get_mock  # type: ignore[assignment]
        try:
            result = await TaggedItem.get_cached(id="99")
            assert result is not None
            assert result.name == "cached"
            assert result.category == "gen"
            get_mock.assert_not_called()
        finally:
            TaggedItem.get = original_get

    @pytest.mark.asyncio
    async def test_get_cached_cache_miss_writes_to_cache(self) -> None:
        backend = MockRedisBackend(default_ttl=300)
        TaggedItem._cache_backend = backend

        db_instance = TaggedItem.construct(id=5, name="db", category="db_cat")
        get_mock = AsyncMock(return_value=db_instance)

        original_get = TaggedItem.get
        TaggedItem.get = get_mock  # type: ignore[assignment]
        try:
            result = await TaggedItem.get_cached(id="5")
            assert result is not None
            assert result.name == "db"
            get_mock.assert_awaited_once_with(id="5")

            cache_key = TaggedItem._cache_key_for("get", id="5")
            assert await backend.exists(cache_key)
        finally:
            TaggedItem.get = original_get

    @pytest.mark.asyncio
    async def test_get_cached_returns_none_when_not_found(self) -> None:
        backend = MockRedisBackend(default_ttl=300)
        TaggedItem._cache_backend = backend

        get_mock = AsyncMock(side_effect=DoesNotExist("not found"))
        original_get = TaggedItem.get
        TaggedItem.get = get_mock  # type: ignore[assignment]
        try:
            result = await TaggedItem.get_cached(id="999")
            assert result is None
        finally:
            TaggedItem.get = original_get

    @pytest.mark.asyncio
    async def test_get_cached_ttl_zero_passthrough(self) -> None:
        get_mock = AsyncMock(return_value="db_result")
        original_get = ShortLivedItem.get
        ShortLivedItem.get = get_mock  # type: ignore[assignment]
        try:
            result = await ShortLivedItem.get_cached(id="1")
            assert result == "db_result"
            get_mock.assert_awaited_once_with(id="1")
        finally:
            ShortLivedItem.get = original_get

    @pytest.mark.asyncio
    async def test_get_cached_corrupt_data_falls_through_to_db(self) -> None:
        """CacheDataError (a CacheError subclass) is caught by the except
        CacheError handler, so corrupt data silently falls through to DB."""
        backend = MockRedisBackend(default_ttl=300)
        TaggedItem._cache_backend = backend

        cache_key = TaggedItem._cache_key_for("get", id="1")
        await backend.set(cache_key, "not-a-dict")

        db_instance = TaggedItem.construct(id=1, name="from_db", category="db")
        get_mock = AsyncMock(return_value=db_instance)

        original_get = TaggedItem.get
        TaggedItem.get = get_mock  # type: ignore[assignment]
        try:
            result = await TaggedItem.get_cached(id="1")
            assert result is not None
            assert result.name == "from_db"
            get_mock.assert_awaited_once_with(id="1")
        finally:
            TaggedItem.get = original_get

    @pytest.mark.asyncio
    async def test_get_cached_read_error_swallowed(self) -> None:
        class ErrorBackend(MockRedisBackend):
            """Backend that raises on get."""

            async def get(self, key: str) -> CacheValue | None:
                raise CacheError("read failed")

        backend = ErrorBackend(default_ttl=300)
        TaggedItem._cache_backend = backend

        db_instance = TaggedItem.construct(id=1, name="from_db", category="db")
        get_mock = AsyncMock(return_value=db_instance)

        original_get = TaggedItem.get
        TaggedItem.get = get_mock  # type: ignore[assignment]
        try:
            result = await TaggedItem.get_cached(id="1")
            assert result is not None
            assert result.name == "from_db"
        finally:
            TaggedItem.get = original_get


# ---------------------------------------------------------------------------
# 6. filter_cached() lifecycle
# ---


class TestFilterCached:
    """filter_cached() cache hit, miss, and error paths."""

    @pytest.mark.asyncio
    async def test_filter_cached_cache_hit(self) -> None:
        backend = MockRedisBackend(default_ttl=300)
        TaggedItem._cache_backend = backend

        items = [
            {"_model": "TaggedItem", "_pk": "1", "name": "a", "category": "c"},
            {"_model": "TaggedItem", "_pk": "2", "name": "b", "category": "c"},
        ]
        cache_key = TaggedItem._cache_key_for("filter", category="c")
        await backend.set(cache_key, items)

        # Verify cache hit bypasses DB — cls.filter is sync, should not be called
        filter_mock = Mock(side_effect=CacheError("should not be called"))
        original_filter = TaggedItem.filter
        TaggedItem.filter = filter_mock  # type: ignore[assignment]
        try:
            result = await TaggedItem.filter_cached(category="c")
            assert len(result) == 2
            assert result[0].name == "a"
            assert result[1].name == "b"
            filter_mock.assert_not_called()
        finally:
            TaggedItem.filter = original_filter

    @pytest.mark.asyncio
    async def test_filter_cached_cache_miss_populates(self) -> None:
        backend = MockRedisBackend(default_ttl=300)
        TaggedItem._cache_backend = backend

        instances = [
            TaggedItem.construct(id=1, name="x", category="c"),
            TaggedItem.construct(id=2, name="y", category="c"),
        ]

        # cls.filter is sync — returns a queryset-like with async .all()
        mock_qs = Mock()
        mock_qs.all = AsyncMock(return_value=instances)
        filter_mock = Mock(return_value=mock_qs)

        original_filter = TaggedItem.filter
        TaggedItem.filter = filter_mock  # type: ignore[assignment]
        try:
            result = await TaggedItem.filter_cached(category="c")
            assert len(result) == 2
            filter_mock.assert_called_once_with(category="c")

            cache_key = TaggedItem._cache_key_for("filter", category="c")
            assert await backend.exists(cache_key)
        finally:
            TaggedItem.filter = original_filter

    @pytest.mark.asyncio
    async def test_filter_cached_ttl_zero_passthrough(self) -> None:
        # cls.filter is sync — returns a queryset-like with async .all()
        mock_qs = Mock()
        mock_qs.all = AsyncMock(return_value=["db_result"])
        filter_mock = Mock(return_value=mock_qs)

        original_filter = ShortLivedItem.filter
        ShortLivedItem.filter = filter_mock  # type: ignore[assignment]
        try:
            result = await ShortLivedItem.filter_cached(name="x")
            assert result == ["db_result"]
            filter_mock.assert_called_once_with(name="x")
        finally:
            ShortLivedItem.filter = original_filter

    @pytest.mark.asyncio
    async def test_filter_cached_corrupt_data_falls_through_to_db(self) -> None:
        """CacheDataError (a CacheError subclass) is caught by the except
        CacheError handler, so corrupt data silently falls through to DB."""
        backend = MockRedisBackend(default_ttl=300)
        TaggedItem._cache_backend = backend

        cache_key = TaggedItem._cache_key_for("filter", name="x")
        await backend.set(cache_key, "not-a-list")

        db_instance = TaggedItem.construct(id=1, name="x", category="db")

        # cls.filter(**kwargs) returns a queryset; .all() returns the list
        mock_qs = AsyncMock()
        mock_qs.all = AsyncMock(return_value=[db_instance])
        filter_mock = Mock(return_value=mock_qs)

        original_filter = TaggedItem.filter
        TaggedItem.filter = filter_mock  # type: ignore[assignment]
        try:
            result = await TaggedItem.filter_cached(name="x")
            assert len(result) == 1
            assert result[0].name == "x"
        finally:
            TaggedItem.filter = original_filter


# ---------------------------------------------------------------------------
# 7. Invalidation
# ---


class TestInvalidation:
    """Cache invalidation on save/delete and error swallowing."""

    @pytest.mark.asyncio
    async def test_save_calls_invalidate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inst = TaggedItem.construct(id=10, name="x", category="c")
        invalidate_mock = AsyncMock()
        inst._invalidate_cache = invalidate_mock  # type: ignore[method-assign]

        # Patch Model.save (the super() target) to avoid DB access
        monkeypatch.setattr(models.Model, "save", AsyncMock())
        await inst.save()
        invalidate_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_calls_invalidate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inst = TaggedItem.construct(id=10, name="x", category="c")
        invalidate_mock = AsyncMock()
        inst._invalidate_cache = invalidate_mock  # type: ignore[method-assign]

        # Patch Model.delete (the super() target) to avoid DB access
        monkeypatch.setattr(models.Model, "delete", AsyncMock())
        await inst.delete()
        invalidate_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalidate_cache_error_swallowed(self) -> None:
        class ErrorBackend(MockRedisBackend):
            """Backend that raises on delete."""

            async def delete(self, key: str) -> bool:
                raise CacheError("delete failed")

        backend = ErrorBackend(default_ttl=300)
        inst = TaggedItem.construct(id=5, name="x", category="c")
        TaggedItem._cache_backend = backend

        try:
            # Must not raise — CacheError is swallowed
            await inst._invalidate_cache()
        finally:
            TaggedItem._cache_backend = None


# ---------------------------------------------------------------------------
# 8. rehydrate()
# ---


class TestRehydrate:
    """rehydrate() reloads a cache-hit instance from the database."""

    @pytest.mark.asyncio
    async def test_rehydrate_loads_from_db(self) -> None:
        inst = TaggedItem.construct(id=42, name="cached", category="c")
        reloaded = TaggedItem.construct(id=42, name="fresh", category="db")

        get_mock = AsyncMock(return_value=reloaded)
        original_get = TaggedItem.get
        TaggedItem.get = get_mock  # type: ignore[assignment]
        try:
            result = await inst.rehydrate()
            tagged = cast(TaggedItem, result)
            assert tagged.name == "fresh"
            assert tagged.pk == 42
            get_mock.assert_awaited_once_with(pk=42)
        finally:
            TaggedItem.get = original_get
