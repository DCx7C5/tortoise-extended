# Installation

## Requirements

- Python 3.14+
- PostgreSQL 18+ with extensions:
  - [pgvector](https://github.com/pgvector/pgvector) — vector similarity search
  - `ltree` — hierarchical path queries
  - [TimescaleDB](https://www.timescale.com/) — time-series optimization

## Install from git

There is no PyPI release yet, so `uv add tortoise-extended` resolves nothing — install the
package as a git dependency and pin a release tag:

```bash
uv add "tortoise-extended @ git+https://github.com/DCx7C5/tortoise-extended@v0.2.0"
```

`main` tracks development, so a tag is the fixed point to build against; `v0.2.0` is the current
release and [CHANGELOG](../../CHANGELOG.md) records what each version contains.

## Dependencies

The package installs these dependencies automatically:

```
tortoise-orm[asyncpg] >=1.1.7,<1.2   # asyncpg driver comes via this extra
pypika-tortoise >=0.6.5,<0.7         # query builder used by the expressions
msgspec >=0.21.0                     # fast serialization
argon2-cffi >=25.1.0                 # argon2id hashing for BaseUserModel
pglast >=8.9,<9                      # PostgreSQL grammar for continuous aggregates
```

No `tortoise-orm-stubs` dependency is installed: `tortoise-orm` ships its own `py.typed` marker,
so consumers get `tortoise.*` typing inline. The overlay under
`src/tortoise_extended/stubs/` is a development artifact excluded from the built wheel — it
corrects specific gaps for this library's strict type gate and never reaches a consumer.

Optional Redis caching (`redis[hiredis]>=7.0.0`):

```bash
uv add "tortoise-extended[redis] @ git+https://github.com/DCx7C5/tortoise-extended@v0.2.0"
```

## Verify Installation

```python
import tortoise_extended
from tortoise_extended import VectorField, HNSWIndex, LTreeField, GiSTIndex

tortoise_extended.patch()  # explicitly apply the monkey-patches

print("tortoise-extended installed and working")
```

## Optional: Docker Database

For local development with the full PostgreSQL + pgvector + TimescaleDB
database (plus Redis), use the provided Compose file:

```bash
cp .env.example .env   # set POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
                       # and REDIS_PASSWORD (mandatory; the stack fails to
                       # configure without it)
docker compose -f docker-compose.dev.yml up -d
```

This starts `postgres-ext` on `127.0.0.1:5434` (with `vector`, `ltree`,
`timescaledb`, `pg_trgm`, `uuid-ossp` extensions) and `redis-ext` on
`127.0.0.1:6381`. See [Docker setup](../docker/setup.md).
