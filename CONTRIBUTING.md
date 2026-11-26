# Contributing

## Requirements

- Python 3.14 or newer. This floor is deliberate and is not being lowered.
- [uv](https://docs.astral.sh/uv/) for dependency and environment management.
- Docker, for the PostgreSQL/pgvector/TimescaleDB and Redis services.

## Setup

```bash
uv sync --all-extras
docker compose -f docker-compose.dev.yml up -d
```

The compose file builds the Postgres image from `docker/postgres-ext/Dockerfile`, which compiles
pgvector and TimescaleDB from pinned commits, so the first `up` takes several minutes. It reads
`.env`; copy `.env.example` and set `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` (and the
`REDIS_*` values if you use caching).

## Import order

`import tortoise_extended` must run before `Tortoise.init()`. The import applies the
monkey-patches that register `VectorField`, the index types, the query filters, and the asyncpg
pgvector codec. Importing after `Tortoise.init()` raises `TortoiseExtendedError` instead of
leaving the field silently unregistered.

## Checks

```bash
uv run ruff format --check src tests   # clean
uv run ruff check src tests            # clean
uv run basedpyright                    # 0 errors
uv run pytest tests/ -q                # green, against the Docker database
```

All four must pass before a change is considered done. `tests/` is not type-gated; the strict
basedpyright gate covers `src/`. The live Redis leg needs the password exported:
`REDIS_PASSWORD=… uv run --extra redis pytest tests/ -q`.

## Rules

- Do not depend on `tortoise-embeddings`. It monkey-patches the same functions and only one patch
  can win per function; `VectorField` is self-contained for that reason.
- Concrete models must redeclare `Meta.indexes`. Tortoise does not inherit them from abstract
  bases, and `NodeMixin`, `BaseGraphEdgeModel`, and `BaseHierarchyModel` raise at class creation
  if you forget. Opt out deliberately with `indexes = ()`.
- Raw SQL is only for what Tortoise cannot express: recursive CTEs, `DISTINCT ON`, `ts_rank_cd`,
  and `ARRAY` literals.
- No synchronous database API. Tortoise is async-only.
- Add dependencies with `uv add`, never by hand, so `pyproject.toml` and `uv.lock` stay in sync.

## Tests

- One module per feature area; group tests into `Test*` classes with `# ---` separators.
- `filterwarnings = ["error"]` is set, so a test that emits a warning fails the run.
- PostgreSQL-only features (pgvector, `ltree`, the index types, TimescaleDB) have no SQLite
  fallback and require the Docker database.
