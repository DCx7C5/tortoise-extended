"""Extended cache tests covering edge cases, serializers, CacheBackend,
CacheKey, CacheNamespace, MockRedisBackend advanced operations.

No Redis connection required.
"""

import pytest

from tests.test_cache import MockRedisBackend

from tortoise_extended.cache.base import (
    CacheKey,
    CacheNamespace,
    JSONSerializer,
    NullSerializer,
    PickleSerializer,
)
from tortoise_extended.exceptions import CacheKeyError


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
