# Graph (Node / Edge / Mixin)

Base classes for graph traversal with adjacency list pattern and ltree hierarchies.

## Imports

```python
from tortoise_extended import GraphNode, GraphEdge, HierarchyModel, GiSTIndex
```

---

## GraphNode

Abstract base class for graph nodes with adjacency list traversal.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | `UUIDField` | Primary key |
| `name` | `CharField(100)` | Node name |
| `parent_id` | `UUIDField(null=True)` | Parent node ID |
| `depth` | `IntField(default=0)` | Hierarchy depth (root=0) |
| `is_root` | `BooleanField(default=False)` | True if root node |
| `child_count` | `IntField(default=0)` | Denormalized child count |
| `namespace` | `CharField(100, default="default")` | Multi-tenant namespace |
| `metadata_json` | `JSONField(default=dict)` | Arbitrary metadata |
| `created_at` | `DatetimeField(auto_now_add=True)` | Created timestamp |
| `updated_at` | `DatetimeField(auto_now=True)` | Updated timestamp |

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `children()` | `QuerySet` | Direct children, ordered by name |
| `descendants(max_depth)` | `QuerySet` | All descendants within depth |
| `ancestors()` | `QuerySet` | All ancestors, ordered by depth |
| `siblings()` | `QuerySet` | Siblings (same parent, excluding self) |
| `path_to_root()` | `list[GraphNode]` | Path from root to this node |
| `subtree(max_depth)` | `list[GraphNode]` | BFS subtree traversal |
| `is_leaf` | `bool` | Property: no children |

### Usage

```python
from tortoise import fields, models
from tortoise_extended import GraphNode

class Category(GraphNode, models.Model):
    description = fields.TextField(default="")

    class Meta:
        table = "categories"

# Create root
root = await Category.create(name="Electronics", is_root=True, depth=0)

# Create child
laptops = await Category.create(
    name="Laptops",
    parent_id=root.id,
    depth=1,
)

# Query children
kids = await laptops.children().all()
```

---

## GraphEdge

Abstract base class for typed, weighted graph edges.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | `UUIDField` | Primary key |
| `source_id` | `UUIDField(index=True)` | Source node ID |
| `target_id` | `UUIDField(index=True)` | Target node ID |
| `edge_type` | `CharField(50, index=True)` | Relationship type |
| `weight` | `FloatField(default=1.0)` | Edge weight |
| `properties` | `JSONField(default=dict)` | Edge metadata |
| `namespace` | `CharField(100, default="default")` | Multi-tenant namespace |
| `is_bidirectional` | `BooleanField(default=False)` | Undirected edge flag |
| `created_at` | `DatetimeField(auto_now_add=True)` | Created timestamp |
| `updated_at` | `DatetimeField(auto_now=True)` | Updated timestamp |

### Class Methods (QuerySet-returning, sync)

| Method | Description |
|--------|-------------|
| `between(source_id, target_id, edge_type?, namespace?)` | Edges between two nodes |
| `between_any(node_id, edge_type?, namespace?)` | Edges where node is source OR target |
| `outgoing(source_id, edge_type?, namespace?)` | Outgoing edges from a node |
| `incoming(target_id, edge_type?, namespace?)` | Incoming edges to a node |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `is_self_loop` | `bool` | True if source == target |

### Usage

```python
from tortoise import fields, models
from tortoise_extended import GraphEdge

class Relationship(GraphEdge, models.Model):
    class Meta:
        table = "relationships"

# Create edge
rel = await Relationship.create(
    source_id=node1.id,
    target_id=node2.id,
    edge_type="parent_of",
    weight=1.0,
)

# Query edges
outgoing = await Relationship.outgoing(node1.id, edge_type="parent_of")
between = await Relationship.between(node1.id, node2.id)
any_edge = await Relationship.between_any(node1.id)
```

---

## HierarchyModel

Abstract base class providing tree operations over a PostgreSQL `LTreeField`
materialized path. It extends `Model` directly and declares **all** of its own
fields — subclass it and add only your extra columns.

### Declared Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | `BigIntField` | Primary key |
| `path` | `LTreeField(1024)` | Materialized path (`"root.parent.child"`) |
| `name` | `CharField(255)` | Node name (must equal the last path component) |
| `parent_id` | `BigIntField(null=True, index=True)` | Parent node id (NULL for roots) |
| `depth` | `IntField(default=0)` | Denormalized depth (root=0) |
| `namespace` | `CharField(100, default="default", index=True)` | Multi-tenant partition key |
| `created_at` | `DatetimeField(auto_now_add=True)` | Created timestamp |
| `updated_at` | `DatetimeField(auto_now=True)` | Updated timestamp |

The abstract `Meta` also adds `GiSTIndex(path)` and the composite
`(namespace, depth)` / `(parent_id, depth)` indexes.

### Methods

QuerySet-returning helpers are **sync** and Tortoise QuerySets are awaitable,
so `await node.get_ancestors()` executes the query:

| Method | Returns | Description |
|--------|---------|-------------|
| `get_ancestors(include_self=False)` | `QuerySet` | All ancestors via ltree |
| `get_descendants(include_self=False)` | `QuerySet` | All descendants via ltree |
| `get_children()` | `QuerySet` | Direct children (one level) |
| `get_siblings(include_self=False)` | `QuerySet` | Siblings (same parent) |
| `get_root()` | `Model \| None` | Root node of this tree (async) |
| `get_path_to_root()` | `list` | Nodes from root to this node (async) |
| `move_to(new_parent)` | `None` | Move subtree to new parent (async, atomic) |
| `validate_hierarchy()` | `list[str]` | Integrity check — list of errors (async) |

### Usage

```python
from tortoise import fields, models
from tortoise_extended import HierarchyModel


class Category(HierarchyModel):
    description = fields.TextField(default="")

    class Meta:
        table = "categories"
        # Tortoise does NOT inherit Meta.indexes from the abstract base —
        # redeclare them on every concrete subclass.
        indexes = (
            GiSTIndex(fields=("path",)),
            ("namespace", "depth"),
            ("parent_id", "depth"),
        )


# Create hierarchy (fields are inherited — no manual path/name/depth setup)
root = await Category.create(name="Electronics", path="electronics")
laptops = await Category.create(name="Laptops", path="electronics.laptops", parent_id=root.id)
macbook = await Category.create(name="MacBook", path="electronics.laptops.macbook", parent_id=laptops.id)

# Query ancestors (awaitable QuerySet)
ancestors = await macbook.get_ancestors()
# => [root, laptops]

# Query descendants
descendants = await root.get_descendants()
# => [laptops, macbook]

# Move subtree
phones = await Category.create(name="Phones", path="electronics.phones", parent_id=root.id)
await macbook.move_to(phones)
# macbook.path = "electronics.phones.macbook"

# Validate integrity
errors = await root.validate_hierarchy()
```

## Notes

- `GraphNode` and `GraphEdge` are abstract — subclass them for concrete models
- QuerySet-returning methods on `GraphEdge` are sync (not async) — they return lazy QuerySets
- `HierarchyModel.move_to()` updates all descendant paths atomically
- Use `namespace` field for multi-tenant graph isolation
