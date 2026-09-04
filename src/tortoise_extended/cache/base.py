"""Base cache abstractions.

Provides:
- CacheBackend: Abstract base class for cache backends
- CacheKey: Typed cache key builder
- CacheNamespace: Namespace-based key prefixing
- Serializer: Serialization strategies (JSON, Pickle, Null)
"""

import asyncio
import builtins
import hashlib
import io
import json
import pickle
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import cast, override

from tortoise_extended._types import CacheValue
from tortoise_extended.exceptions import CacheError, CacheKeyError


class CacheKey:
    """Build cache keys from components.

    Each component is length-prefixed (``{len}:{value}``) so component
    boundaries stay unambiguous even when a value contains the separator —
    ``"1:2"`` as one component can never be confused with ``"1"``, ``"2"``
    as two. The format change invalidates cache entries written by older
    versions.
    """

    def __init__(self, prefix: str = "", separator: str = ":"):
        """Create a key builder.

        Args:
            prefix: Leading key component (may be empty).
            separator: String joining key components.
        """
        self.prefix = prefix
        self.separator = separator
        self._parts: list[str] = []

    @staticmethod
    def _length_prefixed(value: str) -> str:
        """Length-prefix a component so values containing the separator are unambiguous."""
        return f"{len(value)}:{value}"

    def add(self, *parts: str | int | float) -> CacheKey:
        """Add parts to the key (chainable)."""
        self._parts.extend(self._length_prefixed(str(p)) for p in parts)
        return self

    def build(self) -> str:
        """Build the final key string.

        Raises:
            ValueError: If both prefix and parts are empty.
        """
        components = (
            [self._length_prefixed(self.prefix), *self._parts]
            if self.prefix
            else self._parts
        )
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


#: Builtin names a :class:`PickleSerializer`-crafted payload may reference.
#: ``CacheValue`` is limited to JSON-serializable primitives, lists and dicts
#: (see :data:`tortoise_extended._types.CacheValue`), so only the primitive
#: constructors need to be reachable. Everything else — arbitrary classes,
#: ``os.system``, ``eval``, ``collections`` reconstructions, etc. — is rejected,
#: which blocks the ``__reduce__``-based remote code execution that vanilla
#: ``pickle.loads`` allows.
_SAFE_PICKLE_BUILTINS: frozenset[str] = frozenset(
    {"bool", "int", "float", "str", "bytes", "list", "dict", "tuple", "set"}
)


class _RestrictedUnpickler(pickle.Unpickler):
    """Unpickler that only allows primitive ``CacheValue`` types.

    ``find_class`` is the only hook pickle uses to resolve global references
    (``frommodule.attribute``) when reconstructing objects. By restricting it
    to a fixed allowlist of builtin constructors we prevent a malicious cache
    payload from instantiating arbitrary classes or invoking arbitrary
    callables, closing the standard pickle RCE vector.
    """

    def find_class(self, module: str, name: str) -> object:
        """Resolve a global reference; deny anything outside the safe builtins."""
        if module == "builtins" and name in _SAFE_PICKLE_BUILTINS:
            value = getattr(builtins, name, None)
            if value is not None:
                return value
        msg = f"pickle global {module}.{name} is forbidden (cache poisoning guard)"
        raise pickle.UnpicklingError(msg)


class PickleSerializer(Serializer):
    """Pickle serializer (fast, supports more types).

    .. warning::

        Pickle can execute arbitrary code on load. Although this serializer
        now uses a **restricted unpickler** that only permits the primitive
        ``CacheValue`` types (rejecting arbitrary class instantiation), it is
        still only safe for trusted cache sources. Prefer :class:`JSONSerializer`
        (the default) unless you need binary-safe or non-string-native values.
    """

    def __init__(self, max_pickle_size: int = 16 * 1024 * 1024) -> None:
        """Create a pickle serializer.

        Args:
            max_pickle_size: Maximum accepted payload size in bytes (default
                16 MiB). Guards against decompression/substream memory
                amplification on untrusted payloads.
        """
        self.max_pickle_size = max_pickle_size

    @override
    def dumps(self, value: CacheValue) -> bytes:
        return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)

    @override
    def loads(self, data: bytes) -> CacheValue:
        if len(data) > self.max_pickle_size:
            msg = f"pickle payload too large: {len(data)} > {self.max_pickle_size}"
            raise pickle.UnpicklingError(msg)
        return cast(CacheValue, _RestrictedUnpickler(io.BytesIO(data)).load())


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

        Raises:
            CacheError: If the stored value is not an integer.
        """
        current = await self.get(key)
        if current is not None:
            try:
                current_value = int(cast(int, current))
            except (TypeError, ValueError) as exc:
                msg = f"Cannot increment non-integer cache value for key {key!r}: {current!r}"
                raise CacheError(msg) from exc
            new_value = current_value + amount
        else:
            new_value = amount
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


class SingleFlight:
    """Deduplicate concurrent work on the same cache key.

    Used to prevent cache stampedes: when several coroutines miss the cache
    for the same key at once, exactly one of them executes the expensive
    work while the rest await the shared ``Future``. The module-level
    instances in ``queryset.py`` / ``decorators.py`` are intentionally
    process-local; per-process deduplication is sufficient because the
    fallback cache miss is harmless.

    Thread-safe under free-threaded (nogil) Python 3.14 builds: the
    internal future registry is guarded by a ``threading.Lock`` so that
    concurrent ``claim`` / ``release`` calls from different OS threads
    cannot corrupt the dict or lose a future reference.
    """

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[CacheValue]] = {}
        self._lock = threading.Lock()

    def claim(
        self, key: str
    ) -> tuple[bool, asyncio.Future[CacheValue]]:
        """Claim exclusive execution for *key*.

        The first caller wins (returns ``(True, future)``); subsequent
        callers share the same future (returns ``(False, future)``).
        """
        with self._lock:
            future = self._futures.get(key)
            if future is None:
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._futures[key] = future
                return True, future
        return False, future

    def release(self, key: str, future: asyncio.Future[CacheValue]) -> None:
        """Release the claim for *key* once its future has settled."""
        with self._lock:
            if self._futures.get(key) is future:
                del self._futures[key]
