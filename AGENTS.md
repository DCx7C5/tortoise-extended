# AGENTS.md

Guidance for AI agents and developers working in the **tortoise-extended** repository.

## Project Overview

`tortoise-extended` extends [tortoise-orm](https://github.com/tortoise/tortoise-orm) with PostgreSQL
graph/vector capabilities. It requires **Python 3.14+** and is managed with **uv** (hatchling build
backend, `src/` layout).

What the package provides:

- **`VectorField`** — self-contained pgvector `vector` column (no `tortoise-embeddings` dependency)
- **`HNSWIndex` / `IVFFlatIndex` / `GiSTIndex`** — pgvector and GiST index types
- **pgvector filters** — `__l2_distance`, `__cosine_distance`, `__inner_product` query filters
- **`RecursiveCTE` / `GraphTraversal` / pathfinding** — recursive CTE builders and graph helpers
- **`HybridSearch`** — weighted vector + full-text search
- **`GraphNode` / `GraphEdge` / `HierarchyModel`** — reusable graph model base classes
- **`LTreeField` + ltree filters** — PostgreSQL `ltree` hierarchical column type
- **TimescaleDB integration** — `HypertableManager`, `CompressionManager`, `RetentionPolicy`,
  `ContinuousAggregateManager`, and migration operations (`CreateHypertable`,
  `CreateContinuousAggregate`)
- **Redis caching** (optional `redis` extra) — `RedisCache`, `CacheableModel`, `CachedQuerySet`,
  `@cached`, `@cached_method`, `@invalidate`

The package **monkey-patches tortoise-orm at import time**. This is intentional and central to the
design — see `src/tortoise_extended/__init__.py` (`_apply_patches()`).

## Non-Negotiable Rules

1. **`import tortoise_extended` must happen BEFORE `Tortoise.init()`.** Importing the package
   registers `VectorField`, the index types, patches `get_filters_for_field`, and injects the
   pgvector asyncpg codec. Code that imports models before importing `tortoise_extended` will
   silently miss the patches. In library code: `import tortoise_extended  # noqa: F401 — apply patches`.
2. **Never depend on `tortoise-embeddings`.** It monkey-patches the same functions
   (`get_filters_for_field`, `MetaInfo.add_field`, `Tortoise.init`, `OperationGenerator.generate`,
   `MigrationWriter._format_operation`). Only one monkey-patch can win per function. `VectorField`
   is intentionally ~50 lines and self-contained to avoid the conflict.
3. **Never use raw SQL for CRUD.** Extensions build on the Tortoise QuerySet API. Raw SQL is
   reserved for what Tortoise cannot express: recursive CTEs, `DISTINCT ON`, `UNION` subqueries,
   `ts_rank_cd` ranking, and `ARRAY[]` literals (see `expressions/`).
4. **Keep monkey-patches idempotent.** Every patch checks `hasattr(...)` or a
   `_tortoise_extended_patched` flag before applying. New patches must follow the same pattern and
   must also patch `tortoise.models` local references when patching `tortoise.filters`.
5. **All DB operations must be async.** Tortoise ORM is async-only. Never add sync wrappers.

## Commands

```bash
# Setup (requires Python 3.14+)
uv sync --all-extras          # install incl. redis extra
uv sync                       # base deps only

# Test (asyncio_mode = "auto" — bare `async def test_*` works)
uv run pytest tests/ -v       # full suite
uv run pytest tests/test_vector_field.py -v

# Lint
uv run ruff check src tests
uv run ruff format --check src tests   # keep style consistent (not a hard gate)

# Build
uv build
```

## Repository Layout

```
src/tortoise_extended/
├── __init__.py              # Public API + monkey-patch application (_apply_patches)
├── fields/
│   ├── vector_field.py      # VectorField (pgvector vector type, 3 input formats)
│   └── ltree_field.py       # LTreeField (PostgreSQL ltree type)
├── indexes/
│   ├── hnsw_index.py        # HNSWIndex, IVFFlatIndex (pgvector index DDL)
│   └── ltree_index.py       # GiSTIndex
├── expressions/
│   ├── recursive_cte.py     # RecursiveCTE builder
│   ├── graph_filters.py     # L2/Cosine/InnerProduct/Hamming/Jaccard distance operators
│   ├── graph_traversal.py   # GraphTraversal (ancestors / descendants / neighbors)
│   ├── pathfinding.py       # shortest_path, all_paths, find_cycles
│   ├── hybrid_search.py     # HybridSearch (vector + FTS weighted scoring)
│   └── ltree_filters.py     # ltree query operators
├── graph/
│   ├── node.py              # GraphNode base
│   ├── edge.py              # GraphEdge base
│   └── hierarchy_model.py   # HierarchyModel (ltree-path hierarchy pattern)
├── backends/                # (currently empty namespace package)
├── migrations/
│   └── operations.py        # CreateHypertable, CreateContinuousAggregate
├── cache/
│   ├── base.py              # CacheBackend, CacheKey, CacheNamespace, serializers
│   ├── redis.py             # RedisCache, RedisCacheBackend
│   ├── queryset.py          # CachedQuerySet
│   ├── model.py             # CacheableModel
│   └── decorators.py        # cached, cached_method, invalidate
├── timescale/
│   ├── hypertable.py        # HypertableManager
│   ├── continuous_aggregate.py  # ContinuousAggregateManager
│   ├── compression.py       # CompressionManager
│   └── retention.py         # RetentionPolicy
└── stubs/tortoise-stubs/     # local typing overlay for tortoise-orm (see Type Checking)

tests/                       # pytest suite — one module per feature area
docker/                      # PostgreSQL 18 + pgvector + TimescaleDB, Redis
doc/                         # user documentation (getting-started, architecture, api, guides, docker)
```

> **⚠️ Documentation drift:** `README.md` describes a module layout (`models.py`, `expressions/graph_functions.py`,
> `backends/client.py`, `backends/schema_generator.py`) that does **not** match the current code.
> The `src/` tree above is authoritative. When editing docs, align them with the actual modules.

## Code Conventions

- **Python 3.14+ only.** Use modern syntax: `str | None`, `list[dict[str, Any]]`, `tuple[int, ...]`,
  `type` unions.
- **Async-first.** All DB-facing APIs are `async def`. Tests use `pytest.mark.asyncio` or rely on
  `asyncio_mode = "auto"` for bare `async def test_*`.
- **Type-annotate public APIs.** Public functions and methods have full type hints.
- **Module docstrings** use reST style with `Usage::` examples (see `graph_traversal.py`).
- **Public re-exports live in `__init__.py`** (and subpackage `__init__.py` files); implementations
  stay in their own module. Keep `__all__` in sync.
- **Ruff clean.** `ruff check src tests` must pass (RUF002 is the only ignored rule).
- **Test sections** use `# ---` separator comments grouping `Test*` classes.

## Type Checking (basedpyright)

- **Zero errors is the gate.** `uv run basedpyright` must pass with **0 errors**
  (`pyrightconfig.json`: strict mode, `pythonVersion 3.14`, `stubPath src/tortoise_extended/stubs`).
  Remaining warnings are tracked as todos and cleaned in batches — never introduce new warnings.
- **`Any` policy:** the only sanctioned `Any` spelling is `LibraryAny` from
  `src/tortoise_extended/_types.py`, reserved for signatures that must mirror upstream library types
  (e.g. overriding `Field.to_db_value` / `to_python_value`). Banned: bare `Any` outside that case,
  `# type: ignore` comments, `_typeshed.Incomplete`, bare `return wrapper` in decorators, and
  assigning attributes on function wrappers (use `setattr`). NOTE: `reportExplicitAny` fires at every
  `LibraryAny` *use site* — each annotation must carry its own trailing
  `# pyright: ignore[reportExplicitAny]` (the alias-line ignore does not propagate; see `_types.py`).
- **Local `tortoise-stubs` overlay** (`src/tortoise_extended/stubs/tortoise-stubs/`) **replaces** the
  installed `tortoise-orm-stubs` package *and* runtime analysis — `# pyright: partial` does NOT merge
  runtime typing back in. Stubs must be self-sufficient and mirror the runtime API surface exactly
  (e.g. `Model._meta`, `Model.pk`, classmethods returning `QuerySet[Self]` / `QuerySetSingle[Self]`).
  `# pyright: reportExplicitAny=false` is permitted at the top of stub files.
- **Migrations are CLI-checked.** `pyrightconfig.json` previously excluded `**/migrations`, which
  silently hid `src/tortoise_extended/migrations/` errors from the CLI (PyCharm IDE lint saw them).
  The exclude entry was removed; keep it out — never re-add a migrations exclusion.

## Verification Gate (PyCharm IDE)

PyCharm MCP tools are **available and expected** for verification — read-only inspection
(`pycharm_read_file`, `pycharm_search_*`, `pycharm_git_status`, `pycharm_list_*`, ...) plus the
two linter tools: `pycharm_get_file_problems` and `pycharm_lint_files`.

**After EVERY code change** (implementation or review fixes), before reporting work done, run in
order:

1. `uv run ruff check src tests` — clean
2. `uv run basedpyright` — 0 errors (pass `src/tortoise_extended/migrations/` explicitly if touched)
3. `uv run pytest tests/ -q` — 428 passed, 1 skipped
4. **`pycharm_lint_files`** on all changed files (min severity: error)
5. **`pycharm_get_file_problems`** on all changed files — resolve any remaining IDE-reported issues

IDE lint uses PyCharm's basedpyright with the IDE SQL dialect, so it catches what the CLI misses.
Known false positive: SQL syntax errors reported inside `timescale/` and test files are IDE dialect
artifacts — ignore when CLI is clean.

## Task Tracking (opencode.db)

Todos are stored in the **default opencode SQLite database**:

- **DB path:** `/home/daen/.local/share/opencode/opencode.db`
- **Project row:** `fe11928eaae08e83eec30af1464e8adb752eb2da` (worktree
  `/home/daen/Projects/python-development/tortoise-extended`)
- **Do NOT use** the plan-dev DB at `Projects/skill-development/.css/plan/plan.db` for this repo.

`todo` schema: `session_id, content, status, priority, position, time_created, time_updated`
(composite PK `(session_id, position)`; `time_*` are epoch milliseconds).

Read todos for this project:

```bash
sqlite3 /home/daen/.local/share/opencode/opencode.db -separator " || " \
  "SELECT t.status, t.priority, t.content FROM todo t
   JOIN session s ON t.session_id = s.id
   WHERE s.project_id = 'fe11928eaae08e83eec30af1464e8adb752eb2da'
     AND t.status != 'completed'
   ORDER BY t.time_created;"
```

- The in-session `todowrite()` list mirrors this DB; status transitions (pending/in_progress/completed)
  must be written back to `opencode.db` so the durable record stays accurate.
- New todos are inserted into the current session's `session_id` (one row per concern; keep
  `position` unique within that session).

## Testing

- `tests/` mirrors feature areas: `test_vector_field.py`, `test_hnsw_index.py`, `test_ltree_field.py`,
  `test_graph.py`, `test_graph_traversal.py`, `test_pathfinding.py`, `test_hybrid_search.py`,
  `test_recursive_cte.py`, `test_hypertable.py`, `test_cache.py`, `test_migration_operations.py`, ...
- Tests are organized into `Test*` classes per concern.
- Target: **zero warnings** — the suite currently passes with zero warnings. Do not add tests that
  emit deprecation/async warnings.
- Non-PostgreSQL-specific tests run against SQLite (e.g. `VectorField` falls back to `BLOB`).
- `test_pg_integration.py` requires the Docker database (see below).

## Docker

```bash
docker compose -f docker-compose.dev.yml up -d       # start postgres-ext + redis-ext
docker compose -f docker-compose.dev.yml logs -f postgres-ext
docker compose -f docker-compose.dev.yml down        # stop
```

| Service       | Image / Build                                   | Host port | Purpose            |
|---------------|-------------------------------------------------|-----------|--------------------|
| `postgres-ext`| `docker/postgres-ext/Dockerfile` (PG 18, pgvector 0.8.5, TimescaleDB) | `127.0.0.1:5433` | Graph/vector DB |
| `redis-ext`   | `redis:7-alpine`                                | `127.0.0.1:6380` | Cache backend      |

- Init scripts run from `docker/postgres-ext/scripts/` on first start:
  `00-extensions.sql` (creates `vector`, `ltree`, `timescaledb`, `pg_trgm`, `uuid-ossp`).
- The Postgres base image is **digest-pinned** (`postgres:18@sha256:...`); pgvector and TimescaleDB
  are **commit-pinned** via ARG. Bump version + pin together, deliberately, when upgrading.
- `docker-compose.dev.yml` uses `env_file: .env` — the `.env` file must exist locally with
  `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` (and `REDIS_*` if used).
- Postgres listens on the host only at `127.0.0.1:5433` (never exposed publicly).

## Dependencies

- `tortoise-orm>=1.1.7,<1.2` — with the `[asyncpg]` extra (the ORM being extended)
- `tortoise-orm-stubs>=1.0.2` — type stubs
- `msgspec>=0.21.0` — fast serialization
- `pypika-tortoise>=0.6.5,<0.7` — query builder used by expressions
- optional: `redis[hiredis]>=5.0.0` (the `redis` extra)
- dev: `pytest>=9.0.3`, `pytest-asyncio>=1.4.0`

**Never add a dependency by hand.** Use `uv add <pkg>` so both `pyproject.toml` and `uv.lock`
stay in sync. Note `uv.lock` is currently not committed; if you introduce one deliberately, pin it
to the project (`[tool.uv] package = true` is already set).

## Documentation

User-facing docs live in `doc/` and must stay in sync with code:

- `doc/getting-started/` — installation, quickstart, configuration
- `doc/architecture/` — overview, schema, vector search, graph traversal, design decisions
- `doc/api/` — API reference
- `doc/guides/` — migration, performance, troubleshooting
- `doc/docker/` — docker setup and configuration

When changing public API, update the relevant `doc/api/*.md` and the module docstring in the same
change.

## CRITICAL RULE:

> [!CAUTION]
> reduce your chat and thinking output to less than 1000 tokens. only headings for tools etc. 
