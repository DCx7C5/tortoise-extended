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
| `add_child(**fields)` | `Self` | Insert a child linked to this node |
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

```python
root = await Category.create(name="Electronics")
laptops = await root.add_child(name="Laptops")

assert laptops.parent_id == root.pk
assert laptops.depth == root.depth + 1
assert root.child_count == 1  # in-memory copy matches the single DB bump
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
