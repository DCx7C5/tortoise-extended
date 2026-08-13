"""Cached QuerySet for automatic query result caching.

Provides:
- CachedQuerySet: QuerySet with automatic Redis caching
"""

import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from datetime import date, datetime, time
from typing import cast, override

from tortoise.expressions import Q
from tortoise.fields.base import Field
from tortoise.models import Model
from tortoise.queryset import QuerySet

from tortoise_extended._types import (
    CacheValue,
    CoercedValue,
    ModelKwargs,
    RowValue,
    SerializedRecord,
)
from tortoise_extended.cache._coerce import coerce_cache_value
from tortoise_extended.cache.base import CacheBackend, CacheKey
from tortoise_extended.exceptions import CacheDataError, CacheError
from tortoise_extended.cache.redis import RedisCache

logger = logging.getLogger(__name__)


def _record_as_model(record: Model | SerializedRecord) -> Model:
    """Pass an already-hydrated model record through as a model instance.

    The defensive deserialization path can receive records that were already
    model instances at runtime (e.g. after a pickling serializer round-trip).
    This helper re-widens the statically-narrowed record so the passthrough
    cast is legal without relying on ``object``/``Any``.
    """
    return cast(Model, record)


class CachedQuerySet(QuerySet[Model]):
    """QuerySet that automatically caches results in Redis.

    Usage:

        # Cache query results for 5 minutes
        entities = await CachedQuerySet(Entity).filter(
            type="TECHNOLOGY"
        ).cache(ttl=300)

        # Cache with custom key
        entities = await CachedQuerySet(Entity).filter(
            type="TECHNOLOGY"
        ).cache(key="tech_entities", ttl=600)

        # Invalidate cache
        await CachedQuerySet(Entity).filter(
            type="TECHNOLOGY"
        ).cache().invalidate_cache()
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
        filters: dict[str, RowValue | list[RowValue]] = {}
        if hasattr(self, "_q_objects") and self._q_objects:
            # Sort so reordered-but-identical filters share one cache key.
            filters["q"] = cast(
                list[RowValue],
                sorted(self._q_signature(f) for f in self._q_objects),
            )
        if self._annotations:
            filters["annotations"] = list(self._annotations.keys())
        if self._orderings:
            filters["order"] = cast(list[RowValue], self._orderings)
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
    async def _execute(self) -> list[Model]:
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
                    raise CacheDataError(
                        f"Expected list from cache, got {type(cached_result).__name__}"
                    )
                return self._deserialize_results(
                    cast(list[SerializedRecord], cached_result)
                )
        except CacheError:
            logger.debug("Cache read error for key %s", cache_key, exc_info=True)

        # Execute query
        results: list[Model] = await super()._execute()

        # Cache results
        try:
            serialized = self._serialize_results(results)
            await backend.set(cache_key, cast(CacheValue, serialized), ttl=self._cache_ttl)
        except CacheError:
            logger.debug("Cache write error for key %s", cache_key, exc_info=True)

        return results

    @staticmethod
    def _serialize_results(results: Sequence[Model | SerializedRecord]) -> list[SerializedRecord]:
        """Serialize model instances to dicts."""
        serialized: list[SerializedRecord] = []
        for instance in results:
            if not isinstance(instance, Model):
                # Defensive branch: instance is an already-serialized record.
                serialized.append(instance)
                continue
            data: SerializedRecord = {
                "_model": instance.__class__.__name__,
            }
            for field_name in instance._meta.fields:
                value: CoercedValue = getattr(instance, field_name, None)
                if isinstance(value, datetime):
                    value = value.isoformat()
                elif value is not None and hasattr(value, "pk"):
                    pk_val = getattr(value, "pk")
                    value = str(pk_val) if pk_val is not None else None
                data[field_name] = cast(RowValue, value)
            serialized.append(data)
        return serialized

    def _deserialize_results(self, data: Sequence[Model | SerializedRecord]) -> list[Model]:
        """Deserialize dicts back to model instances.

        Uses Tortoise ORM's ``construct()`` to create instances without
        hitting the database.
        """
        results: list[Model] = []
        for item in data:
            if isinstance(item, Model):
                results.append(item)
                continue

            model_name = cast(str | None, item.get("_model"))
            if model_name is None:
                # Defensive: already-hydrated record without a ``_model`` marker.
                results.append(_record_as_model(item))
                continue

            model_cls = self._resolve_model(model_name)
            if model_cls is None:
                # Defensive: record whose model class is not registered.
                results.append(_record_as_model(item))
                continue

            field_values: ModelKwargs = {}
            for field_name in model_cls._meta.fields:
                raw = item.get(field_name)
                if raw is None:
                    field_values[field_name] = None
                    continue

                field_obj = model_cls._meta.fields_map[field_name]
                field_values[field_name] = self._coerce_value(raw, field_obj)

            construct = cast(Callable[..., Model], model_cls.construct)
            results.append(construct(**field_values))
        return results

    @staticmethod
    def _resolve_model(model_name: str) -> type[Model] | None:
        """Look up a Tortoise model class by name."""
        from tortoise import Tortoise

        if not Tortoise.apps:
            return None

        for app_config in Tortoise.apps.values():
            # Tortoise.apps maps app name -> {model_name: model_cls}; skip
            # non-dict configs defensively (runtime context can be mutated).
            if not isinstance(app_config, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
                continue
            model_cls = app_config.get(model_name)
            if model_cls is not None:
                return model_cls
        return None

    @staticmethod
    def _q_signature(q: Q) -> str:
        """Build a canonical, order-insensitive signature for a Q object.

        ``str(q)`` depends on the order filters and children were supplied
        in, so two semantically identical filters would miss each other's
        cache entries.  Normalize kwargs by key and children by their own
        signatures so reordered-but-identical filters share one cache key.
        """
        return json.dumps(
            {
                "join": q.join_type,
                "negated": getattr(q, "_is_negated"),
                "filters": cast(dict[str, RowValue | list[RowValue]], q.filters),
                "children": sorted(CachedQuerySet._q_signature(c) for c in q.children),
            },
            sort_keys=True,
            default=CachedQuerySet._q_value_default,
        )

    @staticmethod
    def _q_value_default(value: CacheValue) -> CacheValue:
        """Deterministic JSON fallback for non-serializable Q filter values.

        ``str(set)`` / ``str(dict)`` are insertion-ordered, so sets, dicts,
        and containers are normalized into sorted structures before JSON
        encoding; datetimes become ISO strings.
        """
        if isinstance(value, (set, frozenset)):
            items = list(cast(set[CacheValue] | frozenset[CacheValue], value))
            return sorted((CachedQuerySet._q_value_default(v) for v in items), key=str)
        if isinstance(value, (list, tuple)):
            items = list(cast(list[CacheValue] | tuple[CacheValue, ...], value))
            return [CachedQuerySet._q_value_default(v) for v in items]
        if isinstance(value, dict):
            items = list(cast(dict[str, CacheValue], value).items())
            return {str(k): CachedQuerySet._q_value_default(v) for k, v in items}
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _coerce_value(raw: RowValue, field_obj: "Field[RowValue]") -> CoercedValue:
        """Coerce a JSON-deserialized value back to the field's Python type."""
        return coerce_cache_value(raw, field_obj)

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
