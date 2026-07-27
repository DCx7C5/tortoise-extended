# Graph Traversal

CTE-based graph traversal for edge table patterns.

## Import

```python
from tortoise_extended.expressions.graph_traversal import GraphTraversal
```

## Constructor

```python
GraphTraversal(
    node_model: type,
    edge_model: type,
    source_field: str = "source_id",
    target_field: str = "target_id",
)
```

## Methods

### ancestors

```python
await traversal.ancestors(
    node_id: Any,
    max_depth: int = 10,
    edge_type: str | None = None,
) -> list[dict]
```

Find all ancestors via recursive CTE. Supports bidirectional edges.

### descendants

```python
await traversal.descendants(
    node_id: Any,
    max_depth: int = 10,
    edge_type: str | None = None,
) -> list[dict]
```

Find all descendants via recursive CTE.

### neighbors

```python
await traversal.neighbors(
    node_id: Any,
    direction: str = "both",
    edge_type: str | None = None,
    max_depth: int = 1,
) -> list[dict]
```

Get neighbors within max_depth hops. Direction: `"outgoing"`, `"incoming"`, or `"both"`.

### has_cycle

```python
await traversal.has_cycle(
    edge_type: str | None = None,
    max_depth: int = 20,
) -> bool
```

Check if the graph contains any cycles.

## Usage

```python
from myapp.models import Entity, Relationship
from tortoise_extended.expressions.graph_traversal import GraphTraversal

traversal = GraphTraversal(Entity, Relationship)

# All ancestors of entity 42
ancestors = await traversal.ancestors(node_id=42, max_depth=5)

# Outgoing neighbors
neighbors = await traversal.neighbors(
    node_id=42,
    direction="outgoing",
    edge_type="knows",
    max_depth=2,
)

# Check for cycles
has_cycle = await traversal.has_cycle(max_depth=10)
```

## Notes

- Uses raw SQL via `connections.get("default")` — requires initialized Tortoise ORM
- Bidirectional edges (`is_bidirectional=True`) are traversed in both directions
- Cycle detection uses path array tracking
- `edge_type` filter uses string interpolation (safe — internal constant, not user input)
