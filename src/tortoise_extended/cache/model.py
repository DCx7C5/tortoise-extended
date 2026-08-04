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
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Self, cast, override

from tortoise import models
from tortoise.exceptions import DoesNotExist

from tortoise_extended._types import LibraryAny, ModelKwargs, SerializedRecord
from tortoise_extended.exceptions import CacheDataError, CacheError

if TYPE_CHECKING:
    from tortoise.backends.base.client import BaseDBAsyncClient

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
    def _cache_key_for(cls, op: str, **kwargs: LibraryAny) -> str:  # pyright: ignore[reportExplicitAny]
        """Build cache key from lookup kwargs.

        ``op`` namespaces the operation ("get" vs "filter") so the single
        instance cache and the list cache never collide on the same kwargs.
        """
        key = CacheKey(cls.__name__).add(op)
        for k, v in sorted(kwargs.items()):
            _ = key.add(k, str(v))
        return key.build()

    @classmethod
    async def get_cached(cls, **kwargs: ModelKwargs) -> Self | None:
        """Get instance by kwargs, using cache.

        Usage:

            entity = await Entity.get_cached(id="uuid-here")
        """
        if cls._cache_ttl <= 0:
            return await cls.get(**cast(dict[str, LibraryAny], kwargs))  # pyright: ignore[reportExplicitAny]

        backend = cls._get_backend()
        cache_key = cls._cache_key_for("get", **cast(dict[str, LibraryAny], kwargs))  # pyright: ignore[reportExplicitAny]

        # Try cache
        try:
            cached = await backend.get(cache_key)
            if cached is not None:
                if not isinstance(cached, dict):
                    raise CacheDataError(f"Expected dict from cache, got {type(cached).__name__}")
                return cls._from_cache(cast(SerializedRecord, cached))
        except CacheError:
            logger.debug("Cache read error for key %s", cache_key, exc_info=True)

        # Query database
        try:
            instance = await cls.get(**cast(dict[str, LibraryAny], kwargs))  # pyright: ignore[reportExplicitAny]
        except DoesNotExist:
            return None

        # Cache result
        with contextlib.suppress(CacheError):
            await backend.set(cache_key, cls._to_cache(instance), ttl=cls._cache_ttl)

        return instance

    @classmethod
    async def filter_cached(cls, **kwargs: ModelKwargs) -> list[Self]:
        """Filter instances using cache.

        Usage:

            entities = await Entity.filter_cached(type="TECHNOLOGY")
        """
        if cls._cache_ttl <= 0:
            return await cls.filter(**cast(dict[str, LibraryAny], kwargs)).all()  # pyright: ignore[reportExplicitAny]

        backend = cls._get_backend()
        cache_key = cls._cache_key_for("filter", **cast(dict[str, LibraryAny], kwargs))  # pyright: ignore[reportExplicitAny]

        # Try cache
        try:
            cached = await backend.get(cache_key)
            if cached is not None:
                if not isinstance(cached, list):
                    raise CacheDataError(f"Expected list from cache, got {type(cached).__name__}")
                return [cls._from_cache(item) for item in cast(list[SerializedRecord], cached)]
        except CacheError:
            logger.debug("Cache read error for key %s", cache_key, exc_info=True)

        # Query database
        instances = await cls.filter(**cast(dict[str, LibraryAny], kwargs)).all()  # pyright: ignore[reportExplicitAny]

        # Cache results
        try:
            serialized = [cls._to_cache(i) for i in instances]
            await backend.set(cache_key, serialized, ttl=cls._cache_ttl)
        except CacheError:
            logger.debug("Cache write error for key %s", cache_key, exc_info=True)

        return instances

    @classmethod
    def _to_cache(cls, instance: models.Model) -> SerializedRecord:
        """Serialize instance to cache format."""
        data: SerializedRecord = {
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
            elif value is not None and hasattr(value, "pk"):
                value = str(value.pk)
            data[field_name] = value

        return data

    @classmethod
    def _from_cache(cls, data: SerializedRecord) -> Self:
        """Deserialize instance from cache format.

        Uses ``Model.construct()`` to create instances without validation.
        """
        kwargs: ModelKwargs = {}

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

        # Invalidate by PK — key the lookup on the actual pk field name
        pk_attr = self._meta.pk_attr or "id"
        pk_key = self._cache_key_for("get", **{pk_attr: str(self.pk)})
        _ = await backend.delete(pk_key)

    @override
    async def save(
        self,
        using_db: BaseDBAsyncClient | None = None,
        update_fields: Iterable[str] | None = None,
        force_create: bool = False,
        force_update: bool = False,
    ) -> None:
        """Save and invalidate cache."""
        await super().save(
            using_db=using_db,
            update_fields=update_fields,
            force_create=force_create,
            force_update=force_update,
        )
        await self._invalidate_cache()

    @override
    async def delete(self, using_db: BaseDBAsyncClient | None = None) -> None:
        """Delete and invalidate cache."""
        await super().delete(using_db=using_db)
        await self._invalidate_cache()

    @override
    async def refresh_from_db(
        self,
        fields: Iterable[str] | None = None,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Refresh from DB and update cache."""
        await super().refresh_from_db(fields=fields, using_db=using_db)
        if self._cache_ttl > 0:
            backend = self._get_backend()
            pk_attr = self._meta.pk_attr or "id"
            cache_key = self._cache_key_for("get", **{pk_attr: str(self.pk)})
            with contextlib.suppress(CacheError):
                await backend.set(
                    cache_key,
                    self._to_cache(self),
                    ttl=self._cache_ttl,
                )
