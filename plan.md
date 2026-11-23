# tortoise-extended — Roadmap & Design Notes

> Internal planning document. "Belongs here" = fills a gap in Tortoise ORM
> 1.1.7 that tortoise-extended can supply within its PostgreSQL
> graph/vector/timescale/redis scope. Statuses: `todo` / `design` / `wip` /
> `done`.

## A. Roadmap — Tortoise gaps we can fill

### Tier 1 — model primitives (low risk, no new deps) — `done`
> **Status (2026-08-13):** items 1–3 shipped in `6a308d9` (BaseModel,
> TimestampMixin, BaseSoftDeleteModel + SoftDeleteQuerySet).
>
> **Reconciled (2026-11-22):** `UnifiedIdModel` is **`done`** — shipped at
> `models/unified.py` (BigInt pk + `uid = UUID7Field(unique=True)`), exported
> from `models/__init__.py` and the top-level `__all__`
> (`src/tortoise_extended/__init__.py`), and guarded by
> `tests/test_unified_id_model.py::test_exports`.
1. **Base-model family** (user picks per model, nothing forced):
   - **`BaseModel`** — abstract, `id = BigIntField(primary_key=True)` only.
     Default for internal-only tables (JOIN-fast ints, no extra index).
     (Tortoise auto-creates `id = IntField(primary_key=True)` when no pk is
     declared — manual declaration is only needed for a non-default pk
     type, which is exactly what BigInt pk requires.)
   - **`UnifiedIdModel(BaseModel)`** — adds
     `uid = UUID7Field(unique=True)`. For models that
     participate in cross-table refs, external lookups, cache keys, or
     polymorphic refs. *`done` — shipped and public.*
   - Rationale: forcing `uid` on *every* model is an anti-pattern (extra
     unique-index write on every insert; two-ID confusion on tables that
     never need cross-table refs). Choice = full accessibility.
2. **`TimestampMixin`** — `created_at`/`updated_at` (`DatetimeField`,
   `use_tz=True`, `auto_now_add`/`auto_now`). Tortoise only shows this as a
   docs example, never ships it. Stackable with any base above.
3. **`BaseSoftDeleteModel` + `SoftDeleteQuerySet`** — `deleted_at` column,
   manager auto-filters `deleted_at IS NULL`, helpers `.with_deleted()`,
   `.only_deleted()`, async `.restore()`. Verified: no soft-delete in
   Tortoise core.

### Tier 2 — PostgreSQL index/field gaps — `todo`
4. **`PartialIndex(fields, where=Q(...))`** and **`ExpressionIndex(expr)`** —
   `Meta.indexes` has no partial/expression support; `indexes/` already
   extends index DDL (HNSW/GiST) so this slots in naturally. DDL e.g.
   `CREATE INDEX ... ON t (col) WHERE active` / `(LOWER(name))`.
   **Measured 2026-11-23: already upstream — nothing to build.** tortoise-orm 1.1.7 ships
   `tortoise.indexes.PartialIndex` (`condition={col: value}`, equality-only, AND-joined) and
   `Index(*expressions)` over pypika terms; both reach DDL through the ordinary schema
   generator. Verified against the Docker PostgreSQL by reading `pg_indexes` back:
   `CREATE INDEX ... USING btree (name) WHERE (active = true)` and
   `... USING btree (lower((name)::text))`. Two caveats worth knowing, both measured: a bare
   string expression becomes a *literal* (`lower('name'::text)`), so the column must be a
   `pypika_tortoise.terms.Field`; and two expression indexes resolving to the same column are
   de-duplicated into one (`dict.fromkeys` in `_get_field_indexes_sqls`), while distinct columns
   both survive. Documented in `docs/api/indexes.md` § "Partial and expression indexes (upstream)".
5. **PG type fields** — `InetField`, `MacAddrField`, `MacAddr8Field`, `CitextField` — `done`
   (2026-11-23, `e29e42e` implementation + `7bc184f` tests). Verified absent from
   `tortoise.fields` before building; see "PG FIELDS slice" below for what measuring changed.
   - **Reconciled (2026-09-02):** *shipped* `IPv4Field`, `PathField`, `URLField`,
     `UUID4Field`, `UUID7Field`, and `LTreeField` already exist (see
     "Implemented features not previously tracked"). Related-but-distinct:
     the remainder here was only `InetField` / `MacAddrField` /
     `CitextField` (`PathField` is shipped, not in the todo set).
6. **`SearchVectorField`** — **re-scoped to documentation, `done`** (2026-11-23, `docs/api/full-text-search.md`).
   Measured: tortoise-orm 1.1.x already ships the whole surface — `TSVectorField` (generated
   `tsvector` with `source_fields` / `config` / `weights`), `SearchVector`, `SearchQuery`,
   `SearchRank`, `SearchHeadline`, `Lexeme`, and `GinIndex`/`BrinIndex`/`HashIndex`/`SpGistIndex`/
   `BloomIndex`. Nothing to build; the page records the working shapes and the four traps (missing
   `config=` → `generation expression is not immutable`; a string vector term →
   `to_tsvector(tsvector)`; `SearchVector(weight=...)` → asyncpg binds `'A'` as `tsvector`;
   `ts_rank` is not a match indicator under `~`). `HybridSearch` keeps its raw `ts_rank_cd`.

### Tier 3 — QuerySet/API gaps — `wip`
7. **Window functions** (`row_number()`, `rank()`, `dense_rank()`) —
   verified no window support in `QuerySet`; pypika supports it; home in
   `expressions/`. **Shipped 2026-11-23** (`4a96a1b` implementation, `8c514ec`
   tests): `expressions/windows.py` — `row_number`, `rank`, `dense_rank`,
   `ntile(n)` and the windowed aggregates `sum_over` / `avg_over` /
   `count_over` / `min_over` / `max_over`, taking field **names** (a leading `-`
   means `DESC`), with `docs/api/windows.md`.
8. **`.distinct_on(*fields)`** — `DISTINCT ON` today is raw-SQL-only
   (documented in design-decisions); graduate into QuerySet. **Shipped
   2026-11-23** (`085d7d2`): `expressions/distinct_on.py` ships the free async
   `distinct_on(model_or_queryset, *on, order_by=(), limit=None, where=None,
   params=(), include_deleted=False)` helper — Tortoise cannot express the SQL
   (pypika's `distinct()` takes no arguments), so it is one sanitised statement
   plus hydration into real instances. Not a `QuerySet` method; documented in
   `docs/api/queryset.md`.
9. **`.random()`** — **shipped 2026-11-23** (`de826ed`, `cabf3bc` tests):
   `queryset.py` + `QuerySet.random` as patch site 6, `docs/api/queryset.md`,
   `tests/test_queryset_random.py` (13 cases). Implementation is
   `annotate(_tortoise_extended_random=Random()).order_by(<alias>)` on a clone, so it
   composes with `filter`/`values_list`/`limit`/`paginate` and leaves `count()`
   untouched; an existing annotation of the alias raises `ValueError` instead of being
   silently overwritten, documented in `docs/api/queryset.md`.
10. **`.paginate(page, page_size)` → `PageInfo`** — Tortoise has only
    `offset`/`limit`; return items + total + total_pages. **Shipped 2026-11-23**
    (`1fd2e95` implementation, `5446be1` tests): `pagination.py`, `QuerySet.paginate`
    as patch site 5, `docs/api/pagination.md`.
11. **`bulk_insert(instances)`** — COPY-based bulk insert; shipped as a public
    classmethod on `SampleMixin` / `BaseSample` (`models/sample.py`; requires an
    explicit primary key on every instance). Remaining gap: generalise it to any
    model, not only TimescaleDB streams (PG only).

### Tier 4 — model behavior — `todo`
> **Status (2026-08-14):** item 12 (`BaseUserModel`, Django-style email/password
> auth with argon2id hashing) shipped; item 13 (optimistic locking) still `todo`.
12. **`BaseUserModel`** (shipped; supersedes `UserModel`/`AdminUserModel`
    naming — single-table Django pattern) — abstract auth kit: `email`,
    `password_hash`, `is_active`, `is_staff`, `is_superuser`; argon2id
    hashing via `argon2-cffi`; `set_password`,
    `check_password`, `create_user`, `create_superuser` classmethods.
    ⚠️ Tortoise copies base fields **only for abstract classes** — concrete
    inheritance silently yields an empty child table. Provide two documented
    patterns: (a) single table + role field (recommended), (b) separate
    per-role tables via abstract kit + one concrete subclass each.
13. **Optimistic locking** — `version` BigIntField; `save()` guarded by
    `WHERE version = <stored>`; raises `VersionMismatchError` on conflict.
    Tortoise has no optimistic locking. **Shipped 2026-11-23** (`3e48756`):
    `models/versioned.py` — abstract `VersionMixin` + guarded `save()` as one
    queryset `UPDATE` with `version = version + 1`, exported
    `VersionMismatchError`, `docs/api/models.md` section; bulk paths documented
    as bypassing the guard.

### Implemented features not previously tracked — `done`
> These shipped and are exported in `__init__.py` `__all__` but were absent
> from the roadmap. Reconciliation (2026-09-02) — *all `done`*, nothing to
> implement.
>
> *italics = status notes; features below are fully landed.*
- **`GraphVectorSearch` / `GraphVectorHit`** (`expressions/graph_vector_search.py`)
  — graph + vector compositor. *`done`.*
- **`ContinuousAggregateManager`** (`timescale/continuous_aggregate.py`) —
  TimescaleDB continuous aggregates (`create`/`refresh`/`set_refresh_policy`/
  `drop`). *`done`.*
- **`HNSWIndex`, `IVFFlatIndex`, `GiSTIndex`** (`indexes/`) — pgvector and
  GiST index DDL. *`done`.*
- **`TimestampEndMixin`** (`models/mixins.py`) — adds an end-timestamp to the
  existing `TimestampMixin` family. *`done`.*
- **`IPv4Field`**, **`PathField`**, **`URLField`**, **`UUID4Field`**,
  **`UUID7Field`** (`fields/`) — additional PostgreSQL-friendly column types.
  *`done`.*
- **`BaseCacheableModel`**, **`BaseEventStreamModel`** — already mentioned in
  the plan; confirmed shipped. *`done`.*

### Tier 5 — pgcrypto (encrypted storage + query helpers) — `wip`
> **Landing (2026-09-02):** the pgcrypto query-expression helpers shipped.
> Scope rule: **the library performs NO encryption itself** — it only provides
> fields and SQL wrappers so the *application* can invoke pgcrypto from inside
> Tortoise queries. Requires `CREATE EXTENSION IF NOT EXISTS pgcrypto;` (added
> to `docker/postgres-ext/scripts/00-extensions.sql`).
>
> **Reconciled (2026-11-22):** item 15 is `done`; item 14
> (`PGPEncryptedField`) is **`todo`** — `fields/pgcrypto.py` does not exist.

14. **`PGPEncryptedField`** (`fields/pgcrypto.py`) — *`todo`.* Thin `BYTEA` storage
    field subclassing `BinaryField`. Stores ciphertext
    `bytes` the application produces; does **not** encrypt/decrypt. Pair with
    the `pgp_sym_*` helpers below (or any app-side encryptor).
15. **pgcrypto query helpers** (`expressions/pgcrypto.py`) — *`done`.* tortoise
    `Function` wrappers rendering pgcrypto SQL (algorithm/key/options as string
    literals), usable in `.annotate()`/`.filter()`/`.update()`:
    - `Digest(data, algo)` → `digest()` (`bytea`); `DigestHex(data, algo)` →
      `encode(digest(...),'hex')` (`str`).
    - `HMACDigest(data, key, algo)` → `hmac()` (keyed — preferred over raw
      `digest` when a secret is involved).
    - `PGPSymEncrypt(data, key[, opts])` / `PGPSymDecrypt(data, key[, opts])` →
      `pgp_sym_encrypt()` / `pgp_sym_decrypt()` (app-level symmetric crypto).

**Notes:**
- Encrypted columns are **non-indexable / non-searchable** (non-deterministic
  ciphertext). For searchable encrypted values, derive a blind index with
  `HMACDigest`/`DigestHex` in a companion column.
- Keys are application-supplied and never stored by the library. A plain `str`
  key is rendered as a SQL literal, so it **does** appear in `pg_stat_activity.query`
  and in server logs (`log_statement` / `auto_explain`). Pass an expression
  instead — `HMACDigest(Field("body"), RawSQL("current_setting('app.encryption_key')"), "sha256")`
  — so the key is resolved server-side from a session GUC and stays out of the
  statement text; never hardcode it in queries. (`KeyArg` = `DataArg`, so any
  pypika term is accepted.)

### Tier 1b/4 — Python 3.14 stdlib notes
- **`UUID7Field`** (Tier 1b) should use `uuid.uuid7()` as its default — Python
  3.14 stdlib, time-ordered + sortable (see §F unified-id design). *Shipped —
  `UUID7Field` and its consumer `UnifiedIdModel` are both `done`.*
- **Auth kit** (Tier 4.12): argon2 hashing is sync CPU-bound — run
  it in `asyncio.to_thread` inside `set_password`/`check_password`; never
  block the event loop with hashing.
- **Async helpers**: prefer `asyncio.timeout` over `asyncio.wait_for` and
  `asyncio.TaskGroup` over manual gather, in all new async code.

### Guardrails
- **Argon2id auth dependency only** (argon2-cffi is the single auth-related runtime dep; no other new deps).
- **Idempotent monkey-patches** — every new field/index/filter goes through
  `_apply_patches()` discipline + `get_filters_for_field` care.
- **Don't duplicate what exists** — `Meta.constraints` (check constraints)
  already supported; do not add.
- **PG-only scope** — non-PG backends fall back (like `VectorField` → BLOB)
  or raise `NotSupportedError`.
- Every item needs tests (mirror feature-area layout in `tests/`).

### Internal hygiene — single pyright config — `done` (2026-11-22)
> **Resolved (2026-11-22):** `pyrightconfig.json` at the repo root took
> precedence over `[tool.basedpyright]` in `pyproject.toml` and the two were
> never merged — the JSON's rule map was identical, but `include`
> (`["src"]` vs `["src", "tests"]`) and a pyproject-only `executionEnvironments`
> tests block disagreed, so the JSON silently won. The JSON was folded into
> `[tool.basedpyright]` and deleted at the user's instruction, preserving the
> effective gate (`include = ["src"]`, strict mode, `stubPath`, identical
> rule severities). `[tool.basedpyright]` is now the only config.
> `tests/test_stubs.py::TestStubWiring` reads it directly, and `AGENTS.md`
> records the precedence rule so a root JSON is never re-added.

### Internal hygiene — typing debt — `todo`
> **Gate status (2026-11-22):** green — `uv run basedpyright` reports
> **0 errors, 1 warning**; `uv run ruff check src tests` is clean. The 9
> errors introduced by `c95792b` (pk-free `NodeMixin` split) were cleared in
> commit `4abe6d4` (local `main`, unpushed) via TODO 2957: a typing-only
> `id: UUID | int` declaration on the pk-free `NodeMixin`, the parent-id read
> path widened to `UUID | int | None`, and `BaseNode` / the deprecated
> `BaseGraphNodeModel` declaring the narrowed value type under
> `TYPE_CHECKING` while keeping the real field objects in the `else` branch
> (runtime field registration unchanged).
>
> - Original break-down (resolved): 7× `reportAttributeAccessIssue` on
>   `self.id` in the pk-free mixin; `_fetch_parent_id` returning
>   `UUID | None` against a `UUID | int | None` row; and the `parent_id`
>   field assignment conflicting with the inherited value annotation.
> - The single remaining warning is `reportImplicitOverride` in
>   `cache/base.py:173` (`find_class` overrides `Unpickler`); it predates
>   this work and is below the earlier 11-warning baseline.
>   **Closed 2026-11-23 (`73aa766`):** the method carries `@override` now, so
>   the gate is **0 errors, 0 warnings, 0 notes** — the first fully clean run.
>
> **Re-measured (2026-11-22):** 11 `# pyright: ignore` comments in total —
> 6 in `src/` (`models/soft_delete.py` ×5, all
> `reportIncompatibleMethodOverride`; `cache/queryset.py` ×1,
> `reportUnnecessaryIsInstance`) and 5 in `tests/` (3×
> `reportUnusedFunction` on `_db` fixtures, 2×
> `reportIncompatibleMethodOverride`). `timescale/` has **zero**. The
> `LibraryAny` alias no longer exists (`_types.py`), so there is no
> `reportExplicitAny` cluster to clean.
>
> **Full gate + infrastructure (2026-11-22):** with the rebuilt
> `postgres-ext` container (PostgreSQL **18.7**, pgvector 0.8.6,
> TimescaleDB 2.29.2) and `redis-ext` up, `uv run --extra redis pytest
> tests/ -q` reports **1412 passed, 1 skipped** (the `test_all.py`
> aggregator) — zero failures, zero errors, 1413 collected. Host ports are
> **Postgres 5434** and **Redis 6381** (`5433`/`6380` belong to css-mcp);
> every test, benchmark and doc reference was remapped in `9b8a029`.
>
> The image had been unbuildable: TimescaleDB 2.29.2 was compiled against
> `postgresql-server-dev-18` **18.7** headers while the runtime base was
> `postgres:18` **18.4**, so `initdb` aborted with `undefined symbol:
> heap_datum_check`. Both stages are now pinned to
> `postgres:18.7@sha256:eb63372c…` with
> `postgresql-server-dev-18=18.7-1.pgdg13+5`. The 18.4 dev headers no
> longer exist in PGDG, so the apt pin must move with the base — never bump
> one without the other.
>
> Observed once at first boot of the rebuilt image: a non-fatal TimescaleDB
> background-job message (`job 3 threw an error` / `cannot drop portal ""
> while it is being set up`) during init. `initdb` completed, all six
> extensions loaded, and the container restarted cleanly into the same data
> directory; not reproducible on the running container.

0. ~~**Clear the 9 `models/node.py` gate errors**~~ — **done** in `4abe6d4`
   (TODO 2957); gate is green at 0 errors. See the status note above.

1. **Type asyncpg result-row shapes** — `execute_query` returns `(sql, rows)`
   where `rows` is untyped, so strict mode cannot infer `result[1]`. Define
   small typed `Row` protocols / aliases for the `execute_query` tuples and
   annotate the call sites in `timescale/` (`hypertable.py`,
   `retention.py`, `compression.py`) plus `expressions/ltree_filters.py`,
   `graph_filters.py`. Expected result: no new `# pyright: ignore` comments
   in `timescale/` (currently zero) and clearer row typing at those call
   sites. Gate stays `basedpyright` 0/0/0 + ruff clean.

---

## B. Files + ltree graphs — design

Two shapes, used together (see `docs/guides/project-file-tree.md` for the
wiring guide):

```python
class FileNode(BaseHierarchyModel):
    """File/dir tree. ltree path = materialized path from project root."""

    project = fields.ForeignKeyField(
        "models.Project", related_name="files", on_delete=fields.CASCADE
    )
    is_directory = fields.BooleanField(default=True)
    content_hash = fields.CharField(max_length=64, null=True)  # sha256
    size_bytes = fields.BigIntField(null=True)
    mtime = fields.DatetimeField(null=True)
    embedding = VectorField(dimensions=1536, null=True)  # content vec

    class Meta:
        table = "file_nodes"
        indexes = [  # inherited GiST(path) + partial for files
            PartialIndex(
                fields=("project_id", "is_directory"),
                where=Q(is_directory=False),
                name="idx_files_only",
            ),
        ]


class FileLink(BaseGraphEdgeModel):
    """Cross-file edges (imports/deps/copies) — arbitrary, not a tree."""

    class Meta:
        table = "file_links"
```

- **Why both:** tree structure (subtree moves, ancestors) = ltree;
  cross-references (import graphs, dependency cycles) = edge table. The two
  are joined by `(project, path)` ↔ `(source_id, target_id)`.
- **`namespace`** (from `BaseHierarchyModel`) mirrors `project.id` → partition
  safe without joins; every inherited helper filters it automatically.
- **Moves:** `move_to()` cascades path+depth to descendants and validates
  cycles — this is the invalidation trigger for caches (see D).

## C. File caching — design

Four layers (Redis):

| Layer | Key | Invalidation |
|-------|-----|--------------|
| Content blob | `fs:{ns}:blob:{content_hash}` | never (immutable, GC/refcount) |
| Stat/metadata | `fs:{ns}:path:{path}` (`BaseCacheableModel`) | on write/move/delete |
| Tree listing | `fs:{ns}:tree:{path_prefix}*` (`CachedQuerySet`) | subtree invalidation |
| Search results | `fs:{ns}:search:{vec_query_hash}` (`@cached`) | on embedding writes |

- **Content cache is rename-safe** — keyed by `content_hash`, not path.
- **Metadata/listing are path-keyed** — cheap, short TTL, invalidated on
  structural change.
- `RedisCache.init()` once; `BaseCacheableModel._cache_namespace` per model.

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

1. ✅ Tier 1 (`BaseModel`, `TimestampMixin`, `BaseSoftDeleteModel`) — landed
   (`6a308d9`). Unlocks the rest (BaseModel becomes the parent of `FileNode`).
2. Tier 2 (`PartialIndex`/`ExpressionIndex`, PG fields) — `FileNode` needs
   the partial index in section B. **Reconciled (2026-09-02):** the PG fields
   portion largely landed (`IPv4Field`/`PathField`/`URLField`/`UUID4Field`/
   `UUID7Field`, plus index types) and is tracked separately under
   "Implemented features"; **still `todo`** here: `PartialIndex`/
   `ExpressionIndex`, `InetField`/`MacAddrField`/`CitextField`, and
   `SearchVectorField`.
3. B + C + D — files/ltree/cache feature as a vertical slice (models, cache
   layers, invalidation signals, tests).
4. Tier 3 + 4 as independent PRs.

---

## F. Unified identifier across tables — design

**Problem:** per-table `BigInt` auto-increment collides (`FileNode` and
`User` can both have `id=42`) → a plain `source_id`/`ref_id` column cannot
safely point at "any table". `BaseGraphEdgeModel` already sidesteps this with UUID
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

      id = fields.BigIntField(primary_key=True)  # JOIN-fast ints

      class Meta:
          abstract = True


  class UnifiedIdModel(BaseModel):
      """Abstract base: BigInt pk + UUID7 unified id for cross-table refs."""

      uid = UUID7Field(unique=True, index=True)  # shared ID space

      class Meta:
          abstract = True
  ```
  - `BaseModel` — internal-only tables: JOINs, no extra index.
  - `UnifiedIdModel` — models referenced cross-table / externally:
    `uid` is the unified id (graph edges, polymorphic refs, cache keys,
    cross-table lookups; path = alias, not identity).
- **`BaseGraphNodeModel(UnifiedIdModel)` / `BaseGraphEdgeModel(UnifiedIdModel)`** — BigInt pk
  **plus** `uid`; the old UUID pk column becomes `uid`. Edges are uniform:
  **`source_id`/`target_id` always reference `uid`** (UUID7) — no
  "depends on target family" polymorphism, and graph nodes gain faster
  BigInt JOINs consistent with `BaseHierarchyModel`. One-time repo-internal
  code change only (no shipped data to migrate).
- **`BaseHierarchyModel(BaseModel)`** stays BigInt pk (internal trees); derive
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
`UnifiedIdModel`, re-points `BaseGraphNodeModel`/`BaseGraphEdgeModel` (UUID pk → uid, BigInt
pk) and `FileNode` (§B) at it in one repo-internal change.

> **Reconciled (2026-11-22):** the `BaseGraphNodeModel(UnifiedIdModel)`
> re-point above was **superseded and never executed**. What shipped is a
> pk-free `NodeMixin` plus `BaseNode` with a UUID7 primary key
> (`models/node.py`); `BaseGraphNodeModel` remains the UUID4 compatibility
> alias of `BaseNode` (`models/graph_node.py`), and `BaseGraphEdgeModel`
> still extends plain `Model` with UUID `source_id`/`target_id` columns
> (`models/graph_edge.py`). `BaseSample` / `SampleMixin`
> (`models/sample.py`) is the stream-side counterpart with a BigInt
> identity. The decision record is kept as written; this note records the
> outcome.

**Boundaries:**
- `uid` is opt-in — forcing it on every model is the anti-pattern (extra
  unique-index write per insert on hot tables; two-ID confusion on
  internal-only tables). Documented rule: `id` = internal FK target, `uid`
  = cross-table/external references.
- `BaseEventStreamModel` keeps its composite pk (`stream_id`, `time_field`);
  no `uid` on the stream hot path.
- **PostgreSQL-only (ruling 2026-11-23):** the SQLite fallback that once kept
  non-PG tests green is gone — `UUID7Field` is a native `uuid` column and the
  suite runs against the Docker PostgreSQL. Anything that only worked on
  SQLite is deleted, not skipped.
- New roadmap item: **Tier 1b — `UUID7Field` + `UnifiedIdModel`** (sits
  between Tier 1 and Tier 2; §B `FileNode` derives `UnifiedIdModel`).

---

## G. Tortoise ORM review — 2026-08-04 (findings, pre-Tier-1 work)

Review of the current graph/cache model layer. Fixes below land *before*
Tier 1 (they touch the bases Tier 1 derives from).

### G1. HIGH — cache-hit instances are detached `construct()` objects — `done`
`BaseCacheableModel._from_cache` / `CachedQuerySet._deserialize_results` build
instances via `Model.construct()`, which sets `_saved_in_db = False`.
`.save()` on a cache hit issues an `INSERT` with an existing PK →
`IntegrityError`. Contract fix: document cache-hit instances as read-only
proxies + provide a `rehydrate()` helper (DB `get(pk=...)`).

### G2. HIGH — BaseGraphNodeModel/BaseGraphEdgeModel UUID PKs have no default — `done`
`id = fields.UUIDField(primary_key=True)` without `default=uuid.uuid4`
forces every `create()` to supply an id manually. Add the default to both
bases (superseded by `UUID7Field`/`UnifiedIdModel` in Tier 1b, but fix now
for current users).

### G3. HIGH — bare UUID adjacency columns, no ON DELETE — `done`
`BaseGraphNodeModel.parent_id`, `BaseGraphEdgeModel.source_id/target_id` are plain
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
- `BaseCacheableModel` docstring shows `class Entity(BaseCacheableModel, models.Model)`
  — `models.Model` base is redundant; use `class Entity(BaseCacheableModel):`.
- `BaseGraphEdgeModel.between/outgoing/incoming` annotate `source_id: str` but
  values are `UUID` — use `UUID`.
- Single-column indexes (`source_id`, `target_id`, `edge_type`,
  `namespace`) partially overlap the composite trio — acceptable for
  base-class ergonomics, revisit at scale.

**Order:** G1+G2+G3 before Tier 1 (they touch base models); G4+G5 are
independent cache-layer fixes; G6 trivia bundled with other edits.

**Status (2026-08-04):** G1–G6 all `done`.
- G1: `rehydrate()` + read-only-proxy contract on `BaseCacheableModel`.
- G2: `default=uuid4` on `BaseGraphNodeModel.id` / `BaseGraphEdgeModel.id`.
- G3: orphan policy documented on `BaseGraphNodeModel`/`BaseGraphEdgeModel`;
  `BaseGraphNodeModel._block_orphan_delete` opt-in guard raises `GraphError`.
- G4: shared `cache/_coerce.py` `coerce_cache_value()` (datetime/date/time
  + int/float/bool) wired into `BaseCacheableModel._from_cache` and
  `CachedQuerySet._coerce_value`; tests assert typed datetimes.
- G5: `CachedQuerySet._q_signature()` normalizes Q kwargs/children;
  `_build_cache_key` sorts signatures — reordered identical filters share
  one cache key.
- G6: `Entity(BaseCacheableModel)` docstring; BaseGraphEdgeModel helpers annotate `UUID`.

---

## H. Node & stream ergonomics — build-out on `BaseNode` / `BaseSample` (2026-11-22)

> **Trigger:** user request — "what can we build on top of BaseNode/BaseSample, their
> mixins and hybrids, for easier usage". All seven proposed bundles were selected
> 2026-11-22; **breaking changes are permitted** (version `0.1.0`, no git tags); no
> off-limits surface was declared.

### H.1 Measured starting point

| Surface | Shipped today |
|---|---|
| `NodeMixin` | pk-free: `parent_id` annotation, `depth`, `is_root`, `child_count`, `namespace`, `metadata_json`; `children()`, `descendants()`, `ancestors()`, `siblings()`, `path_to_root()`, `subtree()`, `is_leaf`; `child_count` maintained in `save()`/`delete()` |
| `BaseNode` | `UUID7Field` pk + `TimestampMixin` + a **forced** `name` field; `ordering_field = "name"` |
| `BaseGraphEdgeModel` | `between()`, `between_any()`, `outgoing()`, `incoming()`, `is_self_loop` — reachable only from the edge model |
| `SampleMixin` | ClassVar config (`time_field`, `stream_field`, `chunk_time_interval`, `number_partitions`, `compress_after`, `drop_after`); `setup()`, `bulk_insert()`, `in_range()`, `latest_per_stream()`, `time_series()` |
| Query layer | `GraphTraversal`, `GraphVectorSearch`, `pathfinding`, `RecursiveCTE` — all take **model classes + raw column names** |

Gaps that motivate the work:

1. **Asymmetry:** `BaseHierarchyModel` has `move_to()`; `NodeMixin` does not. Reparenting
   an adjacency-list subtree by hand means recomputing `depth` for every descendant and
   fixing `child_count` on two parents — silent corruption if one step is missed.
2. **No constructor:** creating a child requires hand-setting `parent_id` *and*
   `depth=parent.depth+1`; nothing bumps `child_count`.
3. **Flat results:** `descendants()`/`subtree()` return flat lists; every consumer
   immediately rebuilds a tree.
4. **Edges unreachable from nodes:** linking two nodes means instantiating the edge model
   directly; there is no `node.connect(other)`.
5. **No hybrid proof:** `NodeMixin`'s docstring advertises a BigInt-keyed hybrid node
   table ("a hybrid node table can key on `BigInt` instead of the default `UUID`"), but no
   shipped class exercises it.
6. **Soft delete orphans trees:** `BaseSoftDeleteModel` + adjacency list soft-deletes a
   parent while its children stay visible — a broken tree.
7. **Deployment footguns:** `AGENTS.md` documents three manual steps (import order,
   `CREATE EXTENSION`, per-model `Sample.setup()`); nothing in `src/` executes
   `CREATE EXTENSION` at all.

### H.2 Decisions

- **D1 — placement.** Node helpers are **methods on `NodeMixin`**, not a helper module.
  Precedent: `children()`/`descendants()`/`path_to_root()`/`subtree()` are methods, and
  `BaseHierarchyModel.move_to()` is a method.
- **D2 — `move_to` follows the ltree *guards*, not its locking trick.** Same guards as
  `BaseHierarchyModel.move_to` (`models/hierarchy_model.py:431-528`): namespace, self-move,
  cycle; same `in_transaction()` + `refresh_from_db()` envelope; raises `GraphError`
  (node.py's error type, **not** `HierarchyError`). The ltree version is a
  **two-statement** shape — a self `UPDATE` (`parent_id`, `depth`, plus `is_root` here)
  followed by a descendant bulk `UPDATE` that `.exclude(pk=self.pk)` — and that shape is
  mandatory, not stylistic: a single subtree `UPDATE` would never write the moved node's own
  `parent_id`. Three adjacency-list differences are explicit:
  (i) the cycle guard cannot be an O(1) path-prefix test — there is no path, so the
  descendant set must be materialised (recursive CTE or `pk__in=`) from **unlocked** rows,
  which makes the guard TOCTOU-prone;
  (ii) `select_for_update()` must take the two rows in a deterministic order (sorted by pk),
  or concurrent A↔B moves deadlock instead of raising;
  (iii) the descendant bulk `UPDATE` needs an explicit predicate (`pk__in=<materialised
  ids>` or a recursive-CTE `UPDATE` — the AGENTS.md rule-3 raw-SQL carve-out).
  Extension: `new_parent=None` detaches to a root (`parent_id=None`, `depth=0`,
  `is_root=True`).
- **D3 — tree shape.** `to_tree()` returns `list[NodeTree[ModelT]]` where
  `NodeTree(msgspec.Struct, Generic[ModelT])` holds `node: ModelT` + `children`. Precedent
  for a model-carrying Struct: `GraphVectorHit{node, distance, hops}`. Build is O(n) via a
  `parent_id` map. A node whose `parent_id` is `None` **or points outside the input set**
  becomes a root of the returned forest (promotion — the normal case is a subtree slice such
  as `descendants()`); only a genuine cycle raises `GraphError`.
- **D4 — edge declaration is duck-typed.** `edge_model: ClassVar[type[Model] | None] = None`
  validated structurally (`source_id`/`target_id` present in `_meta.fields_map`) — mirrors
  `GraphTraversal.__init__`'s validation and keeps the pk-free mixin independent of any
  concrete edge class. (Not for cycle avoidance: `graph_edge.py` imports neither `node.py`
  nor `graph_node.py`, and `node.py` imports neither — there is no cycle to avoid.)
- **D5 — lifecycle module.** New `src/tortoise_extended/lifecycle.py` exporting
  `ensure_extensions()`, `setup()` and `SetupReport`. `CREATE EXTENSION IF NOT EXISTS` for
  `vector`, `ltree`, `timescaledb`, `pgcrypto`, `pg_trgm`, `uuid-ossp`. Missing connection
  raises a new `SetupError(TortoiseExtendedError)`.
  **No import-order assertion inside `setup()`** — importing `tortoise_extended.lifecycle`
  necessarily executes `tortoise_extended/__init__.py`, which calls `_apply_patches()` at
  module scope, so any in-setup check is true by construction; the real flag lives on
  `tortoise.filters` (`_tortoise_extended_patched`, set in `__init__.py:325`), not on
  `tortoise_extended`. The contract is already covered by
  `tests/test_import_order.py:54-60` — reference it, do not re-test it.
  **Ordering is fixed:** `generate_schemas()` (when requested) runs **before** any
  `model.setup()`, because `SampleMixin.setup()` calls `create_hypertable` immediately and
  its own docstring requires the table to exist (`models/sample.py:159-172`).
- **D6 — hybrids get their own modules** (`models/unified_node.py`,
  `models/soft_delete_node.py`) following the `models/graph_node.py` precedent.
  `BaseSoftDeleteNode` fixes **both** delete paths — the instance `delete()`/`restore()`
  and the inherited `SoftDeleteQuerySet.delete()`/`restore()`, which bulk-soft-delete
  whatever the filter matched and would otherwise leave children visible. The invariant is
  stated, not deferred: `child_count` counts **live** children, so a subtree soft delete
  decrements the deleted subtree's parent by exactly one and `restore()` re-increments.
- **D7 — stream sugar is classmethods on `SampleMixin`.** `record(*, stream=None, at=None,
  **fields)` maps `stream`→`stream_field`, `at`→`time_field`; `gapfill()` is
  TimescaleDB-only (`time_bucket_gapfill` + `locf`/`interpolate`) and must bound the range.
  **`at` is required in practice:** `BaseSample` declares no time column
  (`models/sample.py:575-576`) and tortoise-orm 1.1.x has no `auto_now`/`auto_now_add`
  (the examples at `sample.py:49` and `:338-339` are stale), so `at=None` has no guaranteed
  DB default to fall back on. `record()` therefore raises `TimescaleError` on `at=None`
  unless the subclass's time column carries an explicit default — the row picks one and
  asserts it. `gapfill()` takes `start`/`end` as `datetime | None = None` and raises
  `TimescaleError` when either is missing (a required keyword-only parameter would raise
  `TypeError` first and could never be tested).
- **D8 — declarative config layers over the ClassVars.** `class Meta: timescale = {...}`
  wins over the existing ClassVars, read via `cls.__dict__["Meta"]` because Tortoise's
  `MetaInfo` carries only known keys. **Spike before implementing** — whether `MetaInfo`
  tolerates an unknown `Meta` attribute is unverified.
  **Spike verdict (2026-11-23, measured — every claim cited):** the mechanism *works*, but as a
  design it is strictly worse than the ClassVars, so 2973 is closed and the ClassVars stay.
  - `MetaInfo` is slot-only: `__slots__` is a fixed 40-name tuple
    (`tortoise/models.py:180-220`) and `__init__` reads a fixed key list through
    `getattr(meta, ..., default)` (`:222-232`). An unknown `Meta.timescale` therefore raises
    nothing at class creation, is never validated, and is never stored — measured:
    `child._meta.timescale` → `AttributeError: 'MetaInfo' object has no attribute 'timescale'`,
    and assigning it raises too ("no `__dict__` for setting new attributes").
  - **No inheritance.** `ModelMeta.__new__` reads `attrs.get("Meta", type("Meta", (), {}))`
    (`:509`) — the class's *own* dict only. A concrete subclass must redeclare `timescale` on
    every model, which is the one thing the ClassVars exist to avoid. Worse, the obvious read is
    a trap: a subclass with no `Meta` of its own has `cls.__dict__["Meta"] is None` while
    `cls.Meta` resolves through the MRO to the *abstract base's* Meta (measured), so
    `getattr(cls, "Meta")` would silently return the base's config that Tortoise did not use.
    Any read must be `cls.__dict__.get("Meta")`.
  - **Typing.** The stub overlay declares `MetaInfo` as an explicit attribute list with no
    `__getattr__` (`src/tortoise_extended/stubs/tortoise-stubs/models/__init__.pyi:45-62`) and
    `Model` declares no nested `Meta` (`:67+`), so `cls._meta.timescale` is
    `reportAttributeAccessIssue` and `cls.__dict__["Meta"]` is untyped. A typed read needs a
    `TypedDict` plus a `cast` at each site — allowed, but it is per-read ceremony that a ClassVar
    does not need.
  - **Verdict:** not viable as a replacement for the ClassVars (no inheritance, no validation,
    two config surfaces, a cast per read). It would only be worth revisiting if a *runtime*
    consumer needed to read the config from outside the model class, which nothing does.
- **D9 — parked.** Splitting `BaseNode`'s forced `name` field into a `NamedNodeMixin` was
  discussed but **not selected**; it stays a parked low-priority row.
- **D10 — the sample "latest" name (2026-11-23, user decision).** `SampleMixin.latest(stream)`
  as originally specced shadows `Model.latest(*orderings)` — the inherited sync classmethod that
  returns `QuerySetSingle` — so every sample model lost the ORM helper and a generic call like
  `await Event.latest("created_at")` silently changed meaning. Renamed to
  `latest_for_stream(stream)`, matching the existing `latest_per_stream`; `Model.latest` stays
  intact and the method needs no `@override` / pyright suppression.

### H.3 Phases → TODOs

| Phase | TODO | Depends on |
|---|---|---|
| P1 node ergonomics | `NODE-ERGO` (`add_child`, `root`/`is_ancestor_of`/`is_descendant_of`, `NodeTree` + `to_tree`) | — |
| P1 subtree move | `NODE-MOVE-TO` (`move_to` only — the TOCTOU/locking piece, split out) | `NODE-ERGO` (same file) |
| P1 node↔edge | `NODE-EDGE-HYBRID` (`edge_model`, `connect`/`disconnect`/`neighbours`) | `NODE-MOVE-TO` (same file) |
| P2 lifecycle | `LIFECYCLE-EXTENSIONS` (`ensure_extensions`, `SetupError`, `SetupReport`) → `LIFECYCLE-SETUP` (`setup()` fan-out) | — |
| P3 hybrids | `UNIFIED-NODE` (`BaseUnifiedNode`) → `NODE-SOFTDELETE` (`BaseSoftDeleteNode`) | `NODE-ERGO` |
| P4 facade | `GRAPH-FACADE` (`Graph`) | `NODE-EDGE-HYBRID` |
| P5 streams | `SAMPLE-RECORD-LATEST` → `SAMPLE-GAPFILL` | — |
| P6 declarative | `TIMESCALE-META-SPIKE` → `TIMESCALE-META-IMPL` | `SAMPLE-*` |
| P7 docs | `DOCS-ERGO` | all of the above |

**Status 2026-11-23:** `NODE-ERGO` (2964) is shipped (`add_child(*, using_db=None)`,
`root`/`is_ancestor_of`/`is_descendant_of`, `NodeTree`/`to_tree`; the `using_db` widening
landed in `1910d44`). `NODE-MOVE-TO` (2977) is shipped in `c6f5711`:
`NodeMixin.move_to` implements D2's mandatory two-statement shape, sorted-pk
`select_for_update()` lock order, and the recursive-CTE descendant materialisation
(`_descendant_ids`, `UNION` so a pre-existing data cycle terminates) with the TOCTOU
window documented in the method docstring and `docs/api/models.md`. `NODE-EDGE-HYBRID`
(2965) is shipped too: `NodeMixin.edge_model` (duck-typed, never imported by
`models/node.py`) plus `connect` / `disconnect` / `neighbours`, with
`NodeMixin`'s remaining file contention now free for `NODE-SOFTDELETE` (2968).

**Bulk and save invariants (3011 + 3012, 2026-11-23).** `bulk_create` /
`bulk_update` now raise on both hierarchy bases. Measured before the fix:
`bulk_create`ing two children of a node left `depth=0` (want 1) and the
parent's `child_count=0` (want 2), with no exception. `NodeMixin` and
`BaseHierarchyModel` each gained a `bulk_add_children(parent, rows)` that
derives `parent_id`/`depth`/`is_root`/`child_count` (and `path` for ltree),
inserts the level in one transaction and bumps the parent counter once; the
rows go in one at a time because tortoise's `bulk_create` returns no
database-generated keys and the next level needs them. 3012 took option
**(b)**, extended to `NodeMixin`: `save()` refuses a *persisted* `parent_id`
change (`GraphError` / `HierarchyError`, naming `move_to()`), because a
single-row save cannot shift descendant depths or update `is_root` — the same
drift class on both bases. Option (a) was rejected: recomputing the path in
`save()` duplicates `move_to`'s cycle guard outside a transaction, which is
exactly the TOCTOU shape 2977 documents. A partial `save(update_fields=[...])`
that omits `parent_id` still writes through.

**Repair and parity (3013 + 3014, 2026-11-23).** `BaseHierarchyModel.rebuild_paths()` and
`NodeMixin.rebuild_derived()` (both `*, namespace: str | None = None`) recompute the affected
tree in dependency order and return the number of rows changed — the escape hatch for rows
written outside the library. `NodeMixin.validate_node()` mirrors `validate_hierarchy()`
(`list[str]`, empty when clean) and checks one node's invariants: `depth`, `is_root`,
`child_count`, parent namespace and the parent chain. Shipped `d834fd9` / `d3801a6`; both
shared-list rows still read `pending` afterwards and were closed on 2026-11-23 after the
symbols were re-read in the tree.

**Security rows from 2976, 2026-11-23.** 2981 + 2985 (Docker) and 2986 + 2987 (credentials) are
shipped in `2b33972` / `b854059`: the Postgres init SQL is generated from `\getenv` +
`format(%I/%L)` + `\gexec` (measured end to end with `DB_USER_1=we"ird'user`), both source
dependencies are pinned to full shas with a build-time format check *and* a post-checkout
`git rev-parse HEAD` assertion, Redis AUTH is mandatory (entrypoint exits non-zero; compose
`${…:?}`), and `POSTGRES_PASSWORD` / `REDIS_PASSWORD` ship as `CHANGEME` with compose refusing
an empty value. `REDIS_PASSWORD` also gates the live cache tests (`tests/_redis_env.py`
authenticates and percent-encodes), so the three cache modules skip — visibly — when the secret
is absent. 2963 (SQLite doc claims) was already resolved by `4bd5d8c`; measured, not assumed.

**P2 lifecycle, P3 first hybrid, P5 streams — shipped 2026-11-23.** `LIFECYCLE-EXTENSIONS` (2966,
`f8e8dd6`) adds `ensure_extensions()` / `EXTENSIONS` / `SetupReport` / `SetupError`;
`LIFECYCLE-SETUP` (2978, `62fb090`) adds `setup()`, whose ordering is pinned by measuring that the
reversed order raises (`create_hypertable` before `generate_schemas` →
`TimescaleError` naming the partitioning column). `UNIFIED-NODE` (2967, `c72c161`) ships
`BaseUnifiedNode`, with one measured correction to that row: `NodeMixin.__init_subclass__` returns
early for abstract bases (`models/node.py:233-234`), so the index guard bites *concrete*
subclasses, not the abstract base — the base redeclares the index as documentation.
`SAMPLE-RECORD-LATEST` (2970, `57fe354`) ships `record()` / `downsample()` and the renamed
`latest_for_stream()` (D10); `at=None` follows D7 — the value is omitted only when the time column
carries a non-`None` `default`, otherwise the call raises. `SAMPLE-GAPFILL` (2971, `6af5206`) ships
`gapfill()` over `time_bucket_gapfill` with `locf` / `interpolate` / no fill: `start`/`end` are
required because TimescaleDB derives the synthetic buckets from the range, unlike `downsample()`
which infers them. Two follow-ups were filed out of these
rows rather than folded in: `LIFECYCLE-SETUP-EXPORT` (3021 — `setup` was public in
`tortoise_extended.lifecycle` but not re-exported from the top-level `__init__.py`; shipped
`227f77b`, so `from tortoise_extended import setup` is the documented entry point), and `3010`,
which needs one `pyproject.toml` line the user has agreed to add (the tool-level edit denial stands;
do not work around it).

**P3 second hybrid and P4 facade — shipped 2026-11-23.** `NODE-SOFTDELETE` (2968, `b9e4f35`)
composes `BaseSoftDeleteModel` with `NodeMixin`: instance and queryset deletes both hide the whole
live subtree in one transaction, `restore()` mirrors it, and `child_count` counts **live** children
throughout (internal counters are zeroed on delete and recomputed from live children on restore —
leaving them intact double-counts a partial restore). The subtree walk is an ORM loop, not raw SQL,
and the CTE builders' `deleted_at IS NULL` auto-filter is pinned by a test rather than inherited
silently. `GRAPH-FACADE` (2969) shipped in `c57344e`; see the search-signature note above.

**`GRAPH-FACADE` search signature (2026-11-23, planner decision).** The row specced`search(vector, *, k, max_hops, metric, min_distance)` with no seed, but `GraphVectorSearch` is
graph-scoped by construction — its constructor takes `seed_id` and `max_hops`
(`expressions/graph_vector_search.py:115-175`) and `search()` takes no arguments — so a seedless
facade method cannot be composed without new SQL, which the row forbids. The facade therefore takes
`seed` as a **required** node instance and states in its docstring that it has no vector-only mode;
`k` maps to the underlying result limit. The worker spun on this gap for several minutes, which is
why the decision is recorded here rather than left to the next reader. Shipped `c57344e` —
`Graph` (481 lines), 15 tests, `docs/api/graph.md`; the constructor also validates
`edge_type_field` eagerly, so an edge model without an edge-type column is rejected at
construction instead of at the first `neighbours(edge_type=...)` call.

**P7 docs — shipped 2026-11-23.** `DOCS-ERGO` (2974, `703b008`) closed the reference against the
shipped surface: the four new modules added to the AGENTS.md and README layout trees (plus the
long-missing `expressions/fusion.py` in README), an ergonomics method table for `BaseNode`, the
four `SampleMixin` helpers in `timescale.md`, `Graph.search` in `graph-vector-search.md`, and the
two new bases / soft-delete queryset / lifecycle exports in `index.md` and `guides/llm-reference.md`.
The AGENTS.md §Testing drift the row expected (`test_pathfinding.py`, `test_graph_traversal.py`)
was already gone — measured before editing. Every documented symbol was checked importable, and
nothing describes `Meta.timescale`, which D8 cancelled.

**Gate after the ergonomics slice (2026-11-23):** ruff clean, basedpyright **0 errors, 0 warnings**
(the last one, `cache/base.py:173`, was closed in `73aa766`), full suite **1711 passed, 13
skipped** against
`tortoise_test_push` — the 13 are the 12 live-Redis cases (AUTH secret absent in this worktree) plus
the `test_all.py` aggregator. Baseline before this slice was 1563 passed.

**PAGINATION slice (3043, `1fd2e95` + `5446be1`).** Tier 3 item 10, user-selected on 2026-11-23.
`pagination.py` ships a frozen `slots` dataclass `PageInfo[T: Model]` (`items`, `page`, `page_size`,
`total`; `total_pages` / `has_next` / `has_prev`) and `async paginate(queryset, page, page_size)` —
one `COUNT(*)` plus one `offset((page - 1) * page_size).limit(page_size)` fetch, `ValueError` naming
the argument and the received value below 1, and a page past the end returning `[]` with the true
`total` instead of raising. `QuerySet.paginate` is **patch site 5**, same identity contract as
sites 1-2 (absent → install, ours → no-op, foreign → `PatchConflictError`). A dataclass, not a
msgspec `Struct`: the payload is hydrated model instances, not a wire DTO. Ordering stays the
caller's job and the docstring says why — `OFFSET` without `ORDER BY` is not a stable page
boundary, since PostgreSQL may order the count query and the page query differently.
Tests (`5446be1`, 35 cases in `tests/test_pagination.py`): page math including the empty set
(`total_pages == 0`) and the past-the-end page, validation that would fail with a connection error
if the checks ever moved below `count()`, patch identity / idempotency / foreign conflict with the
foreign object restored in the test, and a live walk over `tortoise_test_pag` asserting the pages
are disjoint and cover every row, `total` honouring a filter rather than the table, and paged slices
concatenating to exactly `order_by(...).limit(total)`. Measured: `pagination.py` at **100 % line
and branch**, and a floor-division mutation of `total_pages` turns 13 cases red. Full suite
**1750 passed, 13 skipped** (the ergonomics figure above measures **1715** without this module);
ruff clean; basedpyright **0 errors, 0 warnings, 0 notes**.

**PG FIELDS slice (3044 + 3045, `e29e42e` + `7bc184f`).** Tier 2 item 5 remainder, user-selected
2026-11-23. Four field classes, no patch site: `InetField` (`inet`), `MacAddrField` (`macaddr`),
`MacAddr8Field` (`macaddr8`), `CitextField` (`citext`). Three measurements changed the shape the
rows assumed:

- **`inet` decodes to an interface object.** asyncpg returns `ipaddress.IPv4Interface` /
  `IPv6Interface`, not `IPv4Address`, so the obvious `isinstance(value, IPv4Address | IPv6Address)`
  read branch never fires and `ip_address(<interface>)` raises. The shipped read path normalises: a
  full-length prefix is the host address and is returned as one, a shorter prefix is a real
  interface value and raises `ValueError`; interface strings are refused on write. The typeshed
  `IPv4Interface`/`IPv6Interface` classes do not declare `prefixlen`/`max_prefixlen`, so the guard
  reads them through `.network` (that was 2 errors + 1 warning until fixed).
- **Two classes, not one.** `macaddr` and `macaddr8` differ only by octet count, but `SQL_TYPE`
  must stay a plain class attribute here (row 2110: a `@property` there produced 14 basedpyright
  errors), so they share a private concrete base and each declares its own `SQL_TYPE`. Accepted
  input is colon, dash or dotted-Cisco spelling, normalised to lowercase colon form on both sides of
  the round-trip; a wrong octet count or bad hex raises `ValueError` naming the value.
- **`citext` has no length modifier.** `max_length` (default 256) is a Python-side guard in
  `to_db_value`, the same device `LTreeField` uses. The extension is now in
  `docker/postgres-ext/scripts/00-extensions.sql`, but that only applies to a fresh volume — the
  live volume predates it (recreating it is 3042, still blocked on the css-mcp socket-dir row), so
  the tests issue `CREATE EXTENSION IF NOT EXISTS citext` themselves.

Tests (`7bc184f`, 51 cases in `tests/test_inet_macaddr_fields.py` + `tests/test_citext_field.py`):
column types read back from `information_schema` (`inet` / `macaddr` / `macaddr8` /
`citext`, not `text`), address objects out of the driver, `ORDER BY` on the `inet` column ordering
numerically rather than as text, every accepted MAC spelling, the citation of rejection paths, and
citext equality / uniqueness / `icontains` measured against PostgreSQL rather than assumed.
**Known gap (row 3051):** no test drives a short-prefix interface object through the read path —
reaching that branch needs a raw-SQL insert, so it is defensive and currently unpinned.

Full suite **1809 passed, 13 skipped** (the 12 live-Redis cases plus the `test_all.py` aggregator);
ruff clean; basedpyright **0 errors, 0 warnings, 0 notes**. Re-measured baseline for this commit
line: `c08af17` collects **1771** (1758 passed), so the pagination note's "1750 / 1715" figures above
are **8 low** — the same unexplained drift the earlier note flagged, now measured rather than
guessed.

**FTS slice (3046).** Tier 2 item 6 was written when the roadmap assumed `HybridSearch` was the only
FTS surface. Measured 2026-11-23: tortoise-orm 1.1.x ships the entire stack upstream —
`TSVectorField(source_fields, config, weights, stored)` generating
`GENERATED ALWAYS AS (TO_TSVECTOR('english',COALESCE(...)) || ...) STORED`, the `SearchVector` /
`SearchQuery` / `SearchRank` / `SearchHeadline` / `Lexeme` expressions, the `@@` path through
`filter(<col>__search=...)`, and GIN/Brin/Hash/SpGist/Bloom index types. So the row became a
documentation row: `docs/api/full-text-search.md` records the working shapes plus four measured
traps that make the library look broken — a generated column without `config=` fails DDL with
`generation expression is not immutable` (one-argument `to_tsvector` is `STABLE`); `SearchRank`
with a *string* column name wraps it in `SearchVector` and emits `TO_TSVECTOR("search")`
(`function to_tsvector(tsvector) does not exist`), so the stored column needs `F("search")`;
`SearchVector(weight="A")` binds the weight as a `tsvector` parameter and asyncpg rejects the string;
and `ts_rank` is not a match indicator under `~SearchQuery(...)` (the matching rows score 0, the
non-matching rows score > 0 — negated queries belong in `filter(<col>__search=...)`).
`docs/api/hybrid-search.md` now says which half of it is upstream. Docs-only: no Python, so the gate
is untouched.

**ERGONOMICS slice round 2 (3047-3050).** Tier 3 items 7/8/9 plus Tier 4 item 13, user-selected
2026-11-23. Four features, three patch sites between them, all verified against the live
`postgres-ext` cluster before commit:
- **3047 `QuerySet.random()`** (`de826ed` feat, `cabf3bc` tests, 13 cases) — **patch site 6**;
  composes with `filter`/`values_list`/`limit`/`paginate`, leaves `count()` untouched, refuses an
  existing annotation of its alias with `ValueError`.
- **3048 window builders** (`4a96a1b` feat, `8c514ec` tests, 32 cases) — nine builders in
  `expressions/windows.py`, field **names** only; `count_over()` with no field emits `COUNT(*)`;
  `ntile(n < 1)` and non-identifier names raise `ValueError`. The live test run found a real defect
  the probe had missed: a tortoise `F("x")` fell through to iteration and raised `TypeError`
  instead of the documented `ValueError`; fixed in `8c514ec` (guarded `tuple()`), along with a bare
  `order_by="-"` now naming `"-"` rather than the stripped `""`.
- **3049 `distinct_on()`** (`085d7d2`) — no patch site: pypika-tortoise's `Query.distinct()` takes
  no arguments and `QuerySet.distinct()` is a bool, so this is one sanitised statement plus
  hydration through `execute_query_dict` + `Model._init_from_db` (the only supported path, and the
  reason the select list aliases every column to its own db name). `order_by` must lead with the
  `on` columns (PostgreSQL's rule) and defaults to them ascending; ten refused inputs raise
  `ValueError` instead of reaching the server.
- **3050 `VersionMixin`** (`3e48756`) — abstract base, `version` BigInt default 0, guarded
  `save()` as one `UPDATE … WHERE <pk> = $1 AND version = $2` with `version = version + 1` through
  the queryset API (AGENTS.md rule 3, so no raw SQL), rowcount decides the outcome, new exported
  `VersionMismatchError` carries `model`/`pk`/`expected`/`current` and leaves the instance
  untouched. Two findings shaped it: `auto_now` never fires through `QuerySet.update()` (the
  executor converts without an instance), so the mixin stamps it explicitly; and the guard uses a
  **plain** `QuerySet`, which is what makes it work on soft-delete models — `SoftDeleteQuerySet`
  would hide the row from the version check. Bulk paths bypass it by design, documented in the
  docstring, `docs/api/models.md` and the CHANGELOG rather than refused.

**Deviation recorded:** both workers for this round died on the account-wide free-tier quota
(several times, in sequence), so after the user's "Planner implements directly" approval the
planner wrote the four implementations and probed them himself; tests stayed with `css-tester`
except where a quota death left a half-finished module. All three test modules landed:
`cabf3bc` (13), `8c514ec` (32), `9eeea39` (25) and `bc906e8` (15). The distinct_on tester was
killed mid-module and left 16 red cases; finishing it found two real defects — validation ran
after `_model_and_connection`, so an unbound model raised `ConfigurationError` where the docs
promised `ValueError` (split into `_target_model` + `_connection_for`, connection resolved only
just before the statement), and the where-before-grouping case filtered on the *winner* column,
which cannot distinguish the two orders at all (now `value < $1`, where grouping first returns
nothing). Stub-overlay additions were required and are
part of the same commits: `MetaInfo.basetable` / `fields_db_projection[_reverse]`,
`execute_query_dict(Sequence[CoercedValue])`, `Field.generated` / `Field.auto_now`,
`Model._saved_in_db` / `_partial` / `_set_async_default_field` / `_pre_save` / `_post_save`, and a
new `tortoise.timezone` stub. Gate at the end of the round: ruff clean, basedpyright
**0 errors, 0 warnings, 0 notes**, full suite **1920 passed, 13 skipped** (the 13 are the 12
live-Redis cases plus the `test_all.py` aggregator); the first full run caught a missing
`models.__all__` mirror in `tests/test_exports.py` (`9bb3da4`), which is exactly what that guard
exists for.

**Wheel payload (3010, `50910f0`).** The user added the one line —
`exclude = ["src/tortoise_extended/stubs"]` under `[tool.hatch.build.targets.wheel]` — and the
rebuilt wheel was measured: **0 `.pyi` entries**, no `/stubs/` path, `py.typed` still present, 62
entries (the 70 originally counted, minus the 12 stubs, plus the four modules shipped since).
basedpyright still reads the overlay through `stubPath`, so the dev gate is unchanged at 0 errors.
**Cross-repo consequence (appended to css-mcp row 2758):** css-mcp's own `stubPath`
(`pyproject.toml:143`) points into the *installed* `tortoise_extended/stubs`, so its next pin bump
plus `uv lock` leaves that path dangling — decide stubPath there before re-running the db lane.

**Healthcheck (3024, `0c1aa2e`).** The row blamed `${POSTGRES_DB}` for an always-unhealthy
container. Half of that is wrong, and measuring changed the fix: `pg_isready` returns 0 on an
ERROR reply (`pg_isready -d definitely_missing_db` printed "accepting connections", exit 0), so the
app-database coupling never gated liveness — it only logged one FATAL per interval. The real defect
is the **cross-cluster probe**: both stacks publish `.s.PGSQL.5432` into `/var/run/postgresql`, and
inside `postgres-ext` a socket-less `psql` listed css-postgres's databases (`core`, `secrets`,
`testcore`, `testsecrets`) while `-h 127.0.0.1` listed the tortoise ones. The healthcheck now runs
`pg_isready -h 127.0.0.1 -p 5432 -U ${POSTGRES_USER:-postgres} -d postgres`. Verified on a rebuilt
image (full cache hit, 1.9s) in an isolated container: healthy in ~4s, baked command inspected, no
FATAL. `tortoise_test` already exists on the live volume, so the row's create-the-DB half was moot.
The running container is deliberately still on the old image — recreating it before css-mcp 3022
isolates its socket dir would make the restart fight css-postgres for the shared host socket. That
is row 3042.

**Standing TODO verdicts (2026-11-23).** `TORTOISE-STUB-SQL-TYPE` (2110) **cancelled**: declaring
`SQL_TYPE` as a `@property` in `fields/base.pyi` produces 14 basedpyright errors
(`reportIncompatibleMethodOverride`, `reportAssignmentType`) across `fields/__init__.pyi:162,445`
and the six `tortoise_extended` fields that assign `SQL_TYPE = "TEXT" | "ltree" | "inet" | "vector"`.
`SQL_TYPE` is a plain class attribute at runtime (measured: `Field.SQL_TYPE is None`,
`BooleanField` "BOOL", `IntField` "INT", only `CharField` a property), so the attribute declaration
is the truthful one and the PyCharm-native "Missing super method" warning is a PyCharm limitation
for property-over-attribute, not a stub defect. Nothing to change in this repo.

**TODO positions:** NODE-ERGO 2964 · NODE-EDGE-HYBRID 2965 · LIFECYCLE-EXTENSIONS 2966 ·
UNIFIED-NODE 2967 · NODE-SOFTDELETE 2968 · GRAPH-FACADE 2969 · SAMPLE-RECORD-LATEST 2970 ·
SAMPLE-GAPFILL 2971 · TIMESCALE-META-SPIKE 2972 · TIMESCALE-META-IMPL 2973 · DOCS-ERGO 2974 ·
NODE-NAME-SPLIT 2975 (parked → cancelled) · SEC-REVIEW-2026-11-22 2976 ·
NODE-MOVE-TO 2977 · LIFECYCLE-SETUP 2978 · TRACKING-DRIFT-SESSION-JOIN 2979.

Row names are the row titles in the shared list (`NODE-SOFTDELETE` is the P3 soft-delete
hybrid). `TRACKING-DRIFT-SESSION-JOIN` is not an ergonomics row — it records the measured
`todo`↔`session` join drift found while verifying the review (see H.5).

**Serialisation — the real rule.** One writer at a time per shared file:
`models/node.py` (**2995 → 2996 → NODE-MOVE-TO 2977 → NODE-EDGE-HYBRID 2965**, all shipped);
`models/sample.py` (SAMPLE-RECORD-LATEST ✔ 2970 → SAMPLE-GAPFILL 2971); `models/__init__.py` +
top-level `__init__.py` + `docs/api/models.md` (UNIFIED-NODE ✔ 2967 → NODE-SOFTDELETE 2968);
top-level `__init__.py` again for GRAPH-FACADE 2969 → LIFECYCLE-SETUP-EXPORT 3021. The earlier
claim that "P2–P5 are file-disjoint" was **false**: P3's two rows share four files, P5's two rows
share `models/sample.py`, and three rows edit the top-level `__init__.py`.

`models/node.py` is now the worst contention point — four rows in sequence, and the two
`add_child` follow-ups discovered during NODE-ERGO (2995 widens the kwargs to the JSON-capable
alias, 2996 adds `using_db`) must land before `move_to` and `edge_model` so their signatures are
stable first. `_types.py` is repo-wide, so 2995 is gated on the **full** suite, not a subset.

**Size exception (recorded deliberately).** `NODE-ERGO` and the lifecycle row were each
split because they exceeded the single-session size guide — `move_to` alone carries the
locking/TOCTOU design, and `ensure_extensions`/`SetupReport` are separable from the
`setup()` fan-out. Every remaining row is one concern over ≤3 declared files.

### H.4 Deliberately out of scope

Nested-set / closure-table materialisation (adjacency + `RecursiveCTE` is the design),
pandas/DataFrame export (new dependency), sync wrappers (AGENTS.md rule 5), and **hard-delete**
cascade (the orphan policy is deliberate and documented — subtree soft delete is in scope as
`NODE-SOFTDELETE`).

### H.5 Review verdict and the corrections it forced (2026-11-22)

A `css-review` Track 2 (plan) + Track 3 (TODO batch) pass returned **HOLD — do not dispatch**
with 8 HIGH findings. All eight were re-verified against source before acting (AGENTS.md
"never trust an audit report's severity blindly") and all eight were real. Corrections applied
to this section and to the rows:

| # | Finding | Correction |
|---|---|---|
| H1 | The batch is orphaned: `todo.session_id` names a session absent from `session`, so the documented `todo`↔`session` join returns zero rows | Escalated to `TRACKING-DRIFT-SESSION-JOIN` (2979) — measured **1266** orphaned rows, and **no** `session` row exists after 2026-09-24, so this is wider than one batch. `css_todo_*` still resolves the owner; the raw-SQL bootstrap in AGENTS.md is the broken path |
| H2 | "P2–P5 are file-disjoint" was false (P3 pair shares 4 files, P5 pair shares `models/sample.py`, three rows edit the top-level `__init__.py`) | Replaced with an explicit one-writer-per-file serialisation rule (H.3) |
| H3 | D3 said an absent parent raises `GraphError`; the TODO said it becomes a root | Decided **promotion** (a `descendants()` slice must be renderable) — D3 and the row now agree, and the case is asserted |
| H4 | `move_to`'s stated shape never wrote the moved node's own `parent_id` | Split to `NODE-MOVE-TO` (2977) with the mandatory **two-statement** shape: self `UPDATE` then descendant bulk `UPDATE` |
| H5 | `root()` as "last of `path_to_root()`" returned self (`path_to_root` sorts ascending) | Fixed to index `[0]`, with an assertion |
| H6 | `setup()` ordered per-model DDL before `generate_schemas()` | Ordering fixed: extensions → `generate_schemas()` → per-model `setup()`; the row's acceptance test is the ordering proof |
| H7 | The import-order assertion inside `setup()` cannot fail, and named the wrong sentinel | Removed from `setup()`; the flag lives on `tortoise.filters`, and `tests/test_import_order.py:54-60` already covers the contract |
| H8 | Two of three named pytest paths in `GRAPH-FACADE` do not exist | Replaced with `tests/test_graph_integration.py` + `tests/test_graph_traversal_struct.py` |

Also folded in from the MEDIUM/LOW set: `add_child` must not double-bump `child_count`
(`NodeMixin.save()` already does); `move_to` needs a deterministic lock order and an explicit
descendant predicate (TOCTOU is documented, not hidden); `Tortoise.apps` can be `None` and
`get_models_iterable()` is the right accessor; `TimescaleConfig` is now defined by the row that
uses it; `record(at=None)` had no guaranteed behaviour (tortoise-orm 1.1.x has no `auto_now`);
one "mandatory start/end" error path was unreachable (`TypeError` first); `NODE-SOFTDELETE` now
covers the queryset path and states the `child_count` invariant instead of deferring it;
`DOCS-ERGO` gained five missing surfaces; `NODE-NAME-SPLIT` was set to `cancelled`.

**Design-level consequence worth naming:** the ltree `move_to` cannot be "mirrored exactly" —
an adjacency list has no path column, so the cycle guard must materialise the descendant set
from unlocked rows. That is a genuine TOCTOU window, documented in D2 rather than papered over.

**Also measured during verification:** AGENTS.md §Testing lists `tests/test_pathfinding.py` and
`tests/test_graph_traversal.py`; neither exists. Recorded in `DOCS-ERGO` for correction.

## I. External adoption assessment — triage (2026-11-23)

An unsolicited third-party review of the library arrived (adoption-oriented: "what would make
this the go-to foundation for hybrid RAG / GraphRAG"). It is recorded here with a verdict per
item, because roughly half of it is **already planned** in §H, one item is **factually wrong**,
and one is a **doctrine reversal that only the user can decide**. Nothing below is scheduled
until the scope question is answered.

### I.1 Already covered by §H (no new work)

| Assessment ask | Already |
|---|---|
| "clean registration API instead of silent import-time side effects" | `LIFECYCLE-EXTENSIONS` (2966) + `LIFECYCLE-SETUP` (2978) — `ensure_extensions()` + `setup()`. Note: a `register()` **cannot** replace the import-time patches; the patches must be live before any model class is imported (AGENTS.md rule 1), so `setup()` is additive, not a replacement |
| "patch applied / not-applied runtime checks" | partially `LIFECYCLE-SETUP`; the *guard-identity* half is now `PATCH-GUARD-IDENTITY` (2988) |
| "soft-delete + graph traversal consistency" | `NODE-SOFTDELETE` (2968) — subtree semantics + the `child_count` invariant |
| "batch-friendly traversal / pathfinding" | `GRAPH-FACADE` (2969) |
| "improve type stubs / pyright experience" | `STUBS-RUNTIME-DEP` (2991) — the overlay vs the PyPI stub package question |
| "documentation is the weak point" | `DOCS-ERGO` (2974), which already carries the five surfaces the Track 2/3 review found missing |
| "keep the Redis extra optional" | already true — `[project.optional-dependencies].redis` |
| "make Meta.indexes inheritance work or document it loudly" | needs a measurement first — see I.4 |

### I.2 Factually wrong or already true

- **"Deprecate old aliases (`BaseGraphNodeModel` → `BaseNode`)"** — the aliases are *already*
  documented deprecated (`models/graph_node.py:32`, `models/event_stream.py:78`). The genuine
  gap is narrower: they carry a `.. deprecated::` docstring but emit **no `DeprecationWarning`**,
  so nothing fails or warns at runtime. That is a small, real follow-up — see I.4.
- **"contribute VectorField/filters upstream so the extension shrinks"** — not a code task in
  this repo; it is a relationship/roadmap matter and cannot be scheduled as a TODO here.
- **"more comprehensive test suite against real Postgres + pgvector + Timescale"** — the suite
  already runs **1412 passed / 1 skipped** against live PostgreSQL 18.7 + pgvector 0.8.6 +
  TimescaleDB 2.29.2 + Redis (measured 2026-11-22). The gap is not coverage breadth.

### I.3 The one doctrine reversal — user decision only

**"Lower the Python floor to 3.11 or 3.12."** Verified: `requires-python = ">=3.14"` is this
library's own choice, **not** inherited — `tortoise-orm` itself declares `Requires-Python:
>=3.10`. So the assessment's premise is correct and the floor is technically movable.

But lowering it would reverse the repo's stated doctrine and break its tooling: PEP 695 type
parameters, bracketless `except`, t-strings and the `css-py314-devel` skill all assume 3.14, and
`[tool.basedpyright] pythonVersion` is pinned to 3.14. This is a **product decision, not a bug
fix** — it is not scheduled here and must not be inferred from the assessment.

**Decided 2026-11-23 (user ruling): keep `requires-python >= 3.14`.** The adoption cost is
accepted; PEP 695, t-strings, bracketless `except` and the 3.14 basedpyright pin all stay. Do
not reopen this from an adoption critique alone — it needs an explicit instruction.

### I.4 Genuinely new, not covered anywhere (candidate TODOs — NOT yet filed)

Ordered by value/cost. None of these is in the shared list.

1. **Fusion utilities** — reciprocal rank fusion (RRF), weighted score combination, diversity
   re-ranking, layered over `GraphVectorSearch` / `HybridSearch`. Small, self-contained, and the
   highest ratio of usefulness to risk on this list. No such code exists today (grep: no
   `reciprocal_rank` / `rrf` / `fusion`). → **FILED as `FUSION-UTILITIES` (2992).**
2. **Tortoise-version compatibility guard** — a clear error at import when the installed
   `tortoise-orm` is outside the tested range, instead of a silent patch miss. Pairs with 2988.
   → **FILED as `TORTOISE-VERSION-GUARD` (2993).**
3. **Runtime `DeprecationWarning` on the alias bases** — completes the deprecation the
   docstrings already announce. **Caveat measured 2026-11-23:** `models/graph_node.py:34-37`
   explicitly states the alias "emits no runtime warning" — this is a *documented decision*, so
   changing it is a deliberate reversal, not a fix. → **FILED as `ALIAS-DEPRECATION-WARNING`
   (2994), low priority.**
4. ~~**`Meta.indexes` inheritance**~~ — **already solved, no TODO.** Measured 2026-11-23:
   `NodeMixin.__init_subclass__` (`models/node.py:146-170`) raises `NotImplementedError` at
   class-creation time when a concrete subclass omits `Meta.indexes`, with a message naming the
   re-declaration requirement. That *is* the "document it loudly" ask, enforced rather than
   described.
5. **Batch traversal API** — multi-source/multi-target traversal in one round trip. (Deferred.)
6. **Benchmarks** — `GraphVectorSearch` / `HybridSearch` / traversal vs pure SQL, with HNSW
   tuning notes (m, ef_construction, ef_search, rebuild thresholds). (Deferred.)
7. **Observability hooks** — query logging and slow-graph-expansion warnings. (Deferred.)
8. **Embedding-library thin helpers** — no hard dependency. (Deferred.)
9. **Worked examples** — vector-only, hybrid vector + 1-hop, and a full lightweight GraphRAG
   (entities + relations + optional communities). (Deferred.)
10. **Entity resolution / dedup** (exact + embedding + optional LLM confirm) and
    **community detection** (Leiden / k-core integration points) + a `CommunityReport` model.
    This is the largest item by an order of magnitude and is arguably a **separate library**;
    it should not be smuggled into an ergonomics release. (Deferred, deliberately.)
11. **Contribution guide + public roadmap**. (Deferred.)
12. **HNSW knob picker** (`hnsw_tuning`) — pure computation beside `HNSWIndex`, no UI: inputs are
    row count / dimensions / metric / recall bias; outputs `m` (pgvector's 2–100, shrinking as the
    table grows so the graph does not balloon), `ef_construction` (≥ 2×`m`, clamped 4–1000),
    `ef_search` (a **session** setting on the querying connection, not part of the index, ≥ k), a
    storage split (float32 vectors vs graph overhead ≈ `n × m × 8` bytes), the `CREATE INDEX`
    DDL and the equivalent `HNSWIndex(...)` snippet, and an explicit "a sequential scan is
    probably faster" verdict below a row threshold. Overlaps item 6 (documentation of the same
    three knobs) but is executable. **Parked 2026-11-23 by user choice — no TODO filed.**

**Scope ruling 2026-11-23 (user):** items 1–3 filed; item 4 closed as already-solved; items
5–11 deferred until the §H ergonomics build-out ships. Item 12 was described by the user later
the same day and parked under the same ruling (plan-only, not filed).

### I.5 Reconciliation with the v0.2.0 task prompt (2026-11-23)

The user then delivered a standalone "Release v0.2.0 — Fusion Utilities + Correctness Fixes"
prompt. It covers the same three items, so nothing new was filed; three points where it
contradicts the repo were settled by measurement and an explicit ruling:

| Prompt says | Repo says | Ruling |
|---|---|---|
| "Full type hints (`from __future__ import annotations`)" | `css-py314-devel` bans it; §I.3 keeps PEP 695; `src/` has **zero** occurrences (only 3 test files) | **Repo doctrine** — no future import; PEP 695 + native annotations. Deferred evaluation is already the 3.14 default, so the import is a no-op anyway |
| `diversity_rerank` = "MMR-style or greedy … optional similarity callable" | Row 2992 had filed a deterministic group-cap and excluded MMR | **Full MMR with a *required* `similarity` callable** — user ruling. Signature carries `lambda_`, deterministic tie-breaks, `O(n²k)` documented; no embedding dependency |
| `weighted_score_fusion(..., normalization=Literal["minmax","zscore","none"], weights=None)` | 2992 had `weighted_scores(rankings, weights, normalise=bool)` | **Prompt signature adopted** — strict superset; row 2992 rewritten |
| "Deprecate old aliases (`BaseGraphNodeModel` → `BaseNode`)" + names `BaseGraphEdgeModel` | `BaseGraphEdgeModel` is a **live** class, not an alias (`models/graph_edge.py:130`); the deprecated pair is `BaseGraphNodeModel` + `BaseEventStreamModel` | Corrected — never warn on `BaseGraphEdgeModel` |
| "Investigate `Meta.indexes` inheritance thoroughly … strengthen docs + test" | Already enforced at class-creation in **three** bases (`node.py:144`, `graph_edge.py:130`, `hierarchy_model.py:185`), tested (`test_node_mixin.py:135,145,152` incl. the `Meta.indexes = ()` opt-out), documented (README:138-139) | **Closed, zero work** — wider than §I.4 item 4 recorded (which cited only `node.py`) |
| `packaging` for the version guard | In `uv.lock` **transitively only**, not declared | **Manual parse** — an undeclared dep would violate "zero new runtime dependencies". `tortoise.__version__` measured real (`1.1.9`) |
| "Release v0.2.0" | `pyproject.toml` is `0.1.0` | **No bump this batch** — user ruling; versioning/tagging stays separate |

`BaseGraphNodeModel`/`BaseEventStreamModel` have ~15 subclasses across 8 test files; because
`filterwarnings = ["error"]`, a `DeprecationWarning` from `__init_subclass__` fires at
**collection** time, so 2994's real cost is a test migration, not a two-line `warnings.warn`.
User confirmed proceeding anyway (the documented "emits no runtime warning" decision is
deliberately reversed).

**Shipped 2026-11-23** — items 1 and 3 of §I.4 are done, item 2 in flight:

| Row | Commit | What landed |
|---|---|---|
| `FUSION-UTILITIES` (2992) | `38c72f9` | `expressions/fusion.py` — RRF, weighted fusion (minmax/zscore/none), greedy MMR with a caller-supplied similarity callable, and two adapters (`ranking_from_hits`, `scores_from_hybrid_rows`); exported from both `__all__`s; 45 tests; `docs/api/fusion.md` |
| `ALIAS-DEPRECATION-WARNING` (2994) | `eeebe39` | `DeprecationWarning` from `__init_subclass__` at `stacklevel=3` on both aliases; 13 test classes migrated to `BaseNode`/`BaseSample`, 2 kept on the aliases under explicit `pytest.warns` |
| `TORTOISE-VERSION-GUARD` (2993) | `a710b82` | `_check_tortoise_version()` runs before the first patch; manual version parse (no `packaging`); `TortoiseVersionError` / `PatchConflictError`; identity guards on `VectorField` + the three index types — folded with `PATCH-GUARD-IDENTITY` (2988) in one pass over `__init__.py` |

**v0.2.0 is fully shipped.** Gate at `a710b82`: ruff clean, basedpyright **0 errors / 1 warning**,
full suite **1526 passed, 1 skipped**. Two defects found by planner review rather than by the
workers: `diversity_rerank` contradicted its own docstring at `lambda_=0.0` (first pick was input
order, not max relevance), and the fusion module initially introduced four new basedpyright
warnings, which AGENTS.md forbids.

**Remaining from §I.4 is item 2's sibling**: the `2980` cagg injection fix (HIGH) ships `pglast`
as a hard dependency — decided 2026-11-23, with the cp314 manylinux wheel measured as real. Its
validator replacement is the last security item of the review batch.

### I.6 Production-readiness slice — prompt reconciliation (2026-11-23)

A second external prompt ("production-readiness slice") arrived written against a stale snapshot.
Nine of its claims contradict the tree; each was measured, then either settled by best practice or
put to the user. The rule the user set for this pass: **do not follow the prompt blindly — ask, or
apply production-readiness judgement.**

| # | Prompt claim | Reality | Resolution |
|---|---|---|---|
| 1 | `from __future__ import annotations` on every new module | `src/` has zero; PEP 649 makes annotations lazy on 3.14 by default | **Rejected** — the import would re-stringify and is a no-op for the gate. User: "deprecated, we don't use that" |
| 2 | "gate of roughly 1010 passed, 1 skipped" | Actual 1538 / 1 | Use the measured count; AGENTS.md forbids stale numbers |
| 3 | Add `.github/workflows/ci.yml` (H1) | No `.github/` | **Won't fix** — user: "wont be executed on github". Local CLI gate is the accepted control |
| 4 | New runtime dependencies out of scope | `pglast` added as a hard dep for 2980 | Accepted by prior ruling; pin **bounded `<9`** because the code depends on its AST |
| 5 | "Fusion does not exist — do not add" | Shipped `38c72f9` | Read as "add no more" |
| 6 | "`UnifiedIdModel` is still todo" | Exists (`models/unified.py:51`), exported, documented | Stale |
| 7 | "`bulk_copy` is private to `BaseEventStreamModel`" | `bulk_insert` is public on `SampleMixin`; the event-stream base is a deprecated alias | plan.md entry corrected (`a949927`) |
| 8 | A subclass omitting `Meta.indexes` shows them "absent" | The base **raises `NotImplementedError`** at class creation; `indexes = ()` opts out | Test the raise, not absence |
| 9 | H4: codec "only at import" | `AsyncpgDBClient.create_pool` is already patched per connection | H4 structurally satisfied; a proof test remains |

**Decisions recorded:**

- **CI — won't fix.** The repo is not executed on GitHub; building a workflow is dead work. Row
  3002 cancelled. The Dockerfile compiles pgvector + TimescaleDB from source (15–25 min first
  build), which is why a full-stack CI was never cheap.
- **`pglast` pinned `>=8.9,<9`** — the validator depends on `SelectStmt`, `RangeVar`,
  `referenced_relations` and scanner token names; an unbounded pin lets a parser major break the
  security fix silently. AGENTS.md §Dependencies updated (it had been missed).
- **Docker pins repinned** to full SHAs: pgvector `159b79aa…`, TimescaleDB `4a77ff5c…`. Versions
  unchanged; base image digest untouched; image build is the verification step and was not run.
- **M2 (opclass/filter mismatch)** — document + consistency test (user ruling), not document-only.
  `docs/api/indexes.md` now carries the operator column and the silent-seq-scan warning; a test
  keeps the table honest (row 3009).
- **H3 (soft-delete vs CTE)** — auto-filter when `deleted_at` is in `_meta.fields_map`, in both
  the anchor and recursive terms, for nodes and edges; no new flag. Models without the column
  must emit byte-identical SQL.
- **No version bump** — 0.1.0 is untagged and unreleased, so a behaviour change inside it needs a
  CHANGELOG entry, not a bump.

**Shipped 2026-11-23 (this slice so far):**

| Commit | What |
|---|---|
| `aa6baf1` | 2980 — cagg validator rewritten on `pglast`; +12 tests; `pglast>=8.9` added |
| `d67324b` | pglast bound `<9`; AGENTS.md §Dependencies corrected |
| `0c89953` | pgvector / TimescaleDB commit pins refreshed (full SHAs) |
| `a949927` | plan.md `bulk_insert` entry corrected |
| `456c018` | README import-order/patching section; `Meta.indexes` names all three bases; `docs/api/indexes.md` operator table |
| `e7b8de3` | `CHANGELOG.md` + `CONTRIBUTING.md` |

Gate after `aa6baf1`: ruff clean, basedpyright **0 errors / 1 warning**, **1538 passed, 1
skipped**.

**Slice complete 2026-11-23** — the remaining rows landed:

| Commit | Row | What |
|---|---|---|
| `028e72b` | 3006, 3007, 3009 | codec freshness proof (H4 **confirmed**: a post-patch pool decodes `vector` as `list[float]`), edge-base index guard test, opclass/filter doc-consistency test |
| `fb696aa` | 3003 | soft-delete predicate in all three CTE builders; 56 captured statements diffed byte-identical for models without `deleted_at`; 10 regression tests |
| `2a0ea53` | 3008 | mixed-case `source_table` matches by PostgreSQL folding (the regression `aa6baf1` introduced) |

Two of the tester's tests were **wrong before they were right**: `Comparator` members are enums
(`.value`, not `.strip()`), and `AsyncpgDBClient` needs `connection_name` positionally. Both
failed before reaching their assertions, so the codec proof had been vacuous until fixed.

**H3 behaviour choice (one sentence):** traversal, pathfinding and `GraphVectorSearch` exclude
rows whose model declares `deleted_at`, in both the anchor and the recursive term, for nodes and
edges alike; models without the column generate byte-identical SQL.

**Final gate:** ruff clean, basedpyright **0 errors / 1 warning**, **1563 passed, 1 skipped**.

**H1 is closed as won't-fix** (row 3002 cancelled): the repository is not executed on GitHub, so a
workflow would never run. The local CLI gate is the accepted control.

### I.7 PostgreSQL-only — SQLite removal (ruling 2026-11-23)

**User ruling:** *"everything sqlite in tortoise-extended can be removed. everything that is not
postgres can be removed."* The package advertises PostgreSQL graph/vector capabilities and has no
shipped SQLite code path, so the SQLite harness was a testing artefact that let modules pass
without exercising the backend the library actually targets.

**Measured before any edit:** `grep -rn -i sqlite src/` finds **zero code branches** — only
docstrings (`fields/uuid.py`, `fields/ipv4.py`, `fields/char.py`, `indexes/_dialect.py`,
`models/node.py`) and the typing overlay. So this was never a `src/` refactor: it is the test
harness, the docstrings and the policy text.

**Inventory correction.** The first pass counted 14 affected test modules; three never open a
connection and are *kept*: `test_hnsw_index.py` and `test_indexes.py` use a fake schema generator
with `DIALECT = "sqlite"` to assert the PostgreSQL-only guard, and `test_pgcrypto.py` is a
PostgreSQL module whose only mention is a docstring note. Deleting them would delete the guard
they pin.

**Deletion, not rework.** Anything that only worked on SQLite is deleted, never skipped
(`test_sqlite_blob_roundtrip_decodes_floats`, `test_sqlite_sql_type_is_int`). The remaining
modules are re-pointed to the Docker database using the established integration-module pattern:
module-level `DB_URL` (env override `TORTOISE_TEST_DB`), a `_pg_available()` socket probe behind
`pytestmark = skipif`, `DROP TABLE ... CASCADE` at the module boundary and `TRUNCATE` between
tests — because `sqlite://:memory:` used to provide isolation and teardown for free.

**Scope split (rows):** 2962 umbrella + policy; 3015 cache cluster (4 modules); 3016 graph
cluster (3 modules + 1 deleted test); 3017 models cluster (4 modules + 1 deleted test).

**Consequences:** the suite now needs the Docker stack for every database-backed module, and the
`redis` extra must be installed for `basedpyright` to see a clean `src/` (an un-synced venv
produces three spurious `cache/redis.py` errors). AGENTS.md §Testing, `docs/docker/setup.md`,
`docs/guides/llm-reference.md`, `docs/api/fields.md` and the affected docstrings were corrected
in the same pass.

**Round-2 review (2026-11-23) — verdict and what survived verification.** A `css-reviewer` pass over
`versioned.py` and `distinct_on.py` produced 18 findings; three did not survive measurement and are
recorded here so nobody re-files them:
- *"`distinct_on` hydrates with column names `_init_from_db` rejects"* — the opposite is true.
  `MetaInfo.db_native_fields` is `(db_column, model_field, field)` and `_init_from_db` does
  `_setattr(self, model_field, kwargs[db_column])`, so the helper's `SELECT "col" AS "col"` is the
  exact shape it wants. Measured with a `source_field` model (`sensor = CharField(source_field=
  "sensor_col")`): both fields populated, no stray attributes, the instance saves. The citation of
  `timescale/stream.py:_row_to_model_kwargs` was inverted — that helper maps column → field name and
  its own comment about "mirroring `_init_from_db`" is the questionable one.
- *"a transposed `order_by` reaches PostgreSQL and errors"* — PostgreSQL 18.7 accepts it:
  `DISTINCT ON (a,b) … ORDER BY b, a` returns rows. Measured what the server actually enforces: it
  rejects only when the DISTINCT ON expressions are absent from the ORDER BY list
  (`DISTINCT ON (a) … ORDER BY b` → "must match initial ORDER BY expressions"), and it also accepts
  a strict subset (`DISTINCT ON (a,b) … ORDER BY a`) and no ORDER BY at all. The helper's set
  comparison is therefore correct and actually stricter than the server.
- *"a partial instance without the pk raises `AttributeError` before `IncompleteInstanceError`"* —
  it raises the documented `IncompleteInstanceError`, because `self.pk` is `None` for such an
  instance and the insert branch delegates to upstream's own check. Measured.

What did survive, one TODO each (3066-3079): an unsaved-but-pk'd instance taking the guarded UPDATE
with a defaulted version 0 (measured: the write goes through, other columns untouched — the review's
"every projected field" claim was wrong, but the lock is honest only by luck); `_pre_save` mutating
`auto_now` on the instance before a refused save, contradicting `VersionMismatchError`'s docstring;
`update_fields` accepting an unknown name (`KeyError`) and `[]` (a version-only bump, measured);
`distinct_on(Model.all())` refused on soft-delete models although the docs name that form; the
quote-blind placeholder scan (`where="title <> '$1'"` is refused, measured); `_QUERYSET_CARRIED`
missing `select_for_update`/`distinct`/`prefetch_related`/`only`; `limit=True` reaching the server as
`LIMIT true` (measured `OperationalError`); plus five LOW items (FK payload path, target-type check,
generator `update_fields`, transaction/rollback doc, `use_tz`, `deleted_at` by column, psycopg
backend).

**Round 3 (3066-3071, `7ea80d0`).** The six MEDIUMs the review left, fixed with the user's four
contract decisions taken in one batched question round (all six as one round; 3066 raises instead of
documenting; `update_fields=[]` means "all fields"; the soft-delete target is stripped rather than
dropped from the docs). Both `css-developer` dispatches died on the account-wide quota after writing
their code but before reporting, and the gate was clean on inspection: ruff clean, basedpyright
**0 errors, 0 warnings, 0 notes**, full suite **1922 passed, 13 skipped** — the round changed no
behaviour the 40 existing round-2 cases cover. Verified by probe on `tortoise_test`: unsaved-but-pk'd
save refused with `force_update=True` as the escape hatch; `auto_now` unchanged after a mismatch;
`[]` bumping 1 -> 2 with the rest of the row intact; unknown/M2M `update_fields` both
`OperationalError`; generator accepted; `SDoc.all()` accepted with 1 live row vs 2 with
`include_deleted`; `'$1'`, `$$ $1 $$` and `/* $1 */` accepted while `$0`/`$01` are refused by name;
`select_for_update()`/`distinct()`/`only()` refused. Two doc corrections that came out of measuring:
`order_by` "must lead with the `on` columns" is *this helper's* rule (PostgreSQL 18.7 in fact accepts
a superset — see the round-2 note), and a `--` comment in `where` swallows the `ORDER BY` the helper
appends after it, which the docs now say.
