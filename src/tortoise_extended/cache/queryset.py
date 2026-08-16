"""Cached QuerySet for automatic query result caching.

Provides:
- CachedQuerySet: QuerySet with automatic Redis caching
"""

import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from datetime import date, datetime, time
from typing import Protocol, cast, override

from pypika_tortoise.context import DEFAULT_SQL_CONTEXT
from pypika_tortoise.queries import Table
from pypika_tortoise.terms import Term
from tortoise.expressions import Expression, Q, ResolveContext
from tortoise.fields.base import Field
from tortoise.filters import FilterInfoDict
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
from tortoise_extended.cache.base import CacheBackend, CacheKey, SingleFlight
from tortoise_extended.exceptions import CacheDataError, CacheError
from tortoise_extended.cache.redis import RedisCache

logger = logging.getLogger(__name__)

# Process-local deduplication of concurrent cache misses per cache key.
_single_flight = SingleFlight()


class _MetaWithBasetable(Protocol):
    """Minimal ``_meta`` surface needed for expression resolution.

    The stub overlay's :class:`MetaInfo` does not declare ``basetable``
    (a pypika ``Table`` created lazily by the runtime metaclass); the
    protocol keeps the resolve path type-safe without widening the overlay.
    """

    basetable: Table


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

        # Build deterministic key from query.
        # NOTE: ``_group_bys``, ``_joins`` and ``_select_related`` are
        # intentionally omitted: they are not part of the pre-existing key,
        # and ``_select_related`` is a set whose repr is order-nondeterministic.
        model_name = self.model.__name__
        filters: dict[
            str,
            RowValue | list[RowValue] | dict[str, str],
        ] = {}
        if hasattr(self, "_q_objects") and self._q_objects:
            # Sort so reordered-but-identical filters share one cache key.
            filters["q"] = cast(
                list[RowValue],
                sorted(self._q_signature(f) for f in self._q_objects),
            )
        if self._annotations:
            # Two annotations under the same alias with different expressions
            # must not share a cache entry — include the resolved SQL, not
            # just the aliases (the pre-fix ``str()`` of an annotation embeds
            # the object id and is nondeterministic across instances).
            filters["annotations"] = {
                name: self._annotation_signature(annotation)
                for name, annotation in sorted(self._annotations.items())
            }
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

    def _annotation_signature(self, annotation: Expression | Term) -> str:
        """Render an annotation expression to SQL for the cache key.

        ``str(annotation)`` embeds the object id (``<tortoise.functions.Sum
        object at 0x...>``) and is therefore nondeterministic across
        instances; keying annotations by alias alone would let
        ``annotate(total=Sum(x))`` and ``annotate(total=Count(y))`` share one
        cache entry. Resolving the expression to its SQL text (e.g.
        ``SUM("title")``) keeps distinct expressions apart while identical
        expressions produce identical keys. If resolution fails (e.g. test
        doubles that are not real expressions), fall back to the stable
        type name.
        """
        if isinstance(annotation, Term):
            return annotation.get_sql(DEFAULT_SQL_CONTEXT)

        try:
            result = annotation.resolve(
                ResolveContext(
                    model=self.model,
                    table=cast(
                        _MetaWithBasetable, cast(object, self.model._meta)
                    ).basetable,
                    annotations=self._annotations,
                    custom_filters=cast(
                        dict[str, FilterInfoDict],
                        getattr(self, "_custom_filters", None) or {},
                    ),
                )
            )
            return result.term.get_sql(DEFAULT_SQL_CONTEXT)
        except Exception:
            return f"{type(annotation).__module__}.{type(annotation).__qualname__}"

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

        # Cache stampede control: on concurrent misses for the same key only
        # one coroutine executes the query; the others await the shared
        # future and receive the same serialized result.
        claimed, future = _single_flight.claim(cache_key)
        if not claimed:
            return cast(list[Model], await future)

        try:
            results: list[Model] = await super()._execute()

            # Cache results
            serialized = self._serialize_results(results)
            try:
                await backend.set(
                    cache_key, cast(CacheValue, serialized), ttl=self._cache_ttl
                )
            except CacheError:
                logger.debug("Cache write error for key %s", cache_key, exc_info=True)

            if not future.done():
                future.set_result(cast(CacheValue, serialized))
            return results
        except BaseException as exc:
            # Propagate the failure to awaiters instead of leaving them
            # hanging on an unresolved future.
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            _single_flight.release(cache_key, future)

    @staticmethod
    def _serialize_results(
        results: Sequence[Model | SerializedRecord],
    ) -> list[SerializedRecord]:
        """Serialize model instances to dicts."""
        serialized: list[SerializedRecord] = []
        for instance in results:
            if not isinstance(instance, Model):
                # Defensive branch: instance is an already-serialized record.
                serialized.append(instance)
                continue
            data: SerializedRecord = {
                "_model": instance.__class__.__name__,
                # ``_meta.app`` is the Tortoise app name the model was
                # registered under; it disambiguates same-named models from
                # different apps during deserialization.
                "_model_app": instance._meta.app,
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

    def _deserialize_results(
        self, data: Sequence[Model | SerializedRecord]
    ) -> list[Model]:
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

            app_name = cast(str | None, item.get("_model_app"))
            model_cls = self._resolve_model(model_name, app_name)
            if model_cls is None:
                if app_name is not None:
                    # New-style record (has an app marker) whose model is not
                    # registered — stale or corrupt entry. Raising makes the
                    # caller treat it as a cache miss instead of silently
                    # serving the passthrough record.
                    msg = (
                        f"Cached record references model {model_name!r} in "
                        f"app {app_name!r} which is not registered"
                    )
                    raise CacheDataError(msg)
                # Defensive: legacy record (no app marker) whose model class
                # is not registered — pass through as-is (pre-fix behavior).
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
    def _resolve_model(
        model_name: str, app_name: str | None = None
    ) -> type[Model] | None:
        """Look up a Tortoise model class by name.

        Two apps can register models with the same class name (e.g. ``User``
        in ``auth`` and ``admin``), so the app-scoped lookup is preferred
        whenever the caller has the ``_model_app`` marker. When *app_name* is
        None (legacy records written before the marker existed) fall back to
        a name-only search.
        """
        from tortoise import Tortoise

        if not Tortoise.apps:
            return None

        if app_name is not None:
            try:
                app_config = Tortoise.apps[app_name]
            except KeyError:
                return None
            model_cls = app_config.get(model_name)
            if model_cls is not None:
                return model_cls
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
