# Installation

## Requirements

- Python 3.14+
- PostgreSQL 18+ with extensions:
  - [pgvector](https://github.com/pgvector/pgvector) — vector similarity search
  - `ltree` — hierarchical path queries
  - [TimescaleDB](https://www.timescale.com/) — time-series optimization

## Install via pip

```bash
pip install tortoise-extended
```

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

## Optional: Docker Image

For development, use the provided Docker image with PostgreSQL + pgvector + TimescaleDB:

```bash
docker build -t tortoise-extended-pg .docker/postgres/
docker run -p 5432:5432 tortoise-extended-pg
```

This gives you a PostgreSQL 18 instance with all extensions pre-installed.
