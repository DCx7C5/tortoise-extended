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
- `pglast`-based validation of continuous-aggregate queries.

### Changed

- `validate_cagg_query` parses the query with PostgreSQL's grammar instead of a regular
  expression. A single trailing `;` is accepted and stripped from the returned text.
- `add_child` accepts arbitrary column values as keyword arguments.
- `GraphTraversal`, the pathfinding helpers, and `GraphVectorSearch` now exclude soft-deleted
  nodes and edges when the model declares a `deleted_at` column, matching `SoftDeleteQuerySet`.
  Models without the column generate identical SQL.

### Removed

- The SQLite test harness. The package is PostgreSQL-only, so every database-backed test module
  now runs against the Docker PostgreSQL (`127.0.0.1:5434`, override with `TORTOISE_TEST_DB`),
  dropping its tables at the module boundary and truncating between tests. Tests that only made
  sense on SQLite — the raw-BLOB pgvector round-trip and the `BooleanField._db_sqlite` type
  assertion — were deleted, not skipped.

### Fixed

- Continuous-aggregate SQL injection: two quotes inside a `/* */` comment could hide a statement
  separator, letting a second statement reach `CREATE MATERIALIZED VIEW ... AS (...)`.
- A declared source table that appears only inside a comment (or only as a CTE name) no longer
  satisfies the source-reference check.

## [0.1.0]

Initial feature set: `VectorField` with the asyncpg codec, the pgvector distance filters and
`HNSWIndex` / `IVFFlatIndex` / `GiSTIndex`, `LTreeField` and the ltree filters, `RecursiveCTE`,
`GraphTraversal`, pathfinding, `GraphVectorSearch` and `HybridSearch`, the opt-in model bases
(`BaseModel`, `BaseUserModel`, `BaseSoftDeleteModel`, `NodeMixin`/`BaseNode`,
`BaseGraphEdgeModel`, `BaseHierarchyModel`, `BaseCacheableModel`, `SampleMixin`/`BaseSample`,
`UnifiedIdModel`, `TimestampMixin`), the TimescaleDB managers and migration operations, the
pgcrypto helpers, and optional Redis caching.
