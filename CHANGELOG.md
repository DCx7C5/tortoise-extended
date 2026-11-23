# Changelog

All notable changes to `tortoise-extended` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project has no release tags yet, so
the versions below are the `pyproject.toml` version at the time of the change.

## [Unreleased]

### Added

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
