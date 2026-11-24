"""Explicit async helpers for cache-backed fields.

:class:`~tortoise_extended.fields._cached_base._CachedFieldBase` owns the
column contract, the cache key and the backend; this module owns the async
read/write paths a caller uses when it wants to control *when* the cache is
consulted. Four helpers cover the matrix:

- :func:`cached_get` — cache-aside read. A hit is decoded and returned; a miss
  reads the loaded model attribute, writes it back with the field's TTL and
  returns it.
- :func:`cached_set` — writes the attribute first (it is authoritative for the
  instance), then applies the field's write policy: ``cache_aside`` deletes the
  key, ``write_through`` rewrites it.
- :func:`cached_invalidate` — deletes the key and returns the backend's
  boolean.
- :func:`prefetch_cached` — warms a list of instances with one ``get_many`` and
  one ``set_many``; never fails on an unsaved or partial instance.

The two concrete field types build on it: :class:`CachedField` (the column is
authoritative; cache-aside writes) and :class:`RedisPrimaryField` (Redis-first;
write-through writes). Both are read through the same helpers — Redis first —
and differ only in their write policy.

Failure matrix (``plan.md`` §J.4, nothing outside it): a
:class:`~tortoise_extended.exceptions.CacheBackendNotInitializedError`
propagates from every helper. A
:class:`~tortoise_extended.exceptions.RedisCacheError` propagates from set and
invalidate, but is caught in get and prefetch — the attribute is served and the
cache is left unfilled. A corrupt payload is a miss: the key is deleted, the
event is logged at debug level, and the attribute is served. A value the codec
cannot encode skips caching in get/prefetch and propagates from set. A name
that is not a cache field raises :class:`TypeError`; a partial/deferred
instance that never loaded the attribute raises :class:`ValueError` instead of
leaking ``AttributeError``.

The TTL is passed through unchanged: ``None`` means "backend default" and
``0`` means "no expiry", so no helper ever rewrites it.

The attribute is authoritative for the instance being written: ``getattr``
returns the value just assigned because ``Field.__get__`` exists only under
``TYPE_CHECKING`` (``tortoise/fields/base.py:204-218``). Cache-aside stays
correct across instances because the deleted key makes every other instance
miss and read its own loaded column value.

Usage::

    from tortoise_extended.fields.cached import cached_get

    value = await cached_get(instance, "field")
"""

import logging
from collections.abc import Sequence
from typing import Literal, cast

from tortoise.models import Model

from tortoise_extended._types import CacheScalar, CacheValue
from tortoise_extended.cache.base import CacheBackend
from tortoise_extended.exceptions import CacheSerializationError, RedisCacheError
from tortoise_extended.fields._cache_codec import CacheCodec
from tortoise_extended.fields._cached_base import _CachedFieldBase

__all__ = [
    "CachedField",
    "RedisPrimaryField",
    "cached_get",
    "cached_invalidate",
    "cached_set",
    "prefetch_cached",
]

logger = logging.getLogger(__name__)

_CODEC = CacheCodec()
"""Stateless codec shared by the helpers (safe for concurrent tasks/threads)."""


def _require_cached_field(instance: Model, name: str) -> _CachedFieldBase[CacheScalar]:
    """Resolve *name* on *instance* to a cache field.

    :param instance: The model instance the helper was called with.
    :param name: The attribute name.
    :returns: The cache field declared under *name*.
    :raises TypeError: If *name* is absent or is not a ``_CachedFieldBase``.
    """
    field = instance._meta.fields_map.get(name)
    if not isinstance(field, _CachedFieldBase):
        msg = (
            f"{type(instance).__name__}.{name} is not a cache field; "
            "the cached helpers require a _CachedFieldBase attribute"
        )
        raise TypeError(msg)
    return cast(_CachedFieldBase[CacheScalar], field)


def _require_loaded(instance: Model, name: str) -> None:
    """Refuse a partial/deferred instance that never loaded *name*.

    Tortoise leaves the attribute absent for ``only()`` / ``defer()`` loads and
    guards its own partial updates the same way (``tortoise/models.py``
    ``Model.save``); without this check the caller would see a bare
    ``AttributeError``, which is outside the published failure matrix.

    :param instance: The model instance being read.
    :param name: The attribute name.
    :raises ValueError: If *name* is not loaded on *instance*.
    """
    if not hasattr(instance, name):
        msg = (
            f"{type(instance).__name__}.{name} is not loaded on this instance "
            "(partial/deferred load); call refresh_from_db() or load the field"
        )
        raise ValueError(msg)


def _resolve_backend(
    field: _CachedFieldBase[CacheScalar], backend: CacheBackend | None
) -> CacheBackend:
    """Return the injected backend, or the field's lazily resolved default."""
    return backend if backend is not None else field.backend


async def _delete_quietly(backend: CacheBackend, key: str) -> None:
    """Delete *key*, downgrading a Redis failure to a debug log.

    Used only on read paths, where the failure matrix serves the attribute
    instead of surfacing ``RedisCacheError``.

    :param backend: The backend to delete from.
    :param key: The cache key to delete.
    """
    try:
        _ = await backend.delete(key)
    except RedisCacheError as exc:
        logger.debug("cache delete failed for %s: %s", key, exc)


async def cached_get[V = CacheScalar](
    instance: Model, name: str, *, backend: CacheBackend | None = None
) -> V:
    """Read *name* through the cache, falling back to the loaded attribute.

    A hit is decoded and returned without touching the instance. On a miss — or
    a corrupt payload, or a cache read failure — the loaded attribute is read,
    written back to the cache with the field's TTL, and returned.

    :param instance: A saved model instance.
    :param name: The cache-field attribute name.
    :param backend: Backend override; defaults to
        :attr:`~tortoise_extended.fields._cached_base._CachedFieldBase.backend`.
    :returns: The decoded cached value, or the attribute's value.
    :raises TypeError: If *name* is not a cache field.
    :raises ValueError: If *name* is not loaded on a partial instance.
    :raises tortoise_extended.exceptions.CacheBackendNotInitializedError:
        If no backend was injected and ``RedisCache.init`` has not run.
    """
    field = _require_cached_field(instance, name)
    resolved = _resolve_backend(field, backend)
    key = field.cached_key(instance)
    try:
        raw = await resolved.get(key)
    except RedisCacheError as exc:
        logger.debug("cache read failed for %s: %s", key, exc)
        raw = None
    if raw is not None:
        try:
            return cast(V, _CODEC.decode(raw))
        except CacheSerializationError as exc:
            logger.debug("corrupt cache payload for %s: %s", key, exc)
            await _delete_quietly(resolved, key)
    _require_loaded(instance, name)
    raw_value = getattr(instance, name)
    value = cast(V, raw_value)
    try:
        payload = _CODEC.encode(cast(CacheScalar, raw_value))
    except CacheSerializationError as exc:
        logger.debug("%s.%s is not cacheable: %s", type(instance).__name__, name, exc)
        return value
    try:
        await resolved.set(key, payload, ttl=field.ttl)
    except RedisCacheError as exc:
        logger.debug("cache write failed for %s: %s", key, exc)
    return value


async def cached_set[V = CacheScalar](
    instance: Model, name: str, value: V, *, backend: CacheBackend | None = None
) -> None:
    """Assign *value* to *instance* and update the cache per the write policy.

    The attribute is written first and is authoritative for this instance:
    ``getattr`` returns the value just assigned because ``Field.__get__`` is
    declared only under ``TYPE_CHECKING`` (``tortoise/fields/base.py:204-218``).
    Cache-aside then deletes the key; the cross-instance invariant is that
    every other instance misses and reads its own loaded column value.
    Write-through rewrites the key with the field's TTL.

    :param instance: A saved model instance.
    :param name: The cache-field attribute name.
    :param value: The value to assign.
    :param backend: Backend override; defaults to
        :attr:`~tortoise_extended.fields._cached_base._CachedFieldBase.backend`.
    :raises TypeError: If *name* is not a cache field.
    :raises tortoise_extended.exceptions.CacheSerializationError: If *value*
        cannot be encoded (write-through only).
    :raises tortoise_extended.exceptions.RedisCacheError: If the backend
        delete/set fails.
    :raises tortoise_extended.exceptions.CacheBackendNotInitializedError:
        If no backend was injected and ``RedisCache.init`` has not run.
    """
    field = _require_cached_field(instance, name)
    resolved = _resolve_backend(field, backend)
    setattr(instance, name, value)
    key = field.cached_key(instance)
    # ``_write_policy`` is protected; ``getattr`` is the sanctioned access path
    # for a private attribute owned by another module (see ``_types.py``).
    write_policy = cast(
        Literal["cache_aside", "write_through"], getattr(field, "_write_policy")
    )
    if write_policy == "cache_aside":
        _ = await resolved.delete(key)
        return
    await resolved.set(key, _CODEC.encode(cast(CacheScalar, value)), ttl=field.ttl)


async def cached_invalidate(
    instance: Model, name: str, *, backend: CacheBackend | None = None
) -> bool:
    """Delete the cache key holding *instance*'s value for *name*.

    :param instance: A saved model instance.
    :param name: The cache-field attribute name.
    :param backend: Backend override; defaults to
        :attr:`~tortoise_extended.fields._cached_base._CachedFieldBase.backend`.
    :returns: ``True`` when the key existed and was deleted.
    :raises TypeError: If *name* is not a cache field.
    :raises tortoise_extended.exceptions.RedisCacheError: If the backend
        delete fails.
    :raises tortoise_extended.exceptions.CacheBackendNotInitializedError:
        If no backend was injected and ``RedisCache.init`` has not run.
    """
    field = _require_cached_field(instance, name)
    resolved = _resolve_backend(field, backend)
    return await resolved.delete(field.cached_key(instance))


async def prefetch_cached(
    instances: Sequence[Model], name: str, *, backend: CacheBackend | None = None
) -> None:
    """Warm the cache for *name* across *instances* in one batched round-trip.

    Empty input returns without touching the backend. Unsaved instances
    (``pk is None``) and instances that never loaded the attribute are skipped:
    a prefetch over a mixed list must not fail. Every remaining instance
    contributes one key; validated hits are left alone, misses are encoded from
    the loaded attribute, and corrupt payloads are treated as misses and
    evicted. One ``get_many`` and one ``set_many`` are issued, both carrying the
    field's TTL (``ttl=0`` survives).

    :param instances: The model instances to warm.
    :param name: The cache-field attribute name.
    :param backend: Backend override; defaults to the field's backend.
    :raises TypeError: If *name* is not a cache field.
    :raises tortoise_extended.exceptions.CacheBackendNotInitializedError:
        If no backend was injected and ``RedisCache.init`` has not run.
    """
    if not instances:
        return
    field = _require_cached_field(instances[0], name)
    resolved = _resolve_backend(field, backend)
    eligible: list[Model] = []
    keys: list[str] = []
    for instance in instances:
        if instance.pk is None:
            continue
        if not hasattr(instance, name):
            continue
        eligible.append(instance)
        keys.append(field.cached_key(instance))
    if not keys:
        return
    try:
        cached = await resolved.get_many(keys)
    except RedisCacheError as exc:
        logger.debug("cache prefetch read failed for %s: %s", name, exc)
        return
    mapping: dict[str, CacheValue] = {}
    corrupt: list[str] = []
    for instance, key in zip(eligible, keys, strict=True):
        raw = cached.get(key)
        if raw is not None:
            try:
                _ = _CODEC.decode(raw)
            except CacheSerializationError as exc:
                logger.debug("corrupt cache payload for %s: %s", key, exc)
                corrupt.append(key)
            else:
                continue
        try:
            mapping[key] = _CODEC.encode(cast(CacheScalar, getattr(instance, name)))
        except CacheSerializationError as exc:
            logger.debug(
                "%s.%s is not cacheable: %s", type(instance).__name__, name, exc
            )
    for key in corrupt:
        await _delete_quietly(resolved, key)
    try:
        await resolved.set_many(mapping, ttl=field.ttl)
    except RedisCacheError as exc:
        logger.debug("cache prefetch write failed for %s: %s", name, exc)


class CachedField[V](_CachedFieldBase[V]):
    """Postgres-authoritative cache field: the column is the source of truth.

    The wrapped inner field owns a real SQL column that always holds the value;
    Redis is a read-through accelerant with a TTL. A read consults Redis first —
    a hit is decoded and returned — and a miss reads the loaded instance
    attribute, writes it back with the field's TTL and returns it. Redis is
    never authoritative: eviction or a Redis outage degrades to the column.

    :func:`cached_set` assigns the attribute and then **deletes** the key
    (``cache_aside``). The attribute is authoritative for the instance being
    written; the deleted key makes every *other* instance miss and read its own
    loaded column value. A later :func:`cached_get` on the same instance returns
    the value just assigned (ordinary Python attribute semantics) and
    repopulates Redis with it; a following ``save()`` (through
    ``CachedFieldsMixin``) rewrites the key from the saved value.

    TTL: ``ttl=None`` (the default) is passed to the backend, which applies its
    own 300 seconds; ``ttl=0`` means no expiry and survives every write path.
    No helper applies ``ttl or default``.

    Writes that bypass ``save()`` / ``delete()`` do not refresh the key, so it
    can stay stale until the TTL expires or :func:`cached_invalidate` clears it:
    ``QuerySet.update()``, ``QuerySet.delete()``, ``Model.bulk_update`` /
    ``QuerySet.bulk_update``, ``bulk_create`` and ``Model.bulk_insert`` (COPY).
    A ``save(update_fields=...)`` that omits this field **invalidates** the key
    rather than encoding a possibly-unsaved in-memory value, because that would
    make the cache contradict the column for a whole TTL. ``update_or_create()``
    is **not** a bypass — its update branch calls ``instance.save()``.
    ``refresh_from_db()`` re-reads the row but does not refresh the key.

    A partial/deferred load (``only()`` / ``defer()``) that never loaded the
    attribute raises :class:`ValueError` from the helpers instead of serving a
    silent ``None``.

    The constructor is inherited from
    :class:`~tortoise_extended.fields._cached_base._CachedFieldBase`.

    Usage::

        from tortoise import models
        from tortoise.fields import CharField

        from tortoise_extended.fields.cached import CachedField, cached_get

        class Article(models.Model):
            #: URL slug, cached for one minute
            slug = CachedField(CharField(max_length=64), ttl=60)

        article = await Article.get(id=1)
        slug = await cached_get(article, "slug")
    """

    _write_policy: Literal["cache_aside"] = "cache_aside"
    """Set the attribute and delete the key (cache-aside)."""


class RedisPrimaryField[V](_CachedFieldBase[V]):
    """Redis-first cache field with the SQL column as a real fallback copy.

    The inner field's column is written on every ``save()``, so the row always
    holds the last **saved** value; Redis is read first, and a miss reads the
    loaded instance value and repopulates the key. Eviction or Redis loss
    therefore degrades to the database rather than to an empty value.

    **Durability requires ``save()``.** :func:`cached_set` updates the attribute
    *and* the key immediately, but the column is written only by ``save()``: a
    ``cached_set`` followed by eviction or process exit loses the Redis copy,
    and a fresh instance then returns the last **saved** column value. The key
    is a faster copy of what the last save persisted, never a replacement for
    the database.

    A Redis failure fails open on reads: :func:`cached_get` logs at debug level
    and serves the instance value, leaving the cache unfilled.
    :func:`cached_set` and :func:`cached_invalidate` propagate
    :class:`~tortoise_extended.exceptions.RedisCacheError`. The TTL, bypass and
    partial-instance rules match :class:`CachedField`: ``ttl=None`` defers to
    the backend's own 300 seconds, ``ttl=0`` means no expiry, writes that skip
    ``save()`` (``QuerySet.update()`` / ``delete()``, both ``bulk_*`` levels,
    ``bulk_create``, ``Model.bulk_insert``) do not refresh the key, an omitted
    ``update_fields`` entry invalidates rather than encodes,
    ``update_or_create()`` is not a bypass, and a partial/deferred instance
    raises :class:`ValueError` from the helpers.

    Both cache field types read Redis first; the only difference between them is
    the write policy — ``"write_through"`` here, ``"cache_aside"`` on
    :class:`CachedField`. There is **no** ``read_priority`` knob, and none is
    planned.

    Usage::

        from tortoise import models
        from tortoise.fields import CharField

        from tortoise_extended.fields.cached import RedisPrimaryField, cached_set

        class Profile(models.Model):
            #: Display name, Redis-first for hot reads
            display_name = RedisPrimaryField(CharField(max_length=64), ttl=300)

        profile = await Profile.get(id=1)
        await cached_set(profile, "display_name", "Ada")
        await profile.save()  # durability: the column now holds "Ada"
    """

    _write_policy: Literal["write_through"] = "write_through"
    """Set the attribute and rewrite the key (write-through)."""
