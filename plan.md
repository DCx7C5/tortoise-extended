# tortoise-extended — Roadmap & Design Notes

> Internal planning document. "Belongs here" = fills a gap in Tortoise ORM
> 1.1.7 that tortoise-extended can supply within its PostgreSQL
> graph/vector/timescale/redis scope. Statuses: `todo` / `design` / `wip` /
> `done`.

## A. Roadmap — Tortoise gaps we can fill

### Tier 1 — model primitives (low risk, no new deps) — `todo`
1. **Base-model family** (user picks per model, nothing forced):
   - **`BaseModel`** — abstract, `id = BigIntField(primary_key=True)` only.
     Default for internal-only tables (JOIN-fast ints, no extra index).
     (Tortoise auto-creates `id = IntField(primary_key=True)` when no pk is
     declared — manual declaration is only needed for a non-default pk
     type, which is exactly what BigInt pk requires.)
   - **`UnifiedIdModel(BaseModel)`** — adds
     `uid = UUID7Field(unique=True, index=True)`. For models that
     participate in cross-table refs, external lookups, cache keys, or
     polymorphic refs. Ships once `UUID7Field` lands (Tier 1b).
   - Rationale: forcing `uid` on *every* model is an anti-pattern (extra
     unique-index write on every insert; two-ID confusion on tables that
     never need cross-table refs). Choice = full accessibility.
2. **`TimestampMixin`** — `created_at`/`updated_at` (`DatetimeField`,
   `use_tz=True`, `auto_now_add`/`auto_now`). Tortoise only shows this as a
   docs example, never ships it. Stackable with any base above.
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

### Internal hygiene — typing debt — `todo`
1. **Type asyncpg result-row shapes** — the `reportUnknown*` suppression
   cluster in `timescale/` is the only *reducible* part of the 114
   `# pyright: ignore` comments. `execute_query` returns
   `(sql, rows)` where `rows` is untyped; strict mode can't infer
   `result[1]`. Define small typed `Row` protocols / aliases for the
   `execute_query` tuples and annotate:
   - `timescale/hypertable.py` — 7 `reportUnknown*` sites
   - `timescale/retention.py` — 4
   - `timescale/compression.py` — 3
   - stragglers in `expressions/ltree_filters.py`, `graph_filters.py`,
     `cache/queryset.py` — 1 each
   Expected result: eliminate ~15 `reportUnknown*` suppressions. The
   `reportExplicitAny`/`LibraryAny` cluster (~75) is permanent by design
   (untyped upstream — tortoise/asyncpg/redis runtime) and must NOT be
   touched. Gate stays `basedpyright` 0/0/0 + ruff clean.

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

**G15 reconciliation — unified id is a FIELD on an opt-in base (2026-08-04):**
- **Pre-1.0, zero users → no migration-compat burden.** Clean breaks are
  free; design for uniformity, not backward compatibility.
- **Base-model family, user picks per model** (nothing forced on everyone):
  ```python
  class BaseModel(models.Model):
      """Abstract base: BigInt pk. For internal-only tables."""
      id = fields.BigIntField(primary_key=True)          # JOIN-fast ints

      class Meta:
          abstract = True

  class UnifiedIdModel(BaseModel):
      """Abstract base: BigInt pk + UUID7 unified id for cross-table refs."""
      uid = UUID7Field(unique=True, index=True)          # shared ID space

      class Meta:
          abstract = True
  ```
  - `BaseModel` — internal-only tables: JOINs, no extra index.
  - `UnifiedIdModel` — models referenced cross-table / externally:
    `uid` is the unified id (graph edges, polymorphic refs, cache keys,
    cross-table lookups; path = alias, not identity).
- **`GraphNode(UnifiedIdModel)` / `GraphEdge(UnifiedIdModel)`** — BigInt pk
  **plus** `uid`; the old UUID pk column becomes `uid`. Edges are uniform:
  **`source_id`/`target_id` always reference `uid`** (UUID7) — no
  "depends on target family" polymorphism, and graph nodes gain faster
  BigInt JOINs consistent with `HierarchyModel`. One-time repo-internal
  code change only (no shipped data to migrate).
- **`HierarchyModel(BaseModel)`** stays BigInt pk (internal trees); derive
  `UnifiedIdModel` when cross-tree refs are needed.
- **Polymorphic refs** (audit/notifications "on any object"):
  `ref_uid: UUID7Field` + `ref_type: CharField` pair — natural on
  `UnifiedIdModel` rows.

```python
class UUID7Field(fields.UUIDField):
    def __init__(self, **kw) -> None:
        super().__init__(default=uuid.uuid7, **kw)  # py3.14 fallback
        # PG18: db_default=SqlDefault("uuidv7()") on postgres backend
```

**Upgrades:** no shipped data → no migration. Tier-1b adds `UUID7Field` +
`UnifiedIdModel`, re-points `GraphNode`/`GraphEdge` (UUID pk → uid, BigInt
pk) and `FileNode` (§B) at it in one repo-internal change.

**Boundaries:**
- `uid` is opt-in — forcing it on every model is the anti-pattern (extra
  unique-index write per insert on hot tables; two-ID confusion on
  internal-only tables). Documented rule: `id` = internal FK target, `uid`
  = cross-table/external references.
- `EventStreamMixin` keeps its composite pk (`stream_id`, `time_field`);
  no `uid` on the stream hot path.
- SQLite fallback (uuid7 stored as string) keeps non-PG tests green.
- New roadmap item: **Tier 1b — `UUID7Field` + `UnifiedIdModel`** (sits
  between Tier 1 and Tier 2; §B `FileNode` derives `UnifiedIdModel`).

---

## G. Tortoise ORM review — 2026-08-04 (findings, pre-Tier-1 work)

Review of the current graph/cache model layer. Fixes below land *before*
Tier 1 (they touch the bases Tier 1 derives from).

### G1. HIGH — cache-hit instances are detached `construct()` objects — `done`
`CacheableModel._from_cache` / `CachedQuerySet._deserialize_results` build
instances via `Model.construct()`, which sets `_saved_in_db = False`.
`.save()` on a cache hit issues an `INSERT` with an existing PK →
`IntegrityError`. Contract fix: document cache-hit instances as read-only
proxies + provide a `rehydrate()` helper (DB `get(pk=...)`).

### G2. HIGH — GraphNode/GraphEdge UUID PKs have no default — `done`
`id = fields.UUIDField(primary_key=True)` without `default=uuid.uuid4`
forces every `create()` to supply an id manually. Add the default to both
bases (superseded by `UUID7Field`/`UnifiedIdModel` in Tier 1b, but fix now
for current users).

### G3. HIGH — bare UUID adjacency columns, no ON DELETE — `done`
`GraphNode.parent_id`, `GraphEdge.source_id/target_id` are plain
`UUIDField`s — deleting a node silently orphans children/edges. Deliberate
(polymorphic graph), but add a documented cascade policy + optional
`pre_delete` guard. Tier 1b re-points these at `uid`.

### G4. MEDIUM — datetime round-trip returns `str` on cache hits — `done`
`_to_cache`/`_serialize_results` store `isoformat()` strings;
`_from_cache`/`_coerce_value` never convert back (only int/float/bool).
Tests at `tests/test_cache_extended.py:947/1148` codify the string
behavior — fix means updating those tests to assert typed datetimes.
Cache hits and DB hits then share one type.

### G5. MEDIUM — `CachedQuerySet._build_cache_key` is Q-order-sensitive — `done`
Uses `str(f)` for Q objects; reordered but identical filters miss cache.
Hash a normalized Q structure instead.

### G6. LOW — docstrings/typing nits — `done`
- `CacheableModel` docstring shows `class Entity(CacheableModel, models.Model)`
  — `models.Model` base is redundant; use `class Entity(CacheableModel):`.
- `GraphEdge.between/outgoing/incoming` annotate `source_id: str` but
  values are `UUID` — use `UUID`.
- Single-column indexes (`source_id`, `target_id`, `edge_type`,
  `namespace`) partially overlap the composite trio — acceptable for
  base-class ergonomics, revisit at scale.

**Order:** G1+G2+G3 before Tier 1 (they touch base models); G4+G5 are
independent cache-layer fixes; G6 trivia bundled with other edits.

**Status (2026-08-04):** G1–G6 all `done`.
- G1: `rehydrate()` + read-only-proxy contract on `CacheableModel`.
- G2: `default=uuid4` on `GraphNode.id` / `GraphEdge.id`.
- G3: orphan policy documented on `GraphNode`/`GraphEdge`;
  `GraphNode._block_orphan_delete` opt-in guard raises `GraphError`.
- G4: shared `cache/_coerce.py` `coerce_cache_value()` (datetime/date/time
  + int/float/bool) wired into `CacheableModel._from_cache` and
  `CachedQuerySet._coerce_value`; tests assert typed datetimes.
- G5: `CachedQuerySet._q_signature()` normalizes Q kwargs/children;
  `_build_cache_key` sorts signatures — reordered identical filters share
  one cache key.
- G6: `Entity(CacheableModel)` docstring; GraphEdge helpers annotate `UUID`.
