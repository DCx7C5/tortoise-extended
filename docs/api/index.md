# API Reference

API documentation for the public modules.

## Modules

- **[VectorField](vector-field.md)** — pgvector column type for vector similarity search
- **[LTreeField](ltree-field.md)** — PostgreSQL ltree type for hierarchical paths
- **[Fields](fields.md)** — IPv4Field, PathField, URLField, UUID4Field, UUID7Field, CharField
- **[Indexes](indexes.md)** — HNSW, IVFFlat, and GiST index types
- **[Graph Filters](graph-filters.md)** — pgvector distance operators (L2, cosine, inner product)
- **[Recursive CTE](recursive-cte.md)** — Recursive Common Table Expressions
- **[Graph Traversal](graph-traversal.md)** — CTE-based ancestors, descendants, neighbors
- **[Graph Vector Search](graph-vector-search.md)** — single-query vector + graph compositor with typed results
- **[Pathfinding](pathfinding.md)** — Shortest path, all paths, cycle detection
- **[Hybrid Search](hybrid-search.md)** — Vector + FTS weighted scoring
- **[Fusion](fusion.md)** — RRF, weighted score fusion, MMR diversity re-ranking (pure Python)
- **[pgcrypto](pgcrypto.md)** — pgcrypto query helpers (Digest, DigestHex, HMACDigest)
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
from tortoise_extended import IPv4Field, PathField, URLField, UUID4Field, UUID7Field
```

### Indexes
```python
from tortoise_extended import HNSWIndex, IVFFlatIndex, GiSTIndex
```

### Expressions
```python
from tortoise_extended import RecursiveCTE
from tortoise_extended import L2Distance, CosineDistance, InnerProduct
from tortoise_extended import GraphTraversal, GraphVectorSearch, HybridSearch
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
)
```

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
    TimescaleError,
    MigrationOperationError,
    TortoiseExtendedError,
)
```
