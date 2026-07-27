# Indexes

HNSW and IVFFlat index types for pgvector approximate nearest-neighbor search.

## HNSWIndex

Hierarchical Navigable Small World index. Best for read-heavy workloads.

### Import

```python
from tortoise_extended import HNSWIndex
```

### Constructor

```python
HNSWIndex(
    fields: tuple[str, ...],
    m: int = 16,
    ef_construction: int = 200,
    dist_metric: str = "vector_l2_ops",
    name: str | None = None,
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `fields` | `tuple[str, ...]` | Required | Field names to index |
| `m` | `int` | `16` | Max connections per layer |
| `ef_construction` | `int` | `200` | Candidate list size during build |
| `dist_metric` | `str` | `"vector_l2_ops"` | Distance metric |
| `name` | `str \| None` | `None` | Custom index name |

### Distance Metrics

| Value | Description |
|-------|-------------|
| `vector_l2_ops` | Euclidean distance |
| `vector_ip_ops` | Inner product |
| `vector_cosine_ops` | Cosine distance |

### Usage

```python
from tortoise import fields, models
from tortoise_extended import VectorField, HNSWIndex

class Chunk(models.Model):
    id = fields.IntField(pk=True)
    content = fields.TextField()
    embedding = VectorField(dimensions=1536)

    class Meta:
        table = "chunks"
        indexes = [
            HNSWIndex(
                fields=("embedding",),
                m=32,
                ef_construction=400,
                dist_metric="vector_cosine_ops",
            )
        ]
```

### Generated DDL

```sql
CREATE INDEX "hnsw_chunks_embedding_abc123" ON chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 32, ef_construction = 400);
```

### Parameters Guide

**m (connections per layer):**

| Value | Memory | Recall | Build Time |
|-------|--------|--------|------------|
| 8 | Low | Lower | Fast |
| 16 | Medium | Good | Medium |
| 32 | High | Better | Slow |
| 64 | Very High | Best | Very Slow |

**ef_construction (build candidate list):**

| Value | Index Quality | Build Time |
|-------|---------------|------------|
| 100 | Good | Fast |
| 200 | Better | Medium |
| 400 | Best | Slow |
| 800 | Excellent | Very Slow |

**Recommended settings:**

| Use Case | m | ef_construction | ef (query) |
|----------|---|-----------------|------------|
| General | 16 | 200 | 64 |
| High recall | 32 | 400 | 128 |
| Low memory | 8 | 100 | 32 |
| Production | 24 | 300 | 100 |

### Performance

- **Build time:** O(n × log(n))
- **Query time:** O(log(n))
- **Memory:** O(n × m × sizeof(float))
- **Recall:** >95% with proper parameters

---

## IVFFlatIndex

Inverted File with Flat quantization. Best for write-heavy workloads.

### Import

```python
from tortoise_extended import IVFFlatIndex
```

### Constructor

```python
IVFFlatIndex(
    fields: tuple[str, ...],
    lists: int = 100,
    dist_metric: str = "vector_l2_ops",
    name: str | None = None,
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `fields` | `tuple[str, ...]` | Required | Field names to index |
| `lists` | `int` | `100` | Number of lists (clusters) |
| `dist_metric` | `str` | `"vector_l2_ops"` | Distance metric |
| `name` | `str \| None` | `None` | Custom index name |

### Usage

```python
from tortoise import fields, models
from tortoise_extended import VectorField, IVFFlatIndex

class Chunk(models.Model):
    id = fields.IntField(pk=True)
    content = fields.TextField()
    embedding = VectorField(dimensions=1536)

    class Meta:
        table = "chunks"
        indexes = [
            IVFFlatIndex(
                fields=("embedding",),
                lists=100,
                dist_metric="vector_cosine_ops",
            )
        ]
```

### Generated DDL

```sql
CREATE INDEX "ivfflat_chunks_embedding_abc123" ON chunks
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

### Lists Parameter

| Rows | Recommended Lists |
|------|-------------------|
| < 10K | 100 |
| 10K - 100K | 100-500 |
| 100K - 1M | 500-2000 |
| > 1M | 2000-10000 |

**Rule of thumb:** `lists = sqrt(rows)` for < 1M rows, `lists = rows / 1000` for > 1M rows.

### Requirements

- Data must exist before index creation
- If table is empty, index creation will fail
- Use `CREATE INDEX ... AFTER` pattern for new tables

### Performance

- **Build time:** O(n × lists)
- **Query time:** O(n/lists + lists)
- **Memory:** O(n × sizeof(float))
- **Recall:** 90-95% with proper lists

---

## Comparison

| Feature | HNSW | IVFFlat |
|---------|------|---------|
| Build time | Slower | Faster |
| Query time | Faster | Slower |
| Memory | Higher | Lower |
| Recall | Higher | Lower |
| Write performance | Lower | Higher |
| Best for | Read-heavy | Write-heavy |
| Index size | ~2x data | ~1x data |

## Creating Indexes

### At Model Definition

```python
class Chunk(models.Model):
    embedding = VectorField(dimensions=1536)

    class Meta:
        indexes = [
            HNSWIndex(fields=("embedding",), m=32, ef_construction=400)
        ]
```

### After Data Insertion

```python
# For IVFFlat (requires data)
await connections.get("default").execute_query("""
    CREATE INDEX ivfflat_chunks_embedding 
    ON chunks USING ivfflat (embedding vector_cosine_ops) 
    WITH (lists = 100);
""")
```

### In Migrations

```python
from tortoise.migrations.operations import RunSQL

operations = [
    RunSQL(
        sql="CREATE INDEX hnsw_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 32, ef_construction = 400);",
        reverse_sql="DROP INDEX hnsw_chunks_embedding;",
    )
]
```

## Notes

- HNSW indexes are created with the table
- IVFFlat indexes require data to exist
- Both indexes are approximate (not exact)
- Recall improves with more resources (memory, build time)
- Distance metric must match query filter (cosine query → cosine index)
