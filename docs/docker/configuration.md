# Docker Configuration

This page documents the Docker Compose dev stack in `docker-compose.dev.yml`.

## Services

| Service       | Image / Build                                   | Host port    | Purpose            |
|---------------|-------------------------------------------------|--------------|--------------------|
| `postgres-ext`| `docker/postgres-ext/Dockerfile` (PG 18, pgvector 0.8.6, TimescaleDB) | `127.0.0.1:5434` | Graph/vector DB |
| `redis-ext`   | `docker/redis-ext/Dockerfile` (Redis 7-alpine, digest-pinned)         | `127.0.0.1:6381` | Cache backend  |

## Environment File

`docker-compose.dev.yml` reads `env_file: .env`. Create `.env` at the repo
root before starting the stack:

```
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=tortoise_extended
```

`REDIS_*` variables are used if you override the Redis service settings.
Set `REDIS_PASSWORD` in `.env` to enable Redis authentication — when set,
clients must authenticate with `--requirepass`.

## Starting / Stopping

```bash
docker compose -f docker-compose.dev.yml up -d       # start postgres-ext + redis-ext
docker compose -f docker-compose.dev.yml logs -f postgres-ext
docker compose -f docker-compose.dev.yml down        # stop
```

The database listens on the host **only** at `127.0.0.1:5434` — it is never
exposed publicly.

## Initialization Scripts

On first start, scripts from `docker/postgres-ext/scripts/` run in
alphabetical order:

- `00-extensions.sql` — creates `vector`, `ltree`, `timescaledb`,
  `pg_trgm`, `uuid-ossp`.

## Connecting

```python
# Tortoise ORM
db_url = "postgres://postgres:postgres@127.0.0.1:5434/tortoise_extended"

# psql
psql -h 127.0.0.1 -p 5434 -U postgres tortoise_extended

# Redis
redis-cli -h 127.0.0.1 -p 6381
```

## Pinning Policy

- The Postgres base image is **digest-pinned** (`postgres:18@sha256:...`).
- pgvector and TimescaleDB are **commit-pinned** via `ARG` in
  `docker/postgres-ext/Dockerfile`.
- Bump version + pin together, deliberately, when upgrading.

See also [Docker setup](setup.md) and
[Getting started / Installation](../getting-started/installation.md).
