# Performance Tuning

## Overview

This guide covers optimization strategies for tortoise-extended in production environments.

## Vector Search Optimization

### Index Selection

| Use Case | Recommended Index | Parameters |
|----------|-------------------|------------|
| Read-heavy | HNSW | m=32, ef_construction=400 |
| Write-heavy | IVFFlat | lists=sqrt(rows) |
| Balanced | HNSW | m=16, ef_construction=200 |
| Low memory | IVFFlat | lists=100 |

### Query Optimization

```python
# Bad: No LIMIT, no filtering
entities = await Entity.all()

# Good: LIMIT + vector filter
entities = (
    await Entity.filter(embedding__cosine_distance=[[query_vec], 0.5])
    .order_by("embedding__cosine_distance")
    .limit(10)
)

# Better: Type filter + vector filter
entities = (
    await Entity.filter(
        type="TECHNOLOGY", embedding__cosine_distance=[[query_vec], 0.5]
    )
    .order_by("embedding__cosine_distance")
    .limit(10)
)
```

### Batch Operations

```python
# Bad: Individual inserts
for entity in entities:
    await Entity.create(**entity)

# Good: Bulk insert
await Entity.bulk_create([Entity(**entity) for entity in entities])
```

### Memory Management

```python
# Estimate memory usage
dimensions = 1536
rows = 1_000_000

# Vector storage
vector_storage = rows * (4 + dimensions * 4) / (1024**3)  # ~5.7 GB

# HNSW index (2x vector storage)
hnsw_storage = vector_storage * 2  # ~11.4 GB

# IVFFlat index (1x vector storage)
ivfflat_storage = vector_storage  # ~5.7 GB

# Total
total_storage = vector_storage + hnsw_storage  # ~17.1 GB
```

## Graph Traversal Optimization

### Index Strategy

```sql
-- Critical indexes for traversal
CREATE INDEX ix_relationships_source ON relationships(source_entity_id);
CREATE INDEX ix_relationships_target ON relationships(target_entity_id);

-- Composite index for common patterns
CREATE INDEX ix_relationships_source_type ON relationships(source_entity_id, type);

-- Entity lookup index
CREATE INDEX ix_entities_title ON entities(title);
```

### Depth Limiting

```python
# Bad: Unbounded traversal
traversal = GraphTraversal(Entity, Relationship)
neighbors = await traversal.neighbors(node_id=entity.id, max_depth=10)

# Good: Limited depth
neighbors = await traversal.neighbors(node_id=entity.id, max_depth=2)
```

### Materialized Views

```sql
-- Pre-compute 1-hop neighborhoods
CREATE MATERIALIZED VIEW entity_neighbors AS
SELECT 
    e.id AS entity_id,
    e.title,
    r.target_entity_id,
    t.title AS target_title,
    r.type AS relationship_type
FROM entities e
JOIN relationships r ON e.id = r.source_entity_id
JOIN entities t ON r.target_entity_id = t.id;

-- Refresh periodically
REFRESH MATERIALIZED VIEW entity_neighbors;
```

### Query Patterns

```python
# Bad: Multiple queries per node
entity = await Entity.get(title="Python")
outgoing = await Relationship.outgoing(source_id=entity.id).all()
incoming = await Relationship.incoming(target_id=entity.id).all()

# Good: eager-load the related node in a single batch
from tortoise.query_utils import Prefetch

nodes = await Entity.all().prefetch_related(
    Prefetch("relationships", queryset=Relationship.all().select_related("target"))
)
```

## Connection Pool Tuning

### Pool Sizes

| Environment | min_size | max_size | Timeout |
|-------------|----------|----------|---------|
| Development | 1 | 5 | 30s |
| Production | 5 | 20 | 60s |
| High Traffic | 10 | 50 | 30s |
| Batch Processing | 20 | 100 | 120s |

### Configuration

```python
await Tortoise.init(
    db_url="postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended"
    "?min_size=5"
    "&max_size=20"
    "&timeout=60"
    "&max_inactive_connection_lifetime=300",
    modules={"models": ["myapp.models"]},
)
```

### Monitoring

```python
# Check pool status
pool = connections.get("default").pool
print(f"Size: {pool.get_size()}")
print(f"Free: {pool.get_idle_size()}")
```

## Query Optimization

### Index Usage

```python
# Verify the planner uses the index instead of a sequential scan
sql = "EXPLAIN ANALYZE SELECT * FROM entities WHERE type = 'TECHNOLOGY'"
result = await connections.get("default").execute_query(sql, [])
print(result[1])
```

Indexes are declared on the model (`Meta.indexes`) or via raw DDL; PostgreSQL
picks them automatically when the query shape matches.

### Query Analysis

```sql
-- Analyze query performance
EXPLAIN ANALYZE
SELECT e.id, e.title, e.type
FROM entities e
WHERE e.embedding <=> $1::vector < 0.5
ORDER BY e.embedding <=> $1::vector
LIMIT 10;
```

### Caching

```python
# Initialize Redis once at startup
from tortoise import Tortoise, fields, models
from tortoise_extended import RedisCache, cached
from tortoise_extended.cache import BaseCacheableModel, CachedQuerySet

await RedisCache.init(url="redis://localhost:6379/0")
await Tortoise.init(
    db_url="postgres://user:pass@localhost:5432/db",
    modules={"models": ["myapp.models"]},
)


# Model-level cache (BaseCacheableModel) — auto-invalidated on save/delete
class Entity(BaseCacheableModel, models.Model):
    _cache_ttl = 600
    title = fields.CharField(max_length=512)

    class Meta:
        table = "entities"


entity = await Entity.get_cached(id="uuid-here")

# Query-level cache (CachedQuerySet — a drop-in QuerySet subclass)
from tortoise_extended.cache import CachedQuerySet

rows = await CachedQuerySet(Event).filter(kind="click").cache(ttl=300)


# Function-level cache
@cached(ttl=300)
async def search(query: str):
    return await execute_search(query)
```

See [Cache (Redis)](../api/cache.md) for the full API.

## Batch Processing

### Bulk Insert

```python
# Process in chunks
async def bulk_insert(entities: list[dict], chunk_size: int = 1000):
    for i in range(0, len(entities), chunk_size):
        chunk = entities[i : i + chunk_size]
        await Entity.bulk_create([Entity(**entity) for entity in chunk])
```

### Parallel Processing

```python
import asyncio


async def parallel_search(query_embeddings: list[list[float]]):
    async def search_single(embedding):
        return (
            await Entity.filter(embedding__cosine_distance=[[embedding], 0.5])
            .order_by("embedding__cosine_distance")
            .limit(10)
        )

    # Execute in parallel
    tasks = [search_single(emb) for emb in query_embeddings]
    results = await asyncio.gather(*tasks)

    return results
```

## Monitoring

### Metrics to Track

1. **Query latency** — p50, p95, p99
2. **Connection pool usage** — active, idle, waiting
3. **Index size** — HNSW vs IVFFlat memory usage
4. **Cache hit rate** — query_cache hit_count / total queries
5. **Traversal depth** — average and max depth

### Logging

```python
import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("tortoise").setLevel(logging.DEBUG)
logging.getLogger("asyncpg").setLevel(logging.DEBUG)
```

### Profiling

```python
import cProfile
import pstats


async def profile_search():
    profiler = cProfile.Profile()
    profiler.enable()

    # Execute search
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

## Production Checklist

- [ ] HNSW indexes created with proper parameters
- [ ] Connection pool sized appropriately
- [ ] Query limits enforced
- [ ] Materialized views created for frequent traversals
- [ ] Cache enabled for repeated queries
- [ ] Monitoring configured
- [ ] Logging level set to INFO
- [ ] Backup strategy in place
- [ ] Load testing completed
- [ ] Failover configured

## Common Issues

### Slow Queries

```python
# Check query core
sql = "EXPLAIN ANALYZE SELECT ..."
result = await connections.get("default").execute_query(sql, [])

# Add missing index
await connections.get("default").execute_query("""
    CREATE INDEX ix_entities_type ON entities(type);
""")
```

### High Memory Usage

```python
# Reduce HNSW parameters
class Chunk(models.Model):
    embedding = VectorField(dimensions=1536)

    class Meta:
        indexes = [HNSWIndex(fields=("embedding",), m=8, ef_construction=100)]
```

### Connection Pool Exhaustion

```python
# Increase pool size
await Tortoise.init(
    db_url="postgres://postgres:postgres@127.0.0.1:5433/tortoise_extended?min_size=10&max_size=50",
    modules={"models": ["myapp.models"]},
)
```

### Index Build Timeout

```python
# Build IVFFlat index in batches
await connections.get("default").execute_query("""
    CREATE INDEX CONCURRENTLY ivfflat_entities_embedding 
    ON entities USING ivfflat (embedding vector_cosine_ops) 
    WITH (lists = 100);
""")
```
