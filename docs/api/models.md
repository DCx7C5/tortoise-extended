# Model Bases

Reusable abstract model primitives: a `BigInt` primary-key base, a timestamp
mixin, soft delete, graph node/edge/hierarchy bases, model-level Redis
caching, and a TimescaleDB event-stream base. All are opt-in — nothing is
forced on a model that doesn't need it.

## Imports

```python
from tortoise_extended import (
    BaseModel,
    UnifiedIdModel,
    TimestampMixin,
    TimestampEndMixin,
    BaseSoftDeleteModel,
    SoftDeleteQuerySet,
    BaseNode,
    BaseGraphNodeModel,
    BaseGraphEdgeModel,
    BaseHierarchyModel,
    BaseCacheableModel,
    BaseSample,
    BaseEventStreamModel,
    BaseUserModel,
)
```

## Choosing a Base

| Need | Use | Notes |
|------|-----|-------|
| No extra columns, Tortoise default | plain `tortoise.models.Model` | auto `IntField` pk |
| 64-bit JOIN-fast primary key | `BaseModel` | internal-only tables |
| `created_at` / `updated_at` columns | `TimestampMixin` | stackable with any base |
| Soft delete (`deleted_at`, auto-filtered) | `BaseSoftDeleteModel` | pairs with `SoftDeleteQuerySet` |
| Cross-table / external refs | `UnifiedIdModel` | BigInt pk + UUID7 `uid`, see below |
| Graph nodes / edges | `BaseNode` / `BaseGraphEdgeModel` | `BaseGraphNodeModel` is the deprecated alias, see [Graph](graph.md) |
| ltree trees | `BaseHierarchyModel` | see [Graph](graph.md) |
| Redis row caching | `BaseCacheableModel` | see [Cache](cache.md) |
| Time-series stream tables | `BaseSample` | `BaseEventStreamModel` is the deprecated alias, see [Event Streams](event-streams.md) |
| Django-style email/password auth | `BaseUserModel` | argon2id, extends `BaseModel`; stack with `TimestampMixin` |

> **Dependency note:** `BaseModel`, `TimestampMixin`, and `BaseSoftDeleteModel`
> are independent of the graph/cache/timescale features — they extend plain
> Tortoise `Model`. `BaseNode`, `BaseHierarchyModel`, and `BaseSample` declare
> their own primary keys; do **not** combine them with `BaseModel` (two `id`
> definitions = field collision).
>
> `UnifiedIdModel` (BigInt pk + UUID7 `uid` for cross-table references) is
> shipped and exported from the top-level package.

---

## BaseModel

Abstract base declaring `id = BigIntField(primary_key=True)`.

Tortoise auto-creates `id = IntField(primary_key=True)` when a model declares
no primary key; `BaseModel` exists for models that want a 64-bit key instead
(JOIN-fast ints, large tables). Deliberately minimal — use
`UnifiedIdModel` when a cross-table/external `uid` is needed too.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | `BigIntField` | 64-bit auto-increment primary key |

### Usage

```python
from tortoise import fields
from tortoise_extended.models.base import BaseModel


class Account(BaseModel):
    name = fields.CharField(max_length=64)

    class Meta:
        table = "accounts"
```

---

## UnifiedIdModel

Abstract base extending `BaseModel` with a time-ordered UUID7 `uid` column.
The BigInt `id` stays the internal FK target; `uid` is the globally unique
reference used when a key must point at "any table" (graph edges, polymorphic
refs, cache keys, public API identifiers) without shared-sequence coordination.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | `BigIntField` | 64-bit auto-increment primary key (inherited) |
| `uid` | `UUID7Field(unique=True)` | Time-ordered UUID7 for cross-table / external refs |

### Usage

```python
from tortoise import fields
from tortoise_extended import UnifiedIdModel


class Document(UnifiedIdModel):
    title = fields.CharField(max_length=200)

    class Meta:
        table = "documents"


doc = await Document.create(title="Design Doc")
doc.id   # int (internal FK target)
doc.uid  # UUID7 (cross-table reference)
```

> `uid` is opt-in: forcing it on every model adds a unique-index write per
> insert and introduces two-ID confusion on internal-only tables.

---

## TimestampMixin

Adds timezone-aware `created_at` / `updated_at` columns. Tortoise documents
`auto_now_add`/`auto_now` but ships no ready-made mixin — this is the
canonical one. Stackable with any abstract base.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `created_at` | `DatetimeField(auto_now_add=True, use_tz=True)` | Set on first insert |
| `updated_at` | `DatetimeField(auto_now=True, use_tz=True)` | Set on every save/update |

### Usage

```python
from tortoise import fields
from tortoise_extended.models.base import BaseModel
from tortoise_extended.models.mixins import TimestampMixin


class Account(BaseModel, TimestampMixin):
    name = fields.CharField(max_length=64)

    class Meta:
        table = "accounts"
```

> **Mixin-last convention:** the real model base (``BaseModel``,
> ``BaseSoftDeleteModel``, …) goes **first**, mixins (``TimestampMixin``,
> ``TimestampEndMixin``) go **last**.

---

## BaseUserModel

Django-style email/password auth base. Extends `BaseModel` (BigInt pk);
email is the login identifier (normalized to lowercase), admins are
distinguished by `is_staff` / `is_superuser` flags on the same table
(single-table Django pattern) rather than a separate admin model. Django's
`username` field is deliberately omitted. Hashing uses `argon2-cffi`
(PHC format) and runs in `asyncio.to_thread` — the event loop is never
blocked.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `email` | `CharField(255, unique=True)` | Login identifier, normalized to lowercase (unique index) |
| `password_hash` | `CharField(255)` | argon2id hash, PHC format `$argon2id$v=19$m=...,t=...,p=...$salt$hash` |
| `is_active` | `BooleanField(default=True)` | Login allowed |
| `is_staff` | `BooleanField(default=False)` | Admin-area access |
| `is_superuser` | `BooleanField(default=False)` | All permissions |
| `last_login` | `DatetimeField(null=True, use_tz=True)` | Last successful login time |
| `USERNAME_FIELD` | `ClassVar[str] = "email"` | Login field (Django naming) |

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `create_user(email, password, **kwargs)` | `Self` | Create + save; normalizes email, hashes password |
| `create_superuser(email, password, **kwargs)` | `Self` | Same, forces `is_active`/`is_staff`/`is_superuser` |
| `set_password(raw_password)` | `None` | Hash + assign `password_hash` (call `save()` to persist) |
| `check_password(raw_password)` | `bool` | Constant-time verify; `False` on malformed/no hash |
| `normalize_email(email)` | `str` | `strip().lower()` |

### Usage

```python
from tortoise_extended import BaseUserModel


class User(BaseUserModel):
    class Meta:
        table = "users"


admin = await User.create_superuser("Admin@Example.com", "hunter2")
await admin.check_password("hunter2")  # True

user = await User.create_user("alice@example.com", "s3cret!")
user.email  # "alice@example.com" (normalized)
await user.set_password("new-password")  # re-hash in place
await user.save()
```

### Notes

- `create_user`/`create_superuser` raise `ValueError` on empty email/password.
- Hashing is CPU-bound — always use the async methods; never call
  argon2 hashing directly in the event loop.
- `check_password` returns `False` for an empty/malformed `password_hash`.

---

## BaseSoftDeleteModel + SoftDeleteQuerySet

Soft-delete base (`deleted_at` column) paired with a queryset that
auto-filters `deleted_at IS NULL`. Every `all()` / `filter()` / `get()` /
`count()` / `exists()` / `update()` / `delete()` call on the default manager
excludes soft-deleted rows; opt out per-query with `.with_deleted()` /
`.only_deleted()`.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `deleted_at` | `DatetimeField(null=True, default=None, db_index=True)` | `NULL` = live row |

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `Model.all()/.filter()/.exclude()/.get()/.get_or_none()` | `SoftDeleteQuerySet` | Live rows only |
| `Model.with_deleted()` | `SoftDeleteQuerySet` | Include soft-deleted rows |
| `Model.only_deleted()` | `SoftDeleteQuerySet` | Soft-deleted rows only |
| `instance.delete()` | `None` | Soft delete (sets `deleted_at`) |
| `instance.restore()` | `None` | Clear `deleted_at` |
| `qs.restore()` | `int` | Restore all rows matched by queryset |
| `qs.hard_delete()` | `int` | Physically delete matched rows |

### Usage

```python
from tortoise import fields
from tortoise_extended.models.base import BaseModel
from tortoise_extended.models.soft_delete import BaseSoftDeleteModel


class Account(BaseModel, BaseSoftDeleteModel):
    name = fields.CharField(max_length=64)

    class Meta:
        table = "accounts"


account = await Account.create(name="alice")

await Account.all()  # live rows only
await Account.filter(name="alice")  # live rows only
await Account.with_deleted()  # everything
await Account.only_deleted()  # soft-deleted only

await account.delete()  # soft delete (sets deleted_at)
await account.restore()  # back to live
await Account.only_deleted().restore()  # restore all deleted rows
await Account.with_deleted().hard_delete()  # physical delete
```

### Notes

- `instance.delete()` keeps the PK and fires `pre_save`/`post_save` signals
  (it uses `save(update_fields=["deleted_at"])`).
- `SoftDeleteQuerySet.restore()` operates on the soft-deleted rows it
  matches; `hard_delete()` physically removes them.
- Tortoise does **not** inherit `Meta.indexes` from abstract bases — redeclare
  indexes on every concrete subclass. `NodeMixin`, `BaseGraphEdgeModel`, and
  `BaseHierarchyModel` enforce this at class-creation time
  (`NotImplementedError`); opt out deliberately with `indexes = ()`.

---

## NodeMixin traversal helpers

`NodeMixin` (and every `BaseNode` subclass) carries write/ancestry helpers on
top of the adjacency-list fields. The field tables and the query helpers
(`children()`, `descendants()`, `subtree()`, `path_to_root()`, ...) are in
[Graph](graph.md); this section covers the helpers added on top of them.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `add_child(*, using_db=None, **fields)` | `Self` | Insert a child linked to this node |
| `bulk_add_children(parent, rows, *, using_db=None)` | `list[Self]` | Insert a whole level with derived columns; `parent=None` seeds roots (classmethod) |
| `bulk_create(...)` / `bulk_update(...)` | — | **Refused** — they skip `save()`, so the derived columns would drift |
| `move_to(new_parent)` | `None` | Reparent this node and its subtree under `new_parent` (or detach to root with `None`) |
| `connect(other, *, edge_type?, weight?, **attrs)` | `Model` | Create an edge to `other` through the configured `edge_model` |
| `disconnect(other, *, edge_type?)` | `int` | Delete edges to `other`; returns the count |
| `neighbours(*, edge_type?, direction="both", namespace?)` | `list[Self]` | Peer nodes reached through `edge_model` |
| `validate_node()` | `list[str]` | Check this node's own invariants; empty list when valid |
| `rebuild_derived(*, namespace?)` | `int` | Recompute `depth`/`is_root`/`child_count` from `parent_id`; returns rows changed |
| `root()` | `Self` | Topmost node on this node's parent chain |
| `is_ancestor_of(other)` | `bool` | `True` when this node is a strict ancestor of `other` |
| `is_descendant_of(other)` | `bool` | `True` when this node is a strict descendant of `other` |
| `to_tree(nodes)` | `list[NodeTree[Self]]` | Build a forest from a flat node slice (classmethod) |

### add_child

Creates and persists a child with `parent_id`, `depth + 1`, `is_root=False`
and this node's `namespace` pre-filled; pass any of those explicitly to
override the inherited value. Raises `GraphError` when this node has not been
saved yet.

`child_count` is maintained by `save()` itself, so `add_child` issues **no**
second counter update — it only syncs the in-memory counter, keeping the
database at exactly +1 per child.

`using_db` selects the connection for the child INSERT. You rarely need it:
tortoise already routes writes made on the default connection to the ambient
`in_transaction()` connection, so creating a parent and its children inside one
transaction is atomic without passing anything. Pass the handle explicitly to
target a specific connection, such as a transaction opened on another
connection name.

```python
root = await Category.create(name="Electronics")
laptops = await root.add_child(name="Laptops")

assert laptops.parent_id == root.pk
assert laptops.depth == root.depth + 1
assert root.child_count == 1  # in-memory copy matches the single DB bump
```

### bulk_add_children

Seeds one level of a tree. Each row is a mapping of ordinary field values —
like `add_child` keyword arguments — and the derived columns are computed for
you: `parent_id`, `depth = parent.depth + 1`, `is_root=False` and
`child_count=0`, plus the parent's own `child_count` bump (one `UPDATE` for
the whole batch). Pass `parent=None` to seed roots (`parent_id=None`,
`depth=0`, `is_root=True`).

```python
roots = await Category.bulk_add_children(
    None, [{"namespace": "shop"}, {"namespace": "shop"}]
)
children = await Category.bulk_add_children(
    roots[0], [{"metadata_json": {"label": "laptops"}}]
)

assert children[0].depth == 1 and children[0].is_root is False
assert await children[0].validate_node() == []
```

Rows may **not** set `parent_id`, `depth`, `is_root` or `child_count` (they are
derived — that raises `GraphError`), and a row's `namespace` must match the
parent's. The batch shares one transaction; the rows are inserted one at a
time because Tortoise's `bulk_create` returns no database-generated keys and
the next level needs the parents' `pk`. For a raw `COPY`-speed load, insert
outside the library and repair with `rebuild_derived()`.

`bulk_create()` and `bulk_update()` themselves **raise** on these bases: they
do not run `save()`, so a bulk-inserted child keeps `depth=0` and the parent's
`child_count` stays at zero — silently. `move_to()` is the reparent path, and
`rebuild_derived()` is the repair path.

### move_to

Reparents the node and its entire subtree in one transaction. `parent_id`,
`depth` and `is_root` are updated on the moved node, every descendant's
`depth` shifts by the same delta in a single bulk `UPDATE`, and the old and
new parents' `child_count` counters are adjusted. Passing `None` detaches the
node to a root (`parent_id=None`, `depth=0`, `is_root=True`). Raises
`GraphError` for a cross-namespace move, a move under itself, or a move into
one of its own descendants.

The source and target rows are locked with `SELECT ... FOR UPDATE` in a
deterministic (sorted primary key) order, so concurrent `A → B` / `B → A`
moves serialise on the same first row instead of deadlocking. The cycle guard
itself is a point-in-time check: adjacency lists carry no path column, so the
descendant set is materialised with a recursive CTE over **unlocked** rows,
and the bulk `UPDATE` does not re-check it — a concurrent write that changes
the subtree in between can defeat the guard or leave a moved-in node with a
stale `depth`. Serialise concurrent writers per tree (for example with a
PostgreSQL advisory lock on the namespace) when that race matters.

```python
await laptops.move_to(kitchen)
assert laptops.parent_id == kitchen.pk
assert laptops.depth == kitchen.depth + 1

await laptops.move_to(None)  # detach: depth == 0, is_root is True
```

### connect / disconnect / neighbours

`NodeMixin` also carries node → edge sugar. Point a concrete class at an edge
model (`edge_model = Relationship`) and `connect(other, ...)`,
`disconnect(other, ...)` and `neighbours(...)` become available; the edge
model is duck-typed — only `source_id` and `target_id` are required — and is
never imported by `models/node.py`, so there is no import cycle.

```python
edge = await laptops.connect(kitchen, edge_type="related", weight=0.5)
assert [n.pk for n in await laptops.neighbours(direction="out")] == [kitchen.pk]
assert await laptops.disconnect(kitchen, edge_type="related") == 1
```

Writes are directed; `neighbours()` defaults to `direction="both"` and
deduplicates peers. Cross-namespace links and self-links raise `GraphError`
(a self-link is allowed only when the edge model declares
`allow_self_loops = True`). Full details: [graph](graph.md).

### validate_node

`validate_node()` audits **one** node against its persisted row and returns a
list of human-readable errors (empty when valid): `is_root` vs `parent_id`,
`depth` vs the parent's depth + 1, `child_count` vs the live child count, a
missing parent (or one in another namespace), and a cycle in the parent
chain. It is the adjacency-list counterpart of
`BaseHierarchyModel.validate_hierarchy()`.

```python
assert await laptops.validate_node() == []
```

It validates a single node; call it in a loop to survey a whole tree, or call
it once per node after a raw SQL / `COPY` load to find the rows
`rebuild_derived()` needs to repair.

### rebuild_derived

The repair path for rows written outside the library (raw SQL, a migration, a
bulk load): one recursive-CTE `UPDATE` walks `parent_id` from the roots and
rewrites `depth`, `is_root` and `child_count`. Only rows that differ are
written, so a second call returns `0`; `namespace=` scopes it to one tenant.
Nodes unreachable from a root (an orphan, or inside a parent cycle) are left
alone — `validate_node()` reports them.

```python
assert await HybridNode.rebuild_derived() == 0   # already consistent
```

### root / is_ancestor_of / is_descendant_of

`root()` is `(await node.path_to_root())[0]`: `path_to_root()` returns the
chain sorted by ascending `depth`, so index `0` is the root and `-1` is the
node itself.

The two ancestry predicates walk the `parent_id` chain of the other node
(link-exact, unlike the depth-range `ancestors()`), so they are correct even
when `depth` values are inconsistent or a namespace is shared. A node is
neither its own ancestor nor its own descendant.

```python
assert await electronics.is_ancestor_of(macbook) is True
assert await macbook.is_descendant_of(electronics) is True
assert await macbook.is_ancestor_of(macbook) is False
assert (await macbook.root()).pk == electronics.pk
```

### NodeTree

`NodeTree` is a `msgspec.Struct` pairing a hydrated node with its nested
children (`node`, `children` — empty list for a leaf). `NodeMixin.to_tree(nodes)`
(the classmethod is inherited by every node model) builds a whole forest in one
O(n) pass keyed by `parent_id` — no per-node query and no recursion.

A node whose `parent_id` is `None` **or** points outside the input slice
becomes a root of the returned forest: promotion is the normal case for a
`descendants()` slice, whose parent lives outside it. A genuine `parent_id`
cycle leaves nodes unreachable from every root and raises `GraphError`.

```python
from tortoise_extended import NodeTree

forest = Category.to_tree(await electronics.descendants().all())


def walk(tree: NodeTree[Category], level: int = 0) -> None:
    print("  " * level, tree.node.name)
    for child in tree.children:
        walk(child, level + 1)
```
