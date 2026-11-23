# Graph (Node / Edge / Hierarchy)

Base classes for graph traversal with adjacency list pattern and ltree hierarchies.

> **Naming:** `BaseNode` (+ `NodeMixin`) and `BaseSample` (+ `SampleMixin`) are the primary
> bases. `BaseGraphNodeModel` / `BaseEventStreamModel` below are deprecated aliases kept for
> compatibility — build new models on `BaseNode`.

## Imports

```python
from tortoise_extended import (
    BaseNode,
    NodeMixin,
    BaseGraphNodeModel,  # deprecated alias of BaseNode
    BaseGraphEdgeModel,
    BaseHierarchyModel,
    GiSTIndex,
)
```

---

## BaseNode

Primary graph-node base: `NodeMixin` (pk-free adjacency-list fields and
traversal helpers) layered over a time-ordered `UUID7Field` primary key and
`TimestampMixin`. Subclass it and add only your extra columns.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | `UUID7Field` | Primary key (time-ordered, index-friendly) |
| `name` | `CharField(100)` | Node name |
| `parent_id` | `UUIDField(null=True)` | Parent node ID (bare column, no FK) |
| `depth` | `IntField(default=0)` | Hierarchy depth (root=0) |
| `is_root` | `BooleanField(default=False)` | True if root node |
| `child_count` | `IntField(default=0)` | Denormalized child count |
| `namespace` | `CharField(100, default="default")` | Multi-tenant namespace |
| `metadata_json` | `JSONField(default=dict)` | Arbitrary metadata |
| `created_at` / `updated_at` | `DatetimeField` | From `TimestampMixin` |

Traversal methods (`children()`, `descendants()`, `ancestors()`, `siblings()`,
`path_to_root()`, `subtree()`, `is_leaf`) are the same as listed below.

### Usage

```python
from tortoise import fields
from tortoise_extended import BaseNode


class Category(BaseNode):
    description = fields.TextField(default="")

    class Meta:
        table = "categories"
        indexes = (("namespace", "depth"),)
```

---

## BaseGraphNodeModel (deprecated alias of `BaseNode`)

> Deprecated: `BaseGraphNodeModel` is the UUID4-pinned compatibility alias of
> `BaseNode` (`models/node.py`). New models should derive from `BaseNode`.

Abstract base class for graph nodes with adjacency list traversal. Extends
`Model` directly and declares all of its own fields — subclass it and add only
your extra columns.

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
| `path_to_root()` | `list[BaseGraphNodeModel]` | Path from root to this node |
| `subtree(max_depth)` | `list[BaseGraphNodeModel]` | BFS subtree traversal |
| `is_leaf` | `bool` | Property: no children |

### Usage

```python
from tortoise import fields
from tortoise_extended import BaseGraphNodeModel


class Category(BaseGraphNodeModel):
    description = fields.TextField(default="")

    class Meta:
        table = "categories"
        # Tortoise does NOT inherit Meta.indexes from the abstract base —
        # redeclare them on every concrete subclass.
        indexes = (("namespace", "depth"),)


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

## BaseGraphEdgeModel

Abstract base class for typed, weighted graph edges. Extends `Model` directly
and declares all of its own fields.

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
from tortoise import fields
from tortoise_extended import BaseGraphEdgeModel


class Relationship(BaseGraphEdgeModel):
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

## Node ↔ edge sugar (`connect` / `disconnect` / `neighbours`)

`NodeMixin` can talk to an edge model without importing it. Point a concrete
node class at one and the three helpers become available:

```python
class HybridEdge(BaseModel):
    source_id = fields.BigIntField(db_index=True)
    target_id = fields.BigIntField(db_index=True)
    edge_type = fields.CharField(max_length=50, default="link")
    weight = fields.FloatField(default=1.0)
    namespace = fields.CharField(max_length=100, default="default")

    class Meta:
        table = "hybrid_edges"
        indexes = (("source_id", "target_id"),)


class HybridNode(BaseModel, NodeMixin):
    parent_id = fields.BigIntField(null=True, db_index=True)
    edge_model = HybridEdge  # duck-typed: no import from models/node.py

    class Meta:
        table = "hybrid_nodes"
        indexes = (("namespace", "depth"),)
```

| Method | Returns | Description |
|--------|---------|-------------|
| `connect(other, *, edge_type?, weight?, **attrs)` | `Model` | Create an edge from this node to `other` |
| `disconnect(other, *, edge_type?)` | `int` | Delete edges from this node to `other`; returns the count |
| `neighbours(*, edge_type?, direction="both", namespace?)` | `list[Self]` | Peer nodes reached through `edge_model` |

The edge model is **duck-typed**: only `source_id` and `target_id` are
required. `namespace`, `edge_type` and `weight` are passed only when the model
declares them, and any other keyword must name a real field (unknown names
raise `GraphError`). A node without `edge_model` raises `GraphError` listing
the missing configuration.

```python
a = await HybridNode.create(namespace="shop", depth=0, is_root=True)
b = await HybridNode.create(namespace="shop", depth=0, is_root=True)

edge = await a.connect(b, edge_type="cites", weight=0.5)
assert [n.pk for n in await a.neighbours(direction="out")] == [b.pk]
assert [n.pk for n in await b.neighbours(direction="in")] == [a.pk]
assert await a.disconnect(b, edge_type="cites") == 1
```

Rules:

- **Directed writes, symmetric reads.** `connect` writes one row
  `source_id=a, target_id=b`; `disconnect` deletes rows in that direction
  only. `neighbours(direction="both")` (the default) reads both directions, so
  a link is visible from either end and peers are deduplicated.
- **Same namespace only.** A cross-namespace link raises `GraphError`.
- **No self-links unless declared.** `a.connect(a)` raises `GraphError`; add a
  plain `allow_self_loops = True` attribute to the edge model to permit it.
- Both nodes must be persisted, and `neighbours` orders peers by the node's
  `ordering_field`.

---

## BaseHierarchyModel

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
| `bulk_add_children(parent, rows, *, using_db=None)` | `list` | Insert a level with `path`/`depth` derived (async, classmethod) |
| `bulk_create(...)` / `bulk_update(...)` | — | **Refused** — they skip `save()`, so `path`/`depth` would drift |
| `validate_hierarchy()` | `list[str]` | Integrity check — list of errors (async) |
| `rebuild_paths(*, namespace?)` | `int` | Recompute `path`/`depth` for the tree; returns rows changed (async) |

### Bulk seeding

`bulk_add_children(parent, rows, *, using_db=None)` inserts a whole level with
`path` and `depth` derived: each row's `path` is the parent's path plus its own
`name`, `depth` is the parent's depth `+ 1`, and `parent_id`/`namespace` are
inherited. Pass `parent=None` to seed roots — their `path` is the `name` and
their `depth` is `0`.

```python
roots = await Category.bulk_add_children(None, [{"name": "electronics"}])
children = await Category.bulk_add_children(
    roots[0], [{"name": "laptops"}, {"name": "phones"}]
)

assert [c.path_str for c in children] == ["electronics.laptops", "electronics.phones"]
assert all(await c.validate_hierarchy() == [] for c in children)
```

`name` is required and must be one ltree label (non-empty, no `"."`); rows may
not set `path`, `parent_id` or `depth` (they raise `HierarchyError`), and a
row's `namespace` must match the parent's. The batch shares one transaction and
inserts row by row — Tortoise's `bulk_create` returns no generated keys, and the
next level needs them.

`bulk_create()` and `bulk_update()` themselves **raise** on this base:
neither runs `save()`, so `path`/`depth` (and every descendant path, on a
reparent) would silently keep their old values. Use `move_to()` to reparent and
`rebuild_paths()` to repair a load that bypassed the ORM.

### Repairing a hierarchy

`validate_hierarchy()` detects drift; `rebuild_paths()` repairs it. It walks
the tree from the roots (parents before children) and rewrites `path` and
`depth`, writing only rows that differ — so it is idempotent and safe to run
after a raw-SQL migration or a bulk load.

```python
changed = await Category.rebuild_paths(namespace="shop")  # 0 when consistent
assert await category.validate_hierarchy() == []
```

Nodes unreachable from a root (an orphan, or a parent cycle) are skipped:
their `parent_id` gives no path to derive, and `validate_hierarchy()` reports
them per node.

### Usage

```python
from tortoise import fields
from tortoise_extended import BaseHierarchyModel, GiSTIndex


class Category(BaseHierarchyModel):
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
laptops = await Category.create(
    name="Laptops", path="electronics.laptops", parent_id=root.id
)
macbook = await Category.create(
    name="MacBook", path="electronics.laptops.macbook", parent_id=laptops.id
)

# Query ancestors (awaitable QuerySet)
ancestors = await macbook.get_ancestors()
# => [root, laptops]

# Query descendants
descendants = await root.get_descendants()
# => [laptops, macbook]

# Move subtree
phones = await Category.create(
    name="Phones", path="electronics.phones", parent_id=root.id
)
await macbook.move_to(phones)
# macbook.path = "electronics.phones.macbook"

# Validate integrity
errors = await root.validate_hierarchy()
```

## Notes

- `BaseNode` and `BaseGraphEdgeModel` are abstract — subclass them for concrete models
  (`BaseGraphNodeModel` is the deprecated alias of `BaseNode`)
- QuerySet-returning methods on `BaseGraphEdgeModel` are sync (not async) — they return lazy QuerySets
- `BaseHierarchyModel.move_to()` updates all descendant paths atomically
- Use `namespace` field for multi-tenant graph isolation
