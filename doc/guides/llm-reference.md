# LLM Reference — When to Use What

Quick decision guide for AI agents and developers building with tortoise-extended.
This is the **single** "when to use which feature" file — use it before reaching
for any of the API pages.

## Decision Tree

```
What do you need?
│
├─ Store vector embeddings ──────────────► VectorField + HNSWIndex/IVFFlatIndex
│
├─ Hierarchical data (trees, org charts) ├─ ltree + parent_id ──► HierarchyModel
│                                         └─ Typed/weighted edges ──► GraphEdge
│
├─ Graph traversal (edge table) ─────────► GraphTraversal (ancestors/descendants/neighbors)
│
├─ Graph + vector in one query ──────────► GraphVectorSearch
│
├─ Shortest path / pathfinding ──────────► shortest_path, all_paths, find_cycles
│
├─ Hybrid search (vector + FTS) ─────────► HybridSearch
│
├─ Custom recursive SQL ─────────────────► RecursiveCTE
│
├─ Distance search between vectors ──────► L2Distance, CosineDistance, InnerProduct
│
├─ Time-series data ─────────────────────► HypertableManager + CompressionManager
│
├─ Append-heavy event streams ───────────► EventStreamMixin (+ typed rollups)
│
├─ Auto-refresh aggregations ────────────► ContinuousAggregateManager
│
├─ Data retention (auto-delete old) ─────► RetentionPolicy
│
└─ Cache frequently accessed data ───────► RedisCache + CacheableModel / CachedQuerySet / @cached
```

## Which Graph Layer?

| Need | Use | Why |
|------|-----|-----|
| Trees with fast subtree queries | `HierarchyModel` (ltree) | `@>`/`<@` operators, GiST index, `get_ancestors`/`get_descendants` |
| Heterogeneous nodes, one edge table | `GraphNode` + `GraphEdge` | `source_id`/`target_id` are plain columns — link nodes of different types |
| Multi-hop traversal over an edge table | `GraphTraversal` | CTE-based ancestors/descendants/neighbors, typed edge filters |
| Paths, cycles, reachability | `shortest_path` / `all_paths` / `find_cycles` | BFS over the edge table |
| Fully custom recursive queries | `RecursiveCTE` | Build your own `WITH RECURSIVE` SQL |
| Same query: vector-similar AND graph-reachable | `GraphVectorSearch` | Single recursive CTE + pgvector predicate |

Rule of thumb: adjacency (`GraphNode`/`GraphEdge`) for arbitrary graphs,
materialized paths (`HierarchyModel`) for trees you query by subtree,
`GraphTraversal`/pathfinding on top of either.

## Module Map

| Module | Import | Use When |
|--------|--------|----------|
| `VectorField` | `from tortoise_extended import VectorField` | Store embeddings (pgvector) |
| `HNSWIndex` | `from tortoise_extended import HNSWIndex` | Read-heavy ANN search |
| `IVFFlatIndex` | `from tortoise_extended import IVFFlatIndex` | Write-heavy ANN search (data must exist first) |
| `GiSTIndex` | `from tortoise_extended import GiSTIndex` | ltree column index |
| `LTreeField` | `from tortoise_extended import LTreeField` | Materialized paths (`a.b.c`) |
| `L2Distance` | `from tortoise_extended import L2Distance` | Euclidean distance filter |
| `CosineDistance` | `from tortoise_extended import CosineDistance` | Cosine similarity filter |
| `InnerProduct` | `from tortoise_extended import InnerProduct` | Inner product filter |
| `RecursiveCTE` | `from tortoise_extended import RecursiveCTE` | Custom recursive graph SQL |
| `GraphTraversal` | `from tortoise_extended import GraphTraversal` | CTE-based edge table traversal |
| `GraphVectorSearch` | `from tortoise_extended import GraphVectorSearch` | Graph reachability + vector similarity in one query |
| `shortest_path` | `from tortoise_extended import shortest_path` | BFS shortest path |
| `all_paths` | `from tortoise_extended import all_paths` | Find all paths between nodes |
| `find_cycles` | `from tortoise_extended import find_cycles` | Detect cycles in graph |
| `HybridSearch` | `from tortoise_extended import HybridSearch` | Vector + FTS weighted scoring |
| `GraphNode` | `from tortoise_extended import GraphNode` | Graph nodes with adjacency (no FK on edges) |
| `GraphEdge` | `from tortoise_extended import GraphEdge` | Typed/weighted edges |
| `HierarchyModel` | `from tortoise_extended import HierarchyModel` | ltree tree operations |
| `CacheableModel` | `from tortoise_extended import CacheableModel` | Model-level Redis caching |
| `CachedQuerySet` | `from tortoise_extended import CachedQuerySet` | QuerySet caching (`CachedQuerySet(Model).filter(...).cache()`) |
| `@cached` | `from tortoise_extended import cached` | Function-level caching |
| `HypertableManager` | `from tortoise_extended.timescale import HypertableManager` | TimescaleDB hypertables |
| `EventStreamMixin` | `from tortoise_extended import EventStreamMixin` | Append-heavy stream tables + typed rollups |
| `CompressionManager` | `from tortoise_extended.timescale import CompressionManager` | Chunk compression |
| `RetentionPolicy` | `from tortoise_extended.timescale import RetentionPolicy` | Auto-delete old data |
| `ContinuousAggregateManager` | `from tortoise_extended.timescale import ContinuousAggregateManager` | Auto-refresh aggregations |
| `CreateHypertable` | `from tortoise_extended.migrations.operations import CreateHypertable` | Hypertable via aerich migration |

## Filter Syntax Cheat Sheet

### Vector Search

```python
# Cosine distance (most common for embeddings)
Entity.filter(embedding__cosine_distance=[[query_vec], 0.5])

# L2 distance
Entity.filter(embedding__l2_distance=[[query_vec], 0.3])

# Inner product (negated — higher = more similar)
Entity.filter(embedding__inner_product=[[query_vec], 0.8])
```

> Note the double brackets: the value is a **list containing** `[vector, threshold]`.

### ltree Queries

```python
# Ancestors (path @> value)
Category.filter(path__ancestor_of="root.parent")

# Descendants (path <@ value)
Category.filter(path__descendant_of="root")

# Pattern match
Category.filter(path__match="root.*.child")
```

## Common Patterns

### "Find similar items"

```python
items = await Item.filter(
    embedding__cosine_distance=[[query_embedding], 0.5]
).order_by("embedding__cosine_distance").limit(10)
```

### "Get all children of a node"

```python
# Option A: ltree tree (HierarchyModel) — best for subtree queries
children = await node.get_descendants(include_self=False)

# Option B: adjacency via GraphEdge
children = await GraphEdge.outgoing(source_id=node.id, edge_type="contains").all()

# Option C: custom FK + related_name (your own models)
children = await node.children.all()
```

### "Cache a database query"

```python
from tortoise_extended import cached

@cached(ttl=300)
async def get_popular_items():
    return await Item.order_by("-view_count").limit(10)
```

### "Cache a specific QuerySet"

```python
from tortoise_extended import CachedQuerySet

rows = await CachedQuerySet(Item).filter(active=True).cache(ttl=600)
# invalidate:
await CachedQuerySet(Item).filter(active=True).cache().invalidate_cache()
```

### "Auto-expire old data"

```python
from tortoise_extended.timescale import HypertableManager, RetentionPolicy

await HypertableManager.create_hypertable("logs", time_column="created_at")
await RetentionPolicy.set_retention("logs", drop_after="30 days")
```

### "Traverse graph edges (CTE)"

```python
from tortoise_extended import GraphTraversal

traversal = GraphTraversal(Entity, Relationship)
ancestors = await traversal.ancestors(node_id=42, max_depth=5, edge_type="parent_of")
neighbors = await traversal.neighbors(node_id=42, direction="outgoing")
```

### "Find shortest path between nodes"

```python
from tortoise_extended import shortest_path

path = await shortest_path(Entity, Relationship, from_id=a.id, to_id=b.id, max_hops=5)
```

### "Hybrid search (vector + keywords)"

```python
from tortoise_extended import HybridSearch

search = HybridSearch(model=Entity, vector_field="embedding", text_field="description")
results = await search.search(
    query_vector=[0.1, 0.2, ...],
    query_text="machine learning",
    max_results=20,
)
```

## Harness / Project Scenarios

Real end-to-end shapes you can copy. Each scenario names the modules it needs
and the "why".

### Scenario 1 — GraphRAG document pipeline

Documents → text units → entities → relationships → communities. Vector
search for retrieval, `GraphTraversal` for 1-hop context, `HybridSearch` for
keyword-augmented recall.

```python
from tortoise import fields, models
from tortoise_extended import (
    GraphNode, GraphEdge, VectorField, HNSWIndex, HybridSearch, GraphTraversal,
)

class TextUnit(models.Model):
    id = fields.UUIDField(pk=True)
    content = fields.TextField()
    embedding = VectorField(dimensions=1536)

class Entity(GraphNode):
    description = fields.TextField(default="")
    embedding = VectorField(dimensions=1536, null=True)

    class Meta:
        indexes = [HNSWIndex(fields=("embedding",), m=32, ef_construction=400)]

class Relationship(GraphEdge):
    pass

# retrieval: hybrid over text units
search = HybridSearch(model=TextUnit, vector_field="embedding", text_field="content")
hits = await search.search(query_vector=vec, query_text="agent memory", max_results=10)

# context: 1-hop neighborhood around a matched entity
neighbors = await GraphTraversal(Entity, Relationship).neighbors(
    node_id=entity.id, direction="both", max_depth=2
)
```

**Why:** one edge table links entities of any type; HNSW keeps ANN recall
high; `GraphTraversal` bounds the neighborhood query in SQL.

### Scenario 2 — Project file tree (ltree) with cross-links (GraphEdge)

Files/folders live in a `HierarchyModel` tree (path = `project.src.pkg`); ad
hoc cross-references between files use a `GraphEdge` table.

```python
from tortoise_extended import HierarchyModel, GraphEdge, GiSTIndex

class FileNode(HierarchyModel):
    size_bytes = fields.BigIntField(default=0)

    class Meta:
        table = "file_nodes"
        # Tortoise does NOT inherit Meta.indexes from the abstract base —
        # redeclare them on every concrete subclass.
        indexes = (
            GiSTIndex(fields=("path",)),
            ("namespace", "depth"),
            ("parent_id", "depth"),
        )

class CodeLink(GraphEdge):
    class Meta:
        table = "code_links"
        # Redeclared for the same reason.
        indexes = (
            ("source_id", "edge_type"),
            ("target_id", "edge_type"),
            ("source_id", "target_id", "edge_type"),
        )

# subtree query
docs = await FileNode.get_descendants()  # from a root node

# arbitrary cross-reference (not expressible as a tree)
imports = await CodeLink.outgoing(source_id=file.id, edge_type="imports").all()
```

**Why:** trees you query by subtree → ltree (`@>`/`<@` are index-assisted);
cyclic or cross-type links → adjacency edge table. See
`doc/guides/project-file-tree.md`.

### Scenario 3 — Event stream analytics (COPY ingestion + rollups)

High-cardinality events (clicks, sensor reads) with per-stream bucketing,
continuous aggregates, and retention.

```python
from tortoise_extended import EventStreamMixin, fields

class ClickEvent(EventStreamMixin):
    stream_id = fields.CharField(max_length=64)          # overrides default
    ts = fields.DatetimeField()                           # overrides default
    user_id = fields.UUIDField()
    url = fields.TextField()
    latency_ms = fields.IntField()

    class Meta:
        table = "click_events"

# one-time setup (hypertable + partitions + policies)
await ClickEvent.setup(compress_after="7 days", drop_after="90 days")

# bulk COPY ingestion
await ClickEvent.bulk_insert(rows)

# latest row per stream / bucketed time series
latest = await ClickEvent.latest_per_stream()
series = await ClickEvent.time_series(bucket="1 hour")
```

**Why:** `bulk_insert` uses COPY (much faster than per-row inserts);
partitioning + compression + retention are managed for you.

### Scenario 4 — E-commerce recommendations (hybrid + cache)

Hot product vectors cached in Redis; cold ANN in pgvector.

```python
from tortoise_extended import (
    VectorField, HNSWIndex, RedisCache, CacheableModel, CachedQuerySet, cached,
)

class Product(CacheableModel):
    _cache_ttl = 300
    _cache_fields = ["title", "price"]

    title = fields.CharField(max_length=255)
    price = fields.DecimalField(max_digits=10, decimal_places=2)
    embedding = VectorField(dimensions=768, null=True)

    class Meta:
        indexes = [HNSWIndex(fields=("embedding",), m=16, ef_construction=200)]

await RedisCache.init(url="redis://localhost:6379/0")

@cached(ttl=120)
async def similar_products(vec):
    return await Product.filter(
        embedding__cosine_distance=[[vec], 0.7]
    ).order_by("embedding__cosine_distance").limit(12)
```

**Why:** CacheableModel for row-level reads, `@cached` for the query result,
HNSW for low-latency ANN.

### Scenario 5 — Social/org graph with pathfinding

```python
from tortoise_extended import GraphNode, GraphEdge, find_cycles, shortest_path

class Person(GraphNode):
    pass

class Follows(GraphEdge):
    pass

# degrees of separation
path = await shortest_path(Person, Follows, from_id=a.id, to_id=b.id, max_hops=6)

# integrity check
cycles = await find_cycles(Person, Follows, max_hops=10)
```

**Why:** `source_id`/`target_id` plain columns keep the edge table tiny and
queryable by both directions.

### Scenario 6 — Metrics ingestion with retention

```python
from tortoise_extended.timescale import (
    HypertableManager, CompressionManager, RetentionPolicy, ContinuousAggregateManager,
)

await HypertableManager.create_hypertable("metrics", time_column="ts", chunk_time_interval="1 day")
await CompressionManager.set_compression("metrics", compress_after="7 days")
await RetentionPolicy.set_retention("metrics", drop_after="90 days")
await ContinuousAggregateManager.create_continuous_aggregate(
    "metrics_daily", "SELECT ...", time_column="bucket", refresh_interval="1 hour"
)
```

**Why:** hypertable + compression for old chunks, retention drops raw data
while the continuous aggregate keeps history.

## Index Selection

| Scenario | Index | Why |
|----------|-------|-----|
| Read-heavy vector search | HNSW (m=32, ef_construction=400) | Best recall |
| Write-heavy + large dataset | IVFFlat (lists=sqrt(rows)) | Lower memory |
| Balanced workload | HNSW (m=16, ef_construction=200) | Good default |
| Low memory | IVFFlat (lists=100) | ~1x data size |
| ltree path queries | GiSTIndex | Required for @>, <@, ~ operators |

## Gotchas

1. **Import order**: `import tortoise_extended` MUST come before `from tortoise import Tortoise`
2. **Vector dimensions**: Must match `VectorField(dimensions=N)` exactly
3. **IVFFlat**: Table must have data before creating the index
4. **Filter tuples**: Use `=[[vector], threshold]` (list containing the pair), not `=([vector], threshold)`
5. **HierarchyModel**: declares `path`, `name`, `parent_id`, `depth`, `namespace` itself — subclass and add your own fields
6. **GraphEdge helpers are sync QuerySet-returning**: call `.all()` (async) to execute
7. **Cache is optional**: Redis must be initialized separately via `RedisCache.init()`
8. **CachedQuerySet**: construct it directly (`CachedQuerySet(Model)`) or wire it as a custom manager — plain `Model.filter()` returns a standard QuerySet without `.cache()`
9. **EventStreamMixin**: PostgreSQL + composite primary key (`stream_id`, `time_field`) required; `bulk_insert` uses COPY and cannot generate identity IDs
10. **TimescaleDB**: Table must be a hypertable before compression/retention
