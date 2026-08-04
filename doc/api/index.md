# API Reference

Complete API documentation for all tortoise-extended modules.

## Modules

- **[VectorField](vector-field.md)** — pgvector column type for vector similarity search
- **[LTreeField](ltree-field.md)** — PostgreSQL ltree type for hierarchical paths
- **[Indexes](indexes.md)** — HNSW, IVFFlat, and GiST index types
- **[Graph Filters](graph-filters.md)** — pgvector distance operators (L2, cosine, inner product)
- **[Recursive CTE](recursive-cte.md)** — Recursive Common Table Expressions
- **[Graph Traversal](graph-traversal.md)** — CTE-based ancestors, descendants, neighbors
- **[Graph Vector Search](graph-vector-search.md)** — single-query vector + graph compositor with typed results
- **[Pathfinding](pathfinding.md)** — Shortest path, all paths, cycle detection
- **[Hybrid Search](hybrid-search.md)** — Vector + FTS weighted scoring
- **[Graph (Node/Edge/Mixin)](graph.md)** — GraphNode, GraphEdge, HierarchyMixin base classes
- **[TimescaleDB](timescale.md)** — HypertableManager, CompressionManager, RetentionPolicy, ContinuousAggregateManager
- **[Event Streams](event-streams.md)** — EventStreamMixin: multi-stream hypertables, COPY ingestion, typed rollups
- **[Cache (Redis)](cache.md)** — RedisCache, CacheableModel, CachedQuerySet, decorators
- **[Exceptions](exceptions.md)** — domain error hierarchy (all derive from `TortoiseExtendedError`)
- **[Migrations](migrations.md)** — CreateHypertable, CreateContinuousAggregate migration operations

## Quick Reference

### Fields
```python
from tortoise_extended import VectorField, LTreeField
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
```

### Graph

```python
from tortoise_extended import GraphNode, GraphEdge, HierarchyModel
```

### TimescaleDB
```python
from tortoise_extended.timescale import (
    HypertableManager,
    CompressionManager,
    RetentionPolicy,
    ContinuousAggregateManager,
)
from tortoise_extended import EventStreamMixin, TimeBucketRow
```

### Cache
```python
from tortoise_extended import (
    RedisCache,
    CacheableModel,
    CachedQuerySet,
    cached,
    cached_method,
    invalidate,
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
    CacheError, CacheKeyError, CacheSerializationError, CacheDataError,
    CacheBackendNotInitializedError, RedisCacheError,
    FieldDefinitionError, VectorFieldError, LTreeFieldError,
    IndexDefinitionError, GraphError, GraphTraversalError, HierarchyError,
    RecursiveCTEError, HybridSearchError, TimescaleError,
    MigrationOperationError, TortoiseExtendedError,
)
```
