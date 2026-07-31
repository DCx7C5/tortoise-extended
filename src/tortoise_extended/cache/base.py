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
from typing import override

from tortoise_extended._types import LibraryAny
from tortoise_extended.exceptions import CacheKeyError


class CacheKey:
    """Build cache keys from components."""

    def __init__(self, prefix: str = "", separator: str = ":"):
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
    def from_dict(prefix: str, data: dict[str, object]) -> CacheKey:
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
    def dumps(self, value: LibraryAny) -> bytes:  # pyright: ignore[reportExplicitAny]
        """Serialize value to bytes."""
        raise NotImplementedError

    @abstractmethod
    def loads(self, data: bytes) -> LibraryAny:  # pyright: ignore[reportExplicitAny]
        """Deserialize bytes to value."""
        raise NotImplementedError


class JSONSerializer(Serializer):
    """JSON serializer (safe, human-readable)."""

    def __init__(self, default: LibraryAny = None):  # pyright: ignore[reportExplicitAny]
        self.default = default

    @override
    def dumps(self, value: LibraryAny) -> bytes:  # pyright: ignore[reportExplicitAny]
        return json.dumps(value, default=self.default or str, ensure_ascii=False).encode()

    @override
    def loads(self, data: bytes) -> LibraryAny:  # pyright: ignore[reportExplicitAny]
        return json.loads(data.decode())


class PickleSerializer(Serializer):
    """Pickle serializer (fast, supports more types).

    .. warning::

        ``pickle.loads()`` can execute arbitrary code from crafted payloads.
        Only use with trusted data. Never use with shared Redis instances
        or untrusted cache sources.
    """

    @override
    def dumps(self, value: LibraryAny) -> bytes:  # pyright: ignore[reportExplicitAny]
        return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)

    @override
    def loads(self, data: bytes) -> LibraryAny:  # pyright: ignore[reportExplicitAny]
        return pickle.loads(data)


class NullSerializer(Serializer):
    """No-op serializer (for bytes values)."""

    @override
    def dumps(self, value: LibraryAny) -> bytes:  # pyright: ignore[reportExplicitAny]
        if isinstance(value, bytes):
            return value
        return str(value).encode()

    @override
    def loads(self, data: bytes) -> LibraryAny:  # pyright: ignore[reportExplicitAny]
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
        self.default_ttl = default_ttl
        self.serializer = serializer or JSONSerializer()

    @abstractmethod
    async def get(self, key: str) -> LibraryAny | None:  # pyright: ignore[reportExplicitAny]
        """Get value by key."""
        raise NotImplementedError

    @abstractmethod
    async def set(self, key: str, value: LibraryAny, ttl: int | None = None) -> None:  # pyright: ignore[reportExplicitAny]
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

    async def get_many(self, keys: list[str]) -> dict[str, LibraryAny]:  # pyright: ignore[reportExplicitAny]
        """Get multiple values at once."""
        result: dict[str, LibraryAny] = {}  # pyright: ignore[reportExplicitAny]
        for key in keys:
            value = await self.get(key)
            if value is not None:
                result[key] = value
        return result

    async def set_many(
        self, mapping: dict[str, LibraryAny], ttl: int | None = None,  # pyright: ignore[reportExplicitAny]
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
        if current is None:
            current = 0
        new_value = int(current) + amount
        await self.set(key, new_value)
        return new_value

    async def flush(self) -> None:
        """Flush all keys in the namespace.

        Subclasses should override this method.
        """
        raise NotImplementedError

    def serialize(self, value: LibraryAny) -> bytes:  # pyright: ignore[reportExplicitAny]
        """Serialize value."""
        return self.serializer.dumps(value)

    def deserialize(self, data: bytes) -> LibraryAny:  # pyright: ignore[reportExplicitAny]
        """Deserialize value."""
        return self.serializer.loads(data)
