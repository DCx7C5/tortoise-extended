"""Advanced Redis caching for Tortoise ORM.

Provides:
- RedisCache: Singleton connection manager with connection pooling
- CacheableModel: Mixin for model-level caching
- CachedQuerySet: QuerySet with automatic caching
- @cached: Function-level caching decorator
- @cached_method: Method-level caching decorator
- @invalidate: Cache invalidation utilities

Usage:

    import tortoise_extended  # noqa: F401 — apply patches

    from tortoise import Tortoise
    from tortoise_extended.cache import CacheableModel, cached, RedisCache

    await Tortoise.init(...)
    await RedisCache.init(url="redis://localhost:6379/0")

    class Entity(CacheableModel, models.Model):
        _cache_ttl = 600
        title = fields.CharField(max_length=512)
        ...
"""

from tortoise_extended.cache.base import (
    CacheBackend,
    CacheKey,
    CacheNamespace,
    JSONSerializer,
    NullSerializer,
    PickleSerializer,
    Serializer,
)
from tortoise_extended.cache.decorators import cached, cached_method, invalidate
from tortoise_extended.cache.model import CacheableModel
from tortoise_extended.cache.queryset import CachedQuerySet
from tortoise_extended.cache.redis import RedisCache, RedisCacheBackend

__all__ = [
    "CacheBackend",
    "CacheKey",
    "CacheNamespace",
    "CacheableModel",
    "CachedQuerySet",
    "JSONSerializer",
    "NullSerializer",
    "PickleSerializer",
    "RedisCache",
    "RedisCacheBackend",
    "Serializer",
    "cached",
    "cached_method",
    "invalidate",
]
