# Installation

## Requirements

- Python 3.14+
- PostgreSQL 18+ with extensions:
  - [pgvector](https://github.com/pgvector/pgvector) — vector similarity search
  - `ltree` — hierarchical path queries
  - [TimescaleDB](https://www.timescale.com/) — time-series optimization

## Install via uv

```bash
uv add tortoise-extended
```

## Dependencies

The package installs these dependencies automatically:

```
tortoise-orm >=1.1.7,<1.2
asyncpg >=0.31.0,<0.32
pypika-tortoise >=0.6.5,<0.7
```

Optional Redis caching:

```bash
uv add "tortoise-extended[redis]"
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
cp .env.example .env   # configure POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
docker compose -f docker-compose.dev.yml up -d
```

This starts `postgres-ext` on `127.0.0.1:5433` (with `vector`, `ltree`,
`timescaledb`, `pg_trgm`, `uuid-ossp` extensions) and `redis-ext` on
`127.0.0.1:6380`. See [Docker setup](../docker/setup.md).
