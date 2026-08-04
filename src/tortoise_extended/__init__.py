"""tortoise-extended: Tortoise ORM extensions for PostgreSQL workloads.

Monkey-patches tortoise-orm to add:
- VectorField (pgvector type codec)
- HNSWIndex / IVFFlatIndex / GiSTIndex (index types)
- Custom filters for vector similarity search
- RecursiveCTE, GraphTraversal, pathfinding helpers
- GraphVectorSearch (single-query vector + graph compositor with typed results)
- HybridSearch (vector + FTS weighted scoring)
- GraphNode / GraphEdge / HierarchyModel (graph patterns)
- LTreeField + ltree filters (hierarchical data)
- TimescaleDB hypertable migration operations
- Redis caching (optional)

Patches are applied automatically on import, and can also be applied
explicitly via :func:`patch` for consumers that want to make the
monkey-patching visible in their entry point. Either way, importing this
package (and calling :func:`patch` if used) must happen before
``Tortoise.init()``:

    import tortoise_extended

    tortoise_extended.patch()  # explicit; also applied automatically on import

    await Tortoise.init(
        db_url="...",
        modules={"models": ["..."]},
"""

from collections.abc import Awaitable, Callable

from asyncpg import Pool
from tortoise.fields import Field
from tortoise.filters import FilterInfoDict

from tortoise_extended._types import LibraryAny
from tortoise_extended.cache import (
    CacheableModel,
    CachedQuerySet,
    RedisCache,
    RedisCacheBackend,
    cached,
    cached_method,
    invalidate,
)
from tortoise_extended.exceptions import (
    CacheBackendNotInitializedError,
    CacheDataError,
    CacheError,
    CacheKeyError,
    CacheSerializationError,
    FieldDefinitionError,
    GraphError,
    GraphTraversalError,
    HierarchyError,
    HybridSearchError,
    IndexDefinitionError,
    LTreeFieldError,
    MigrationOperationError,
    RecursiveCTEError,
    RedisCacheError,
    TimescaleError,
    TortoiseExtendedError,
    VectorFieldError,
)
from tortoise_extended.expressions.graph_filters import (
    CosineDistance,
    InnerProduct,
    L2Distance,
    get_vector_filters,
    vector_encoder,
)
from tortoise_extended.expressions.graph_traversal import GraphTraversal
from tortoise_extended.expressions.graph_vector_search import (
    GraphVectorHit,
    GraphVectorSearch,
)
from tortoise_extended.expressions.hybrid_search import HybridSearch
from tortoise_extended.expressions.ltree_filters import get_ltree_filters
from tortoise_extended.expressions.pathfinding import (
    all_paths,
    find_cycles,
    shortest_path,
)
from tortoise_extended.expressions.recursive_cte import RecursiveCTE
from tortoise_extended.fields.ltree_field import LTreeField
from tortoise_extended.fields.vector_field import VectorField
from tortoise_extended.graph import GraphEdge, GraphNode, HierarchyModel
from tortoise_extended.indexes.hnsw_index import HNSWIndex, IVFFlatIndex
from tortoise_extended.indexes.ltree_index import GiSTIndex
from tortoise_extended.migrations.operations import (
    CreateContinuousAggregate,
    CreateHypertable,
)
from tortoise_extended.timescale import EventStreamMixin, TimeBucketRow

# ---------------------------------------------------------------------------
# pgvector codec helpers (module-level so every branch is unit-testable)
# ---------------------------------------------------------------------------


def _encode_vector(value: list[float] | str | None) -> str:
    """Encode a vector value into the pgvector text format."""
    if isinstance(value, str):
        return value
    if value:
        return "[" + ",".join(str(x) for x in value) + "]"
    return "[]"


def _decode_vector(value: str) -> list[float]:
    """Decode the pgvector text format into a list of floats."""
    stripped = value.strip("[]")
    if not stripped:
        return []
    return [float(x) for x in stripped.split(",") if x]


async def _pgvector_codec_init(conn: object) -> None:
    """Set the pgvector type codec on a single connection.

    Gracefully skips if the ``vector`` extension is not yet created in the
    database (e.g. before ``CREATE EXTENSION vector``) or if the connection
    does not support custom type codecs.
    """
    set_codec = getattr(conn, "set_type_codec", None)
    if set_codec is None:
        return
    try:
        await set_codec(
            "vector",
            encoder=_encode_vector,
            decoder=_decode_vector,
            schema="public",
        )
    except (ValueError, AttributeError):
        # ValueError: "unknown type: pgvector.vector" — extension not loaded
        # AttributeError: conn doesn't support set_type_codec
        pass


async def _combined_codec_init(
    conn: object, original_init: Callable[[object], Awaitable[None]] | None
) -> None:
    """Run the pgvector codec setup before a caller-provided init callback."""
    await _pgvector_codec_init(conn)
    if original_init is not None:
        await original_init(conn)


# ---------------------------------------------------------------------------
# Apply monkey-patches
# ---------------------------------------------------------------------------


def patch() -> None:
    """Apply all monkey-patches to tortoise-orm explicitly.

    Idempotent — safe to call any number of times, including after the
    patches were already applied automatically at import time. Re-applying
    never double-wraps a patched function.

    Usage in a consumer entry point (e.g. ``main.py``)::

        import tortoise_extended

        tortoise_extended.patch()  # apply all patches before Tortoise.init()

        await Tortoise.init(
            db_url="...",
            modules={"models": ["..."]},
        )

    The import itself already applies the patches, so calling ``patch()`` is
    optional but recommended when you want the monkey-patching to be explicit
    in the entry point instead of relying on the import side effect.
    """
    _apply_patches()


def _apply_patches() -> None:
    """Apply all monkey-patches to tortoise-orm.

    Called at import time. Safe to call multiple times (idempotent).
    """
    import tortoise.backends.asyncpg.client as _asyncpg_client_mod
    import tortoise.fields as _fields_mod
    import tortoise.filters as _filters_mod
    import tortoise.indexes as _indexes_mod

    # 1. Register VectorField in the fields module
    if not hasattr(_fields_mod, "VectorField"):
        _fields_mod.VectorField = VectorField
        _fields_mod.__all__.append("VectorField")

    # 2. Register HNSWIndex, IVFFlatIndex and GiSTIndex in the indexes module
    if not hasattr(_indexes_mod, "HNSWIndex"):
        _indexes_mod.HNSWIndex = HNSWIndex
    if not hasattr(_indexes_mod, "IVFFlatIndex"):
        _indexes_mod.IVFFlatIndex = IVFFlatIndex
    if not hasattr(_indexes_mod, "GiSTIndex"):
        _indexes_mod.GiSTIndex = GiSTIndex

    # 3. Patch get_filters_for_field to handle VectorField (idempotent)
    if not getattr(_filters_mod, "_tortoise_extended_patched", False):
        _original_get_filters = _filters_mod.get_filters_for_field

        def _patched_get_filters_for_field(
            field_name: str,
            field: Field[object] | None,
            source_field: str,
        ) -> dict[str, FilterInfoDict]:
            if field is not None and isinstance(field, VectorField):
                return get_vector_filters(field_name, source_field)
            if field is not None and isinstance(field, LTreeField):
                return get_ltree_filters(field_name, source_field)
            return _original_get_filters(field_name, field, source_field)

        _filters_mod.get_filters_for_field = _patched_get_filters_for_field
        setattr(_filters_mod, "_tortoise_extended_patched", True)

        # Also patch the local reference in tortoise.models — it imports
        # get_filters_for_field via ``from tortoise.filters import ...`` which
        # captures the original before our patch.
        import tortoise.models as _models_mod

        _models_mod.get_filters_for_field = _patched_get_filters_for_field

    # 4. Register pgvector codec on EVERY asyncpg connection via init callback
    if getattr(
        _asyncpg_client_mod.AsyncpgDBClient, "_tortoise_extended_codec_patched", False
    ):
        return
    _original_create_pool = _asyncpg_client_mod.AsyncpgDBClient.create_pool

    async def _patched_create_pool(
        self: _asyncpg_client_mod.AsyncpgDBClient,
        **kwargs: LibraryAny,  # pyright: ignore[reportExplicitAny]
    ) -> Pool:
        # Inject init callback so EVERY new connection gets the codec
        original_init = kwargs.pop("init", None)

        async def _combined_init(conn: object) -> None:
            await _combined_codec_init(conn, original_init)

        kwargs["init"] = _combined_init
        return await _original_create_pool(self, **kwargs)

    _asyncpg_client_mod.AsyncpgDBClient.create_pool = _patched_create_pool
    _asyncpg_client_mod.AsyncpgDBClient._tortoise_extended_codec_patched = True


__all__ = [
    "CacheBackendNotInitializedError",
    "CacheDataError",
    "CacheError",
    "CacheKeyError",
    "CacheSerializationError",
    "CacheableModel",
    "CachedQuerySet",
    "CosineDistance",
    "CreateContinuousAggregate",
    "CreateHypertable",
    "EventStreamMixin",
    "FieldDefinitionError",
    "GiSTIndex",
    "GraphEdge",
    "GraphError",
    "GraphNode",
    "GraphTraversal",
    "GraphTraversalError",
    "GraphVectorHit",
    "GraphVectorSearch",
    "HNSWIndex",
    "HierarchyError",
    "HierarchyModel",
    "HybridSearch",
    "HybridSearchError",
    "IVFFlatIndex",
    "IndexDefinitionError",
    "InnerProduct",
    "L2Distance",
    "LTreeField",
    "LTreeFieldError",
    "MigrationOperationError",
    "RecursiveCTE",
    "RecursiveCTEError",
    "RedisCache",
    "RedisCacheBackend",
    "RedisCacheError",
    "TimescaleError",
    "TortoiseExtendedError",
    "VectorField",
    "VectorFieldError",
    "TimeBucketRow",
    "all_paths",
    "cached",
    "cached_method",
    "find_cycles",
    "get_ltree_filters",
    "get_vector_filters",
    "invalidate",
    "patch",
    "shortest_path",
    "vector_encoder",
]

# Apply patches at import time (idempotent; `patch()` re-applies safely)
_apply_patches()
