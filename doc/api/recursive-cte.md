# Recursive CTE

Builder for recursive Common Table Expressions (CTEs) in PostgreSQL.

## Import

```python
from tortoise_extended import RecursiveCTE
```

## Constructor

```python
RecursiveCTE(name: str)
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | CTE name (used in `WITH RECURSIVE <name> AS`) |

## Methods

### anchor(query)

Define the anchor (base case) query.

```python
cte.anchor(query: PostgreSQLQuery) -> RecursiveCTE
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | `PostgreSQLQuery` | Base query (SELECT only) |

**Returns:** `self` for chaining

### union(query)

Define the recursive query.

```python
cte.union(query: PostgreSQLQuery) -> RecursiveCTE
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | `PostgreSQLQuery` | Recursive query (SELECT only) |

**Returns:** `self` for chaining

### build()

Build the final query.

```python
cte.build() -> QueryBuilder
```

**Returns:** a pypika `QueryBuilder` — wrap with `str()` to get the
`WITH RECURSIVE <name> AS (...)` SQL, or pass it directly to a raw query.
Raises `RecursiveCTEError` if no anchor query was set.

## Usage

### Basic Pattern

```python
from pypika_tortoise import PostgreSQLQuery, Table, RawSQL
from tortoise_extended import RecursiveCTE

entities = Table("entities")
relationships = Table("relationships")

cte = (
    RecursiveCTE("ancestors")
    .anchor(
        PostgreSQLQuery.from_(entities)
        .select(entities.id, entities.title, RawSQL("0").as_("depth"))
        .where(entities.id == 42)
    )
    .union(
        PostgreSQLQuery.from_(entities)
        .join(relationships)
        .on(entities.id == relationships.source_entity_id)
        .select(
            entities.id, entities.title, (RawSQL("ancestors.depth") + 1).as_("depth")
        )
    )
    .build()
)

# Execute
sql = str(cte)
results = await connections.get("default").execute_query(sql, [])
```

### Ancestor Traversal

Find all ancestors of an entity.

```python
cte = (
    RecursiveCTE("ancestors")
    .anchor(
        PostgreSQLQuery.from_(entities)
        .select(entities.id, entities.title, RawSQL("0").as_("depth"))
        .where(entities.id == entity_id)
    )
    .union(
        PostgreSQLQuery.from_(entities)
        .join(relationships)
        .on(entities.id == relationships.source_entity_id)
        .select(
            entities.id, entities.title, (RawSQL("ancestors.depth") + 1).as_("depth")
        )
        .where(RawSQL("ancestors.depth") < max_depth)
    )
    .build()
)
```

### Descendant Traversal

Find all descendants of an entity.

```python
cte = (
    RecursiveCTE("descendants")
    .anchor(
        PostgreSQLQuery.from_(entities)
        .select(entities.id, entities.title, RawSQL("0").as_("depth"))
        .where(entities.id == entity_id)
    )
    .union(
        PostgreSQLQuery.from_(entities)
        .join(relationships)
        .on(entities.id == relationships.target_entity_id)
        .select(
            entities.id, entities.title, (RawSQL("descendants.depth") + 1).as_("depth")
        )
        .where(RawSQL("descendants.depth") < max_depth)
    )
    .build()
)
```

### Path Finding

Find shortest path between two entities.

```python
cte = (
    RecursiveCTE("paths")
    .anchor(
        PostgreSQLQuery.from_(entities)
        .select(
            entities.id,
            entities.title,
            RawSQL("ARRAY[entities.id]").as_("path"),
            RawSQL("0").as_("depth"),
        )
        .where(entities.id == source_id)
    )
    .union(
        PostgreSQLQuery.from_(entities)
        .join(relationships)
        .on(entities.id == relationships.source_entity_id)
        .select(
            entities.id,
            entities.title,
            (RawSQL("paths.path") + RawSQL("ARRAY[entities.id]")).as_("path"),
            (RawSQL("paths.depth") + 1).as_("depth"),
        )
        .where(
            (RawSQL("paths.depth") < max_hops)
            & (~RawSQL("entities.id = ANY(paths.path)"))  # Avoid cycles
        )
    )
    .build()
)

# Filter to target
sql = f"""
    SELECT * FROM ({str(cte)}) sub
    WHERE sub.id = $1
    ORDER BY sub.depth
    LIMIT 1
"""
results = await connections.get("default").execute_query(sql, [target_id])
```

### Weighted Traversal

Accumulate edge weights during traversal.

```python
cte = (
    RecursiveCTE("weighted_paths")
    .anchor(
        PostgreSQLQuery.from_(entities)
        .select(
            entities.id,
            entities.title,
            RawSQL("0").as_("total_weight"),
            RawSQL("0").as_("depth"),
        )
        .where(entities.id == source_id)
    )
    .union(
        PostgreSQLQuery.from_(entities)
        .join(relationships)
        .on(entities.id == relationships.source_entity_id)
        .select(
            entities.id,
            entities.title,
            (RawSQL("weighted_paths.total_weight") + relationships.weight).as_(
                "total_weight"
            ),
            (RawSQL("weighted_paths.depth") + 1).as_("depth"),
        )
        .where(RawSQL("weighted_paths.depth") < max_depth)
    )
    .build()
)
```

## Query Construction

### Select Columns

Always include:
- Entity ID (for joining)
- Entity title (for display)
- Depth (for filtering)
- Any additional columns needed

### Join Patterns

```python
# Outgoing relationships
.join(relationships).on(entities.id == relationships.source_entity_id)

# Incoming relationships
.join(relationships).on(entities.id == relationships.target_entity_id)

# Both directions
.join(relationships).on(
    (entities.id == relationships.source_entity_id)
    | (entities.id == relationships.target_entity_id)
)
```

### Cycle Prevention

```python
# Track visited nodes
.where(~RawSQL("entities.id = ANY(cte_name.path)"))

# Limit depth
.where(RawSQL("cte_name.depth") < max_depth)

# Use visited set (requires application logic)
```

## Performance

| Hops | Latency | RPS (u=50) | Memory |
|------|---------|------------|--------|
| 1 | <1ms | 22,581 | Low |
| 2 | <10ms | 5,000 | Medium |
| 3 | <100ms | 500 | High |
| 4 | <1s | 100 | Very High |
| 5+ | >1s | <50 | Critical |

> **Illustrative.** Machine-dependent — reproduce on your hardware with
> `uv run python benchmarks/bench_graph_traversal.py` (docker PG required).

**Best practice:** Limit traversal depth to 2-3 hops for most use cases.

## Notes

- CTEs are materialized in PostgreSQL (results stored temporarily)
- Recursive CTEs can be expensive for deep traversals
- Use `LIMIT` in anchor and union queries to reduce memory
- PostgreSQL has a default recursion limit of 1000 iterations
- Consider materialized views for frequently executed traversals
