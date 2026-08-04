# tortoise-extended — Roadmap & Design Notes

> Internal planning document. "Belongs here" = fills a gap in Tortoise ORM
> 1.1.7 that tortoise-extended can supply within its PostgreSQL
> graph/vector/timescale/redis scope. Statuses: `todo` / `design` / `wip` /
> `done`.

## A. Roadmap — Tortoise gaps we can fill

### Tier 1 — model primitives (low risk, no new deps) — `todo`
1. **`BaseModel(models.Model)`** — `id = BigIntField(primary_key=True)` on an
   abstract base. Simple, one class. Tortoise requires manual pk declaration
   on every model; `GraphNode`/`HierarchyModel` already use BigInt pk —
   unify. Models that need a globally-unique id (cross-table refs) opt in by
   declaring `id = UUID7Field(pk=True)` themselves (Tier 1b).
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

**G15 reconciliation — pk strategy decided (2026-08-04):**
- **`GraphNode` stays `UUIDField` (uuid4, upgraded to `UUID7Field` in
  Tier-1b).** Graph edges are long-lived cross-table references; UUID is the
  unified ID space for the graph subsystem.
- **`HierarchyModel` stays `BigIntField`.** Tree nodes are internal to one
  table; BigInt FKs JOIN faster and `EventStreamMixin` keeps its composite
  pk. `HierarchyModel` nodes may opt into the shared ID space by declaring
  `id = UUID7Field(pk=True)` in the concrete subclass.
- **Constraint (documented): `GraphEdge.source_id`/`target_id` are UUID and
  therefore only link `GraphNode`-family nodes.** They cannot directly
  reference a BigInt-pk `HierarchyModel`. If an edge table must span both
  families, use `UUID7Field(pk=True)` on the hierarchy subclass (or a
  polymorphic `ref_id: UUID7Field` + `ref_type: CharField` pair), not a
  shared edge type.
- Tier-1b (`UUID7Field` + `GraphNode`/`GraphEdge` uuid4→uuid7 upgrade) is
  therefore unblocked; §B `FileNode` uses `UUID7Field(pk=True)`.

```python
class UUID7Field(fields.UUIDField):
    def __init__(self, **kw) -> None:
        super().__init__(default=uuid.uuid7, **kw)  # py3.14 fallback
        # PG18: db_default=SqlDefault("uuidv7()") on postgres backend
```

**One base, one opt-in field (keep it simple):**
- `BaseModel` — `id = BigIntField(primary_key=True)`, abstract. Default for
  every model; JOIN-fast ints for internal FKs.
- `UUID7Field` — opt-in for models that must participate in cross-table
  references (graph nodes, polymorphic refs): declare
  `id = UUID7Field(pk=True)` in the subclass. No second base class.

**Upgrades:** `GraphNode`/`GraphEdge` `UUIDField` default `uuid4` → `uuid7`
(drop-in, better locality). `FileNode` (§B) uses `UUID7BaseModel` so
`FileLink` edges remain unambiguous and its metadata cache key becomes
`fs:{ns}:node:{uuid7}` (rename-safe; path = alias, not identity).

**Boundaries:**
- Don't force UUID7 everywhere — BigInt FKs JOIN faster; `EventStreamMixin`
  keeps its composite pk (`stream_id`, `time_field`).
- Polymorphic refs (audit/notifications "on any object"): `ref_id:
  UUID7Field` + `ref_type: CharField` pair; optional `PolymorphicRef`
  msgspec helper (Tier 4).
- SQLite fallback (uuid7 stored as string) keeps non-PG tests green.
- New roadmap item: **Tier 1b — `UUID7Field` + graph uuid4→uuid7 upgrade**
  (sits between Tier 1 and Tier 2; §B `FileNode` uses `UUID7Field` when it
  needs cross-table edge targets).

---

## G. Audit findings — 2026-08-04

Method: 4 parallel auditors (devel-tortoise-db-engineer, python-code-reviewer,
python-developer, rubber-duck) reviewed every module + ran the verification
gate (`ruff` clean, `basedpyright` 0/0/0, 664 passed / 1 skipped) and did
mandatory online research (tortoise 1.1.7, pgvector 0.8.x, TimescaleDB 2.x,
redis-py 8, Python 3.14). Findings cross-validated; severity = max across
auditors. One auditor finding rejected (bracket syntax — see DISPUTED).

| ID | Sev | Location | Finding | Fix |
|----|-----|----------|---------|-----|
| G1 | ✅ FIXED | `cache/decorators.py:42` | `@cached` key collision for plain functions: `args[1:]` drops the first positional arg → `foo(1)`/`foo(2)` share a key, wrong cached data served. Bound methods safe by accident. Tests only cover methods. | Key on full `args`; regression tests `TestCachedKeyCollision` (2 cases) added |
| G2 | ✅ FIXED | `cache/model.py` | `CacheableModel` invalidation hardcodes `id=str(self.pk)` while `get_cached(**kwargs)` keys on caller kwargs → stale cache for any model whose pk isn't named `id`. Also `get_cached`/`filter_cached`/`delete_cached` share one key space and `filter_cached` ignores order/limit. | Key from `model._meta.pk_attr`; namespace by op (`get:`/`filter:`) + order/limit in filter keys |
| G3 | ✅ FIXED | `graph/hierarchy_model.py`, `graph/edge.py` | Abstract `Meta.indexes` do **not** propagate to concrete subclasses (empirically confirmed `Child._meta.indexes == ()`) → `HierarchyModel` GiST(path) and `GraphEdge` composite indexes are never created; ltree + edge queries lose indexes. | `__init_subclass__` guard raises `NotImplementedError` at import time unless concrete subclasses redeclare `Meta.indexes` (explicit `indexes = ()` opts out); abstract subclasses exempt; test subclasses + docs redeclared; 5 unit tests added |
| G4 | ✅ FIXED | `expressions/graph_filters.py` | Bare `filter(embedding=<vec>)` maps to `IS NULL` via `get_vector_filters` → silent wrong results for non-None values. | `_vector_eq_guard` raises `VectorFieldError` for any non-None bare value (Tortoise redirects `None` → `__isnull` first); +8 regression tests (2 unit, 3 SQLite BLOB integration, 3 live-PG) |
| G5 | ✅ FIXED | `graph/hierarchy_model.py` | `get_ancestors`/`get_descendants` omit the `namespace` filter (unlike `get_path_to_root`/`get_root`) → cross-tenant data leak in multi-tenant trees. | Added `namespace: str \| None = None` param (defaults to instance namespace) to both; 3 PG regression tests added |
| G6 | ✅ FIXED | `graph/hierarchy_model.py` | `move_to` is non-atomic (row-by-row UPDATEs, no `in_transaction`), N+1, and self-move (`new_parent=self`) bypasses the cycle guard → partial-tree corruption on mid-cascade failure. | Wrapped in `in_transaction()`; descendant cascade is one bulk UPDATE (`_PrefixReplace` prefix-only rewrite + `F("depth") + delta`, namespace-scoped); self-move guard; 3 PG regression tests added |
| G7 | ✅ FIXED | `timescale/hypertable.py`, `compression.py`, `retention.py`, `continuous_aggregate.py` | Table/interval names interpolated into SQL at 24+ sites, unquoted/unescaped; internal `_timescaledb_catalog`/`_timescaledb_config` tables queried instead of public `timescaledb_information` views. | Shared `_quote.py` helpers (`quote_ident`/`quote_literal`) extracted from `migrations/operations.py`; all interpolated names quoted/escaped; `is_hypertable` → `timescaledb_information.hypertables`, `get_stats` → `timescaledb_information.chunks` (`is_compressed`), `list_policies` → `timescaledb_information.jobs`; +9 unit tests, live-PG suite green. Also folded in the G19 division-by-zero guard |
| G8 | ✅ FIXED | `indexes/hnsw_index.py`, `ltree_index.py` | HNSW/IVFFlat/GiST emit PG DDL on **any** backend → SQLite `generate_schemas` breaks; no dialect guard. | `indexes/_dialect.py` `assert_postgres_dialect` checks `schema_generator.DIALECT` and raises `IndexDefinitionError` on non-PG (getattr default `"postgres"` keeps test fakes working); +5 unit tests |
| G9 | ✅ FIXED | `timescale/stream.py` | `bulk_insert` (COPY) does not populate `auto_now_add` fields; unquoted identifiers in DDL; only the PK caveat is documented. | `auto_now_add`/`auto_now` confirmed populated via `DatetimeField.to_db_value` (instance passed through) and documented + regression-tested; `db_default`-only columns now omitted from COPY when unset on all instances (mixed usage → `OperationalError`, mirroring `bulk_create`); identifiers quoted via `_quote.py` in `setup()` DDL and `latest_per_stream`/`time_series`; stub overlay gained `has_db_default`/`get_db_default_value`; +3 live-PG tests |
| G10 | ✅ FIXED | `migrations/operations.py` | `_patch_format_operation` swallows ALL `ValueError`s from the original formatter → masks real errors as `MigrationOperationError`. | Re-raise non-serialization errors — only the stock writer's terminal `"Unsupported operation type"` ValueError routes to the fallback serializer; render-helper ValueErrors (e.g. unserializable lambda in `SQLOperation`) propagate; +1 unit test |
| G11 | ✅ FIXED | `AGENTS.md`, `doc/architecture/overview.md`, `design-decisions.md` | Docs claim `OperationGenerator.generate` is patched — **it is not** (only `MigrationWriter._format_operation`). | False patch claims removed from `overview.md`/`design-decisions.md`/README (commit `dde6836`); AGENTS.md mention only describes tortoise-embeddings' own patch surface |
| G12 | ✅ FIXED | README + `doc/architecture/design-decisions.md`, `graph-traversal.md`, `recursive-cte.md`, `api/cache.md` | Benchmark claims (22,581 RPS, 290x, 4ms, 100K-sec) have no benchmark harness/evidence in repo. | Added `benchmarks/bench_graph_traversal.py` — reproducible harness measuring 0/1/3-hop recursive-CTE retrieval on docker PG; all doc/README tables marked **illustrative** with harness pointer; AGE/Neo4j comparison rows explicitly flagged as non-reproducible; IVFFlat lists table attributed to pgvector docs |
| G13 | ✅ FIXED | `timescale/compression.py` | `add_compression_policy`/`compress_chunk`/`decompress_chunk` deprecated since TimescaleDB 2.18 (→ `add_columnstore_policy`/`convert_to_columnstore`/`convert_to_rowstore`). | Migrated to 2.18+ columnstore API: `timescaledb.enable_columnstore` reloption, `CALL add_columnstore_policy(hypertable, after => INTERVAL, if_not_exists)`, `CALL remove_columnstore_policy`, `CALL convert_to_columnstore`/`convert_to_rowstore`, `hypertable_columnstore_stats` for `get_stats`; method names kept stable; verified live against docker TimescaleDB 2.28.3; +1 unit test (deprecation-free SQL assertions) |
| G14 | ✅ FIXED | `doc/guides/migration.md` | Guide steers to aerich, but code patches built-in `tortoise.migrations` writer (aerich = legacy). | Migration guides/api rewritten around the built-in `python -m tortoise` CLI; all Aerich references removed repo-wide (commit `dde6836`) |
| G15 | ✅ FIXED | `plan.md` §A/§F | Tier-1 premise wrong: `GraphNode` uses `UUIDField` (not BigInt); `GraphEdge` (UUID source/target) **cannot** link `HierarchyModel` (BigInt) nodes. §F/Tier-1b must first reconcile the pk split. | Pk strategy decided (2026-08-04): `GraphNode` stays UUID (uuid4→uuid7 in Tier-1b); `HierarchyModel` stays BigInt with `UUID7Field(pk=True)` opt-in per subclass; GraphEdge links GraphNode-family only — cross-family edges require the polymorphic `ref_id`+`ref_type` pair, not a shared edge type. Documented in §F |
| G16 | ✅ FIXED | `fields/vector_field.py` | SQLite BLOB round-trip: `to_python_value(bytes)` → `list(bytes)` of ints (memoryview path OK). | `bytes` now decoded via the shared pgvector binary layout (`_decode_binary`), same as memoryview; +1 SQLite round-trip test (`struct.pack`ed header+floats decode to `[0.25, 0.75]`) |
| G17 | ✅ FIXED | `fields/ltree_field.py` | Typed `Field[str]` but returns `list[str]`; `max_length`/`separator` unused in DDL. | Generic fixed to `Field[list[str]]`; `max_length` is now a real Python-side guard in `to_db_value` (ltree DDL has no length modifier — over-long paths raise `ValueError`); `separator` documented as used; +2 unit tests |
| G18 | ✅ FIXED | `__init__.py` codec | `set_type_codec(..., schema="public")` — extension in a non-public schema silently skipped. | `_pgvector_codec_init` probes `pg_type`/`pg_namespace` via `conn.fetchval` when available; falls back to `"public"` on probe failure; codec errors still swallowed. +2 unit tests (non-public schema resolution, probe-failure fallback) |
| G19 | ✅ FIXED | `timescale/hypertable.py` | `get_stats` divides by `after_compression_total_bytes` (0 → DB error); `number_partitions` power-of-2 unvalidated. | Division guard folded into G7; partition validation still pending |
| G20 | ✅ FIXED | `expressions/graph_filters.py` | Compound `[vector, threshold]` detection is an `isinstance(value[0], list)` heuristic; thresholds silently default (1.0/0.0). | `_parse_vector_threshold` validates the threshold slot: compound values whose second element is not a number (e.g. `[[v1], [v2]]` two-vector mistake, bools) raise `VectorFieldError` with the shape spelled out; plain vectors keep documented defaults; +5 unit tests |
| G21 | ✅ FIXED | `timescale/*.py` | 7 `print()` call sites in library code instead of logging. | Resolved with no code change (2026-08-04): all `print()` sites live inside docstring `Example::` blocks — not executable code |
| G22 | ✅ FIXED | docs (3 files) | `pip install/uninstall` at 8 sites vs uv-only rule. | All `pip install`/`pip uninstall` replaced with `uv add`/`uv remove` in `doc/guides/migration.md`, `doc/getting-started/installation.md`, `doc/api/cache.md` (pip-only install section dropped) |
| G23 | ✅ FIXED | `timescale/continuous_aggregate.py` | `create(query=...)` interpolates caller SQL verbatim into `CREATE MATERIALIZED VIEW ... AS {query}` — documented but unvalidated. | `ContinuousAggregateManager.create` rejects bare `query` containing `;` or not starting with `SELECT`/`WITH` → `ValueError`; full `CREATE MATERIALIZED VIEW` passthrough unchanged; docstring warning. +3 unit tests |
| G24 | ✅ FIXED | `cache/redis.py` | `pool.close()` vs redis-py ≥8 `aclose()`; real-Redis path untested (tests use mock). | `close()` prefers `aclose()`, falls back to deprecated `close()`; backend `set` uses modern `SET key value PX` (avoids deprecated `setex()` warning under `-W error`); +1 unit test (aclose preference), +1 docker-gated live-Redis smoke test on 6380 |
| G25 | ✅ FIXED | README | README says "428 tests" (actual 664), references deleted `.env.example`, and lists `backends/` package that doesn't exist. | README test count synced (720); `.env.example` restored (still gitignore-whitelisted — dropped accidentally in `ac07176`); `backends/` removed from architecture tree; `graph_vector_search.py` + `stream.py` added to tree |
| G26 | ✅ FIXED | packaging | Wheel ships stray `stubs/aiodocker/` third-party `.pyi`; README feature list omits `GraphVectorSearch` + `EventStreamMixin`. | `stubs/aiodocker/` deleted (css_mcp leftover, zero references in repo); README feature list adds `GraphVectorSearch` + `EventStreamMixin` |
| G27 | ✅ FIXED | `expressions/graph_traversal.py`, `pathfinding.py` | `_et_clause` helper duplicated verbatim. | Shared `et_clause` extracted to `expressions/_edge_filter.py`; all three callers (`graph_traversal`, `pathfinding`, `graph_vector_search`) import it |
| G28 | ✅ FIXED | whole repo | `orjson`/`ciso8601`/`uvloop` auto-use claims have **zero** usage anywhere — remove claims (AGENTS.md lists them). | Resolved with no code change (2026-08-04): zero claims in repo AGENTS.md and zero usage in `src/` — claims existed only in agent-prompt boilerplate |
| G29 | INFO | roadmap | Python 3.14 offers `uuid.uuid7`, `asyncio.timeout`, `TaskGroup`; planned auth kit (`scrypt`/`pbkdf2_hmac`) must run in `asyncio.to_thread` (sync CPU-bound blocks the loop). | Fold into Tier 1b/4 implementation |
| G30 | ✅ FIXED | infra | Docker PG (5433)/Redis (6380) down during audit → `test_pg_integration.py` unrun; the 664-pass suite is SQLite-backed only. | Docker stack started; `tests/test_pg_integration.py` now runs and passes (40 tests) against live PG 18 + pgvector + TimescaleDB on 5433; live-Redis smoke test added and passing on 6380 (2026-08-04) |
| DISPUTED | — | `doc/guides/migration.md:60` | One auditor claimed `=[[query_vec], 0.5]` is wrong. **Rejected:** tests (`test_pg_integration.py`) and runtime (`graph_filters.py:183` `isinstance(value[0], list)`) both require `[[vec], threshold]`. | No change |

### Roadmap impact
- **Cache fixes (G1, G2): DONE 2026-08-04** — data-correctness bugs fixed,
  +4 regression tests, gate green (668 passed / 1 skipped, ruff clean,
  basedpyright 0/0/0). `filter_cached` still ignores order/limit — doc note
  only (API takes no ordering args).
- **Hierarchy fixes (G3, G5, G6): DONE 2026-08-04** — `__init_subclass__`
  index-redeclaration guard, namespace-scoped ancestor/descendant queries,
  atomic single-UPDATE `move_to`; +11 regression tests, full gate green
  including live-PG hierarchy suite (docker up).
- **§F/Tier-1b unblocked by G15 (2026-08-04)**: pk strategy decided —
  GraphNode stays UUID (uuid4→uuid7 in Tier-1b), HierarchyModel stays BigInt
  with per-subclass `UUID7Field(pk=True)` opt-in; GraphEdge only links
  GraphNode-family nodes (cross-family edges use polymorphic
  `ref_id`+`ref_type`).
- **Timescale 2.18 API drift (G13)** and **built-in migrations (G14)** change
  §B/C/D and migration docs before implementation.
- **Vector bare-value guard (G4): DONE 2026-08-04** — bare non-None
  `filter(embedding=<vec>)` now raises `VectorFieldError` instead of
  silently compiling to `IS NULL`; +8 regression tests (unit + SQLite BLOB
  integration + live-PG), full gate green (687 passed / 1 skipped, ruff
  clean, basedpyright 0/0/0).
- **Timescale SQL hardening (G7): DONE 2026-08-04** — shared
  `_quote.py` quoting helpers; every timescale manager SQL site now quotes
  identifiers/escapes literals; private catalog queries replaced with
  `timescaledb_information` public views (`hypertables`, `chunks`, `jobs`);
  G19 division guard folded in; +9 unit tests, live-Timescale suite green
  (696 passed / 1 skipped, ruff clean, basedpyright 0/0/0).
- **Index dialect guard (G8): DONE 2026-08-04** — HNSW/IVFFlat/GiST
  `get_sql` now raise `IndexDefinitionError` on non-PostgreSQL backends via
  shared `indexes/_dialect.py` (``DIALECT`` check); +5 unit tests
  (701 passed / 1 skipped, ruff clean, basedpyright 0/0/0).
- **Stream COPY defaults + quoting (G9): DONE 2026-08-04** —
  `auto_now_add`/`auto_now` population proven + documented + live-PG
  regression test; `db_default` columns omitted from COPY when unset on all
  instances (mixed → `OperationalError`, mirroring `bulk_create`); all DDL
  and query-helper identifiers quoted via `_quote.py`; +3 live-PG tests
  (704 passed / 1 skipped, ruff clean, basedpyright 0/0/0).
- **Migration writer exception masking (G10): DONE 2026-08-04** —
  `_patch_format_operation` no longer swallows every `ValueError`; only the
  stock writer's terminal `"Unsupported operation type"` error routes to the
  generic deconstruct serializer. Real render errors (e.g. unserializable
  lambda in `SQLOperation.values`) propagate; +1 unit test (705 passed /
  1 skipped, ruff clean, basedpyright 0/0/0).
- **Docs patch-surface (G11) + built-in migrations (G14) + pk split
  (G15): DONE 2026-08-04** — removed the false `OperationGenerator.generate`
  patch claim (design-decisions.md, overview.md, README); rewrote migration
  guides/api around the built-in `python -m tortoise` CLI (`init`,
  `makemigrations`, `sqlmigrate`, `migrate`, `downgrade`) and dropped Aerich
  references; §F pk strategy decided — GraphNode stays UUID
  (uuid4→uuid7 in Tier-1b), HierarchyModel stays BigInt with per-subclass
  `UUID7Field(pk=True)` opt-in, GraphEdge links GraphNode-family only.
- **Timescale 2.18 columnstore API (G13): DONE 2026-08-04** —
  `CompressionManager` migrated off every deprecated pre-2.18 function:
  `timescaledb.enable_columnstore` reloption, `CALL
  add_columnstore_policy`/`remove_columnstore_policy`/
  `convert_to_columnstore`/`convert_to_rowstore`, and
  `hypertable_columnstore_stats` for `get_stats`; method names kept stable;
  verified live against docker TimescaleDB 2.28.3 (docker ARG already
  2.28.3 — no bump needed); +1 unit test (706 passed / 1 skipped, ruff
  clean, basedpyright 0/0/0).
- **Fields fixes (G16, G17, G20): DONE 2026-08-04** — `VectorField`
  `to_python_value` decodes `bytes`/`memoryview` via shared pgvector binary
  layout (`_decode_binary`); `LTreeField` generic fixed to
  `Field[list[str]]` with real `max_length` guard; `_parse_vector_threshold`
  rejects non-number/bool thresholds; +8 tests (714 passed / 1 skipped,
  ruff clean, basedpyright 0/0/0).
- **Codec schema + cagg validation + redis hygiene (G18, G23, G24, G27) +
  docs/README/AGENTS sweep (G21, G22, G25, G26, G28): DONE 2026-08-04** —
  codec schema resolved via `pg_type`/`pg_namespace` probe with `"public"`
  fallback; `ContinuousAggregateManager.create` validates bare SELECT/WITH
  queries; `RedisCache.close` prefers `aclose()` and backend `set` uses
  modern `SET key value PX` (no deprecated `setex`); shared `et_clause`
  extracted to `expressions/_edge_filter.py`; docs uv-only, README synced
  (720 tests, `.env.example` restored, feature list + tree updated), stray
  `stubs/aiodocker/` deleted; +8 unit tests + 1 docker-gated live-Redis
  smoke test (720 passed / 1 skipped, ruff clean, basedpyright 0/0/0).
- **Benchmark provenance (G12): DONE 2026-08-04** —
  `benchmarks/bench_graph_traversal.py` harness added (0/1/3-hop CTE
  retrieval on docker PG, verified live: 10K nodes ≈ 10.4K RPS 0-hop, 9.1K
  RPS 1-hop); all README/doc performance tables marked illustrative with
  harness pointer; AGE/Neo4j rows flagged non-reproducible; IVFFlat lists
  table attributed to pgvector docs.
- New recommended order: roadmap Tiers.
- plan.md itself is currently **untracked** in git — commit alongside first
  fix batch.
