# Model Bases

Reusable abstract model primitives: a `BigInt` primary-key base, a timestamp
mixin, soft delete, graph node/edge/hierarchy bases, model-level Redis
caching, field-level Redis caching, and a TimescaleDB event-stream base. All
are opt-in — nothing is forced on a model that doesn't need it.

## Imports

```python
from tortoise_extended import (
    BaseModel,
    UnifiedIdModel,
    TimestampMixin,
    TimestampEndMixin,
    VersionMixin,
    BaseSoftDeleteModel,
    SoftDeleteQuerySet,
    BaseSoftDeleteNode,
    SoftDeleteNodeQuerySet,
    BaseNode,
    BaseUnifiedNode,
    BaseGraphNodeModel,
    BaseGraphEdgeModel,
    BaseHierarchyModel,
    BaseCacheableModel,
    CachedFieldsMixin,
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
| Optimistic locking (`version` column) | `VersionMixin` | guarded `save()`, see below |
| Soft delete (`deleted_at`, auto-filtered) | `BaseSoftDeleteModel` | pairs with `SoftDeleteQuerySet` |
| Soft-deleted graph nodes (whole subtree) | `BaseSoftDeleteNode` | `BaseSoftDeleteModel` + `NodeMixin`, see below |
| Cross-table / external refs | `UnifiedIdModel` | BigInt pk + UUID7 `uid`, see below |
| Graph nodes / edges | `BaseNode` / `BaseGraphEdgeModel` | `BaseGraphNodeModel` is the deprecated alias, see [Graph](graph.md) |
| BigInt-keyed graph nodes | `BaseUnifiedNode` | `NodeMixin` + UUID7 `uid`, integer `parent_id`, see below |
| ltree trees | `BaseHierarchyModel` | see [Graph](graph.md) |
| Redis row caching | `BaseCacheableModel` | see [Cache](cache.md) |
| Field-level Redis cache (`CachedField` / `RedisPrimaryField`) | `CachedFieldsMixin` | mix in **outermost**, see below and [Caching](../guides/caching.md) |
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

## Model.bulk_insert — COPY ingestion (any model)

`bulk_insert(instances)` is installed on **every** model by the patch set
(site 7), so it is not tied to TimescaleDB streams. It writes rows with a single
asyncpg `COPY` statement — 3–10× faster than `bulk_create`, which renders one
multi-row `INSERT` per batch.

```python
from tortoise import fields
from tortoise.models import Model


class Reading(Model):
    id = fields.IntField(primary_key=True)
    sensor = fields.CharField(max_length=64)
    value = fields.FloatField()

    class Meta:
        table = "readings"


await Reading.bulk_insert(
    [Reading(id=i, sensor="a", value=0.5 * i) for i in range(1, 5)]
)
```

### Rules

| Rule | Why |
|------|-----|
| Explicit primary keys on every instance | `COPY` cannot use identity/serial defaults; a missing id would insert a row the ORM cannot address (`ValueError` before any connection is used) |
| No relation fields on the model | A `COPY` record is a flat column list: an FK, M2M or backward relation would be silently dropped. The call raises `ValueError` naming the fields and pointing at `bulk_create` |
| `db_default` columns: all instances defer, or all set a value | Mirrors `bulk_create`; mixed usage raises `OperationalError` because one statement cannot express both |
| Empty list returns `0` | No connection is acquired |

`source_field` mappings are honoured — the `COPY` targets the real column, so a
mapped field is never silently left at its default. `auto_now` / `auto_now_add`
columns are stamped through the field's `to_db_value`, exactly as `bulk_create`
does. Every instance is marked persisted after a successful `COPY`, so a later
`instance.save()` updates the row instead of inserting a duplicate.

Requires PostgreSQL (asyncpg backend). For stream tables the same method is also
available on `SampleMixin` / `BaseSample`, which delegates to this
implementation — see [Event Streams](event-streams.md#ingestion--copy).

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

## VersionMixin — optimistic locking

`VersionMixin` adds a `version` column (`BigIntField`, default `0`) and a
`save()` that refuses to overwrite a newer row:

```python
from tortoise_extended import BaseModel, TimestampMixin, VersionMixin

class Document(BaseModel, VersionMixin, TimestampMixin):
    title = fields.CharField(max_length=200)

    class Meta:
        table = "documents"

doc = await Document.get(pk=1)       # version 0
doc.title = "Draft"
await doc.save()                     # UPDATE … WHERE id = 1 AND version = 0
                                     # SET …, version = version + 1

first = await Document.get(pk=1)     # someone else loaded the same row
doc.title = "Mine"
await doc.save()                     # still holds version 0 -> conflict
first.title = "Theirs"
await first.save()                   # version 1 -> 2, succeeds
await doc.save()                     # VersionMismatchError
```

What the guarded `save()` does:

- **Inserts are untouched.** `force_create`, an instance with no `pk` yet, or
  an unsaved instance saved without `update_fields` delegate to the next
  `save` in the MRO, so defaults, generated keys, `auto_now_add` and other
  mixins' hooks behave as upstream. `update_fields=[]` is treated as `None` —
  a full save — matching upstream's `if self._saved_in_db or update_fields`.
- **An instance that was never loaded is refused.** `Doc(id=5, title="x")`
  with `update_fields` set carries no version to check (`_saved_in_db` is
  `False`), so `save()` raises `IncompleteInstanceError` instead of running
  the guarded `UPDATE` against a row it never read. This deliberately diverges
  from upstream `Model.save()`, which updates in that shape: reload the row,
  or pass `force_update=True` if you really mean to write it unguarded.
- **Updates are one statement**: `UPDATE <table> SET …, version = version + 1
  WHERE <pk> = $1 AND version = $2`, built through the queryset API — no raw
  SQL. `version` is bumped in the database with `F("version") + 1`, so two
  writers cannot both read `0` and both write `1`.
- **`auto_now` fields are stamped explicitly.** The queryset executor converts
  values without an instance, so `auto_now` would never fire — the same reason
  it does not fire through `QuerySet.update()`. The mixin writes the timestamp
  itself and mirrors it onto the instance. The stamp is an **aware** instant in
  both modes: tortoise's `to_db_value` only adds a timezone when `use_tz` is on
  and the value is naive — it never strips one — so an aware value stores the
  right instant under `use_tz=False` too, and the instance receives the field's
  own conversion (naive there, aware under `use_tz=True`). Converting the stamp
  to naive before the write would be a bug, not a fix: asyncpg encodes a naive
  datetime in the *host's* timezone, so a project whose `TIME_ZONE` differs from
  the host's offset would store an instant off by that offset.
- **`update_fields` narrows the payload**; `version` is always written. An
  empty iterable means "all fields". Naming an unknown field, a field without
  a database column (M2M / backward relation), the primary key or a generated
  column raises `OperationalError` before any query or `pre_save` listener
  runs.
- **On conflict** nothing is written — in the database or in memory, the
  `auto_now` stamps `_pre_save` applied included — and `VersionMismatchError`
  names the model, `pk`, expected version and the version now in the row, so
  the caller can reload and retry. Reloading is not cosmetic: the instance is
  exactly as it was before the call. A row that disappeared raises
  `IntegrityError` (upstream's message for a forced update of a missing
  object), with the same guarantee.
- **Partial instances** (`.only(...)`) must have `version` loaded *and* must
  pass `update_fields`; without a version there is nothing to guard, so
  `IncompleteInstanceError` is raised rather than an unguarded write.

**Not covered — deliberately.** `bulk_create`, `bulk_update` and
`QuerySet.update()` bypass model `save()` entirely: they do not check the
version and do not bump it. They are the bulk escape hatch; use
`instance.save()` for anything that must be race-safe, and treat the bulk
paths as last-write-wins.

The guard uses a plain `QuerySet` internally, so it also works on soft-delete
models: a soft-deleted row is still version-checked (the auto-filtering
`SoftDeleteQuerySet` is not involved and cannot hide the row from the guard).

**Transactions and stale instances.** `version` and `_saved_in_db` are updated
in memory when the guarded `UPDATE` succeeds, inside whatever transaction
`using_db` belongs to. A rollback therefore leaves the instance advertising a
version the committed row does not have, and the next `save()` raises a
spurious `VersionMismatchError`. The instance is only as current as the last
*committed* save: after a rollback — or after another writer touched the row —
call `refresh_from_db()` before saving again.

---

## CachedFieldsMixin — field-level cache lifecycle

Supplies the `save()` / `delete()` hooks for models that declare
`CachedField` or `RedisPrimaryField` columns. The fields themselves and the
codec are documented in [Fields](fields.md); the semantics, failure matrix and
bypass paths are in the [Caching guide](../guides/caching.md). This section
covers only what the mixin adds to the model.

**Order matters — mix it in outermost.** `CachedFieldsMixin` must come first in
the bases, before `VersionMixin` and before `BaseCacheableModel`:

```python
class Doc(CachedFieldsMixin, VersionMixin, BaseModel):
    ...
```

`VersionMixin.save()` overrides `Model.save()` without chaining into a mixin
that sits *inside* it, so an inner `CachedFieldsMixin` would never run its
hooks. Put an outer one and the guarded `UPDATE` executes through
`super().save()` and the cache keys are refreshed after it returns.

What the hooks do:

| Situation | Behaviour |
|---|---|
| Full `save()` | every loaded cache field is re-encoded and written |
| `save(update_fields=[...])` | a cache field **omitted** from `update_fields` is invalidated, because the column may have changed by another writer |
| Partial instance, field **not loaded** | skipped — writing `None` over a live key would be data loss |
| Insert (pk unknown before `INSERT`) | keys are computed after `super().save()` returns, so the pk is populated |
| `delete()` | each cache field's key is removed after the row is gone |
| Backend unreachable / not initialised | every hook catches `CacheError` and logs; the database write still succeeds |

The helpers (`cached_get`, `cached_set`, `cached_invalidate`,
`prefetch_cached`) are the explicit async API and do **not** swallow errors —
they raise. Only the automatic hooks fail open.

Automatic prefetch on load is deliberately not implemented: it would patch the
core load path and needs its own decision.

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

## BaseSoftDeleteNode + SoftDeleteNodeQuerySet

Subtree-aware soft delete: `BaseSoftDeleteModel` (the `deleted_at` column and
the live-only manager surface) layered over `NodeMixin` (adjacency list,
`child_count`, traversal helpers). Declare an identity base in front of it —
`class Category(BaseSoftDeleteNode, BaseNode)` — and redeclare `Meta.indexes`.

A plain `class X(BaseSoftDeleteModel, NodeMixin)` would bypass
`NodeMixin.delete()` (the MRO reaches `BaseSoftDeleteModel.delete()`, which
only stamps `deleted_at`), so neither the parent `child_count` nor the
children themselves would be handled. This base replaces both halves.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `deleted_at` | `DatetimeField(null=True, default=None, db_index=True)` | `NULL` = live row (from `BaseSoftDeleteModel`) |
| `parent_id` | declared by the concrete class | Adjacency-list parent key |
| `depth` / `is_root` / `child_count` / `namespace` / `metadata_json` | — | from `NodeMixin` |

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `instance.delete(using_db=None)` | `None` | Soft-delete the node **and its live subtree** in one transaction |
| `instance.restore()` | `None` | Restore the node and its soft-deleted subtree |
| `qs.delete()` | `int` | Soft-delete the matched rows **and their subtrees** |
| `qs.restore()` | `int` | Restore the matched soft-deleted rows and their subtrees |
| `qs.hard_delete()` | `int` | Physical delete of the matched rows only (base behaviour) |

`delete()` / `restore()` are no-ops on a row already in the target state.
Siblings are never touched; the subtree is the set of rows reachable through
`parent_id` links in the queryset's visibility mode (live rows for a delete,
soft-deleted rows for a restore).

### `child_count`

`child_count` counts **live** children. A subtree soft delete zeroes the
counters inside the subtree and decrements each **live** parent by the number
of children it lost — exactly one for the usual single-root delete;
`restore()` recomputes every restored node's counter from its live children
and re-increments each live parent. A hidden row's counter reads `0` until it
is restored.

### Notes

- `_block_orphan_delete` is a **hard**-delete guard and never fires for a soft
  delete: children are hidden, not orphaned.
- The subtree write is one bulk `UPDATE`, so no per-row `pre_save`/`post_save`
  signals fire; the instance's `deleted_at` is updated in memory.
- The CTE builders (`GraphTraversal`, `shortest_path`, `GraphVectorSearch`)
  auto-filter `deleted_at IS NULL`, so soft-deleted nodes disappear from
  traversal and pathfinding without extra calls.
- `rebuild_derived()` is a raw-SQL hard-delete repair that counts deleted
  children too — do not use it on a soft-delete node tree.

### Usage

```python
from tortoise_extended import BaseNode, BaseSoftDeleteNode


class Category(BaseSoftDeleteNode, BaseNode):
    class Meta:
        table = "categories"
        indexes = (("namespace", "depth"),)


root = await Category.create(name="Electronics")
laptops = await root.add_child(name="Laptops")
macbooks = await laptops.add_child(name="MacBooks")
phones = await root.add_child(name="Phones")

await laptops.delete()   # Laptops + MacBooks hidden, Phones untouched
await laptops.restore()  # both live again

await Category.filter(name="Electronics").delete()  # root + whole subtree
await Category.only_deleted().filter(name="Electronics").restore()
```

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

`move_to()` is the **only** reparent path. `save()` raises `GraphError` when an
update would persist a different `parent_id`: a single-row save cannot shift
each descendant's `depth` or update `is_root`, so it would leave a tree that
only `validate_node()` ever notices. A partial
`save(update_fields=[...])` that omits `parent_id` writes no parent change and
is still allowed.

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

---

## SampleMixin

Pk-free mixin behind `BaseSample` for TimescaleDB stream tables. The full
surface — hypertable DDL (`setup()`), COPY ingestion (`bulk_insert()`) and the
query helpers (`latest_per_stream()`, `time_series()`, `in_range()`) — lives in
[Event Streams](event-streams.md); the four ergonomic classmethods below cover
the common single-row / single-stream cases.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `record(*, stream=None, at=None, **fields)` | `Self` | Create one row; maps `stream` → `stream_field`, `at` → `time_field` |
| `latest_for_stream(stream)` | `Self \| None` | Newest row for one stream (wraps `latest_per_stream()`) |
| `downsample(interval, *, agg="avg", field=None, stream=None, start=None, end=None)` | `list[TimeBucketRow]` | Per-stream `time_bucket` rollup; open bounds default to the whole series |
| `gapfill(interval, *, method="locf", field, stream=None, start=None, end=None)` | `list[TimeBucketRow]` | Gap-filled `time_bucket_gapfill` buckets; `method` is `locf`, `interpolate` or `null` (no fill); `start` / `end` are required |

### Usage

```python
from datetime import UTC, datetime

import tortoise_extended  # noqa: F401 — apply patches
from tortoise import fields
from tortoise_extended import BaseSample


class Event(BaseSample):
    created_at = fields.DatetimeField(use_tz=True)
    stream_id = fields.IntField()
    value = fields.FloatField()

    class Meta:
        table = "events"


event = await Event.record(stream=7, at=datetime.now(UTC), value=0.5)

newest = await Event.latest_for_stream(7)  # Event | None

buckets = await Event.downsample(
    "1 hour", agg="avg", field="value", stream=7
)
# → [TimeBucketRow(stream_id=7, bucket=..., value=..., count=...)]
```

`record()` takes its time value from `at=` (or an explicit `created_at=`
field). With `at=None` it only falls back to the time column's Python-level
`default`: `BaseSample` declares no time column, so a subclass whose time
column has no default raises `TimescaleError` instead of inserting a row
without a timestamp. A mapped shorthand that collides with an explicit field
(`record(stream=1, stream_id=2)`) also raises `TimescaleError`.

The ORM's `Model.latest(*orderings)` remains available on sample models, and
`latest_per_stream()` is the multi-stream form.

`downsample()` validates `field` against the model and raises `TimescaleError`
for an unknown name; `start` / `end` default to the earliest row and the latest
row plus one microsecond (the upper bound is exclusive).

### gapfill

`gapfill()` wraps TimescaleDB `time_bucket_gapfill()`: every bucket in the
`start` .. `end` range comes back, including the buckets that hold no raw rows.
`method` decides how those gaps are filled — `"locf"` (the default) carries the
last observation forward, `"interpolate"` linearly interpolates between the
neighbouring observations, and `"null"` applies no fill function at all, so gap
buckets keep `NULL` values. The bucket value is the `avg` of `field`; `count`
is the number of raw rows in the bucket (`0` for a gap bucket).

```python
base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

await Event.record(stream=7, at=base, value=1.0)
await Event.record(stream=7, at=base + timedelta(hours=3), value=4.0)

buckets = await Event.gapfill(
    "1 hour",
    method="locf",
    field="value",
    stream=7,
    start=base,
    end=base + timedelta(hours=4),
)
# 4 buckets: 1.0, 1.0, 1.0, 4.0 — the middle two carried forward

await Event.gapfill(
    "1 hour",
    method="interpolate",
    field="value",
    stream=7,
    start=base,
    end=base + timedelta(hours=4),
)
# 4 buckets: 1.0, 2.0, 3.0, 4.0
```

`start` and `end` are required — a `TimescaleError` when either is missing or
`end <= start` — because TimescaleDB derives the synthesised buckets from that
range, so it cannot be inferred from the data the way `downsample()` infers its
bounds. `field` must be numeric: `IntField` / `BigIntField` / `SmallIntField` /
`FloatField` / `DecimalField` (anything whose `field_type` is `int`, `float` or
`Decimal`); an unknown name or a text / datetime / UUID / JSON column raises
`TimescaleError`. With `stream=None` every stream is gap-filled independently
over the same range. The table must be a hypertable (`setup()`), and a range
holding no rows returns an empty list — TimescaleDB anchors the buckets to real
series data.

---

## BaseUnifiedNode

The BigInt-keyed graph node: `NodeMixin`'s adjacency-list fields and traversal
helpers over `UnifiedIdModel`'s BigInt `id` + UUID7 `uid`. `parent_id` is a
`BigIntField`, so tree links and JOINs stay integer-cheap; the cost is a `uid`
unique-index write per insert and two identities per row (`id` for internal FK
targets, `uid` for cross-table / external references). Use `BaseNode` when
UUID7 keys are enough; use this base when integer adjacency is the priority.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | `BigIntField` | 64-bit auto-increment primary key (inherited) |
| `uid` | `UUID7Field(unique=True)` | Time-ordered UUID7 for cross-table / external refs (inherited) |
| `parent_id` | `BigIntField(null=True, db_index=True)` | Parent node key; `NULL` for a root |

Plus the `NodeMixin` fields (`depth`, `is_root`, `child_count`, `namespace`,
`metadata_json`) and its full helper surface — see [Graph](graph.md) and the
`NodeMixin traversal helpers` section above.

### Usage

```python
from tortoise_extended import BaseUnifiedNode


class Category(BaseUnifiedNode):
    class Meta:
        table = "categories"
        indexes = (("namespace", "depth"),)  # must be redeclared


root = await Category.create(namespace="shop", is_root=True)
laptops = await root.add_child()
assert isinstance(laptops.parent_id, int)  # BigInt adjacency list
assert laptops.uid != root.uid  # UUID7 cross-table identifier

forest = Category.to_tree(await root.descendants().all())
```

> Concrete subclasses must redeclare `Meta.indexes`: Tortoise does not
> propagate `Meta.indexes` from abstract bases, and `NodeMixin`'s
> `__init_subclass__` raises at class-creation time when it is missing.
> `parent_id` itself is declared on the base, so subclasses never repeat it.
