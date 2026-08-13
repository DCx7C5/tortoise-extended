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
- **Model base classes** — `BaseModel`, `BaseUserModel`, `BaseSoftDeleteModel` +
  `SoftDeleteQuerySet`, `BaseGraphNodeModel` / `BaseGraphEdgeModel` /
  `BaseHierarchyModel`, `BaseCacheableModel`, `BaseEventStreamModel`, `TimestampMixin`
  (all opt-in abstract bases under `tortoise_extended.models`)
- **`LTreeField` + ltree filters** — PostgreSQL `ltree` hierarchical column type
- **TimescaleDB integration** — `HypertableManager`, `CompressionManager`, `RetentionPolicy`,
  `ContinuousAggregateManager`, and migration operations (`CreateHypertable`,
  `CreateContinuousAggregate`)
- **Redis caching** (optional `redis` extra) — `RedisCache`, `BaseCacheableModel`, `CachedQuerySet`,
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


## Session & Todo discipline (todowrite)

Every multi-step session starts with `todowrite`. The list IS the plan — keep it current in real time.

**Session reference (canonical todo list):** the project's shared work list lives in opencode session **`ses_003ad5f8effepj1UctRRK4mndd`** (`~/.local/share/opencode/opencode.db`, table `todo`). This is the same session that owns AGENTS.md edits. When starting a new chat, dereference this ID first and continue that list — never fork a parallel todo set in the new session:

```bash
sqlite3 -readonly ~/.local/share/opencode/opencode.db \
  "SELECT position, status, content FROM todo WHERE session_id='ses_003ad5f8effepj1UctRRK4mndd' ORDER BY position;"
```

- This session (the owner) claims/completes items via `todowrite`; statuses live in `todo` keyed by `(session_id, position)`.
- A new chat reads the ID here, mirrors the pending items with `todowrite` in its own session, and keeps them in sync with the referenced list — completed/claimed status is recorded against the owner session.
- If the ID is stale (session compacted/deleted), pick the most recent `python-planner` session from `opencode.db` and update this reference.
- **One `in_progress` at a time.** Exactly one item `in_progress`; the rest `pending` / `completed` / `cancelled`.
- **Claim → work → complete.** Mark a TODO `in_progress` before starting it; mark `completed` ONLY after the work is actually done AND verified (lint/type/test pass). Never complete on intent.
- **One TODO per finding.** Every error and warning deserves its own TODO — never fold multiple diagnostics into a single "cleanup" TODO, never one TODO per file. If findings share a root cause, say so in the descriptions; they still get separate TODOs.
- **Analyze existing TODOs before creating/updating.** Before adding a TODO, check the current list: if one already covers the work, update it; if not, create one. Never duplicate.
- **Todo-first dispatch.** Every `task()` / `bgagent_task()` dispatch MUST have a TODO. No TODO → create one first.

### 1.1 New chat thread — bootstrap (mandatory first step)

Every new chat thread continues the shared work list — never starts a fresh one. First action of the session:

1. **Import pending todos into this session.** Read the reference session's list (query above), then mirror every **non-completed** item into this chat via `todowrite` (same `content` + `priority`, status `pending`, in `position` order). Do NOT import completed items — they stay in the owner session as history.
2. **Last session lookup (if the reference is missing/stale).** Find the most recent python-planner session and update the reference in §1:

```bash
sqlite3 -readonly ~/.local/share/opencode/opencode.db \
  "SELECT id, substr(title,1,50) FROM session WHERE agent='python-planner' ORDER BY time_created DESC LIMIT 1;"
```

3. **Keep this chat's list in sync.** Claim/complete here via `todowrite`; after completing an item, record the status against the owner session (see §1) so the next chat dereferences current state. This chat's copy is a mirror — never let it drift from the reference list.

## 2. Error & Warning → TODO protocol (always)

1. **Always check diagnostics** — `pycharm_get_file_problems` (IDE/type diagnostics) and `pycharm_lint_files` (ruff). All PyCharm tools are available for read-only inspection. **Verification is PyCharm-MCP-only — agents MUST NOT run `ruff`/`basedpyright` directly**; `pycharm_get_file_problems` (IDE/type diagnostics) + `pycharm_lint_files` (ruff) are the canonical gate.
2. **Analyze findings by root cause** — group errors/warnings by concern, not by file; then create ONE TODO PER FINDING (§1).
3. **Typing is first-class — read into it.** Read basedpyright/pyright output thoroughly. Typing warnings AND errors are especially important: they reveal real contract breaks, not noise. Every `report*` diagnostic deserves a TODO.
4. **Update if necessary** — after fixes, re-run diagnostics; anything still failing gets a TODO created or updated.

### 2.1 PyCharm MCP tools — when to use each

All `pycharm_*` tools are read-only inspection or IDE actions, always loaded — use them instead of shelling out to `ruff`/`basedpyright`/`git`/`grep`.

**Diagnostics gate (mandatory before completing any implementation TODO):**
- `pycharm_get_file_problems(file)` — full IDE/type diagnostics for ONE file (basedpyright `report*`, syntax, inspections). Returns per-problem severity + description + location. Use after editing a file to get its complete problem list.
- `pycharm_lint_files(files=[...], min_severity="warning")` — batch lint of multiple project-relative files via IntelliJ inspections (ruff is wired in). `min_severity` accepts `warning` or `error` (default `warning`); only analyzes files inside the project; entries may carry `timedOut: true` or `notAnalyzedReason`.

**Search & symbol tracking (instead of `grep`/`glob` in bash):**
- `pycharm_search_text(query)` — fast text search with match coordinates. Use for plain-occurrence search.
- `pycharm_search_regex(pattern)` — regex search with match coordinates. Use for pattern-based search.
- `pycharm_search_symbol(fragment)` — **the symbol tracker**: semantic lookup of identifiers by name fragment across the project. Use to locate a class/function/variable before touching its callers.
- `pycharm_search_file(glob)` — match file paths using glob syntax.
- `pycharm_get_symbol_info(fqn)` — symbol declaration, semantics, signature. Use to understand a symbol before modifying it.
- `pycharm_analyze_calls(fqn, kind=INCOMING_CALLS|OUTGOING_CALLS)` — callers of / calls from a symbol. Use for refactor-impact and dependency analysis.

**File & directory reading (instead of Read/Glob):**
- `pycharm_read_file(file, max_lines_count=2000, max 5000)` — numbered lines (1-indexed).
- `pycharm_list_directory_tree(dir)` — directory/project tree.
- `pycharm_get_all_open_file_paths()` — current open editors.
- `pycharm_open_file_in_editor(file)` — open a file in the IDE.

**Mutation (IDE-native, minimal diffs):**
- `pycharm_apply_patch(operations=[add|update|remove])` — precise patch edits.
- `pycharm_reformat_file(files=[...])` — apply project formatting rules to files.
- `pycharm_create_new_file(path, content)` — generate new files.
- `pycharm_rename_refactoring(...)` — rename refactoring with usage updates.

**Project, VCS & terminal:**
- `pycharm_build_project()` — compile project/files; returns compilation errors and warnings.
- `pycharm_get_run_configurations()` / `pycharm_execute_run_configuration(name)` — list/run run configurations.
- `pycharm_get_project_modules()` / `pycharm_get_project_dependencies()` — project module/dependency info.
- `pycharm_git_status(...)` — per-repository status with staged/unstaged counts and untracked. Use instead of `git status`.
- `pycharm_get_repositories()` — list git roots in the project.
- `pycharm_execute_terminal_command(...)` — run terminal commands inside the IDE environment.

##  Commands

```bash
uv sync                            # install/lock from pyproject.toml
uv add <pkg>                       # add dependency (pyproject + lock)
uv run pytest                      # full suite — real Postgres test DBs (see §7)
uv run pytest tests/test_x.py -k name

```

always use pycharm_get_file_problems and pycharm_lint_files


## Repository Layout

```
src/tortoise_extended/
├── __init__.py              # Public API + monkey-patch application (_apply_patches)
├── _quote.py                # shared SQL identifier/literal quoting (timescale + migrations)
├── _types.py                # concrete type aliases + duck-typed protocols
├── exceptions.py            # exception hierarchy (VectorFieldError, GraphTraversalError, ...)
├── models/
│   ├── base.py                # BaseModel (BigInt pk)
│   ├── user.py                # BaseUserModel (Django-style email/password auth)
│   ├── mixins.py              # TimestampMixin / TimestampEndMixin (created_at/updated_at)
│   ├── soft_delete.py         # BaseSoftDeleteModel + SoftDeleteQuerySet
│   ├── graph_node.py          # BaseGraphNodeModel (adjacency-list, UUID pk)
│   ├── graph_edge.py          # BaseGraphEdgeModel (typed/weighted edges, UUID pks)
│   ├── hierarchy_model.py     # BaseHierarchyModel (ltree-path hierarchy)
│   ├── cacheable_model.py     # BaseCacheableModel (model-level Redis caching)
│   └── event_stream.py        # BaseEventStreamModel (TimescaleDB multi-stream hypertable)
├── fields/
│   ├── vector_field.py      # VectorField (pgvector vector type, 3 input formats)
│   └── ltree_field.py       # LTreeField (PostgreSQL ltree type)
├── indexes/
│   ├── hnsw_index.py        # HNSWIndex, IVFFlatIndex (pgvector index DDL)
│   ├── ltree_index.py       # GiSTIndex
│   └── _dialect.py          # shared PostgreSQL-dialect guard for index DDL
├── expressions/
│   ├── recursive_cte.py     # RecursiveCTE builder
│   ├── graph_filters.py     # L2/Cosine/InnerProduct/Hamming/Jaccard distance operators
│   ├── graph_traversal.py   # GraphTraversal (ancestors / descendants / neighbors)
│   ├── graph_vector_search.py  # GraphVectorSearch (graph + vector compositor)
│   ├── pathfinding.py       # shortest_path, all_paths, find_cycles
│   ├── hybrid_search.py     # HybridSearch (vector + FTS weighted scoring)
│   ├── ltree_filters.py     # ltree query operators
│   └── _edge_filter.py      # shared edge-table filter clause (et_clause)
├── backends/                # (currently empty namespace package)
├── migrations/
│   └── operations.py        # CreateHypertable, CreateContinuousAggregate
├── cache/
│   ├── base.py              # CacheBackend, CacheKey, CacheNamespace, serializers
│   ├── redis.py             # RedisCache, RedisCacheBackend
│   ├── queryset.py          # CachedQuerySet
│   ├── decorators.py        # cached, cached_method, invalidate
│   └── _coerce.py           # cache-coercion helpers for BaseCacheableModel
├── timescale/
│   ├── hypertable.py        # HypertableManager
│   ├── stream.py            # TimeBucketRow + stream helpers (COPY ingestion, rollups)
│   ├── continuous_aggregate.py  # ContinuousAggregateManager
│   ├── compression.py       # CompressionManager
│   └── retention.py         # RetentionPolicy
└── stubs/tortoise-stubs/     # local typing overlay for tortoise-orm (see Type Checking)

tests/                       # pytest suite — one module per feature area
docker/                      # PostgreSQL 18 + pgvector + TimescaleDB, Redis
doc/                         # user documentation (getting-started, architecture, api, guides, docker)
```

> **Note:** The `src/` tree above is authoritative and README is kept in
> sync with it. When editing docs, align them with the actual modules.

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
- **`Any` policy:** `Any` is **banned** in runtime code, along with `object` and
  aliases of them. The former `LibraryAny` alias is removed (`e58662e`). Runtime annotations use
  concrete unions, recursive unions, or `TypedDict`s only; `cast()` to concrete types is allowed
  and encouraged at boundary sites where upstream signatures are `Any`-typed. Banned: bare `Any`,
  `# type: ignore` comments, `_typeshed.Incomplete`, bare `return wrapper` in decorators, and
  assigning attributes on function wrappers (use `setattr`).
- **Local `stubs` overlay** (`src/tortoise_extended/stubs/tortoise-stubs/`) **replaces** the
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
3. `uv run pytest tests/ -q` — 664 passed, 1 skipped
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
