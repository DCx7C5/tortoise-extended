# Configuration

## Tortoise ORM Initialization

### Basic Configuration

```python
import tortoise_extended  # Must be first import
from tortoise import Tortoise

await Tortoise.init(
    db_url="asyncpg://user:pass@localhost:5432/graphrag",
    modules={"models": ["myapp.models"]},
)
```

### Configuration Options

```python
await Tortoise.init(
    db_url="asyncpg://user:pass@localhost:5432/graphrag",
    modules={"models": ["myapp.models"]},
    # Optional: generate schemas (creates tables if they don't exist)
    # generate_schemas=True,
)
```

## Database URL Format

The `db_url` parameter follows this format:

```
asyncpg://username:password@host:port/database_name
```

### Examples

```python
# Local development (Docker)
db_url = "asyncpg://postgres:postgres@localhost:5432/graphrag"

# Remote PostgreSQL
db_url = "asyncpg://myuser:mypass@db.example.com:5432/graphrag"

# With SSL
db_url = "asyncpg://user:pass@host:5432/graphrag?ssl=require"

# With connection pool
db_url = "asyncpg://user:pass@host:5432/graphrag?min_size=5&max_size=20"
```

## Environment Variables

```bash
# Database connection
export GRAPHRAG_DB_URL="asyncpg://user:pass@localhost:5432/graphrag"

# Vector dimensions
export GRAPHRAG_VECTOR_DIM=1536

# Search parameters
export GRAPHRAG_DEFAULT_M=16
export GRAPHRAG_DEFAULT_EF=200
```

## Schema Generation

```python
# Auto-create all tables
await Tortoise.generate_schemas()
```

## Connection Pool Settings

```python
await Tortoise.init(
    db_url="asyncpg://user:pass@localhost:5432/graphrag?min_size=5&max_size=20",
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
