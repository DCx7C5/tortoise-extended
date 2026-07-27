"""tortoise-extended: Tortoise ORM extensions for PostgreSQL workloads.

Monkey-patches tortoise-orm to add:
- VectorField (pgvector type codec)
- HNSWIndex / IVFFlatIndex / GiSTIndex (index types)
- Custom filters for vector similarity search
- RecursiveCTE, GraphTraversal, pathfinding helpers
- HybridSearch (vector + FTS weighted scoring)
- GraphNode / GraphEdge / HierarchyModel (graph patterns)
- LTreeField + ltree filters (hierarchical data)
- TimescaleDB hypertable migration operations
- Redis caching (optional)

All patches are applied on import. Import this package once, early,
before Tortoise.init():

    import tortoise_extended  # noqa: F401 — apply patches

    await Tortoise.init(
        db_url="...",
        modules={"models": ["..."]},
"""

from tortoise_extended.cache import (
    CacheableModel,
    CachedQuerySet,
    RedisCache,
    RedisCacheBackend,
    cached,
    cached_method,
    invalidate,
)
from tortoise_extended.expressions.graph_filters import (
    CosineDistance,
    InnerProduct,
    L2Distance,
    get_vector_filters,
    vector_encoder,
)
from tortoise_extended.expressions.graph_traversal import GraphTraversal
from tortoise_extended.expressions.hybrid_search import HybridSearch
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

# ---------------------------------------------------------------------------
# Apply monkey-patches
# ---------------------------------------------------------------------------


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

    # 2. Register HNSWIndex and IVFFlatIndex in the indexes module
    if not hasattr(_indexes_mod, "HNSWIndex"):
        _indexes_mod.HNSWIndex = HNSWIndex
    if not hasattr(_indexes_mod, "IVFFlatIndex"):
        _indexes_mod.IVFFlatIndex = IVFFlatIndex

    # 3. Patch get_filters_for_field to handle VectorField (idempotent)
    if not getattr(_filters_mod, "_tortoise_extended_patched", False):
        _original_get_filters = _filters_mod.get_filters_for_field

        def _patched_get_filters_for_field(
            field_name: str,
            field: object,
            source_field: str,
        ) -> dict:
            if field is not None and isinstance(field, VectorField):
                return get_vector_filters(field_name, source_field)
            return _original_get_filters(field_name, field, source_field)

        _filters_mod.get_filters_for_field = _patched_get_filters_for_field
        _filters_mod._tortoise_extended_patched = True

        # Also patch the local reference in tortoise.models — it imports
        # get_filters_for_field via ``from tortoise.filters import ...`` which
        # captures the original before our patch.
        import tortoise.models as _models_mod

        _models_mod.get_filters_for_field = _patched_get_filters_for_field

    # 4. Register pgvector codec on EVERY asyncpg connection via init callback
    _original_create_pool = _asyncpg_client_mod.AsyncpgDBClient.create_pool

    async def _pgvector_codec_init(conn: object) -> None:
        """Set pgvector type codec on a single connection.

        Gracefully skips if the ``vector`` extension is not yet created
        in the database (e.g. before ``CREATE EXTENSION vector``).
        """
        try:
            set_codec = getattr(conn, "set_type_codec", None)
            if set_codec is None:
                return
            await set_codec(
                "vector",
                encoder=lambda v: (
                    v if isinstance(v, str)
                    else "[" + ",".join(str(x) for x in v) + "]" if v
                    else "[]"
                ),
                decoder=lambda v: (
                    [float(x) for x in v.strip("[]").split(",") if x]
                    if isinstance(v, str) and v.strip("[]")
                    else []
                ),
                schema="public",
            )
        except (ValueError, AttributeError):
            # ValueError: "unknown type: pgvector.vector" — extension not loaded
            # AttributeError: conn doesn't support set_type_codec
            pass

    async def _patched_create_pool(self, **kwargs):
        # Inject init callback so EVERY new connection gets the codec
        original_init = kwargs.pop("init", None)

        async def _combined_init(conn: object) -> None:
            await _pgvector_codec_init(conn)
            if original_init is not None:
                await original_init(conn)

        kwargs["init"] = _combined_init
        return await _original_create_pool(self, **kwargs)

    _asyncpg_client_mod.AsyncpgDBClient.create_pool = _patched_create_pool


__all__ = [
    "CacheableModel",
    "CachedQuerySet",
    "CosineDistance",
    "CreateContinuousAggregate",
    "CreateHypertable",
    "GiSTIndex",
    "GraphEdge",
    "GraphNode",
    "GraphTraversal",
    "HNSWIndex",
    "HierarchyModel",
    "HybridSearch",
    "IVFFlatIndex",
    "InnerProduct",
    "L2Distance",
    "LTreeField",
    "RecursiveCTE",
    "RedisCache",
    "RedisCacheBackend",
    "VectorField",
    "all_paths",
    "cached",
    "cached_method",
    "find_cycles",
    "get_vector_filters",
    "invalidate",
    "shortest_path",
    "vector_encoder",
]

# Apply patches at import time
_apply_patches()
