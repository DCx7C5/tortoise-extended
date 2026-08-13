"""Base cache abstractions.

Provides:
- CacheBackend: Abstract base class for cache backends
- CacheKey: Typed cache key builder
- CacheNamespace: Namespace-based key prefixing
- Serializer: Serialization strategies (JSON, Pickle, Null)
"""

import hashlib
import json
import pickle
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import cast, override

from tortoise_extended._types import CacheValue
from tortoise_extended.exceptions import CacheKeyError


class CacheKey:
    """Build cache keys from components."""

    def __init__(self, prefix: str = "", separator: str = ":"):
        """Create a key builder.

        Args:
            prefix: Leading key component (may be empty).
            separator: String joining key components.
        """
        self.prefix = prefix
        self.separator = separator
        self._parts: list[str] = []

    def add(self, *parts: str | float) -> CacheKey:
        """Add parts to the key (chainable)."""
        self._parts.extend(str(p) for p in parts)
        return self

    def build(self) -> str:
        """Build the final key string.

        Raises:
            ValueError: If both prefix and parts are empty.
        """
        components = [self.prefix, *self._parts] if self.prefix else self._parts
        if not components:
            msg = "CacheKey requires a prefix or at least one part"
            raise CacheKeyError(msg)
        return self.separator.join(components)

    @staticmethod
    def from_dict(
        prefix: str, data: dict[str, str | int | float | bool | None]
    ) -> CacheKey:
        """Build a key from a dictionary (sorted, deterministic)."""
        key = CacheKey(prefix)
        for k, v in sorted(data.items()):
            _ = key.add(k, str(v))
        return key

    @staticmethod
    def hash(value: str) -> str:
        """Hash a value for use as a key part."""
        return hashlib.sha256(value.encode()).hexdigest()[:16]


class CacheNamespace:
    """Namespace prefix for cache keys."""

    def __init__(self, name: str, separator: str = ":"):
        """Create a namespace prefix.

        Args:
            name: Namespace name (leading key component).
            separator: String joining key components.
        """
        self.name = name
        self.separator = separator

    def key(self, *parts: str | int) -> str:
        """Build a namespaced key."""
        components = [self.name, *(str(p) for p in parts)]
        return self.separator.join(components)

    def pattern(self, *parts: str) -> str:
        """Build a key pattern (with wildcards)."""
        components = [self.name, *(str(p) for p in parts), "*"]
        return self.separator.join(components)


class Serializer(ABC):
    """Abstract serializer interface."""

    @abstractmethod
    def dumps(self, value: CacheValue) -> bytes:
        """Serialize value to bytes."""
        raise NotImplementedError

    @abstractmethod
    def loads(self, data: bytes) -> CacheValue:
        """Deserialize bytes to value."""
        raise NotImplementedError


class JSONSerializer(Serializer):
    """JSON serializer (safe, human-readable)."""

    def __init__(self, default: Callable[[CacheValue], CacheValue] | None = None):
        """Create a JSON serializer.

        Args:
            default: Fallback callable for values ``json.dumps`` cannot
                encode directly (defaults to ``str``).
        """
        self.default = default

    @override
    def dumps(self, value: CacheValue) -> bytes:
        return json.dumps(
            value,
            default=cast("Callable[..., CacheValue]", self.default or str),
            ensure_ascii=False,
        ).encode()

    @override
    def loads(self, data: bytes) -> CacheValue:
        return cast(CacheValue, json.loads(data.decode()))


class PickleSerializer(Serializer):
    """Pickle serializer (fast, supports more types).

    .. warning::

        ``pickle.loads()`` can execute arbitrary code from crafted payloads.
        Only use with trusted data. Never use with shared Redis instances
        or untrusted cache sources.
    """

    @override
    def dumps(self, value: CacheValue) -> bytes:
        return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)

    @override
    def loads(self, data: bytes) -> CacheValue:
        return cast(CacheValue, pickle.loads(data))


class NullSerializer(Serializer):
    """No-op serializer (for bytes values)."""

    @override
    def dumps(self, value: CacheValue) -> bytes:
        if isinstance(value, bytes):
            return value
        return str(value).encode()

    @override
    def loads(self, data: bytes) -> CacheValue:
        return data


class CacheBackend(ABC):
    """Abstract cache backend interface.

    Subclasses implement Redis, Memcached, etc.
    """

    def __init__(
        self,
        default_ttl: int = 300,
        serializer: Serializer | None = None,
    ) -> None:
        """Create a cache backend.

        Args:
            default_ttl: Default time-to-live in seconds (0 = no expiry).
            serializer: Serialization strategy (defaults to JSON).
        """
        self.default_ttl = default_ttl
        self.serializer = serializer or JSONSerializer()

    @abstractmethod
    async def get(self, key: str) -> CacheValue | None:
        """Get value by key."""
        raise NotImplementedError

    @abstractmethod
    async def set(self, key: str, value: CacheValue, ttl: int | None = None) -> None:
        """Set value with optional TTL (seconds)."""
        raise NotImplementedError

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete a key. Returns True if deleted."""
        raise NotImplementedError

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        raise NotImplementedError

    @abstractmethod
    async def expire(self, key: str, ttl: int) -> bool:
        """Update TTL on existing key."""
        raise NotImplementedError

    @abstractmethod
    async def keys(self, pattern: str) -> list[str]:
        """Get keys matching pattern."""
        raise NotImplementedError

    @abstractmethod
    async def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching pattern. Returns count deleted."""
        raise NotImplementedError

    async def get_many(self, keys: list[str]) -> dict[str, CacheValue]:
        """Get multiple values at once."""
        result: dict[str, CacheValue] = {}
        for key in keys:
            value = await self.get(key)
            if value is not None:
                result[key] = value
        return result

    async def set_many(
        self,
        mapping: dict[str, CacheValue],
        ttl: int | None = None,
    ) -> None:
        """Set multiple values at once."""
        for key, value in mapping.items():
            await self.set(key, value, ttl=ttl)

    async def incr(self, key: str, amount: int = 1) -> int:
        """Increment a counter. Returns new value.

        Note: Base implementation is get→compute→set (not atomic).
        RedisCacheBackend overrides with atomic ``incrby``.
        """
        current = await self.get(key)
        new_value = int(cast(int, current)) + amount if current is not None else amount
        await self.set(key, new_value)
        return new_value

    async def flush(self) -> None:
        """Flush all keys in the namespace.

        Subclasses should override this method.
        """
        raise NotImplementedError

    def serialize(self, value: CacheValue) -> bytes:
        """Serialize value."""
        return self.serializer.dumps(value)

    def deserialize(self, data: bytes) -> CacheValue:
        """Deserialize value."""
        return self.serializer.loads(data)
