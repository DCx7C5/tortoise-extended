# Graph Traversal

## Overview

`tortoise-extended` provides graph traversal capabilities through recursive CTEs and 6 SQL retrieval functions. This enables efficient knowledge graph queries without external graph databases.

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

**Best practice:** Limit traversal depth to 2-3 hops for most use cases.

## Graph Retrieval Functions

The 6 SQL retrieval functions are defined in the database via
`.docker/postgres/scripts/02-functions.sql` (not in `tortoise_extended`).
Call them via raw SQL through Tortoise's connection:

```python
from tortoise.connections import connections

conn = connections.get("default")
result = await conn.execute_query(
    "SELECT * FROM local_search($1, $2, $3, $4)",
    ["Python", None, 2, 50],
)
rows = [dict(r) for r in result[1]]
```

### local_search

BFS neighborhood search starting from an entity.

```sql
SELECT * FROM local_search(
    p_entity_name  => 'Python',
    p_entity_type  => 'TECHNOLOGY',
    p_max_depth    => 2,
    p_max_results  => 50
);
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `p_entity_name` | text | Required | Starting entity name |
| `p_entity_type` | text | NULL | Filter by entity type |
| `p_max_depth` | int | 2 | Maximum traversal depth |
| `p_max_results` | int | 50 | Maximum results |

**Returns:** Entity with neighbors at each depth level.

### community_search

Vector search within a specific community.

```sql
SELECT * FROM community_search(
    p_query_embedding => '[0.1,0.2,...]',
    p_community_id    => 'uuid-here',
    p_max_results     => 20
);
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `p_query_embedding` | text | Required | Query vector |
| `p_community_id` | uuid | NULL | Filter by community |
| `p_max_results` | int | 20 | Maximum results |

**Returns:** Entities ranked by vector similarity within community.

### shortest_path

BFS shortest path between two entities.

```sql
SELECT * FROM shortest_path(
    p_source_name => 'Python',
    p_target_name => 'Machine Learning',
    p_max_hops    => 5
);
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `p_source_name` | text | Required | Starting entity |
| `p_target_name` | text | Required | Target entity |
| `p_max_hops` | int | 5 | Maximum path length |

**Returns:** Path with intermediate entities and edges.

### entity_lookup

Fuzzy entity search by name and type.

```sql
SELECT * FROM entity_lookup(
    p_entity_name          => 'Pyth',
    p_entity_type          => 'TECHNOLOGY',
    p_similarity_threshold => 0.3,
    p_max_results          => 10
);
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `p_entity_name` | text | Required | Search term |
| `p_entity_type` | text | NULL | Filter by type |
| `p_similarity_threshold` | float | 0.3 | Minimum similarity |
| `p_max_results` | int | 10 | Maximum results |

**Returns:** Entities matching name with similarity scores.

### hybrid_search

Weighted combination of vector similarity and full-text search.

```sql
SELECT * FROM hybrid_search(
    p_query_embedding => '[0.1,0.2,...]',
    p_text_query      => 'machine learning framework',
    p_vector_weight   => 0.7,
    p_text_weight     => 0.3,
    p_max_results     => 20
);
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `p_query_embedding` | text | Required | Query vector |
| `p_text_query` | text | Required | Search text |
| `p_vector_weight` | float | 0.7 | Vector similarity weight |
| `p_text_weight` | float | 0.3 | Full-text weight |
| `p_max_results` | int | 20 | Maximum results |

**Returns:** Entities ranked by weighted combination of vector and text similarity.

### raptor_search

Multi-level RAPTOR tree search.

```sql
SELECT * FROM raptor_search(
    p_query_embedding => '[0.1,0.2,...]',
    p_max_levels      => 3,
    p_max_results     => 10
);
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `p_query_embedding` | text | Required | Query vector |
| `p_max_levels` | int | 3 | Maximum abstraction levels |
| `p_max_results` | int | 10 | Maximum results |

**Returns:** Entities at different abstraction levels.

## Query Patterns

### Pattern 1: Local Neighborhood

Use for understanding context around an entity.

```python
from tortoise.connections import connections

conn = connections.get("default")
result = await conn.execute_query(
    "SELECT * FROM local_search($1, NULL, $2, $3)",
    ["Python", 2, 50],
)
rows = [dict(r) for r in result[1]]

for row in rows:
    if row["depth"] == 0:
        print(f"Center: {row['title']}")
    else:
        print(f"  Hop {row['depth']}: {row['title']} ({row['type']})")
```

### Pattern 2: Entity Resolution

Use for finding duplicate or similar entities.

```python
result = await conn.execute_query(
    "SELECT * FROM entity_lookup($1, NULL, $2, $3)",
    ["Pyth", 0.3, 10],
)
rows = [dict(r) for r in result[1]]

for row in rows:
    if row["similarity"] > 0.9:
        print(f"High confidence match: {row['title']}")
    elif row["similarity"] > 0.7:
        print(f"Possible match: {row['title']}")
```

### Pattern 3: Community Exploration

Use for understanding community structure.

```python
# Find community
result = await conn.execute_query(
    "SELECT * FROM entity_lookup($1, NULL, 0.3, 1)",
    ["Machine Learning"],
)
community_id = dict(result[1][0])["community_id"]

# Search within community
result = await conn.execute_query(
    "SELECT * FROM community_search($1, $2, $3)",
    ["[0.1,0.2,...]", community_id, 20],
)
```

### Pattern 4: Path Finding

Use for understanding relationships between entities.

```python
result = await conn.execute_query(
    "SELECT * FROM shortest_path($1, $2, $3)",
    ["Python", "TensorFlow", 5],
)
for i, row in enumerate(result[1]):
    print(f"{'→' if i > 0 else ''} {dict(row)['title']}")
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

### Query Hints

```python
# Force index usage
sql = """
    SELECT /*+ IndexScan(entities ix_entities_title) */
        id, title, type
    FROM entities
    WHERE title ILIKE $1
"""
```

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
