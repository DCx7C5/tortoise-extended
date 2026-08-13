"""Custom exception hierarchy for ``tortoise-extended``.

Every exception raised by the package derives from :class:`TortoiseExtendedError`
so consumers can catch a single base type. Domain subclasses map one-to-one
onto the feature areas:

- :class:`FieldDefinitionError` — field types (``VectorField``, ``LTreeField``)
- :class:`IndexDefinitionError` — index types (``HNSWIndex``, ``IVFFlatIndex``)
- :class:`GraphError` — graph models and traversals
- :class:`RecursiveCTEError` — recursive CTE builders
- :class:`HybridSearchError` — hybrid vector + FTS search
- :class:`TimescaleError` — TimescaleDB managers and migrations
- :class:`MigrationOperationError` — migration operations
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


class TimescaleError(TortoiseExtendedError):
    """TimescaleDB manager or migration failure."""


class MigrationOperationError(TortoiseExtendedError):
    """Invalid migration operation configuration."""


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
