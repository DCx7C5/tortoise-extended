"""Redis cache backend.

Provides:
- RedisCache: Singleton connection manager
- RedisCacheBackend: CacheBackend implementation using Redis

Requires: redis[hiredis] >= 5.0.0
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, NoReturn, Self, TypeAlias, cast, override

from tortoise_extended._types import CacheValue
from tortoise_extended.cache.base import CacheBackend, JSONSerializer, Serializer
from tortoise_extended.exceptions import (
    CacheBackendNotInitializedError,
    CacheSerializationError,
    RedisCacheError,
)

try:
    import redis.asyncio as aioredis
    from redis.exceptions import RedisError as _RedisError
except ImportError:  # pragma: no cover
    aioredis = None
    _RedisError = None

# Exceptions that indicate an infrastructure-level Redis failure. Used to
# translate raw driver errors into :class:`RedisCacheError` at the backend
# boundary so consumers can catch a single domain type.
_REDIS_INFRA_ERRORS: tuple[type[Exception], ...] = (
    (_RedisError, ConnectionError, OSError, TimeoutError)
    if _RedisError is not None
    else (ConnectionError, OSError, TimeoutError)
)

if TYPE_CHECKING:
    import redis.asyncio as _redis_asyncio

    RedisClient: TypeAlias = _redis_asyncio.Redis

logger = logging.getLogger(__name__)


class RedisCache:
    """Singleton Redis connection manager.

    Usage:

        await RedisCache.init(url="redis://localhost:6379/0")
        backend = RedisCache.get_backend(namespace="entity")
    """

    _instance: RedisCache | None = None
    _pool: "RedisClient | None" = None

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        assert cls._instance is not None
        return cast(Self, cls._instance)

    @classmethod
    async def init(
        cls,
        url: str = "redis://localhost:6379/0",
        max_connections: int = 20,
        **kwargs: CacheValue,
    ) -> None:
        """Initialize Redis connection pool.

        Args:
            url: Redis URL (redis://[[username:]password@]host[:port][/db])
            max_connections: Maximum pool connections
            **kwargs: Additional aioredis.Redis kwargs
        """
        if aioredis is None:
            raise ImportError(
                "redis package not installed. Install with: uv add 'redis[hiredis]'"
            )

        instance = cls()
        if instance._pool is not None:
            await instance.close()

        instance._pool = aioredis.from_url(
            url,
            max_connections=max_connections,
            decode_responses=False,
            **kwargs,
        )
        # Test connection
        ping = cast(Callable[[], Awaitable[bool]], instance._pool.ping)
        _ = await ping()
        logger.info("Redis cache connected: %s", url.split("@")[-1])

    @classmethod
    async def close(cls) -> None:
        """Close Redis connection pool.

        Uses the async ``aclose()`` API (redis-py >= 5), falling back to the
        deprecated ``close()`` on older drivers (G24).
        """
        instance = cls()
        pool = instance._pool
        if pool is not None:
            aclose = getattr(pool, "aclose", None)
            if aclose is not None:
                await aclose()
            else:
                await pool.close()
            instance._pool = None
            logger.info("Redis cache disconnected")

    @classmethod
    def get_pool(cls) -> "RedisClient":
        """Get the Redis connection pool.

        Raises:
            RuntimeError: If not initialized
        """
        instance = cls()
        if instance._pool is None:
            raise CacheBackendNotInitializedError(
                "Redis cache not initialized. Call RedisCache.init() first."
            )
        return instance._pool

    @classmethod
    def get_backend(
        cls,
        namespace: str = "default",
        default_ttl: int = 300,
        serializer: Serializer | None = None,
    ) -> RedisCacheBackend:
        """Get a cache backend for a namespace.

        Args:
            namespace: Key prefix namespace
            default_ttl: Default TTL in seconds
            serializer: Serialization strategy
        """
        return RedisCacheBackend(
            pool=cls.get_pool(),
            namespace=namespace,
            default_ttl=default_ttl,
            serializer=serializer or JSONSerializer(),
        )


class RedisCacheBackend(CacheBackend):
    """Redis implementation of CacheBackend."""

    @staticmethod
    def _raise_redis(exc: Exception) -> NoReturn:
        """Translate a driver-level Redis failure into :class:`RedisCacheError`."""
        raise RedisCacheError(str(exc)) from exc

    def __init__(
        self,
        pool: "RedisClient",
        namespace: str = "default",
        default_ttl: int = 300,
        serializer: Serializer | None = None,
    ) -> None:
        super().__init__(
            default_ttl=default_ttl, serializer=serializer or JSONSerializer()
        )
        self.pool = pool
        self.namespace = namespace

    def _key(self, key: str) -> str:
        """Prefix key with namespace."""
        return f"{self.namespace}:{key}"

    @override
    async def get(self, key: str) -> CacheValue | None:
        """Get value from Redis."""
        try:
            data = await self.pool.get(self._key(key))
        except _REDIS_INFRA_ERRORS as exc:
            self._raise_redis(exc)
        if data is None:
            return None
        try:
            return self.deserialize(cast(bytes, data))
        except (TypeError, ValueError) as exc:
            raise CacheSerializationError(str(exc)) from exc

    @override
    async def set(self, key: str, value: CacheValue, ttl: int | None = None) -> None:
        """Set value in Redis with TTL."""
        ttl = ttl or self.default_ttl
        try:
            data = self.serialize(value)
        except (TypeError, ValueError) as exc:
            raise CacheSerializationError(str(exc)) from exc
        try:
            if ttl > 0:
                # Modern SET key value PX ttl form (redis-py >= 3.5); avoids
                # the deprecated setex() which redis-py >= 5 warns on (G24).
                _ = await self.pool.set(self._key(key), data, ex=ttl)
            else:
                _ = await self.pool.set(self._key(key), data)
        except _REDIS_INFRA_ERRORS as exc:
            self._raise_redis(exc)

    @override
    async def delete(self, key: str) -> bool:
        """Delete key from Redis."""
        try:
            count = await self.pool.delete(self._key(key))
        except _REDIS_INFRA_ERRORS as exc:
            self._raise_redis(exc)
        return count > 0

    @override
    async def exists(self, key: str) -> bool:
        """Check if key exists in Redis."""
        try:
            return bool(await self.pool.exists(self._key(key)))
        except _REDIS_INFRA_ERRORS as exc:
            self._raise_redis(exc)

    @override
    async def expire(self, key: str, ttl: int) -> bool:
        """Update TTL on existing key."""
        try:
            return bool(await self.pool.expire(self._key(key), ttl))
        except _REDIS_INFRA_ERRORS as exc:
            self._raise_redis(exc)

    @override
    async def keys(self, pattern: str = "*") -> list[str]:
        """Get keys matching pattern (within namespace) using SCAN."""
        full_pattern = self._key(pattern)
        prefix = f"{self.namespace}:"
        result: list[str] = []
        scan_iter = cast(
            "Callable[..., AsyncIterator[bytes | str]]",
            self.pool.scan_iter,
        )
        try:
            async for key in scan_iter(match=full_pattern, count=100):
                result.append(
                    (key.decode() if isinstance(key, bytes) else key).removeprefix(
                        prefix
                    )
                )
        except _REDIS_INFRA_ERRORS as exc:
            self._raise_redis(exc)
        return result

    @override
    async def delete_pattern(self, pattern: str = "*") -> int:
        """Delete all keys matching pattern in namespace using SCAN."""
        full_pattern = self._key(pattern)
        count = 0
        scan_iter = cast(
            "Callable[..., AsyncIterator[bytes | str]]",
            self.pool.scan_iter,
        )
        try:
            async for key in scan_iter(match=full_pattern, count=100):
                _ = await self.pool.delete(key)
                count += 1
        except _REDIS_INFRA_ERRORS as exc:
            self._raise_redis(exc)
        return count

    @override
    async def get_many(self, keys: list[str]) -> dict[str, CacheValue]:
        """Get multiple values at once."""
        if not keys:
            return {}
        full_keys = [self._key(k) for k in keys]
        try:
            values = await self.pool.mget(full_keys)
        except _REDIS_INFRA_ERRORS as exc:
            self._raise_redis(exc)
        result: dict[str, CacheValue] = {}
        for raw_key, value in zip(keys, values, strict=True):
            if value is not None:
                result[raw_key] = self.deserialize(cast(bytes, value))
        return result

    @override
    async def set_many(
        self, mapping: dict[str, CacheValue], ttl: int | None = None
    ) -> None:
        """Set multiple values at once."""
        if not mapping:
            return
        ttl = ttl or self.default_ttl
        pipe = self.pipeline()
        try:
            for key, value in mapping.items():
                data = self.serialize(value)
                if ttl > 0:
                    _ = pipe.setex(self._key(key), ttl, data)
                else:
                    _ = pipe.set(self._key(key), data)
        except (TypeError, ValueError) as exc:
            raise CacheSerializationError(str(exc)) from exc
        try:
            _ = await pipe.execute()
        except _REDIS_INFRA_ERRORS as exc:
            self._raise_redis(exc)

    @override
    async def incr(self, key: str, amount: int = 1) -> int:
        """Increment counter in Redis."""
        try:
            return await self.pool.incrby(self._key(key), amount)
        except _REDIS_INFRA_ERRORS as exc:
            self._raise_redis(exc)

    @override
    async def flush(self) -> None:
        """Flush all keys in namespace."""
        _ = await self.delete_pattern("*")

    def pipeline(self):
        """Get a Redis pipeline for batch operations."""
        return self.pool.pipeline(transaction=False)
