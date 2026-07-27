"""CacheableModel mixin for Tortoise ORM models.

Provides automatic Redis caching for model instances.

Usage:

    from tortoise import models, fields
    from tortoise_extended.cache import CacheableModel

    class Entity(CacheableModel, models.Model):
        _cache_ttl = 600
        _cache_fields = ["title", "type"]

        title = fields.CharField(max_length=512)
        entity_type = fields.CharField(max_length=100)

        class Meta:
            table = "entities"

    # Cached queries
    entity = await Entity.get_cached(id="uuid-here")
    entities = await Entity.filter_cached(type="TECHNOLOGY")

    # Cache invalidation (automatic on save/delete)
    await entity.save()  # Cache invalidated
    await entity.delete()  # Cache invalidated
"""

import contextlib
import logging
from datetime import datetime
from typing import Any, ClassVar, Self, override

from tortoise import models

from tortoise_extended.cache.base import CacheBackend, CacheKey, Serializer
from tortoise_extended.cache.redis import RedisCache

logger = logging.getLogger(__name__)


class CacheableModel(models.Model):
    """Model mixin with automatic Redis caching.

    Class Variables:
        _cache_ttl: Default TTL in seconds (0 = disabled)
        _cache_fields: Fields to cache (None = all)
        _cache_namespace: Redis namespace
        _cache_backend: Custom cache backend
        _cache_serializer: Custom serializer
    """

    _cache_ttl: ClassVar[int] = 300
    _cache_fields: ClassVar[list[str] | None] = None
    _cache_namespace: ClassVar[str] = "model"
    _cache_backend: ClassVar[CacheBackend | None] = None
    _cache_serializer: ClassVar[Serializer | None] = None

    class Meta:
        abstract = True

    @classmethod
    def _get_backend(cls) -> CacheBackend:
        """Get cache backend for this model."""
        if cls._cache_backend is not None:
            return cls._cache_backend
        return RedisCache.get_backend(
            namespace=f"{cls._cache_namespace}:{cls.__name__}",
            default_ttl=cls._cache_ttl,
            serializer=cls._cache_serializer,
        )

    @classmethod
    def _cache_key_for(cls, **kwargs: Any) -> str:
        """Build cache key from lookup kwargs."""
        key = CacheKey(cls.__name__)
        for k, v in sorted(kwargs.items()):
            _ = key.add(k, str(v))
        return key.build()

    @classmethod
    async def get_cached(cls, **kwargs: Any) -> Self | None:
        """Get instance by kwargs, using cache.

        Usage:

            entity = await Entity.get_cached(id="uuid-here")
        """
        if cls._cache_ttl <= 0:
            return await cls.get(**kwargs)

        backend = cls._get_backend()
        cache_key = cls._cache_key_for(**kwargs)

        # Try cache
        try:
            cached = await backend.get(cache_key)
            if cached is not None:
                if not isinstance(cached, dict):
                    raise TypeError(f"Expected dict from cache, got {type(cached).__name__}")
                return cls._from_cache(cached)
        except Exception:
            logger.debug("Cache read error for key %s", cache_key, exc_info=True)

        # Query database
        try:
            instance = await cls.get(**kwargs)
        except models.Model.DoesNotExist:
            return None

        # Cache result
        with contextlib.suppress(Exception):
            await backend.set(cache_key, cls._to_cache(instance), ttl=cls._cache_ttl)

        return instance

    @classmethod
    async def filter_cached(cls, **kwargs: Any) -> list[Self]:
        """Filter instances using cache.

        Usage:

            entities = await Entity.filter_cached(type="TECHNOLOGY")
        """
        if cls._cache_ttl <= 0:
            return await cls.filter(**kwargs).all()

        backend = cls._get_backend()
        cache_key = cls._cache_key_for(**kwargs)

        # Try cache
        try:
            cached = await backend.get(cache_key)
            if cached is not None:
                if not isinstance(cached, list):
                    raise TypeError(f"Expected list from cache, got {type(cached).__name__}")
                return [cls._from_cache(item) for item in cached]
        except Exception:
            logger.debug("Cache read error for key %s", cache_key, exc_info=True)

        # Query database
        instances = await cls.filter(**kwargs).all()

        # Cache results
        try:
            serialized = [cls._to_cache(i) for i in instances]
            await backend.set(cache_key, serialized, ttl=cls._cache_ttl)
        except Exception:
            logger.debug("Cache write error for key %s", cache_key, exc_info=True)

        return instances

    @classmethod
    def _to_cache(cls, instance: models.Model) -> dict:
        """Serialize instance to cache format."""
        data: dict[str, Any] = {
            "_model": cls.__name__,
            "_pk": str(instance.pk),
        }

        fields_to_cache = cls._cache_fields or cls._meta.fields
        for field_name in fields_to_cache:
            value = getattr(instance, field_name, None)
            # Handle datetime serialization
            if isinstance(value, datetime):
                value = value.isoformat()
            # Handle ForeignKey (store PK)
            elif hasattr(value, "pk"):
                value = str(value.pk)
            data[field_name] = value

        return data

    @classmethod
    def _from_cache(cls, data: dict) -> Self:
        """Deserialize instance from cache format.

        Uses ``Model.construct()`` to create instances without validation.
        """
        kwargs: dict[str, Any] = {}

        # Restore primary key
        pk_field = cls._meta.pk_attr
        if pk_field and "_pk" in data:
            kwargs[pk_field] = data["_pk"]

        # Restore cached fields
        for key, value in data.items():
            if key.startswith("_"):
                continue
            kwargs[key] = value

        return cls.construct(**kwargs)

    async def _invalidate_cache(self) -> None:
        """Invalidate cache entries for this instance.

        Only invalidates the specific PK key. Filter-level cache entries
        are invalidated on TTL expiry.
        """
        if self._cache_ttl <= 0:
            return

        backend = self._get_backend()

        # Invalidate by PK
        pk_key = self._cache_key_for(id=str(self.pk))
        _ = await backend.delete(pk_key)

    @override
    async def save(self, *args: Any, **kwargs: Any) -> None:
        """Save and invalidate cache."""
        await super().save(*args, **kwargs)
        await self._invalidate_cache()

    @override
    async def delete(self, *args: Any, **kwargs: Any) -> None:
        """Delete and invalidate cache."""
        await super().delete(*args, **kwargs)
        await self._invalidate_cache()

    @override
    async def refresh_from_db(self, *args: Any, **kwargs: Any) -> None:
        """Refresh from DB and update cache."""
        await super().refresh_from_db(*args, **kwargs)
        if self._cache_ttl > 0:
            backend = self._get_backend()
            cache_key = self._cache_key_for(id=str(self.pk))
            with contextlib.suppress(Exception):
                await backend.set(
                    cache_key,
                    self._to_cache(self),
                    ttl=self._cache_ttl,
                )
