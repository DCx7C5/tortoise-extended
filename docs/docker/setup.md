# Docker Setup

## Overview

The development stack provides a PostgreSQL 18 instance with pgvector and
TimescaleDB pre-installed (`postgres-ext`), plus a Redis 7 instance for the
optional cache backend (`redis-ext`). Both are managed with
`docker-compose.dev.yml` and listen only on localhost.

| Service | Image / Build | Host port |
|---------|---------------|-----------|
| `postgres-ext` | `docker/postgres-ext/Dockerfile` (PG 18, pgvector 0.8.6, TimescaleDB) | `127.0.0.1:5434` |
| `redis-ext` | `docker/redis-ext/Dockerfile` (Redis 7-alpine, digest-pinned) | `127.0.0.1:6381` |

## Quick Start

```bash
# 1. Create .env (required by docker-compose.dev.yml)
cp .env.example .env
#    set POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB and a
#    REDIS_PASSWORD (mandatory — redis-ext will not start without it)

# 2. Start the stack
docker compose -f docker-compose.dev.yml up -d

# 3. Follow postgres logs (init scripts run on first start)
docker compose -f docker-compose.dev.yml logs -f postgres-ext

# 4. Stop
docker compose -f docker-compose.dev.yml down
```

## Connecting

```bash
# From host (postgres listens only on 127.0.0.1:5434)
psql -h 127.0.0.1 -p 5434 -U <POSTGRES_USER> -d <POSTGRES_DB>

# Redis
redis-cli -h 127.0.0.1 -p 6381 -a "$REDIS_PASSWORD" --no-auth-warning ping
```

AUTH is mandatory: the container refuses to start without `REDIS_PASSWORD`, so
an unauthenticated `PING` answers `NOAUTH Authentication required.`

## From Python

```python
import tortoise_extended
from tortoise import Tortoise

await Tortoise.init(
    db_url="postgres://<user>:<password>@127.0.0.1:5434/<db>",
    modules={"models": ["myapp.models"]},
)
```

## Init Scripts

A single script runs from `docker/postgres-ext/scripts/` on first start:

1. `00-extensions.sql` — Install PostgreSQL extensions (`vector`, `ltree`, `timescaledb`, `pg_trgm`, `uuid-ossp`)

## Base Image Pinning

- The Postgres base image is **digest-pinned** (`postgres:18@sha256:...`)
- pgvector and TimescaleDB are **commit-pinned** via ARG

Bump version + pin together, deliberately, when upgrading.

## Health Checks

```bash
docker compose -f docker-compose.dev.yml exec postgres-ext pg_isready -U <POSTGRES_USER>
docker compose -f docker-compose.dev.yml exec postgres-ext psql -U <POSTGRES_USER> -d <POSTGRES_DB> -c "\dx"
docker compose -f docker-compose.dev.yml logs -f postgres-ext
```

## Troubleshooting

### Container Won't Start

```bash
docker compose -f docker-compose.dev.yml logs postgres-ext
lsof -i :5434
df -h
```

### Init Script Fails

```bash
docker compose -f docker-compose.dev.yml logs postgres-ext 2>&1 | grep "ERROR"
# Re-run init: remove the container (and its volume) so scripts re-run on next start
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up -d
```

### Connection Refused

```bash
docker compose -f docker-compose.dev.yml ps
docker compose -f docker-compose.dev.yml logs -f postgres-ext
```

## Running the Test Suite

```bash
uv sync --all-extras
uv run pytest tests/ -v
```

`tests/test_pg_integration.py` and the `*_integration.py` modules require the Docker
database (`postgres-ext`); so does every module that registers models. The database-backed
modules skip themselves when `localhost:5434` is unreachable, so the pure unit tests still run
without a container.
