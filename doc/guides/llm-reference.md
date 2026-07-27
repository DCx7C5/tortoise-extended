# LLM Reference — When to Use What

Quick decision guide for AI agents and developers building with tortoise-extended.

## Decision Tree

```
What do you need?
│
├─ Store vector embeddings ──────────────► VectorField + HNSWIndex/IVFFlatIndex
│
├─ Hierarchical data (org charts, trees) ├─ Simple tree ──► GraphNode + parent_id
│                                         ├─ ltree queries ──► LTreeField + HierarchyMixin
│                                         └─ Typed edges ──► GraphEdge
│
├─ Graph traversal (edge table) ─────────► GraphTraversal (ancestors/descendants/neighbors)
│
├─ Shortest path / pathfinding ──────────► shortest_path, all_paths, find_cycles
│
├─ Hybrid search (vector + FTS) ─────────► HybridSearch
│
├─ Time-series data ─────────────────────► HypertableManager + CompressionManager
│
├─ Auto-refresh aggregations ────────────► ContinuousAggregateManager
│
├─ Data retention (auto-delete old) ─────► RetentionPolicy
│
├─ Cache frequently accessed data ───────► RedisCache + CacheableModel or @cached
│
├─ Complex graph traversal (CTEs) ───────► RecursiveCTE
│
└─ Distance search between vectors ──────► L2Distance, CosineDistance, InnerProduct
```

## Module Map

| Module | Import | Use When |
|--------|--------|----------|
| `VectorField` | `from tortoise_extended import VectorField` | Store embeddings (pgvector) |
| `HNSWIndex` | `from tortoise_extended import HNSWIndex` | Read-heavy ANN search |
| `IVFFlatIndex` | `from tortoise_extended import IVFFlatIndex` | Write-heavy ANN search |
| `GiSTIndex` | `from tortoise_extended import GiSTIndex` | ltree column index |
| `LTreeField` | `from tortoise_extended import LTreeField` | Materialized paths (`a.b.c`) |
| `L2Distance` | `from tortoise_extended import L2Distance` | Euclidean distance filter |
| `CosineDistance` | `from tortoise_extended import CosineDistance` | Cosine similarity filter |
| `InnerProduct` | `from tortoise_extended import InnerProduct` | Inner product filter |
| `RecursiveCTE` | `from tortoise_extended import RecursiveCTE` | Recursive graph traversal |
| `GraphTraversal` | `from tortoise_extended import GraphTraversal` | CTE-based edge table traversal |
| `shortest_path` | `from tortoise_extended import shortest_path` | BFS shortest path |
| `all_paths` | `from tortoise_extended import all_paths` | Find all paths between nodes |
| `find_cycles` | `from tortoise_extended import find_cycles` | Detect cycles in graph |
| `HybridSearch` | `from tortoise_extended import HybridSearch` | Vector + FTS weighted scoring |
| `GraphNode` | `from tortoise_extended import GraphNode` | Graph nodes with adjacency list |
| `GraphEdge` | `from tortoise_extended import GraphEdge` | Typed/weighted edges |
| `HierarchyModel` | `from tortoise_extended import HierarchyMixin` | ltree tree operations |
| `CacheableModel` | `from tortoise_extended import CacheableModel` | Model-level Redis caching |
| `CachedQuerySet` | `from tortoise_extended import CachedQuerySet` | QuerySet caching |
| `@cached` | `from tortoise_extended import cached` | Function-level caching |
| `HypertableManager` | `from tortoise_extended.timescale import HypertableManager` | TimescaleDB hypertables |
| `CompressionManager` | `from tortoise_extended.timescale import CompressionManager` | Chunk compression |
| `RetentionPolicy` | `from tortoise_extended.timescale import RetentionPolicy` | Auto-delete old data |
| `ContinuousAggregateManager` | `from tortoise_extended.timescale import ContinuousAggregateManager` | Auto-refresh aggregations |

## Filter Syntax Cheat Sheet

### Vector Search

```python
# Cosine distance (most common for embeddings)
Entity.filter(embedding__cosine_distance=([query_vec, 0.5]))

# L2 distance
Entity.filter(embedding__l2_distance=([query_vec, 0.3]))

# Inner product (negated — higher = more similar)
Entity.filter(embedding__inner_product=([query_vec, 0.8]))
```

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
    embedding__cosine_distance=([query_embedding, 0.5])
).order_by("embedding__cosine_distance").limit(10)
```

### "Get all children of a node"

```python
# Option A: adjacency list (GraphNode)
children = await node.children().all()

# Option B: ltree (HierarchyMixin)
children = await node.get_children()
```

### "Cache a database query"

```python
from tortoise_extended import cached

@cached(ttl=300)
async def get_popular_items():
    return await Item.order_by("-view_count").limit(10)
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
4. **HierarchyMixin**: Model needs `path`, `name`, `parent_id`, `depth` fields
5. **QuerySet-returning methods on GraphEdge are sync**: Call `.all()` (async) to execute
6. **Cache is optional**: Redis must be initialized separately via `RedisCache.init()`
7. **TimescaleDB**: Table must be a hypertable before compression/retention
