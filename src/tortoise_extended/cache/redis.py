"""Redis cache backend.

Provides:
- RedisCache: Singleton connection manager
- RedisCacheBackend: CacheBackend implementation using Redis

Requires: redis[hiredis] >= 5.0.0
"""

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Self, TypeAlias, cast, override

from tortoise_extended._types import LibraryAny

try:
    import redis.asyncio as aioredis
except ImportError:  # pragma: no cover
    aioredis = None

if TYPE_CHECKING:
    import redis.asyncio as _redis_asyncio

    RedisClient: TypeAlias = _redis_asyncio.Redis

from tortoise_extended.cache.base import CacheBackend, JSONSerializer, Serializer

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
        **kwargs: LibraryAny,  # pyright: ignore[reportExplicitAny]
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
        """Close Redis connection pool."""
        instance = cls()
        if instance._pool is not None:
            await instance._pool.close()
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
            raise RuntimeError(
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

    def __init__(
        self,
        pool: LibraryAny,  # pyright: ignore[reportExplicitAny]
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
    async def get(self, key: str) -> LibraryAny | None:  # pyright: ignore[reportExplicitAny]
        """Get value from Redis."""
        data = await self.pool.get(self._key(key))
        if data is None:
            return None
        return self.deserialize(data)

    @override
    async def set(self, key: str, value: LibraryAny, ttl: int | None = None) -> None:  # pyright: ignore[reportExplicitAny]
        """Set value in Redis with TTL."""
        ttl = ttl or self.default_ttl
        data = self.serialize(value)
        if ttl > 0:
            await self.pool.setex(self._key(key), ttl, data)
        else:
            await self.pool.set(self._key(key), data)

    @override
    async def delete(self, key: str) -> bool:
        """Delete key from Redis."""
        count = await self.pool.delete(self._key(key))
        return count > 0

    @override
    async def exists(self, key: str) -> bool:
        """Check if key exists in Redis."""
        return bool(await self.pool.exists(self._key(key)))

    @override
    async def expire(self, key: str, ttl: int) -> bool:
        """Update TTL on existing key."""
        return bool(await self.pool.expire(self._key(key), ttl))

    @override
    async def keys(self, pattern: str = "*") -> list[str]:
        """Get keys matching pattern (within namespace) using SCAN."""
        full_pattern = self._key(pattern)
        prefix = f"{self.namespace}:"
        result: list[str] = []
        async for key in self.pool.scan_iter(match=full_pattern, count=100):
            result.append(cast(bytes, key).decode().removeprefix(prefix))
        return result

    @override
    async def delete_pattern(self, pattern: str = "*") -> int:
        """Delete all keys matching pattern in namespace using SCAN."""
        full_pattern = self._key(pattern)
        count = 0
        async for key in self.pool.scan_iter(match=full_pattern, count=100):
            await self.pool.delete(key)
            count += 1
        return count

    @override
    async def get_many(self, keys: list[str]) -> dict[str, LibraryAny]:  # pyright: ignore[reportExplicitAny]
        """Get multiple values at once."""
        if not keys:
            return {}
        full_keys = [self._key(k) for k in keys]
        values = await self.pool.mget(full_keys)
        result: dict[str, LibraryAny] = {}  # pyright: ignore[reportExplicitAny]
        for raw_key, value in zip(keys, values, strict=True):
            if value is not None:
                result[raw_key] = self.deserialize(cast(bytes, value))
        return result

    @override
    async def set_many(self, mapping: dict[str, LibraryAny], ttl: int | None = None) -> None:  # pyright: ignore[reportExplicitAny]
        """Set multiple values at once."""
        if not mapping:
            return
        ttl = ttl or self.default_ttl
        pipe = self.pipeline()
        for key, value in mapping.items():
            data = self.serialize(value)
            if ttl > 0:
                pipe.setex(self._key(key), ttl, data)
            else:
                pipe.set(self._key(key), data)
        await pipe.execute()

    @override
    async def incr(self, key: str, amount: int = 1) -> int:
        """Increment counter in Redis."""
        return await self.pool.incrby(self._key(key), amount)

    @override
    async def flush(self) -> None:
        """Flush all keys in namespace."""
        _ = await self.delete_pattern("*")

    def pipeline(self):
        """Get a Redis pipeline for batch operations."""
        return self.pool.pipeline(transaction=False)
