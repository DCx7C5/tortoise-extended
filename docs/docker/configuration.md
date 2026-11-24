# Docker Configuration

This page documents the Docker Compose dev stack in `docker-compose.dev.yml`.

## Services

| Service       | Image / Build                                   | Host port    | Purpose            |
|---------------|-------------------------------------------------|--------------|--------------------|
| `postgres-ext`| `docker/postgres-ext/Dockerfile` (PG 18, pgvector 0.8.6, TimescaleDB) | `127.0.0.1:5434` | Graph/vector DB |
| `redis-ext`   | `docker/redis-ext/Dockerfile` (Redis 7-alpine, digest-pinned)         | `127.0.0.1:6381` | Cache backend  |

## Environment File

`docker-compose.dev.yml` reads `env_file: .env`. Create `.env` at the repo
root before starting the stack (`cp .env.example .env`):

```
POSTGRES_USER=postgres
POSTGRES_PASSWORD=CHANGEME     # required — compose refuses to configure without it
POSTGRES_DB=tortoise_test
```

`POSTGRES_PASSWORD` is required: compose interpolates it with `${…:?}`, so an
unset or empty value fails the stack instead of falling back to a committed
`postgres/postgres` login. The same applies to `REDIS_PASSWORD`.

`REDIS_*` variables are used if you override the Redis service settings.
`REDIS_PASSWORD` is **mandatory**: `redis-ext` refuses to start without it and
`docker-compose.dev.yml` fails configuration when it is empty. An
unauthenticated Redis accepts `CONFIG SET dir` + `SAVE`, i.e. it is a
file-write pivot; the loopback bind narrows who can reach it, not what an
unauthenticated client can do once it has. Use a throwaway dev value.

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

## Socket Directory and Worker Pools

`postgres-ext` publishes its UNIX socket into a **dedicated** host directory,
`/var/run/postgresql-ext`, bind-mounted to `/var/run/postgresql` inside the
container. Do not point that bind at the host's `/var/run/postgresql`: a host
PostgreSQL, or the neighbouring css-mcp stack, publishes its own
`.s.PGSQL.5432` there, and a socket-less `psql` / `pg_isready` then reaches
whichever container owns the name — which is how the healthcheck came to report
another cluster's databases as healthy.

The entrypoint re-owns the directory on start (`chown postgres:postgres`,
`chmod 3777`) because Docker creates a missing host path as `root:root` and
`/var/run` is tmpfs on the host, so the directory is recreated after every
reboot.

The server also runs with tuned worker pools:

| Setting | Value | Why |
|---|---|---|
| `max_worker_processes` | 48 | background workers for parallel queries and extension jobs |
| `timescaledb.max_background_workers` | 24 | one scheduler + two jobs per database with the extension |
| `max_connections` | 100 | test modules share a pool per module |
| `unix_socket_permissions` | 0770 | socket usable by the container's own user only |

TimescaleDB registers a scheduler per database that has the extension, plus two
jobs each. Seven test databases therefore need 21 workers, which exhausts the
stock defaults (`max_background_workers=16`, `max_worker_processes=8`) with
"out of background workers" / "no available background worker slots". If you
add databases, raise these together.

## Pinning Policy

- The Postgres base image is **digest-pinned** (`postgres:18@sha256:...`).
- pgvector and TimescaleDB are **commit-pinned** via `ARG` in
  `docker/postgres-ext/Dockerfile`.
- The build **checks** each pin: the value must be a 40-character sha, the
  checkout must resolve to exactly that commit, and `git describe --tags
  --exact-match` must return the expected tag (`v0.8.6` / `2.29.2`). A pin that
  resolves to a commit on the wrong branch fails the build instead of shipping.
- Bump version + pin together, deliberately, when upgrading.

See also [Docker setup](setup.md) and
[Getting started / Installation](../getting-started/installation.md).
