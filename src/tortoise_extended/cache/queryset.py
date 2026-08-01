"""Cached QuerySet for automatic query result caching.

Provides:
- CachedQuerySet: QuerySet with automatic Redis caching
"""

import hashlib
import json
import logging
from datetime import datetime
from typing import cast, override

from tortoise.models import Model
from tortoise.queryset import QuerySet

from tortoise_extended._types import LibraryAny
from tortoise_extended.cache.base import CacheBackend, CacheKey
from tortoise_extended.exceptions import CacheDataError, CacheError
from tortoise_extended.cache.redis import RedisCache

logger = logging.getLogger(__name__)


class CachedQuerySet(QuerySet[Model]):
    """QuerySet that automatically caches results in Redis.

    Usage:

        # Cache query results for 5 minutes
        entities = await Entity.filter(type="TECHNOLOGY").cache(ttl=300)

        # Cache with custom key
        entities = await Entity.filter(type="TECHNOLOGY").cache(
            key="tech_entities",
            ttl=600,
        )

        # Invalidate cache
        await Entity.filter(type="TECHNOLOGY").invalidate_cache()
    """

    _cache_ttl: int = 0
    _cache_key: str | None = None
    _cache_backend: CacheBackend | None = None
    _cache_namespace: str = "queryset"

    def cache(
        self,
        ttl: int = 300,
        key: str | None = None,
        backend: CacheBackend | None = None,
        namespace: str = "queryset",
    ) -> CachedQuerySet:
        """Enable caching for this query.

        Args:
            ttl: Time-to-live in seconds
            key: Custom cache key (auto-generated if None)
            backend: Cache backend (default: Redis)
            namespace: Cache namespace
        """
        clone = self._clone()
        clone._cache_ttl = ttl
        clone._cache_key = key
        clone._cache_backend = backend
        clone._cache_namespace = namespace
        return clone

    def _build_cache_key(self) -> str:
        """Build cache key from query parameters."""
        if self._cache_key:
            return self._cache_key

        # Build deterministic key from query
        model_name = self.model.__name__
        filters: dict[str, LibraryAny] = {}  # pyright: ignore[reportExplicitAny]
        if hasattr(self, "_q_objects") and self._q_objects:
            filters["q"] = [str(f) for f in self._q_objects]
        if self._annotations:
            filters["annotations"] = list(self._annotations.keys())
        if self._orderings:
            filters["order"] = self._orderings
        if self._limit:
            filters["limit"] = self._limit
        if self._offset:
            filters["offset"] = self._offset
        if self._distinct:
            filters["distinct"] = True
        if self._fields_for_select:
            filters["fields"] = list(self._fields_for_select)

        key_data = json.dumps(filters, sort_keys=True, default=str)
        key_hash = hashlib.sha256(key_data.encode()).hexdigest()[:16]

        return CacheKey.from_dict(model_name, {"hash": key_hash}).build()

    @override
    async def _execute(self) -> list[LibraryAny]:  # pyright: ignore[reportExplicitAny]
        """Execute query with caching."""
        if self._cache_ttl <= 0 or self._single:
            return await super()._execute()

        backend = self._cache_backend
        if backend is None:
            backend = RedisCache.get_backend(
                namespace=self._cache_namespace,
                default_ttl=self._cache_ttl,
            )

        cache_key = self._build_cache_key()

        # Try cache
        try:
            cached_result = await backend.get(cache_key)
            if cached_result is not None:
                if not isinstance(cached_result, list):
                    raise CacheDataError(f"Expected list from cache, got {type(cached_result).__name__}")
                return self._deserialize_results(cached_result)  # pyright: ignore[reportUnknownArgumentType]
        except CacheError:
            logger.debug("Cache read error for key %s", cache_key, exc_info=True)

        # Execute query
        results: list[LibraryAny] = await super()._execute()  # pyright: ignore[reportExplicitAny]

        # Cache results
        try:
            serialized = self._serialize_results(results)
            await backend.set(cache_key, serialized, ttl=self._cache_ttl)
        except CacheError:
            logger.debug("Cache write error for key %s", cache_key, exc_info=True)

        return results

    @staticmethod
    def _serialize_results(results: list[LibraryAny]) -> list[dict[str, LibraryAny]]:  # pyright: ignore[reportExplicitAny]
        """Serialize model instances to dicts."""
        serialized: list[dict[str, LibraryAny]] = []  # pyright: ignore[reportExplicitAny]
        for instance in results:
            if hasattr(instance, "_meta"):
                data: dict[str, LibraryAny] = {  # pyright: ignore[reportExplicitAny]
                    "_model": instance.__class__.__name__,
                }
                for field_name in instance._meta.fields:
                    value: LibraryAny = getattr(instance, field_name, None)  # pyright: ignore[reportExplicitAny]
                    if isinstance(value, datetime):
                        value = value.isoformat()
                    elif hasattr(value, "pk"):
                        pk_val = value.pk
                        value = str(pk_val) if pk_val is not None else None
                    data[field_name] = value
                serialized.append(data)
            else:
                serialized.append(instance)
        return serialized

    def _deserialize_results(self, data: list[dict[str, LibraryAny]]) -> list[LibraryAny]:  # pyright: ignore[reportExplicitAny]
        """Deserialize dicts back to model instances.

        Uses Tortoise ORM's ``construct()`` to create instances without
        hitting the database.
        """
        results: list[LibraryAny] = []  # pyright: ignore[reportExplicitAny]
        for record in data:
            model_name: str | None = record.get("_model")
            if model_name is None:
                results.append(record)
                continue

            model_cls = self._resolve_model(model_name)
            if model_cls is None:
                results.append(record)
                continue

            field_values: dict[str, LibraryAny] = {}  # pyright: ignore[reportExplicitAny]
            for field_name in model_cls._meta.fields:
                raw = record.get(field_name)
                if raw is None:
                    field_values[field_name] = None
                    continue

                field_obj = model_cls._meta.fields_map[field_name]
                field_values[field_name] = self._coerce_value(raw, field_obj)

            results.append(model_cls.construct(**field_values))
        return results

    @staticmethod
    def _resolve_model(model_name: str) -> type[Model] | None:
        """Look up a Tortoise model class by name."""
        from tortoise import Tortoise

        if not Tortoise.apps:
            return None

        for app_config in Tortoise.apps.values():
            if not isinstance(app_config, dict):
                continue
            # Tortoise.apps maps app name -> {model_name: model_cls}.
            models_by_name = cast("dict[str, type[Model]]", app_config)
            model_cls = models_by_name.get(model_name)
            if model_cls is not None:
                return model_cls
        return None

    @staticmethod
    def _coerce_value(raw: LibraryAny, field_obj: LibraryAny) -> LibraryAny:  # pyright: ignore[reportExplicitAny]
        """Coerce a JSON-deserialized value back to the field's Python type."""
        if isinstance(raw, str) and hasattr(field_obj, "field_type"):
            ft = field_obj.field_type
            if ft is int:
                try:
                    return int(raw)
                except (ValueError, TypeError):
                    pass
            elif ft is float:
                try:
                    return float(raw)
                except (ValueError, TypeError):
                    pass
            elif ft is bool:
                return raw.lower() in ("true", "1", "yes")
        return raw

    async def invalidate_cache(self) -> int:
        """Invalidate cache for this query.

        Returns:
            Number of cache entries deleted
        """
        backend = self._cache_backend
        if backend is None:
            backend = RedisCache.get_backend(namespace=self._cache_namespace)

        cache_key = self._build_cache_key()
        deleted = await backend.delete(cache_key)
        return 1 if deleted else 0

    @override
    def _clone(self) -> CachedQuerySet:
        """Clone the queryset."""
        qs = cast("CachedQuerySet", super()._clone())
        qs._cache_ttl = self._cache_ttl
        qs._cache_key = self._cache_key
        qs._cache_backend = self._cache_backend
        qs._cache_namespace = self._cache_namespace
        return qs
