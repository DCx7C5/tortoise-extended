# Graph Traversal

## Overview

`tortoise-extended` provides graph traversal capabilities through recursive CTEs and a Python query API. This enables efficient knowledge graph queries without external graph databases.

## Recursive CTEs

### How They Work

Recursive CTEs traverse hierarchical data in PostgreSQL:

```sql
WITH RECURSIVE ancestors AS (
    -- Anchor: starting point
    SELECT id, title, 0 AS depth
    FROM entities
    WHERE id = 42
    
    UNION
    
    -- Recursive: follow relationships
    SELECT e.id, e.title, a.depth + 1
    FROM entities e
    JOIN relationships r ON e.id = r.source_entity_id
    JOIN ancestors a ON r.target_entity_id = a.id
    WHERE a.depth < 3
)
SELECT * FROM ancestors;
```

### Python API

```python
from pypika_tortoise import PostgreSQLQuery, Table, RawSQL
from tortoise_extended import RecursiveCTE

entities = Table("entities")
relationships = Table("relationships")

# Build recursive CTE
cte = (
    RecursiveCTE("ancestors")
    .anchor(
        PostgreSQLQuery.from_(entities)
        .select(entities.id, entities.title, RawSQL("0").as_("depth"))
        .where(entities.id == 42)
    )
    .union(
        PostgreSQLQuery.from_(entities)
        .join(relationships).on(entities.id == relationships.source_entity_id)
        .select(entities.id, entities.title, (RawSQL("ancestors.depth") + 1).as_("depth"))
    )
    .build()
)

# Execute
results = await connections.get("default").execute_query(str(cte), [])
```

### Performance

| Hops | Latency | RPS (u=50) |
|------|---------|------------|
| 1 | <1ms | 22,581 |
| 2 | <10ms | 5,000 |
| 3 | <100ms | 500 |
| 4 | <1s | 100 |
| 5+ | >1s | <50 |

> **Illustrative.** Machine-dependent — reproduce on your hardware with
> `uv run python benchmarks/bench_graph_traversal.py` (docker PG required).

**Best practice:** Limit traversal depth to 2-3 hops for most use cases.

## Python API

Graph retrieval is implemented in Python — no SQL functions need to be
loaded into the database. The library builds parameterized recursive CTEs
at runtime.

### GraphTraversal

Neighborhood / ancestor / descendant queries over node + edge tables:

```python
from tortoise_extended import GraphTraversal

traversal = GraphTraversal(Entity, Relationship)

# Local neighborhood search (1-2 hops)
neighbors = await traversal.neighbors(
    node_id=entity.id,
    direction="both",        # "outgoing" | "incoming" | "both"
    edge_type=None,          # optional edge type filter
    max_depth=2,
)

# Ancestors / descendants
ancestors = await traversal.ancestors(node_id=entity.id, max_depth=5)
descendants = await traversal.descendants(node_id=entity.id, max_depth=5)

# Cycle detection
has_cycle = await traversal.has_cycle(max_depth=20)
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `node_id` | int \| str \| UUID | Required | Starting node |
| `direction` | str | `"both"` | Traversal direction (`neighbors` only) |
| `edge_type` | str \| None | None | Filter by edge type |
| `max_depth` | int | `1` (neighbors) / `10` (ancestors, descendants) | Maximum hops |

**Returns:** List of node dicts (`id`, `name`, `depth`) with `hops`
(`neighbors`) or `path_depth` (ancestors/descendants) metadata, ordered by depth.

### Path Finding

```python
from tortoise_extended import shortest_path, all_paths, find_cycles

path = await shortest_path(
    Entity, Relationship,
    from_id=entity_a.id,
    to_id=entity_b.id,
    max_hops=5,
    edge_type=None,
)

paths = await all_paths(
    Entity, Relationship,
    from_id=entity_a.id,
    to_id=entity_b.id,
    max_hops=5,
)

cycles = await find_cycles(Entity, Relationship, max_hops=10)
```

### Hybrid Search

```python
from tortoise_extended import HybridSearch

search = HybridSearch(
    model=Entity,
    vector_field="embedding",
    text_field="description",
    distance_metric="cosine",   # "cosine" | "l2" | "inner_product"
    vector_weight=0.7,
    text_weight=0.3,
)

results = await search.search(
    query_vector=[0.1, 0.2, ...],
    query_text="machine learning framework",
    max_results=20,
    min_distance=None,
)
```

**Returns:** Model rows plus `distance`, `text_score`, and `combined_score` metadata.

### Fuzzy Entity Lookup

Fuzzy name matching uses `pg_trgm` through the Tortoise QuerySet API:

```python
from tortoise import Q

matches = await Entity.filter(
    Q(name__icontains="pyth") | Q(name__icontains="python")
).limit(10)
```

## Query Patterns

### Pattern 1: Local Neighborhood

Use for understanding context around an entity.

```python
from tortoise_extended import GraphTraversal

traversal = GraphTraversal(Entity, Relationship)
neighbors = await traversal.neighbors(
    node_id=entity.id,
    direction="both",
    max_depth=2,
)

for row in neighbors:
    if row["hops"] == 0:
        print(f"Center: {row['name']}")
    else:
        print(f"  Hop {row['hops']}: {row['name']}")
```

### Pattern 2: Entity Resolution

Use for finding duplicate or similar entities by name:

```python
matches = await Entity.filter(name__icontains="pyth").limit(10)
for row in matches:
    print(f"Match: {row.name}")
```

For true pg_trgm similarity scoring, compute it inline with a raw expression
(see `doc/guides/performance.md`).

### Pattern 3: Community Exploration

Explore communities by following typed edges, then filter with the standard
QuerySet API:

```python
from tortoise_extended import GraphTraversal

traversal = GraphTraversal(Entity, Relationship)

# All entities reachable via "member_of" edges
members = await traversal.neighbors(
    node_id=community_entity.id,
    edge_type="member_of",
    max_depth=1,
)

# Narrow with ordinary filters
members = await Entity.filter(community_id=community_entity.id)
```

### Pattern 4: Path Finding

Use for understanding relationships between entities:

```python
from tortoise_extended import shortest_path

path = await shortest_path(
    Entity, Relationship,
    from_id=python_entity.id,
    to_id=tensorflow_entity.id,
    max_hops=5,
)

if path:
    for i, node in enumerate(path):
        arrow = "→" if i > 0 else ""
        print(f"{arrow} {node['name']}")
```

## Performance Optimization

### Indexing

```sql
-- Graph traversal indexes
CREATE INDEX ix_relationships_source ON relationships(source_entity_id);
CREATE INDEX ix_relationships_target ON relationships(target_entity_id);

-- Composite index for common traversal pattern
CREATE INDEX ix_relationships_source_type ON relationships(source_entity_id, type);
```

### Index Usage

The traversal query builder relies on ordinary B-tree indexes on the edge
table — PostgreSQL picks them automatically:

```sql
-- Verify the planner is using them
EXPLAIN ANALYZE
SELECT * FROM relationships
WHERE source_entity_id = $1 AND type = 'member_of';
```

The `BaseGraphEdgeModel` base class already indexes `source_id` / `target_id` /
`edge_type`. For custom edge tables, add the same three indexes plus the
composite `(source_id, edge_type)` pattern above.

### Materialized Views

```sql
-- Pre-compute 1-hop neighborhoods
CREATE MATERIALIZED VIEW entity_neighbors AS
SELECT 
    e.id AS entity_id,
    e.title,
    r.target_entity_id,
    t.title AS target_title,
    r.type AS relationship_type
FROM entities e
JOIN relationships r ON e.id = r.source_entity_id
JOIN entities t ON r.target_entity_id = t.id;

-- Refresh periodically
REFRESH MATERIALIZED VIEW entity_neighbors;
```

## Comparison with Graph Databases

| Feature | PostgreSQL + CTEs | Neo4j | AGE |
|---------|-------------------|-------|-----|
| 1-hop latency | <1ms | 2-5ms | ~15ms |
| 2-hop latency | <10ms | 10-50ms | ~50ms |
| 3-hop latency | <100ms | 50-200ms | ~200ms |
| ACID transactions | ✅ | Limited | Limited |
| SQL compatibility | ✅ | ❌ | Limited |
| Vector search | ✅ | ❌ | ❌ |
| Full-text search | ✅ | Limited | Limited |
| Setup complexity | Low | Medium | High |
| Maintenance | Low | Medium | High |

**Recommendation:** Use PostgreSQL recursive CTEs for 85% of GraphRAG use cases (1-2 hops). Only consider Neo4j for extremely deep traversals or complex graph algorithms.
