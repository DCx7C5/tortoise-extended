# VectorField

Self-contained pgvector field for Tortoise ORM. Does not depend on `tortoise-embeddings`.

## Import

```python
from tortoise_extended import VectorField
```

## Constructor

```python
VectorField(
    dimensions: int | None = None,
    null: bool = False,
    default: Any = None,
    description: str | None = None,
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `dimensions` | `int \| None` | `None` | Number of vector dimensions |
| `null` | `bool` | `False` | Allow NULL values |
| `default` | `Any` | `None` | Default value |
| `description` | `str \| None` | `None` | Column comment |

### SQL Type

- **PostgreSQL:** `vector`
- **SQLite:** `BLOB` (fallback)

### Python Type

```python
list[float] | None
```

## Usage

### Basic Model

```python
from tortoise import fields, models
from tortoise_extended import VectorField


class Chunk(models.Model):
    id = fields.IntField(pk=True)
    content = fields.TextField()
    embedding = VectorField(dimensions=1536, null=True)

    class Meta:
        table = "chunks"
```

### With HNSW Index

```python
from tortoise import fields, models
from tortoise_extended import VectorField, HNSWIndex


class Chunk(models.Model):
    id = fields.IntField(pk=True)
    content = fields.TextField()
    embedding = VectorField(dimensions=1536)

    class Meta:
        table = "chunks"
        indexes = [HNSWIndex(fields=("embedding",), m=32, ef_construction=400)]
```

### Insert with Vector

```python
# From Python list
chunk = await Chunk.create(
    content="Hello world",
    embedding=[0.1, 0.2, 0.3, ...],  # 1536 dimensions
)

# From string (pgvector format)
chunk = await Chunk.create(
    content="Hello world",
    embedding="[0.1,0.2,0.3,...]",
)
```

### Query with Vector

```python
from tortoise_extended import L2Distance, CosineDistance

# Find similar chunks
query_vec = [0.1, 0.2, 0.3, ...]

# Cosine similarity
chunks = (
    await Chunk.filter(embedding__cosine_distance=[[query_vec], 0.5])
    .order_by("embedding__cosine_distance")
    .limit(10)
)

# L2 distance
chunks = (
    await Chunk.filter(embedding__l2_distance=[[query_vec], 0.3])
    .order_by("embedding__l2_distance")
    .limit(10)
)
```

## Input Formats

### list[float]

```python
embedding = [0.1, 0.2, 0.3, ...]
chunk = await Chunk.create(embedding=embedding)
```

### str

```python
embedding = "[0.1,0.2,0.3,...]"
chunk = await Chunk.create(embedding=embedding)
```

### memoryview (from asyncpg)

```python
# asyncpg returns memoryview for vector columns
chunk = await Chunk.get(id=1)
embedding = chunk.embedding  # list[float], decoded from memoryview
```

## Binary Format

When stored in PostgreSQL, vectors use pgvector's binary format:

```
Bytes 0-3:   Dimension count (uint32 little-endian)
Bytes 4-7:   Float32 dimension 0
Bytes 8-11:  Float32 dimension 1
...
Bytes N-4-N: Float32 dimension N-1
```

Total size: 4 + (dimensions × 4) bytes

## Memory Usage

| Dimensions | Bytes per Vector | 1M Vectors | HNSW Index |
|------------|------------------|------------|------------|
| 384 | 1,540 | 1.5 GB | 3 GB |
| 768 | 3,076 | 3 GB | 6 GB |
| 1536 | 6,148 | 6 GB | 12 GB |

## Validation

The field validates:
- Input is a list, string, or memoryview
- String format is valid (`"[float,float,...]"`)
- All values are numeric
- Dimensions match if specified

## Error Handling

```python
# Invalid dimensions
try:
    chunk = await Chunk.create(embedding=[0.1, 0.2])  # Chunk expects 1536
except Exception as e:
    print(f"Dimension mismatch: {e}")

# Invalid format
try:
    chunk = await Chunk.create(embedding="not a vector")
except Exception as e:
    print(f"Invalid format: {e}")
```

## Notes

- Vectors are stored as NULL if not provided
- Empty vectors (`[]`) are not allowed
- Maximum dimensions depends on pgvector version (16000 for 0.7+; 8192 for 0.5/0.6)
- HNSW index creation requires `vector_cosine_ops` or similar operator class
- IVFFlat requires data to exist before index creation
