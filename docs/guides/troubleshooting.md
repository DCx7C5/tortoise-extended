# Troubleshooting

## Common Issues

### Import Order Error

**Error:**
```
ImportError: cannot import name 'VectorField' from 'tortoise.fields'
```

**Cause:** `tortoise_extended` not imported before `tortoise`.

**Fix:**
```python
# Wrong
from tortoise import Tortoise
import tortoise_extended  # Too late!

# Right
import tortoise_extended  # First!

tortoise_extended.patch()  # Explicitly apply the monkey-patches
from tortoise import Tortoise
```

---

### Missing Extension Error

**Error:**
```
psycopg2.errors.UndefinedObject: type "vector" does not exist
```

**Cause:** pgvector extension not installed.

**Fix:**
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Or use the dev database, which includes pgvector:
```bash
docker compose -f docker-compose.dev.yml up -d
```

---

### Port Conflict

**Error:**
```
ConnectionRefusedError: [Errno 111] Connection refused
```

**Cause:** PostgreSQL not running on port 5433 (the dev database port).

**Fix:**
```python
# The dev database listens on 127.0.0.1:5433
db_url = "postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended"

# Start it with
# docker compose -f docker-compose.dev.yml up -d
```

---

### Dimension Mismatch

**Error:**
```
DataError: vector dimension mismatch
```

**Cause:** Embedding dimensions don't match VectorField specification.

**Fix:**
```python
# Wrong: expects 1536 dimensions
class Chunk(models.Model):
    embedding = VectorField(dimensions=1536)


await Chunk.create(embedding=[0.1, 0.2, 0.3])  # Only 3 dimensions

# Right: match dimensions
await Chunk.create(embedding=[0.1] * 1536)  # 1536 dimensions
```

---

### IVFFlat Index Error

**Error:**
```
psycopg2.errors.InvalidParameterValue: tablesample method "ivfflat" is not yet supported
```

**Cause:** An `IVFFlatIndex` was declared on a table before any rows existed.
pgvector's IVFFlat index has no support for empty tables — the underlying
`CREATE INDEX` is rejected.

**Fix:** create the table, load data, then create the index in a later step:

```python
# Insert data first
await Entity.bulk_create([...])

# Then create index
await connections.get("default").execute_query("""
    CREATE INDEX ivfflat_entities_embedding
    ON entities USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
""")
```

If the index already exists and you still see this error, drop it first
(`DROP INDEX IF EXISTS ivfflat_entities_embedding`), load the data, then
re-create the index.

**Search-time note:** IVFFlat recall improves by raising `ivfflat.probes`
(analogous to `hnsw.ef_search`):

```sql
SET ivfflat.probes = 10;
```

---

### Connection Pool Exhaustion

**Error:**
```
asyncpg.exceptions.ConnectionPoolTimeoutError: Timed out while acquiring connection
```

**Cause:** Too many concurrent queries.

**Fix:**
```python
# Increase pool size
await Tortoise.init(
    db_url="postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended?min_size=10&max_size=50",
    modules={"models": ["myapp.models"]},
)

# Or reduce concurrent queries
import asyncio

semaphore = asyncio.Semaphore(10)


async def limited_query():
    async with semaphore:
        return await Entity.all()
```

---

### Query Timeout

**Error:**
```
asyncpg.exceptions.QueryCanceledError: query canceled due to user request
```

**Cause:** Query taking too long.

**Fix:**
```python
# Add LIMIT
entities = (
    await Entity.filter(embedding__cosine_distance=[[query_vec], 0.5])
    .order_by("embedding__cosine_distance")
    .limit(10)
)

# Add index
await connections.get("default").execute_query("""
    CREATE INDEX hnsw_entities_embedding
    ON entities USING hnsw (embedding vector_cosine_ops)
    WITH (m = 32, ef_construction = 400);
""")

# Tune search-time parameters (per session)
await connections.get("default").execute_query("SET hnsw.ef_search = 100")
```

---

### Memory Error

**Error:**
```
MemoryError: Unable to allocate array
```

**Cause:** Too many vectors in memory.

**Fix:**
```python
# Use pagination
async def paginated_search(query_vec, page: int = 1, page_size: int = 10):
    offset = (page - 1) * page_size
    return (
        await Entity.filter(embedding__cosine_distance=[[query_vec], 0.5])
        .order_by("embedding__cosine_distance")
        .offset(offset)
        .limit(page_size)
    )


# Or use streaming
async def streaming_search(query_vec):
    async for entity in Entity.filter(
        embedding__cosine_distance=[[query_vec], 0.5]
    ).order_by("embedding__cosine_distance"):
        yield entity
```

---

### JSONB Error

**Error:**
```
TypeError: Object of type UUID is not JSON serializable
```

**Cause:** UUID objects in metadata.

**Fix:**
```python
import json
from uuid import UUID


class UUIDEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        return super().default(obj)


# Use custom encoder
metadata = json.dumps({"id": uuid_obj}, cls=UUIDEncoder)
```

---

### Library Import Errors

**Error:**
```
ModuleNotFoundError: No module named 'tortoise_extended.models'
ModuleNotFoundError: No module named 'tortoise_extended.backends.client'
ModuleNotFoundError: No module named 'tortoise_extended.expressions.graph_functions'
```

**Cause:** Those module paths come from earlier drafts and do not exist in
the current code (the public surface is `tortoise_extended` top-level
re-exports plus the real submodules below). Importing the old paths raises
`ModuleNotFoundError`. The current layout is:

- `tortoise_extended.fields` — `VectorField`, `LTreeField`
- `tortoise_extended.indexes` — `HNSWIndex`, `IVFFlatIndex`, `GiSTIndex`
- `tortoise_extended.expressions` — `RecursiveCTE`, `GraphTraversal`,
  `HybridSearch`, `shortest_path`, `all_paths`, `find_cycles`
- `tortoise_extended.models` — `BaseGraphNodeModel`, `BaseGraphEdgeModel`, `BaseHierarchyModel`
- `tortoise_extended.timescale` — `HypertableManager`, `BaseEventStreamModel`, ...
- `tortoise_extended.cache` — `RedisCache`, `BaseCacheableModel`, `CachedQuerySet`
- `tortoise_extended.migrations.operations` — `CreateHypertable`, ...

**Fix:** import from the actual modules, or use the top-level re-exports:

```python
from tortoise_extended import VectorField, HNSWIndex, HybridSearch, shortest_path
```

---

### Migration Error

**Error:**
```
ValueError: Table 'entities' already exists
```

**Cause:** Table already exists.

**Fix:**
```python
# Skip schema generation
await Tortoise.init(
    db_url="postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended",
    modules={"models": ["myapp.models"]},
    generate_schemas=False,  # Don't create tables
)

# Or drop and recreate
await connections.get("default").execute_query("DROP TABLE IF EXISTS entities CASCADE")
await Tortoise.generate_schemas()
```

## Debugging

### Enable Logging

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("tortoise").setLevel(logging.DEBUG)
logging.getLogger("asyncpg").setLevel(logging.DEBUG)
```

### Check Query Plan

```python
sql = "EXPLAIN ANALYZE SELECT ..."
result = await connections.get("default").execute_query(sql, [])
print(result[1])
```

### Profile Code

```python
import cProfile
import pstats

profiler = cProfile.Profile()
profiler.enable()

# Your code here
results = (
    await Entity.filter(embedding__cosine_distance=[[query_vec], 0.5])
    .order_by("embedding__cosine_distance")
    .limit(10)
)

profiler.disable()
stats = pstats.Stats(profiler)
stats.sort_stats("cumulative")
stats.print_stats(20)
```

### Check Connection Pool

```python
pool = connections.get("default").pool
print(f"Pool size: {pool.get_size()}")
print(f"Free connections: {pool.get_idle_size()}")
```

## Performance Issues

### Slow Queries

1. **Check indexes:**
   ```sql
   SELECT indexname, indexdef 
   FROM pg_indexes 
   WHERE tablename = 'entities';
   ```

2. **Analyze query:**
   ```sql
   EXPLAIN ANALYZE 
   SELECT * FROM entities 
   WHERE embedding <=> $1::vector < 0.5;
   ```

3. **Add missing index:**
   ```sql
   CREATE INDEX hnsw_entities_embedding 
   ON entities USING hnsw (embedding vector_cosine_ops);
   ```

### High Memory Usage

1. **Check table sizes:**
   ```sql
   SELECT pg_size_pretty(pg_total_relation_size('entities'));
   ```

2. **Check index sizes:**
   ```sql
   SELECT pg_size_pretty(pg_relation_size('hnsw_entities_embedding'));
   ```

3. **Reduce HNSW parameters:**
   ```python
   class Chunk(models.Model):
       embedding = VectorField(dimensions=1536)

       class Meta:
           indexes = [HNSWIndex(fields=("embedding",), m=8, ef_construction=100)]
   ```

### Connection Pool Issues

1. **Check pool status:**
   ```python
   pool = connections.get("default").pool
   print(f"Size: {pool.get_size()}")
   print(f"Free: {pool.get_idle_size()}")
   ```

2. **Increase pool size:**
   ```python
   await Tortoise.init(
       db_url="postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended?min_size=10&max_size=50",
       modules={"models": ["myapp.models"]},
   )
   ```

3. **Add connection timeout:**
   ```python
   await Tortoise.init(
       db_url="postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended?timeout=30",
       modules={"models": ["myapp.models"]},
   )
   ```

## Getting Help

### Check Logs

```bash
# PostgreSQL logs
tail -f /var/log/postgresql/postgresql-18-main.log

# Application logs
python -c "import logging; logging.basicConfig(level=logging.DEBUG)"
```

### Community Resources

- [Tortoise ORM Documentation](https://tortoise-orm.readthedocs.io/)
- [pgvector Documentation](https://github.com/pgvector/pgvector)
- [asyncpg Documentation](https://magicstack.github.io/asyncpg/)
- [TimescaleDB Documentation](https://docs.timescale.com/)

### Report Issues

If you encounter a bug:

1. Check the [GitHub Issues](https://github.com/your-repo/tortoise-extended/issues)
2. Create a minimal reproduction case
3. Include:
   - Python version
   - Package versions (`pip freeze`)
   - PostgreSQL version
   - Error message and traceback
   - Steps to reproduce
