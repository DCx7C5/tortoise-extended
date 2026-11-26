# API Reference

API documentation for the public modules.

## Modules

- **[VectorField](vector-field.md)** — pgvector column type for vector similarity search
- **[LTreeField](ltree-field.md)** — PostgreSQL ltree type for hierarchical paths
- **[Fields](fields.md)** — InetField, IPv4Field, MacAddrField, MacAddr8Field, CitextField, PathField, URLField, UUID4Field, UUID7Field, CharField, PGPEncryptedField
- **[Indexes](indexes.md)** — HNSW, IVFFlat, and GiST index types
- **[Graph Filters](graph-filters.md)** — pgvector distance operators (L2, cosine, inner product)
- **[Recursive CTE](recursive-cte.md)** — Recursive Common Table Expressions
- **[Graph Traversal](graph-traversal.md)** — CTE-based ancestors, descendants, neighbors
- **[Graph Vector Search](graph-vector-search.md)** — single-query vector + graph compositor with typed results
- **[Pathfinding](pathfinding.md)** — Shortest path, all paths, cycle detection
- **[Hybrid Search](hybrid-search.md)** — Vector + FTS weighted scoring
- **[Full-Text Search](full-text-search.md)** — upstream `TSVectorField`, `SearchQuery`, `SearchRank`, `SearchHeadline`, `Lexeme` — and the four traps
- **[Fusion](fusion.md)** — RRF, weighted score fusion, MMR diversity re-ranking (pure Python)
- **[Window Functions](windows.md)** — `row_number`, `rank`, `dense_rank`, `ntile` and `sum_over`/`avg_over`/`count_over`/`min_over`/`max_over` analytics builders for `annotate()`
- **[Pagination](pagination.md)** — `QuerySet.paginate(page, page_size)` and the `PageInfo` metadata
- **[QuerySet helpers](queryset.md)** — `QuerySet.random()` (random ordering) and `distinct_on()` (`DISTINCT ON`, hydrated instances)
- **[pgcrypto](pgcrypto.md)** — pgcrypto query helpers (Digest, DigestHex, HMACDigest, PGPSymEncrypt, PGPSymDecrypt)
- **[Graph (Node/Edge/Hierarchy)](graph.md)** — BaseNode, BaseGraphEdgeModel, BaseHierarchyModel base classes (BaseGraphNodeModel is the deprecated alias)
- **[Model Bases](models.md)** — BaseModel, UnifiedIdModel, BaseUserModel, BaseSoftDeleteModel + SoftDeleteQuerySet, TimestampMixin
- **[TimescaleDB](timescale.md)** — HypertableManager, CompressionManager, RetentionPolicy, ContinuousAggregateManager
- **[Event Streams](event-streams.md)** — BaseSample (BaseEventStreamModel is the deprecated alias): multi-stream hypertables, COPY ingestion, typed rollups
- **[Cache (Redis)](cache.md)** — RedisCache, BaseCacheableModel, CachedQuerySet, decorators
- **[Exceptions](exceptions.md)** — domain error hierarchy (all derive from `TortoiseExtendedError`)
- **[Migrations](migrations.md)** — CreateHypertable, CreateContinuousAggregate migration operations

## Quick Reference

### Fields
```python
from tortoise_extended import VectorField, LTreeField, CharField
from tortoise_extended import InetField, IPv4Field, MacAddrField, MacAddr8Field, CitextField
from tortoise_extended import PathField, URLField, UUID4Field, UUID7Field
```

### Indexes
```python
from tortoise_extended import HNSWIndex, IVFFlatIndex, GiSTIndex
```

### Expressions
```python
from tortoise_extended import RecursiveCTE
from tortoise_extended import L2Distance, CosineDistance, InnerProduct
from tortoise_extended import GraphTraversal, GraphVectorSearch, HybridSearch, Graph
from tortoise_extended import shortest_path, all_paths, find_cycles
from tortoise_extended import (
    reciprocal_rank_fusion,
    weighted_score_fusion,
    diversity_rerank,
    ranking_from_hits,
    scores_from_hybrid_rows,
)
```

### Graph

```python
from tortoise_extended import (
    BaseNode,
    NodeMixin,
    BaseGraphEdgeModel,
    BaseHierarchyModel,
    BaseGraphNodeModel,  # deprecated alias of BaseNode
    BaseUnifiedNode,     # UnifiedIdModel + NodeMixin (integer parent_id)
    BaseSoftDeleteNode,  # BaseSoftDeleteModel + NodeMixin (subtree soft delete)
    SoftDeleteNodeQuerySet,
)
```

### Model Bases

```python
from tortoise_extended import (
    BaseModel,
    UnifiedIdModel,
    TimestampMixin,
    BaseSoftDeleteModel,
    SoftDeleteQuerySet,
    BaseUserModel,
)
```

### TimescaleDB
```python
from tortoise_extended.timescale import (
    HypertableManager,
    CompressionManager,
    RetentionPolicy,
    ContinuousAggregateManager,
)
from tortoise_extended import BaseSample, BaseEventStreamModel, TimeBucketRow  # BaseEventStreamModel is the deprecated alias
```

### Lifecycle

```python
from tortoise_extended import ensure_extensions, setup, SetupReport
```

`setup(models=None, *, generate_schemas=False)` is the startup fan-out:
extensions → schemas (when requested) → each stream model's `setup()`. See
[Getting started → Initialize](../getting-started/quickstart.md).

### Cache
```python
from tortoise_extended import (
    RedisCache,
    BaseCacheableModel,
    CachedQuerySet,
    cached,
    cached_method,
    invalidate,
)
```

### pgcrypto
```python
from tortoise_extended.expressions.pgcrypto import (
    Digest, DigestHex, HMACDigest,
    PGPSymEncrypt, PGPSymDecrypt,
)
# or from the package root: from tortoise_extended import Digest, DigestHex, HMACDigest, PGPSymEncrypt, PGPSymDecrypt
```

### Pagination
```python
from tortoise_extended import PageInfo, paginate
```

`await Chunk.filter(active=True).order_by("name").paginate(2, 25)` → `PageInfo` with the rows plus
`total`, `total_pages`, `has_next`, `has_prev`. See [Pagination](pagination.md).

### Migrations
```python
from tortoise_extended import CreateHypertable, CreateContinuousAggregate
```

### Exceptions
All errors derive from `TortoiseExtendedError`:

```python
from tortoise_extended import (
    CacheError,
    CacheKeyError,
    CacheSerializationError,
    CacheDataError,
    CacheBackendNotInitializedError,
    RedisCacheError,
    FieldDefinitionError,
    VectorFieldError,
    LTreeFieldError,
    IndexDefinitionError,
    GraphError,
    GraphTraversalError,
    HierarchyError,
    RecursiveCTEError,
    HybridSearchError,
    PgCryptoError,
    TimescaleError,
    MigrationOperationError,
    PatchConflictError,
    SetupError,
    TortoiseExtendedError,
    TortoiseVersionError,
)
```
