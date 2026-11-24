"""Write-through cache hooks for models that declare cache fields.

``CachedFieldsMixin`` extends ``Model.save()`` / ``Model.delete()`` so the cache
keys of every :class:`~tortoise_extended.fields.cached.CachedField` and
:class:`~tortoise_extended.fields.cached.RedisPrimaryField` declared on the model
follow the database write. The hooks never replace the column write: they run
after the ORM write succeeded, and every cache failure is logged and swallowed,
so a Redis outage cannot turn a committed row into an exception.

After ``save()`` each cached field that was actually written is encoded from the
instance and stored with its TTL (``ttl=None`` → the backend's own 300 seconds,
``ttl=0`` → no expiry). A cached field omitted by ``save(update_fields=...)``
has its key **invalidated**, not encoded: the column was left unchanged while
the attribute may hold a new unsaved value, and caching that value would make a
Postgres-authoritative field contradict its own authority for a whole TTL.
Partial/deferred instances skip fields whose attribute is absent, matching the
ORM's own save guard. After ``delete()`` each cached field's key is removed.

Usage::

    from tortoise import models
    from tortoise.fields import CharField

    from tortoise_extended.fields.cached import CachedField
    from tortoise_extended.models.cached_mixin import CachedFieldsMixin

    class Article(CachedFieldsMixin, models.Model):
        #: URL slug
        slug = CachedField(CharField(max_length=64), ttl=60)

        class Meta:
            app = "content"

    article = await Article.get(id=1)
    article.slug = "new-slug"
    await article.save()  # column written, cache key refreshed
"""

import logging
from collections.abc import Iterable
from typing import cast, override

from tortoise.backends.base.client import BaseDBAsyncClient
from tortoise.models import Model

from tortoise_extended._types import CacheScalar
from tortoise_extended.exceptions import CacheError, CacheSerializationError
from tortoise_extended.fields._cache_codec import CacheCodec
from tortoise_extended.fields._cached_base import _CachedFieldBase

logger = logging.getLogger(__name__)

_CODEC = CacheCodec()
"""Stateless codec shared by the hooks (safe for concurrent tasks/threads)."""


class CachedFieldsMixin(Model):
    """Write-through cache hooks for models that declare cache fields.

    Abstract: mix it in **outermost**, before the identity base and any other
    mixin::

        class Entity(CachedFieldsMixin, models.Model): ...
        class Entity(CachedFieldsMixin, BaseCacheableModel, models.Model): ...
        class Entity(CachedFieldsMixin, VersionMixin, models.Model): ...

    The position is load-bearing, not stylistic. ``VersionMixin.save()`` runs
    its guarded update through ``QuerySet.update()`` directly and never calls
    ``Model.save()`` (``models/versioned.py:197-202``), so with the order
    reversed — ``(VersionMixin, CachedFieldsMixin, Model)`` — a versioned update
    commits while the write-through hook never runs and the key silently keeps
    the previous value. Do not justify the order with "each class calls
    ``super()``": the guarded update path does not. ``BaseCacheableModel`` also
    defines ``save()``/``delete()``; keeping the cached mixin outermost makes
    the cached-key hook run after every inner hook.

    These hooks fire only for ``instance.save()`` / ``instance.delete()``. They
    are bypassed — with the key left in place until its TTL expires or
    :func:`~tortoise_extended.fields.cached.cached_invalidate` runs — by
    ``QuerySet.update()``, ``QuerySet.delete()``, ``Model.bulk_update`` /
    ``QuerySet.bulk_update``, ``bulk_create`` (both levels) and
    ``Model.bulk_insert`` (COPY). ``update_or_create()`` is **not** a bypass:
    its update branch calls ``instance.save()``.

    Cache failures never fail the write: every
    :class:`~tortoise_extended.exceptions.CacheError` — including
    :class:`~tortoise_extended.exceptions.CacheBackendNotInitializedError` — is
    logged at debug level and swallowed once the column write stands. A value
    the codec cannot encode is logged at warning level and simply not cached.
    """

    class Meta:
        abstract = True

    @classmethod
    def _cached_fields(cls) -> list[tuple[str, _CachedFieldBase[CacheScalar]]]:
        """Return every ``(name, field)`` cache pair declared on the model.

        The scan is the single source of field discovery shared by :meth:`save`
        and :meth:`delete`; the pairs preserve ``fields_map`` order.

        :returns: ``(field_name, field)`` for each
            :class:`~tortoise_extended.fields._cached_base._CachedFieldBase`
            declared on the model.
        """
        return [
            (name, cast(_CachedFieldBase[CacheScalar], field))
            for name, field in cls._meta.fields_map.items()
            if isinstance(field, _CachedFieldBase)
        ]

    @override
    async def save(
        self,
        using_db: BaseDBAsyncClient | None = None,
        update_fields: Iterable[str] | None = None,
        force_create: bool = False,
        force_update: bool = False,
    ) -> None:
        """Save the row, then write through the keys of its cache fields.

        The ORM write runs first and unchanged; the hook runs only after it
        succeeded, so the primary key exists on the insert path too. A field
        named by *update_fields* — or every field when it is ``None`` — is
        encoded from the instance and stored with the field's TTL. A cached
        field **omitted** by *update_fields* has its key invalidated instead:
        the column was not written while the attribute may hold a new unsaved
        value, and caching that value would make a Postgres-authoritative field
        contradict its own authority for a whole TTL. A partial/deferred
        instance skips fields whose attribute is absent. Cache failures are
        logged and swallowed.

        :param using_db: Connection forwarded to the ORM unchanged; cache I/O
            never uses it.
        :param update_fields: Subset of field names written by the ORM;
            ``None`` means every field.
        :param force_create: Forwarded unchanged.
        :param force_update: Forwarded unchanged.
        """
        await super().save(
            using_db=using_db,
            update_fields=update_fields,
            force_create=force_create,
            force_update=force_update,
        )
        written = None if update_fields is None else set(update_fields)
        for name, field in type(self)._cached_fields():
            if not hasattr(self, name):
                logger.debug(
                    "%s.%s is not loaded; skipping its cache write",
                    type(self).__name__,
                    name,
                )
                continue
            key = field.cached_key(self)
            if written is not None and name not in written:
                try:
                    _ = await field.backend.delete(key)
                except CacheError as exc:
                    logger.debug("cache invalidation failed for %s: %s", key, exc)
                continue
            try:
                payload = _CODEC.encode(cast(CacheScalar, getattr(self, name)))
            except CacheSerializationError as exc:
                logger.warning(
                    "%s.%s was saved but is not cacheable: %s",
                    type(self).__name__,
                    name,
                    exc,
                )
                continue
            try:
                await field.backend.set(key, payload, ttl=field.ttl)
            except CacheError as exc:
                logger.debug("cache write failed for %s: %s", key, exc)

    @override
    async def delete(self, using_db: BaseDBAsyncClient | None = None) -> None:
        """Delete the row, then remove the keys of its cache fields.

        :param using_db: Connection forwarded to the ORM unchanged; cache I/O
            never uses it.
        """
        await super().delete(using_db=using_db)
        for name, field in type(self)._cached_fields():
            key = field.cached_key(self)
            try:
                _ = await field.backend.delete(key)
            except CacheError as exc:
                logger.debug(
                    "cache invalidation failed for %s.%s key %s: %s",
                    type(self).__name__,
                    name,
                    key,
                    exc,
                )


__all__ = ["CachedFieldsMixin"]
