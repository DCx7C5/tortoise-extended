# Pathfinding

BFS-based shortest path, all paths, and cycle detection via recursive CTE.

## Import

```python
from tortoise_extended import shortest_path, all_paths, find_cycles
```

---

## shortest_path

```python
await shortest_path(
    node_model: type,
    edge_model: type,
    from_id: Any,
    to_id: Any,
    max_hops: int = 6,
    edge_type: str | None = None,
) -> list[RowMapping] | None
```

Find shortest path between two nodes. Returns list of row dicts or None.

```python
path = await shortest_path(
    Entity,
    Relationship,
    from_id=entity_a.id,
    to_id=entity_b.id,
    max_hops=5,
)
```

---

## all_paths

```python
await all_paths(
    node_model: type,
    edge_model: type,
    from_id: Any,
    to_id: Any,
    max_hops: int = 6,
    max_paths: int = 10,
    edge_type: str | None = None,
) -> list[list[RowMapping]]
```

Find all paths between two nodes (up to `max_paths`). Ordered by length.

```python
paths = await all_paths(
    Entity,
    Relationship,
    from_id=entity_a.id,
    to_id=entity_b.id,
    max_hops=5,
    max_paths=10,
)
for path in paths:
    print(" → ".join(n["name"] for n in path))
```

---

## find_cycles

```python
await find_cycles(
    node_model: type,
    edge_model: type,
    max_depth: int = 10,
    edge_type: str | None = None,
) -> list[list[RowMapping]]
```

Detect cycles in the graph. Returns list of cycles (each a list of row dicts).

```python
cycles = await find_cycles(Entity, Relationship, max_depth=5)
for cycle in cycles:
    print(" → ".join(n["name"] for n in cycle))
```

## Notes

- All functions use raw SQL via `connections.get("default")`
- Path tracking uses PostgreSQL `ARRAY` type for cycle detection
- `max_hops` limits traversal depth to prevent runaway queries
- Supports bidirectional edges via `is_bidirectional` flag
