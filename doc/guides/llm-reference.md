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
├─ Hierarchical data (trees, org charts) ├─ ltree + parent_id ──► BaseHierarchyModel
│                                         └─ Typed/weighted edges ──► BaseGraphEdgeModel
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
├─ Append-heavy event streams ───────────► BaseEventStreamModel (+ typed rollups)
│
├─ Auto-refresh aggregations ────────────► ContinuousAggregateManager
│
├─ Data retention (auto-delete old) ─────► RetentionPolicy
│
├─ Cache frequently accessed data ───────► RedisCache + BaseCacheableModel / CachedQuerySet / @cached
│
├─ Timestamps on every row ───────────────► TimestampMixin (stackable)
│
├─ Soft delete (keep rows, hide them) ─────► BaseSoftDeleteModel + SoftDeleteQuerySet
│
├─ 64-bit JOIN-fast primary key ───────────► BaseModel
├─ User accounts (email + password) ─────► BaseUserModel (argon2id)
│
└─ Plain Tortoise model, no extras ────────► tortoise.models.Model
```

## Which Graph Layer?

| Need | Use | Why |
|------|-----|-----|
| Trees with fast subtree queries | `BaseHierarchyModel` (ltree) | `@>`/`<@` operators, GiST index, `get_ancestors`/`get_descendants` |
| Heterogeneous nodes, one edge table | `BaseGraphNodeModel` + `BaseGraphEdgeModel` | `source_id`/`target_id` are plain columns — link nodes of different types |
| Multi-hop traversal over an edge table | `GraphTraversal` | CTE-based ancestors/descendants/neighbors, typed edge filters |
| Paths, cycles, reachability | `shortest_path` / `all_paths` / `find_cycles` | BFS over the edge table |
| Fully custom recursive queries | `RecursiveCTE` | Build your own `WITH RECURSIVE` SQL |
| Same query: vector-similar AND graph-reachable | `GraphVectorSearch` | Single recursive CTE + pgvector predicate |

Rule of thumb: adjacency (`BaseGraphNodeModel`/`BaseGraphEdgeModel`) for arbitrary graphs,
materialized paths (`BaseHierarchyModel`) for trees you query by subtree,
`GraphTraversal`/pathfinding on top of either.

## Which Model Base?

Pick the base per model — each is opt-in and independent:

| Need | Base | Depends on | Cross-refs |
|------|------|-----------|------------|
| Nothing special | `tortoise.models.Model` | — | — |
| 64-bit pk (large tables, JOIN-heavy) | `BaseModel` | — | builds graph/timescale bases internally |
| `created_at`/`updated_at` | `TimestampMixin` | any base (put it **first** in bases tuple) | stack with `BaseModel`, `BaseSoftDeleteModel` |
| Soft delete (`deleted_at`, auto-filtered) | `BaseSoftDeleteModel` | pairs with `SoftDeleteQuerySet` | stack with `BaseModel`, `TimestampMixin` |
| Graph nodes / edges | `BaseGraphNodeModel` / `BaseGraphEdgeModel` | declare their own `UUID` pk | use with `GraphTraversal`, `shortest_path` |
| ltree trees | `BaseHierarchyModel` | declares `path`, `name`, `parent_id`, `depth`, `namespace` | use with `GiSTIndex` |
| Redis row caching | `BaseCacheableModel` | `RedisCache.init()` first | use with `CachedQuerySet`, `@cached` |
| Stream / time-series tables | `BaseEventStreamModel` | composite pk (`stream_id`, `time_field`) | use with `HypertableManager`, rollups |
| User accounts | `BaseUserModel` | extends `BaseModel`; argon2id hashing | stack with `TimestampMixin` |

**Rules:**
- `BaseModel`/`TimestampMixin`/`BaseSoftDeleteModel` are pure Tortoise — no PG-only
  behavior (they run on SQLite too).
- Do **not** combine `BaseModel` with `BaseGraphNodeModel`/`BaseGraphEdgeModel`/`BaseHierarchyModel`/
  `BaseEventStreamModel` — those declare their own primary keys and the two `id`
  definitions collide.
- Tortoise copies base fields **only for abstract bases** — keep every base
  here abstract; concrete inheritance silently yields an empty child table.
- Tortoise does **not** inherit `Meta.indexes` from abstract bases — redeclare
  them on every concrete subclass.

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
| `BaseGraphNodeModel` | `from tortoise_extended import BaseGraphNodeModel` | Graph nodes with adjacency (no FK on edges) |
| `BaseGraphEdgeModel` | `from tortoise_extended import BaseGraphEdgeModel` | Typed/weighted edges |
| `BaseHierarchyModel` | `from tortoise_extended import BaseHierarchyModel` | ltree tree operations |
| `BaseModel` | `from tortoise_extended import BaseModel` | 64-bit `BigInt` primary key |
| `TimestampMixin` | `from tortoise_extended import TimestampMixin` | `created_at` / `updated_at` columns |
| `BaseSoftDeleteModel` | `from tortoise_extended import BaseSoftDeleteModel` | Soft delete (`deleted_at`, auto-filtered) |
| `SoftDeleteQuerySet` | `from tortoise_extended import SoftDeleteQuerySet` | Auto-filters soft-deleted rows; `.with_deleted()`, `.restore()` |
| `BaseUserModel` | `from tortoise_extended import BaseUserModel` | Django-style email/password auth (argon2id) |
| `BaseCacheableModel` | `from tortoise_extended import BaseCacheableModel` | Model-level Redis caching |
| `CachedQuerySet` | `from tortoise_extended import CachedQuerySet` | QuerySet caching (`CachedQuerySet(Model).filter(...).cache()`) |
| `@cached` | `from tortoise_extended import cached` | Function-level caching |
| `HypertableManager` | `from tortoise_extended.timescale import HypertableManager` | TimescaleDB hypertables |
| `BaseEventStreamModel` | `from tortoise_extended import BaseEventStreamModel` | Append-heavy stream tables + typed rollups |
| `CompressionManager` | `from tortoise_extended.timescale import CompressionManager` | Chunk compression |
| `RetentionPolicy` | `from tortoise_extended.timescale import RetentionPolicy` | Auto-delete old data |
| `ContinuousAggregateManager` | `from tortoise_extended.timescale import ContinuousAggregateManager` | Auto-refresh aggregations |
| `CreateHypertable` | `from tortoise_extended.migrations.operations import CreateHypertable` | Hypertable via built-in migration writer |

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
# Option A: ltree tree (BaseHierarchyModel) — best for subtree queries
children = await node.get_descendants(include_self=False)

# Option B: adjacency via BaseGraphEdgeModel
children = await BaseGraphEdgeModel.outgoing(source_id=node.id, edge_type="contains").all()

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
    BaseGraphNodeModel, BaseGraphEdgeModel, VectorField, HNSWIndex, HybridSearch, GraphTraversal,
)

class TextUnit(models.Model):
    id = fields.UUIDField(pk=True)
    content = fields.TextField()
    embedding = VectorField(dimensions=1536)

class Entity(BaseGraphNodeModel):
    description = fields.TextField(default="")
    embedding = VectorField(dimensions=1536, null=True)

    class Meta:
        indexes = [HNSWIndex(fields=("embedding",), m=32, ef_construction=400)]

class Relationship(BaseGraphEdgeModel):
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

### Scenario 2 — Project file tree (ltree) with cross-links (BaseGraphEdgeModel)

Files/folders live in a `BaseHierarchyModel` tree (path = `project.src.pkg`); ad
hoc cross-references between files use a `BaseGraphEdgeModel` table.

```python
from tortoise_extended import BaseHierarchyModel, BaseGraphEdgeModel, GiSTIndex

class FileNode(BaseHierarchyModel):
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

class CodeLink(BaseGraphEdgeModel):
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
from tortoise_extended import BaseEventStreamModel, fields

class ClickEvent(BaseEventStreamModel):
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
    VectorField, HNSWIndex, RedisCache, BaseCacheableModel, CachedQuerySet, cached,
)

class Product(BaseCacheableModel):
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

**Why:** BaseCacheableModel for row-level reads, `@cached` for the query result,
HNSW for low-latency ANN.

### Scenario 5 — Social/org graph with pathfinding

```python
from tortoise_extended import BaseGraphNodeModel, BaseGraphEdgeModel, find_cycles, shortest_path

class Person(BaseGraphNodeModel):
    pass

class Follows(BaseGraphEdgeModel):
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

### Scenario 7 — Auditable records with soft delete + timestamps

Records keep full history, hide logically deleted rows, and stamp every write.

```python
from tortoise import fields
from tortoise_extended import BaseModel, TimestampMixin, BaseSoftDeleteModel

class Order(TimestampMixin, BaseSoftDeleteModel, BaseModel):
    total = fields.DecimalField(max_digits=12, decimal_places=2)
    status = fields.CharField(max_length=32, default="open")

    class Meta:
        table = "orders"
        indexes = (("created_at",),)   # redeclare — not inherited from bases

order = await Order.create(total=99.50)
await order.delete()                   # soft delete — row stays, hidden
await Order.with_deleted().get(pk=order.pk)   # still retrievable
await order.restore()                  # back to live
```

**Why:** `TimestampMixin` gives `created_at`/`updated_at`; `BaseSoftDeleteModel`
auto-filters `deleted_at IS NULL` on every default-manager query while
`.with_deleted()`/`.only_deleted()` keep full audit access.

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
5. **BaseHierarchyModel**: declares `path`, `name`, `parent_id`, `depth`, `namespace` itself — subclass and add your own fields
6. **BaseGraphEdgeModel helpers are sync QuerySet-returning**: call `.all()` (async) to execute
7. **Cache is optional**: Redis must be initialized separately via `RedisCache.init()`
8. **CachedQuerySet**: construct it directly (`CachedQuerySet(Model)`) or wire it as a custom manager — plain `Model.filter()` returns a standard QuerySet without `.cache()`
9. **BaseEventStreamModel**: PostgreSQL + composite primary key (`stream_id`, `time_field`) required; `bulk_insert` uses COPY and cannot generate identity IDs
10. **TimescaleDB**: Table must be a hypertable before compression/retention
11. **Meta.indexes are not inherited** from abstract bases — redeclare them on every concrete subclass (`BaseHierarchyModel`, `BaseModel`, `BaseSoftDeleteModel`, `TimestampMixin`)
12. **Bases with their own pk collide**: don't stack `BaseModel` with `BaseGraphNodeModel`/`BaseGraphEdgeModel`/`BaseHierarchyModel`/`BaseEventStreamModel`
13. **Keep bases abstract**: Tortoise copies base fields only for `abstract = True` classes
