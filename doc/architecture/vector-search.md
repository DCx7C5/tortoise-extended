# Vector Search

## Overview

`tortoise-extended` provides pgvector integration for approximate nearest-neighbor (ANN) search directly within Tortoise ORM. No external vector database required.

## VectorField

### How It Works

The `VectorField` stores vectors in PostgreSQL using the pgvector extension. It handles three incoming formats from asyncpg:

1. **`list[float]`** — Standard Python list (direct pass-through)
2. **`str`** — String format from pgvector console (`"[0.1,0.2,0.3]"`)
3. **`memoryview`** — Binary format from pgvector protocol (4-byte header + N × 4-byte floats)

### Binary Format

```
Bytes 0-3:   Header (0x00000000 for valid vector)
Bytes 4-7:   Float32 dimension 0
Bytes 8-11:  Float32 dimension 1
...
Bytes N-4-N: Float32 dimension N-1
```

Total size: 4 + (dimensions × 4) bytes

### Memory Considerations

- **1536 dimensions** (OpenAI embeddings): ~6 KB per vector
- **768 dimensions** (MiniLM): ~3 KB per vector
- **384 dimensions** (small models): ~1.5 KB per vector

At 1M entities with 1536-dim embeddings:
- Storage: ~6 GB
- HNSW index: ~12 GB
- IVFFlat index: ~6 GB

## Index Types

### HNSW (Hierarchical Navigable Small World)

Best for: Read-heavy workloads, high query throughput.

```python
from tortoise_extended import HNSWIndex

class Entity(models.Model):
    embedding = VectorField(dimensions=1536)

    class Meta:
        indexes = [HNSWIndex(fields=("embedding",), m=32, ef_construction=400)]
```

**Parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `m` | 16 | Max connections per layer |
| `ef_construction` | 200 | Candidate list size during build |
| `ef` (query) | 64 | Candidate list size during search |

**Trade-offs:**
- Higher `m` → better recall, more memory
- Higher `ef_construction` → better index quality, slower build
- Higher `ef` → better recall, slower queries

**Recommended settings:**

| Use Case | m | ef_construction | ef |
|----------|---|-----------------|-----|
| General | 16 | 200 | 64 |
| High recall | 32 | 400 | 128 |
| Low memory | 8 | 100 | 32 |

### IVFFlat (Inverted File with Flat quantization)

Best for: Write-heavy workloads, large datasets.

```python
from tortoise_extended import IVFFlatIndex

class Entity(models.Model):
    embedding = VectorField(dimensions=1536)

    class Meta:
        indexes = [IVFFlatIndex(fields=("embedding",), lists=100)]
```

**Parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `lists` | 100 | Number of lists (clusters) |

**Rule of thumb:** `lists = sqrt(rows)` for < 1M rows, `lists = rows / 1000` for > 1M rows.

**Trade-offs:**
- More lists → better recall, slower queries
- Fewer lists → faster queries, lower recall
- Requires data to exist before index creation

## Distance Operators

### L2 Distance (Euclidean)

```python
# Query
entities = await Entity.filter(
    embedding__l2_distance=([query_vec, 0.5])
).order_by("embedding__l2_distance").limit(10)
```

**SQL:** `<->` operator
**Range:** 0 to √(2 × dimensions) (0 to ~55 for 1536-dim)
**Use case:** General similarity, when magnitude matters

### Cosine Distance

```python
# Query
entities = await Entity.filter(
    embedding__cosine_distance=([query_vec, 0.3])
).order_by("embedding__cosine_distance").limit(10)
```

**SQL:** `<=>` operator
**Range:** 0 to 2 (0 = identical, 2 = opposite)
**Use case:** Semantic similarity, normalized vectors

### Inner Product

```python
# Query
entities = await Entity.filter(
    embedding__inner_product=([query_vec, 0.8])
).order_by("embedding__inner_product").limit(10)
```

**SQL:** `<#>` operator (negated)
**Range:** -∞ to √(dimensions) (higher = more similar)
**Use case:** When vectors are normalized, faster than cosine

## Query Patterns

### Basic Similarity Search

```python
from myapp.models import Entity  # define in your project

# Find similar entities
query_embedding = await get_embedding("machine learning")
similar = await Entity.filter(
    embedding__cosine_distance=([query_embedding, 0.5])
).order_by("embedding__cosine_distance").limit(10)
```

### Filtered Search

```python
# Search within entity type
similar = await Entity.filter(
    type="TECHNOLOGY",
    embedding__cosine_distance=([query_embedding, 0.5])
).order_by("embedding__cosine_distance").limit(10)

# Search with multiple filters
similar = await Entity.filter(
    type__in=["TECHNOLOGY", "CONCEPT"],
    metadata__contains={"source": "arxiv"},
    embedding__cosine_distance=([query_embedding, 0.5])
).order_by("embedding__cosine_distance").limit(10)
```

### Hybrid Search (Vector + Full-Text)

Use the `HybridSearch` class (vector + full-text scoring):

```python
from tortoise_extended import HybridSearch

search = HybridSearch(
    model=Entity,
    vector_field="embedding",
    text_field="description",
    vector_weight=0.7,
    text_weight=0.3,
)

results = await search.search(
    query_vector=[0.1, 0.2, ...],
    query_text="machine learning framework",
    max_results=20,
)
```

### Batch Search

```python
# Find similar for multiple queries
async def batch_search(query_embeddings: list[list[float]], limit: int = 10):
    results = []
    for embedding in query_embeddings:
        similar = await Entity.filter(
            embedding__cosine_distance=([embedding, 0.5])
        ).order_by("embedding__cosine_distance").limit(limit)
        results.append(similar)
    return results
```

## Performance Tuning

### Index Creation

```python
# Create index after data insertion for IVFFlat
await connections.get("default").execute_query("""
    CREATE INDEX ivfflat_entities_embedding 
    ON entities USING ivfflat (embedding vector_cosine_ops) 
    WITH (lists = 100);
""")
```

### Query Optimization

1. **Use `LIMIT` early** — Always limit results to reduce memory
2. **Filter before vector search** — Reduce candidate set with WHERE clauses
3. **Use `ef` parameter** — Tune recall/speed trade-off per query
4. **Batch inserts** — Use `bulk_create` for large datasets

### Memory Management

```python
# Estimate memory usage
dimensions = 1536
rows = 1_000_000
bytes_per_vector = 4 + (dimensions * 4)  # 6148 bytes
total_storage = rows * bytes_per_vector / (1024**3)  # ~5.7 GB

# HNSW index uses ~2x storage
hnsw_storage = total_storage * 2  # ~11.4 GB
```

## Comparison with Other Solutions

| Feature | pgvector | Pinecone | Weaviate |
|---------|----------|----------|----------|
| HNSW | ✅ | ✅ | ✅ |
| IVFFlat | ✅ | ❌ | ❌ |
| SQL filters | ✅ | ❌ | Limited |
| Full-text search | ✅ | ❌ | ✅ |
| ACID transactions | ✅ | ❌ | ❌ |
| Cost | Free | $70/mo+ | $25/mo+ |
| Latency (top-10) | <5ms | <10ms | <10ms |
