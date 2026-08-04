# tortoise-extended — Roadmap & Design Notes

> Internal planning document. "Belongs here" = fills a gap in Tortoise ORM
> 1.1.7 that tortoise-extended can supply within its PostgreSQL
> graph/vector/timescale/redis scope. Statuses: `todo` / `design` / `wip` /
> `done`.

## A. Roadmap — Tortoise gaps we can fill

### Tier 1 — model primitives (low risk, no new deps) — `todo`
1. **`BaseModel(models.Model)`** — abstract base with
   `id = BigIntField(primary_key=True)` **and**
   `uid = UUID7Field(unique=True, index=True)`. Tortoise requires manual pk
   declaration on every model; every subclass gets a JOIN-fast BigInt pk
   **plus** a UUID7 unified-id field (see §F) for cross-table refs — one
   class, nothing opt-in. `uid` lands with `UUID7Field` (Tier 1b); until
   then the base ships pk-only.
2. **`TimestampMixin`** — `created_at`/`updated_at` (`DatetimeField`,
   `use_tz=True`, `auto_now_add`/`auto_now`). Tortoise only shows this as a
   docs example, never ships it.
3. **`SoftDeleteModel` + `SoftDeleteQuerySet`** — `deleted_at` column,
   manager auto-filters `deleted_at IS NULL`, helpers `.with_deleted()`,
   `.only_deleted()`, async `.restore()`. Verified: no soft-delete in
   Tortoise core.

### Tier 2 — PostgreSQL index/field gaps — `todo`
4. **`PartialIndex(fields, where=Q(...))`** and **`ExpressionIndex(expr)`** —
   `Meta.indexes` has no partial/expression support; `indexes/` already
   extends index DDL (HNSW/GiST) so this slots in naturally. DDL e.g.
   `CREATE INDEX ... ON t (col) WHERE active` / `(LOWER(name))`.
5. **PG type fields** — `InetField`, `MacAddrField`, `CitextField`
   (verified absent from `tortoise.fields`). Mirror `LTreeField`: codec +
   `to_db_value`/`to_python_value` + filter registration.
6. **`SearchVectorField`** — generated `tsvector` column (PG `GENERATED
   ALWAYS AS ... STORED` or maintained via trigger), complements
   `HybridSearch` (FTS currently raw-SQL-only).

### Tier 3 — QuerySet/API gaps — `todo`
7. **Window functions** (`row_number()`, `rank()`, `dense_rank()`) —
   verified no window support in `QuerySet`; pypika supports it; home in
   `expressions/`.
8. **`.distinct_on(*fields)`** — `DISTINCT ON` today is raw-SQL-only
   (documented in design-decisions); graduate into QuerySet.
9. **`.random()`** — `ORDER BY RANDOM()`; verified absent. Useful for
   sampling/ab-tests.
10. **`.paginate(page, page_size)` → `PageInfo`** — Tortoise has only
    `offset`/`limit`; return items + total + total_pages.
11. **`bulk_copy(rows)`** — generalized COPY-based bulk insert; infra
    already exists in `timescale/stream.py` (`EventStreamMixin.bulk_insert`);
    expose for any model (PG only).

### Tier 4 — model behavior — `todo`
12. **`UserModel` / `AdminUserModel`** — abstract auth kit: `email`,
    `password_hash`, `is_active`, `is_staff`, `is_superuser`; stdlib-only
    `scrypt`/`pbkdf2_hmac` hashing (no new deps); `set_password`,
    `check_password`, `create_user`, `create_superuser` classmethods.
    ⚠️ Tortoise copies base fields **only for abstract classes** — concrete
    inheritance silently yields an empty child table. Provide two documented
    patterns: (a) single table + role field (recommended), (b) separate
    per-role tables via abstract kit + one concrete subclass each.
13. **Optimistic locking** — `version` BigIntField; `save()` guarded by
    `WHERE version = <stored>`; raises `VersionMismatchError` on conflict.
    Tortoise has no optimistic locking.

### Tier 1b/4 — Python 3.14 stdlib notes
- **`UUID7Field`** (Tier 1b) should use `uuid.uuid7()` as its default — Python
  3.14 stdlib, time-ordered + sortable (see §F unified-id design).
- **Auth kit** (Tier 4.12): `scrypt`/`pbkdf2_hmac` are sync CPU-bound — run
  them in `asyncio.to_thread` inside `set_password`/`check_password`; never
  block the event loop with hashing.
- **Async helpers**: prefer `asyncio.timeout` over `asyncio.wait_for` and
  `asyncio.TaskGroup` over manual gather, in all new async code.

### Guardrails
- **Zero new deps** (auth uses stdlib `hashlib`).
- **Idempotent monkey-patches** — every new field/index/filter goes through
  `_apply_patches()` discipline + `get_filters_for_field` care.
- **Don't duplicate what exists** — `Meta.constraints` (check constraints)
  already supported; do not add.
- **PG-only scope** — non-PG backends fall back (like `VectorField` → BLOB)
  or raise `NotSupportedError`.
- Every item needs tests (mirror feature-area layout in `tests/`).

---

## B. Files + ltree graphs — design

Two shapes, used together (see `doc/guides/project-file-tree.md` for the
wiring guide):

```python
class FileNode(HierarchyModel):
    """File/dir tree. ltree path = materialized path from project root."""
    project = fields.ForeignKeyField("models.Project", related_name="files",
                                     on_delete=fields.CASCADE)
    is_directory = fields.BooleanField(default=True)
    content_hash = fields.CharField(max_length=64, null=True)  # sha256
    size_bytes = fields.BigIntField(null=True)
    mtime = fields.DatetimeField(null=True)
    embedding = VectorField(dimensions=1536, null=True)  # content vec
    class Meta:
        table = "file_nodes"
        indexes = [  # inherited GiST(path) + partial for files
            PartialIndex(fields=("project_id", "is_directory"),
                         where=Q(is_directory=False), name="idx_files_only"),
        ]

class FileLink(GraphEdge):
    """Cross-file edges (imports/deps/copies) — arbitrary, not a tree."""
    class Meta:
        table = "file_links"
```

- **Why both:** tree structure (subtree moves, ancestors) = ltree;
  cross-references (import graphs, dependency cycles) = edge table. The two
  are joined by `(project, path)` ↔ `(source_id, target_id)`.
- **`namespace`** (from `HierarchyModel`) mirrors `project.id` → partition
  safe without joins; every inherited helper filters it automatically.
- **Moves:** `move_to()` cascades path+depth to descendants and validates
  cycles — this is the invalidation trigger for caches (see D).

## C. File caching — design

Four layers (Redis):

| Layer | Key | Invalidation |
|-------|-----|--------------|
| Content blob | `fs:{ns}:blob:{content_hash}` | never (immutable, GC/refcount) |
| Stat/metadata | `fs:{ns}:path:{path}` (`CacheableModel`) | on write/move/delete |
| Tree listing | `fs:{ns}:tree:{path_prefix}*` (`CachedQuerySet`) | subtree invalidation |
| Search results | `fs:{ns}:search:{vec_query_hash}` (`@cached`) | on embedding writes |

- **Content cache is rename-safe** — keyed by `content_hash`, not path.
- **Metadata/listing are path-keyed** — cheap, short TTL, invalidated on
  structural change.
- `RedisCache.init()` once; `CacheableModel._cache_namespace` per model.

## D. Path ↔ cache ↔ index relations

```
        ltree path (DB)
        ├─ GiST index  → subtree queries (@>, <@, ~)
        ├─ cache prefix → fs:{ns}:path:{path}, fs:{ns}:tree:{path}*
        └─ signals     → post_save/post_delete/move_to
```

1. **Path is the natural structural cache key** — but renames/moves change
   it, so every structural write invalidates the old prefix:
   `move_to(new_parent)` → delete `fs:{ns}:tree:{old_prefix}*` + path keys of
   the moved subtree (walk descendants from DB).
2. **Content hash decouples content from structure** — edits bump `hash` only;
   no path-dependent invalidation for blobs/search.
3. **Index mirrors cache:** GiST on `path` serves the same prefix queries the
   cache serves — cache is the hot layer, GiST the truth layer. Partial
   index (`is_directory=false`) keeps file-only scans small; tsvector/
   embedding index serves search (and its results are the cached layer).
4. **Signals over manual calls:** `post_save`/`post_delete` listeners on
   `FileNode` do cache invalidation; `move_to` is the only special case
   (subtree scope). Keep invalidation in one module
   (`sql/cache_invalidation.py` or `cache/invalidation.py`), not scattered.

## E. Order of work

1. Tier 1 (`BaseModel`, `TimestampMixin`, `SoftDeleteModel`) — unlocks the
   rest (BaseModel becomes the parent of `FileNode`).
2. Tier 2 (`PartialIndex`/`ExpressionIndex`, PG fields) — `FileNode` needs
   the partial index in section B.
3. B + C + D — files/ltree/cache feature as a vertical slice (models, cache
   layers, invalidation signals, tests).
4. Tier 3 + 4 as independent PRs.

---

## F. Unified identifier across tables — design

**Problem:** per-table `BigInt` auto-increment collides (`FileNode` and
`User` can both have `id=42`) → a plain `source_id`/`ref_id` column cannot
safely point at "any table". `GraphEdge` already sidesteps this with UUID
(verified: `id`/`source_id`/`target_id` are `UUIDField`) — the gap is every
other model.

**Two orthogonal concerns:**
1. Global uniqueness (shared ID space) → UUID
2. Table discrimination (which table?) → a `type` column, UUID can't solve it

**Decision: `UUID7Field` as the unified identifier.**
- PG 18 native `uuidv7()` `db_default` (project pins `postgres:18`),
  Python 3.14 `uuid.uuid7()` fallback (verified present) — zero new deps.
- Time-ordered → B-tree locality, range scans, hypertable-friendly
  (UUID4's random order is the classic objection — avoided).
- Collision-free across all tables, no shared-sequence coordination.

**G15 reconciliation — unified id is a FIELD, not a pk (decided 2026-08-04):**
- **`BaseModel` carries both identifiers:** `id = BigIntField(primary_key=True)`
  (JOIN-fast ints, internal FKs, `EventStreamMixin` composite pk untouched)
  **and** `uid = UUID7Field(unique=True, index=True)` — the unified id,
  usable across all models for all entities. One base, nothing opt-in.
- **`GraphEdge.source_id`/`target_id` are `UUID7Field` referencing `uid`** —
  since every `BaseModel` subclass has a `uid`, edges can now link **any**
  family (`GraphNode`, `HierarchyModel`, `FileNode`, ...) regardless of pk
  type. This resolves the original constraint (UUID edges could not point
  at BigInt-pk rows).
- **`GraphNode`/`GraphEdge` adopt `BaseModel` in Tier-1b:** pk becomes
  BigInt, the former UUID pk column is migrated to `uid`, and edges point at
  `uid`. **Breaking change** for existing graph data — one-time migration:
  keep the UUID pk as `uid`, add BigInt pk, repoint edges. (Alternative if
  a smooth cutover matters more: keep the UUID pk and treat it as `uid` via
  `pk = uid`; not preferred — two pk schemes across the codebase.)
- **Polymorphic refs** (audit/notifications "on any object"):
  `ref_uid: UUID7Field` + `ref_type: CharField` pair — natural now, because
  every `BaseModel` row has a `uid`.

```python
class UUID7Field(fields.UUIDField):
    def __init__(self, **kw) -> None:
        super().__init__(default=uuid.uuid7, **kw)  # py3.14 fallback
        # PG18: db_default=SqlDefault("uuidv7()") on postgres backend
```

**One base, both fields (keep it simple):**
```python
class BaseModel(models.Model):
    """Abstract base: BigInt pk + UUID7 unified id for every model."""
    id = fields.BigIntField(primary_key=True)          # JOIN-fast ints
    uid = UUID7Field(unique=True, index=True)          # cross-table refs

    class Meta:
        abstract = True
```
- `id` — internal FK target, JOINs, `EventStreamMixin` composite pk stays
  as-is (`stream_id`, `time_field`).
- `uid` — the shared ID space: graph edges, polymorphic refs, cache keys,
  cross-table lookups (`fs:{ns}:node:{uid}`; path = alias, not identity).

**Upgrades:** graph subsystem migration in Tier-1b (above). `FileNode` (§B)
inherits `uid` from `BaseModel` — no per-class declaration needed.

**Boundaries:**
- Don't force UUID7 everywhere — BigInt pk stays the JOIN workhorse;
  `uid` exists for cross-table refs only.
- `EventStreamMixin` keeps its composite pk (`stream_id`, `time_field`);
  it still inherits `uid` if the stream model derives `BaseModel`.
- SQLite fallback (uuid7 stored as string) keeps non-PG tests green.
- New roadmap item: **Tier 1b — `UUID7Field` + `BaseModel.uid` + graph
  migration** (sits between Tier 1 and Tier 2; §B `FileNode` inherits
  `uid` from `BaseModel`).
