# Architecture Overview

## System Components

```
┌─────────────────────────────────────────────────────────────┐
│                     Application Layer                        │
├─────────────────────────────────────────────────────────────┤
│                    tortoise-extended                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  VectorField │  │ HNSW/IVFFlat │  │ RecursiveCTE │      │
│  │  LTreeField  │  │  GiSTIndex   │  │ GraphTraversal │    │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ HybridSearch │  │ BaseGraphNode│  │ Timescale +  │      │
│  │ GraphVector  │  │ BaseGraphEdge│  │ Redis cache  │      │
│  │ Search       │  │ Hierarchy    │  │ (optional)   │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
├─────────────────────────────────────────────────────────────┤
│                      tortoise-orm                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │    Models    │  │   QuerySet   │  │  Backends    │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
├─────────────────────────────────────────────────────────────┤
│                     asyncpg + pgvector                       │
├─────────────────────────────────────────────────────────────┤
│                   PostgreSQL 18 + Extensions                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   pgvector   │  │ TimescaleDB  │  │  ltree/trgm  │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

## Data Flow

### Write Path

```
Application
    ↓
tortoise-extended (VectorField serialization)
    ↓
tortoise-orm (QuerySet → SQL generation)
    ↓
asyncpg (pgvector codec → binary encoding)
    ↓
PostgreSQL (INSERT + index update)
```

### Read Path (Vector Search)

```
Application
    ↓
tortoise-extended (pgvector filter syntax)
    ↓
tortoise-orm (QuerySet → SQL generation)
    ↓
asyncpg (pgvector codec → binary decoding)
    ↓
PostgreSQL (IVFFlat/HNSW index scan)
    ↓
Results (list[float] or list[dict])
```

### Read Path (Graph Traversal)

```
Application
    ↓
tortoise-extended (RecursiveCTE / GraphTraversal / pathfinding)
    ↓
asyncpg (conn.execute_query)
    ↓
PostgreSQL (recursive CTE execution)
    ↓
Results (list[dict])
```

## Module Responsibilities

| Module | Responsibility | Key Functions |
|--------|---------------|---------------|
| `fields/vector_field.py` | pgvector column type | Binary decoding, string parsing |
| `fields/ltree_field.py` | ltree column type | Materialized path storage |
| `indexes/hnsw_index.py` | ANN index creation | HNSW, IVFFlat DDL |
| `indexes/ltree_index.py` | GiST index creation | ltree GiST DDL |
| `expressions/graph_filters.py` | Distance operators | L2, cosine, inner product |
| `expressions/recursive_cte.py` | CTE construction | Anchor + union pattern |
| `expressions/graph_traversal.py` | Graph traversal | Ancestors, descendants, neighbors |
| `expressions/pathfinding.py` | Pathfinding | Shortest path, all paths, cycles |
| `expressions/hybrid_search.py` | Hybrid search | Vector + FTS weighted scoring |
| `expressions/ltree_filters.py` | ltree operators | Ancestor/descendant/match filters |
| `models/graph_node.py` | Graph nodes | Adjacency list base class |
| `models/graph_edge.py` | Graph edges | Typed/weighted edge base class |
| `models/hierarchy_model.py` | ltree trees | `BaseHierarchyModel` for ltree models |
| `models/cacheable_model.py` | Redis row caching | `BaseCacheableModel` |
| `models/event_stream.py` | Event streams | `BaseEventStreamModel` (COPY ingestion, rollups) |
| `timescale/hypertable.py` | Hypertable manager | Create/drop/list hypertables |
| `timescale/compression.py` | Compression manager | Chunk compression policies |
| `timescale/retention.py` | Retention policies | Auto-delete old chunks |
| `timescale/continuous_aggregate.py` | Continuous aggregates | Auto-refresh materialized views |
| `timescale/stream.py` | Stream helpers | `TimeBucketRow`, COPY/rollup SQL helpers |
| `cache/` | Redis caching | RedisCache, CachedQuerySet, decorators |
| `migrations/operations.py` | Migration ops | TimescaleDB operations |

## Monkey-Patch Strategy

The package applies monkey-patches at import time:

1. **`VectorField` / `LTreeField` registration** — Adds both field types to `tortoise.fields`
2. **`HNSWIndex` / `IVFFlatIndex` / `GiSTIndex` registration** — Adds the index types to `tortoise.indexes`
3. **`get_filters_for_field`** — Adds the pgvector (`__l2_distance`, `__cosine_distance`, `__inner_product`) and ltree (`__ancestor_of`, `__descendant_of`, `__match`, ...) filters (patches both `tortoise.filters` and `tortoise.models`)
4. **pgvector codec** — Injects `set_type_codec("vector", ...)` into every asyncpg connection via the pool init callback
5. **Migration serialization** — Patches `MigrationWriter._format_operation` so `CreateHypertable` / `CreateContinuousAggregate` round-trip through the built-in `tortoise.migrations` writer (generic deconstruct-based fallback for custom operations)

These patches are applied once when `import tortoise_extended` executes, and can also be applied explicitly via the public `tortoise_extended.patch()` function (idempotent, safe to call repeatedly). They are safe because they extend existing functions without modifying core behavior.

## Why Monkey-Patching?

Tortoise ORM doesn't provide plugin hooks for:
- Custom field types (no registration API)
- Custom query filters (no filter factory)
- Custom index types (no index factory)
- Custom migration operations (requires generator patch)
- Custom codec registration (requires connection pool patch)

The alternative was forking tortoise-orm or maintaining a separate package with duplicated code. Monkey-patching allows us to extend the ORM without maintaining a fork.

## Performance Characteristics

| Operation | Latency | Throughput |
|-----------|---------|------------|
| Vector insert (1536-dim) | <1ms | 10K rows/sec |
| HNSW query (top-10) | <5ms | 2K queries/sec |
| IVFFlat query (top-10) | <10ms | 1K queries/sec |
| 1-hop graph traversal | <1ms | 20K queries/sec |
| 2-hop graph traversal | <10ms | 5K queries/sec |
| 3-hop graph traversal | <100ms | 500 queries/sec |
| Full-text search | <5ms | 3K queries/sec |
