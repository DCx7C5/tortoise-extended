# Configuration

## Tortoise ORM Initialization

### Basic Configuration

```python
import tortoise_extended  # Must be first import

tortoise_extended.patch()  # Explicitly apply monkey-patches (idempotent)
from tortoise import Tortoise

await Tortoise.init(
    db_url="postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended",
    modules={"models": ["myapp.models"]},
)
```

### Configuration Options

```python
await Tortoise.init(
    db_url="postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended",
    modules={"models": ["myapp.models"]},
    # Optional: generate schemas (creates tables if they don't exist)
    # generate_schemas=True,
)
```

## Database URL Format

The `db_url` parameter follows this format:

```
postgres://username:password@host:port/database_name
```

### Examples

```python
# Local development (Docker)
db_url = "postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended"

# Remote PostgreSQL
db_url = "postgres://myuser:mypass@db.example.com:5432/mydb"

# With SSL
db_url = "postgres://user:pass@host:5432/mydb?ssl=require"

# With connection pool
db_url = "postgres://user:pass@host:5432/mydb?min_size=5&max_size=20"
```

## Environment Variables

```bash
# Database connection
export POSTGRES_USER="postgres"
export POSTGRES_PASSWORD="postgres"
export POSTGRES_DB="tortoise_extended"
# Optional: full Tortoise DB URL (overrides the three vars above)
export DATABASE_URL="postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended"

# Redis cache
export REDIS_URL="redis://localhost:6379/0"

# Default vector dimension for quickstart models
export PGVECTOR_SEARCH_DIM=1536
```

## Schema Generation

```python
# Auto-create all tables
await Tortoise.generate_schemas()
```

## Connection Pool Settings

```python
await Tortoise.init(
    db_url="postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended?min_size=5&max_size=20",
    modules={"models": ["myapp.models"]},
)
```

### Recommended Pool Settings

| Environment | min_size | max_size |
|-------------|----------|----------|
| Development | 1 | 5 |
| Production | 5 | 20 |
| High Traffic | 10 | 50 |

## Logging

```python
import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("tortoise").setLevel(logging.DEBUG)
```
