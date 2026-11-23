# Changelog

All notable changes to `tortoise-extended` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project has no release tags yet, so
the versions below are the `pyproject.toml` version at the time of the change.

## [Unreleased]

### Added

- `VersionMixin` — opt-in optimistic locking: a `version` column (`BigIntField`, default `0`) and a
  guarded `save()` that runs one `UPDATE … WHERE <pk> = $1 AND version = $2` with
  `version = version + 1` (queryset API, no raw SQL). A stale write raises
  `VersionMismatchError` (new, exported) naming model, pk, expected and current version, and leaves
  the instance untouched so the caller can reload and retry; a missing row raises `IntegrityError`
  as upstream. Inserts delegate to the parent `save()` unchanged, `auto_now` columns are stamped
  explicitly (the queryset executor would skip them), and `update_fields` narrows the payload.
  `bulk_create` / `bulk_update` / `QuerySet.update()` deliberately bypass the guard — documented in
  the docstring and `docs/api/models.md`.
- `distinct_on(model, *on, order_by=(), limit=None, where=None, params=(), include_deleted=False)`
  — PostgreSQL `DISTINCT ON` as one sanitised statement plus real model instances back:
  `SELECT DISTINCT ON (…) <projected columns> FROM … [WHERE …] ORDER BY <on…>, … [LIMIT n]`,
  with `$1`-style bound parameters and the soft-delete `deleted_at IS NULL` filter applied to
  models that declare the column. `order_by` must lead with the `on` columns (PostgreSQL's rule) and
  every refused input — empty `on`, unknown field, wrong ordering prefix, placeholder/param
  mismatch, `limit < 1`, filtered queryset — raises `ValueError` instead of reaching the server,
  and every one of them is decided before the connection is resolved (bad arguments fail identically
  on a model that has no connection yet).
  Hydration goes through `execute_query_dict` + `Model._init_from_db`, the executor's own path, so
  the rows are saveable instances. Documented in `docs/api/queryset.md`.
- Window-function builders for `annotate()`: `row_number`, `rank`, `dense_rank`, `ntile(n)` and the
  windowed aggregates `sum_over`, `avg_over`, `count_over`, `min_over`, `max_over`, each taking
  field **names** — `partition_by` / `order_by` accept one name or a sequence, and a leading `-`
  means `DESC`. The builders construct the pypika `Field` terms themselves, so Tortoise's `F()`
  can no longer be stringified raw into `OVER(...)` (`PARTITION BY <F object …>`) — the trap the
  module docstring records. `count_over()` with no field emits `COUNT(*)`; `ntile` rejects a
  bucket count below 1 with `ValueError`; frame clauses are documented as out of scope, with a
  verified direct-pypika recipe for callers who need `ROWS BETWEEN`. Exported from
  `tortoise_extended` and `tortoise_extended.expressions`, with `docs/api/windows.md`.
- `QuerySet.random()` and the free `random_ordering(queryset)` it is installed from — a clone of the
  queryset annotated with `RANDOM()` and ordered by it, so it composes with `filter` / `values` /
  `limit` / `paginate` and leaves `count()` untouched. Installed at import time as **patch site 6**
  under the same identity guard as site 5; a queryset that already annotates the internal alias
  raises `ValueError` instead of being silently overwritten. The docstring states the cost
  (`O(n log n)`, one `RANDOM()` per matching row) and points at `TABLESAMPLE` for huge tables
  rather than sampling for the caller.
- Four PostgreSQL field types: `InetField` (`inet`, IPv4 **and** IPv6 host addresses, returned as
  `ipaddress` address objects; interface/prefix notation is rejected), `MacAddrField` (`macaddr`,
  48-bit) and `MacAddr8Field` (`macaddr8`, 64-bit) — both accept colon, dash and dotted-Cisco
  spellings and normalise to lowercase colon form on write and on read — and `CitextField`
  (`citext`, case-insensitive text that keeps its original spelling; PostgreSQL has no length
  modifier for `citext`, so `max_length` is a Python-side guard). `IPv4Field` is unchanged and
  still refuses IPv6 values; `CitextField` needs `CREATE EXTENSION IF NOT EXISTS citext`.
- `QuerySet.paginate(page, page_size)` and the `PageInfo` it returns — offset pagination with the
  metadata a page widget needs (`items`, `total`, `total_pages`, `has_next`, `has_prev`). Two
  queries (a `COUNT(*)` over the same filters, then the slice), a `ValueError` naming the argument
  when `page` or `page_size` is below 1, and a page past the end returns no items with the true
  total instead of raising. The free function `paginate(queryset, page, page_size)` is public for
  callers that would rather not depend on the method; the method is installed on every QuerySet at
  import time as patch site 5, under the same guard contract as the field and index patches.
- Fusion utilities: `reciprocal_rank_fusion`, `weighted_score_fusion`, `diversity_rerank`
  (greedy MMR, requires a `similarity` callable), `ranking_from_hits`, `scores_from_hybrid_rows`.
- `TortoiseVersionError` and `PatchConflictError`.
- Import-time tortoise-orm version guard (`>=1.1.7,<1.2`, read from the installed distribution).
- Identity guards on `VectorField` and the index types, so a foreign monkey-patch cannot be
  silently overwritten.
- `DeprecationWarning` from `BaseGraphNodeModel` and `BaseEventStreamModel`; use `BaseNode` and
  `BaseSample`.
- Node ergonomics on `NodeMixin`: `add_child`, `root`, `is_ancestor_of`, `is_descendant_of`,
  `to_tree`, `child_count`, `move_to`, and `NodeTree`.
- `NodeMixin.connect` / `disconnect` / `neighbours` plus the duck-typed `edge_model` ClassVar —
  node↔edge helpers that never import `models/graph_edge.py` (a runtime import would cycle).
  Unknown edge column names raise `GraphError`; a self-link is refused unless the edge model
  declares `allow_self_loops = True`.
- `bulk_add_children(parent, rows, *, using_db=None)` on `NodeMixin` and `BaseHierarchyModel`:
  derives `parent_id` / `depth` / `is_root` / `child_count` (and `path`) for one level, inserts it
  in a single transaction and bumps the parent's counter once.
- `NodeMixin.rebuild_derived()` and `BaseHierarchyModel.rebuild_paths()` — repair paths for rows
  written outside the library (raw SQL, a migration, a `COPY` load); both recompute in dependency
  order and return the number of rows changed.
- `NodeMixin.validate_node()` — one node's invariants (`depth`, `is_root`, `child_count`, parent
  namespace, parent chain) as a list of human-readable errors, empty when clean.
- `pglast`-based validation of continuous-aggregate queries.
- `HMACDigest` accepts a pypika expression as its `key`, so the secret can be resolved
  server-side (`current_setting('app.key')`) instead of being inlined as a literal.
- `ensure_extensions()` — creates the six PostgreSQL extensions every feature of the package
  stands on (`vector`, `ltree`, `timescaledb`, `pgcrypto`, `pg_trgm`, `uuid-ossp`) with
  `CREATE EXTENSION IF NOT EXISTS`, in creation order, and returns the names. Idempotent, so it
  is safe on every application start. `EXTENSIONS` is the public tuple it iterates; a refusal
  (typically missing privilege) raises `SetupError` with the server error chained, never
  swallowed. `SetupReport` is the struct the `setup()` fan-out fills.
- `BaseUnifiedNode(UnifiedIdModel, NodeMixin)` — the BigInt-keyed hybrid the `NodeMixin`
  docstring advertised but nothing shipped: BigInt `id` primary key for compact, JOIN-fast
  foreign keys, plus the UUID7 `uid` for cross-table refs, with `NodeMixin`'s tree helpers
  working on integer `parent_id`s. `NodeMixin.__init_subclass__` returns early on an abstract
  base, so concrete subclasses still redeclare the `(namespace, depth)` index — the base
  redeclares it as documentation, and the guard's error message is what a forgetful subclass
  hits.
- `setup(models=None, *, generate_schemas=False)` — the startup fan-out: `ensure_extensions()`,
  then `Tortoise.generate_schemas()` when asked, then `model.setup()` for every non-abstract
  `SampleMixin` subclass, returning a `SetupReport`. The order is load-bearing —
  `create_hypertable` needs the table to exist, and the test proves it by measuring that the
  reversed order raises. `models` restricts the stream scan by app label; it is not forwarded
  to `Tortoise.init()`. Raises `SetupError` before `Tortoise.init()` has run. Exported from the
  top-level package next to `ensure_extensions` and `SetupReport`.
- `SampleMixin.record(*, stream=None, at=None, **fields)` — one-row create with the partition
  columns mapped from the `stream=` / `at=` shorthands. A shorthand that collides with an
  explicit field raises `TimescaleError` instead of one silently winning. `at=None` has no
  universal fallback (`BaseSample` declares no time column, and `Field.default` stays `None`
  for `auto_now` / `auto_now_add`), so the value is omitted only when the subclass's time column
  carries a non-`None` `default` — otherwise the call raises and names the field.
- `SampleMixin.latest_for_stream(stream)` — newest row for one stream, a wrapper over
  `latest_per_stream(stream_ids=[stream], limit=1)` with no new SQL. Named `latest_for_stream`
  rather than `latest` so `Model.latest(*orderings)` stays available on sample models: the
  shorter name shadowed the inherited ORM helper and silently changed what
  `await Event.latest("created_at")` meant.
- `SampleMixin.downsample(interval, *, agg="avg", field=None, stream=None, start=None, end=None)`
  — rollup over the whole series, delegating to `time_series()`; an unknown `field` raises
  `TimescaleError`, and open bounds are widened to the series' own first and last row (the upper
  bound is exclusive, so `end` becomes last + 1 µs).
- `SampleMixin.gapfill(interval, *, method="locf", field, stream=None, start=None, end=None)` —
  TimescaleDB `time_bucket_gapfill` with `locf()` / `interpolate()` / no fill, emitting every
  bucket of the range including the empty ones. `start` / `end` are required and `end <= start`
  raises: TimescaleDB derives the synthesised buckets from the range, so unlike `downsample()` it
  cannot be inferred from the data. An unknown or non-numeric `field` raises `TimescaleError`
  before any SQL is sent. Raw SQL, as the `expressions/` carve-out allows — Tortoise cannot
  express gapfill — with quoted identifiers and bound parameters.
- `Graph` (`expressions/graph.py`) — a facade that binds a node model and an edge model once and
  exposes the graph operations against model instances: `link`, `unlink`, `neighbours`,
  `shortest_path`, `find_cycles`, `search`. It adds no SQL of its own — every call is one of the
  existing builders, or an ordinary queryset used to hydrate rows into models. `search` takes a
  required `seed` node, because a graph+vector search has no meaning without one; the constructor
  rejects an edge model lacking the source / target / edge-type column, and every node argument is
  checked for class, namespace and persistence before a query runs.
- `BaseSoftDeleteNode` + `SoftDeleteNodeQuerySet` — soft delete and the tree contract at once.
  Deleting a node hides its whole **live** subtree in one transaction; `restore()` brings the
  subtree back and recomputes each restored node's `child_count` from its live children, so a
  partial restore cannot leave a stale count. The queryset path (`filter(...).delete()` /
  `.restore()`) applies the same subtree semantics rather than hiding the matched rows alone, and
  `hard_delete()` keeps the base physical-delete behaviour. `child_count` keeps counting live
  children throughout, and `_block_orphan_delete` — a hard-delete guard — never fires here.

### Changed

- `validate_cagg_query` parses the query with PostgreSQL's grammar instead of a regular
  expression. A single trailing `;` is accepted and stripped from the returned text.
- `add_child` accepts arbitrary column values as keyword arguments.
- `add_child` takes an explicit keyword-only `using_db` connection. It was already atomic without
  one — Tortoise routes writes on the default client to the ambient `in_transaction()`
  connection — so this only widens what a caller can target, it fixes nothing.
- `save()` refuses a **persisted** `parent_id` change on `NodeMixin` and `BaseHierarchyModel`
  (`GraphError` / `HierarchyError`, naming `move_to()`). A single-row save cannot shift each
  descendant's `depth` or `path`, nor update `is_root`, so it used to leave a tree that only
  `validate_node()` / `validate_hierarchy()` ever noticed. A partial
  `save(update_fields=[...])` that omits `parent_id` still writes through.
- `bulk_create` and `bulk_update` raise on both hierarchy bases: they bypass `save()`, so a
  bulk-inserted child kept `depth=0` and the parent's `child_count` stayed at zero — silently.
  Use `bulk_add_children()` to seed a level, `rebuild_derived()` / `rebuild_paths()` to repair.
- `GraphTraversal`, the pathfinding helpers, and `GraphVectorSearch` now exclude soft-deleted
  nodes and edges when the model declares a `deleted_at` column, matching `SoftDeleteQuerySet`.
  Models without the column generate identical SQL.

### Removed

- The SQLite test harness. The package is PostgreSQL-only, so every database-backed test module
  now runs against the Docker PostgreSQL (`127.0.0.1:5434`, override with `TORTOISE_TEST_DB`),
  dropping its tables at the module boundary and truncating between tests. Tests that only made
  sense on SQLite — the raw-BLOB pgvector round-trip and the `BooleanField._db_sqlite` type
  assertion — were deleted, not skipped.
- The `tortoise-orm-stubs` dependency. The in-tree overlay
  (`src/tortoise_extended/stubs/tortoise-stubs/`) shadows the only module that package ships, so
  it was a runtime dependency that changed nothing — measured: the type gate is byte-identical
  with the package uninstalled. The overlay stays dev-only and out of the wheel.
- The wheel no longer carries the typing overlay: `[tool.hatch.build.targets.wheel]` excludes
  `src/tortoise_extended/stubs`, so the 12 inert `.pyi` files (and their directories) are gone —
  measured: 0 `.pyi` entries, no `/stubs/` path, `tortoise_extended/py.typed` still present.
  A stub package is only auto-discovered at the top level of `site-packages`, so nothing changes
  for a type checker; it was payload with no effect. The overlay still serves the dev gate through
  `stubPath`.

### Fixed

- Continuous-aggregate SQL injection: two quotes inside a `/* */` comment could hide a statement
  separator, letting a second statement reach `CREATE MATERIALIZED VIEW ... AS (...)`.
- A declared source table that appears only inside a comment (or only as a CTE name) no longer
  satisfies the source-reference check.
- `drop_hypertable` deletes the table's TimescaleDB scheduler jobs (retention, columnstore)
  before dropping it. They are jobs, not constraints, so `DROP TABLE ... CASCADE` left them
  registered and the scheduler then hammered a hypertable that no longer existed.
- `add_dimension` coerces `number_partitions` with `int()` before it reaches the SQL text; a
  non-numeric value now raises `ValueError` before any statement is sent.
- Continuous-aggregate error messages are flattened to one line, so an identifier containing a
  newline can no longer forge extra log lines.
- `RetentionPolicyInfo` is decodable by msgspec (`drop_after` narrowed to the JSON scalars). It
  advertised a capability it did not have — the `bytes` member of `RowValue` made the union
  undecodable — though nothing in the shipped paths relied on it.
- The `postgres-ext` healthcheck probes its **own** postmaster. It ran a socket-less
  `pg_isready -d ${POSTGRES_DB}`; the container bind-mounts `/var/run/postgresql` (shared with the
  css-mcp stack), so it answered for whichever container owned `.s.PGSQL.5432` — measured: inside
  `postgres-ext`, `psql` without `-h` listed `core`/`secrets`/`testcore` (the neighbour's cluster)
  while `-h 127.0.0.1` listed the tortoise databases. It also treated a missing application
  database as healthy (`pg_isready` returns 0 on an ERROR reply, so `-d tortoise_test` only bought
  a FATAL line per interval), so liveness was never actually tested. It now runs
  `pg_isready -h 127.0.0.1 -p 5432 -U ${POSTGRES_USER:-postgres} -d postgres`.

### Security

- Docker init SQL is generated from `\getenv` plus `format(%I, ...)` / `format(%L, ...)` and
  executed with `\gexec`, so no `DB_USER_N` / `DB_PASS_N` / `DB_NAME_N` value is ever spliced
  into SQL text. Hand-quoting had left embedded double quotes (in `CREATE ROLE "..."`) and
  embedded single quotes (in `rolname = '...'`) undoubled. Verified end to end with a role named
  `we"ird'user`.
- The pgvector and TimescaleDB pins are enforced, not just declared: the build fails unless each
  `*_COMMIT` is a 40-character sha and unless the checkout resolves to exactly that commit. The
  unused `*_VERSION` ARGs and `ENV OPTFLAGS=""` are gone — they read like pins and were not.
- The dev Redis requires AUTH. `protected-mode no` is removed, the entrypoint exits non-zero when
  `REDIS_PASSWORD` is unset, and compose refuses to configure without it: an unauthenticated Redis
  accepts `CONFIG SET dir` + `SAVE`, i.e. it is a file-write pivot.
- `POSTGRES_PASSWORD` and `REDIS_PASSWORD` ship as `CHANGEME` and are interpolated with `${…:?}`,
  and the init script no longer defaults its third credential group to `postgres/postgres`.
  A first start now fails loudly instead of inheriting a committed working login.
- The live cache tests require `REDIS_PASSWORD`: they skip, visibly, when it is unset, and the
  probe authenticates and pings instead of only opening a socket.

## [0.1.0]

Initial feature set: `VectorField` with the asyncpg codec, the pgvector distance filters and
`HNSWIndex` / `IVFFlatIndex` / `GiSTIndex`, `LTreeField` and the ltree filters, `RecursiveCTE`,
`GraphTraversal`, pathfinding, `GraphVectorSearch` and `HybridSearch`, the opt-in model bases
(`BaseModel`, `BaseUserModel`, `BaseSoftDeleteModel`, `NodeMixin`/`BaseNode`,
`BaseGraphEdgeModel`, `BaseHierarchyModel`, `BaseCacheableModel`, `SampleMixin`/`BaseSample`,
`UnifiedIdModel`, `TimestampMixin`), the TimescaleDB managers and migration operations, the
pgcrypto helpers, and optional Redis caching.
