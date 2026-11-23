"""Custom exception hierarchy for ``tortoise-extended``.

Every exception raised by the package derives from :class:`TortoiseExtendedError`
so consumers can catch a single base type. Domain subclasses map one-to-one
onto the feature areas:

- :class:`TortoiseVersionError` — the installed tortoise-orm release is outside
  the range the monkey-patches are tested against
- :class:`PatchConflictError` — a foreign object already owns an attribute this
  package patches (a second monkey-patch would silently lose)
- :class:`FieldDefinitionError` — field types (``VectorField``, ``LTreeField``)
- :class:`IndexDefinitionError` — index types (``HNSWIndex``, ``IVFFlatIndex``)
- :class:`GraphError` — graph models and traversals
- :class:`RecursiveCTEError` — recursive CTE builders
- :class:`HybridSearchError` — hybrid vector + FTS search
- :class:`TimescaleError` — TimescaleDB managers and migrations
- :class:`MigrationOperationError` — migration operations
- :class:`SetupError` — database lifecycle/bootstrap failures (e.g. a
  required PostgreSQL extension cannot be created)
- :class:`CacheError` — caching layer (plus ``CacheKeyError``,
  ``CacheSerializationError``, ``CacheDataError``,
  ``CacheBackendNotInitializedError``, ``RedisCacheError``)

Usage::

    from tortoise_extended.exceptions import CacheError

    try:
        await backend.get(cache_key)
    except CacheError:
        logger.warning("Cache unavailable; falling back to database")
"""


class TortoiseExtendedError(Exception):
    """Base class for all ``tortoise-extended`` errors."""


class TortoiseVersionError(TortoiseExtendedError):
    """The installed tortoise-orm version is outside the supported range.

    Raised at import time (before any monkey-patch is applied) when the
    installed tortoise-orm is not in the range this package is tested against.
    The message names the installed version, the tested range, and the remedy.
    """


class PatchConflictError(TortoiseExtendedError):
    """A foreign object already owns an attribute ``tortoise_extended`` patches.

    Raised instead of silently skipping the patch: when another package has
    already defined e.g. ``tortoise.fields.VectorField``, applying our patch
    would be a no-op while ``tortoise_extended`` keeps exporting this
    package's class — the "only one monkey-patch can win" hazard made
    invisible (AGENTS.md rule 2).
    """


class FieldDefinitionError(TortoiseExtendedError):
    """Invalid field definition or value conversion."""


class VectorFieldError(FieldDefinitionError):
    """Invalid ``VectorField`` configuration or value."""


class LTreeFieldError(FieldDefinitionError):
    """Invalid ``LTreeField`` configuration or value."""


class IndexDefinitionError(TortoiseExtendedError):
    """Invalid index configuration (e.g. bad distance metric)."""


class GraphError(TortoiseExtendedError):
    """Base class for graph model/traversal errors."""


class GraphTraversalError(GraphError):
    """Invalid graph traversal configuration or state."""


class HierarchyError(GraphError):
    """Invalid hierarchy (ltree path) operation."""


class RecursiveCTEError(TortoiseExtendedError):
    """Invalid recursive CTE builder state."""


class HybridSearchError(TortoiseExtendedError):
    """Invalid hybrid search configuration."""


class PgCryptoError(TortoiseExtendedError):
    """Invalid pgcrypto function argument (e.g. unsupported algorithm)."""


class TimescaleError(TortoiseExtendedError):
    """TimescaleDB manager or migration failure."""


class MigrationOperationError(TortoiseExtendedError):
    """Invalid migration operation configuration."""


class SetupError(TortoiseExtendedError):
    """The database is not ready for ``tortoise-extended``.

    Raised by the lifecycle layer when a required PostgreSQL extension cannot
    be created — most commonly because the connecting role lacks the
    privilege ``CREATE EXTENSION`` needs (superuser for a non-trusted
    extension such as ``vector`` or ``timescaledb``; database ``CREATE`` for a
    trusted one), or because the server does not ship the extension at all.
    The message names the failing extension and the original server error is
    chained as the cause.
    """


class CacheError(TortoiseExtendedError):
    """Base class for cache backend failures."""


class CacheKeyError(CacheError):
    """A cache key could not be built from the given components."""


class CacheSerializationError(CacheError):
    """A value could not be serialized/deserialized for the cache."""


class CacheDataError(CacheError):
    """Cached data has an unexpected shape (corrupt or stale entry)."""


class CacheBackendNotInitializedError(CacheError):
    """The cache backend was used before ``init()`` was called."""


class RedisCacheError(CacheError):
    """A Redis infrastructure failure (connection, timeout, etc.)."""
