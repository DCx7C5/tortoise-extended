# VectorField

Self-contained pgvector field for Tortoise ORM. Does not depend on `tortoise-embeddings`.

## Requirements

- **PostgreSQL with the `vector` extension** — run `CREATE EXTENSION vector` before any
  vector read or write. Importing the package and declaring the field work without the
  extension; the database rejects vector I/O until it exists. `halfvec` additionally
  requires pgvector **0.7+**.
- **`import tortoise_extended` before `Tortoise.init()`** — the import registers the
  field, the index types, the distance filters, and the asyncpg codec hook. Importing
  afterwards raises `TortoiseExtendedError` instead of leaving `VectorField` silently
  unregistered.
- **asyncpg text codec (default)** — the shipped asyncpg codec is TEXT: values travel
  as the `"[0.1,0.2,0.3]"` text representation and arrive in Python as `list[float]`.
  No binary codec is registered. The field's binary helpers are a fallback for a
  driver/server that sends the pgvector binary layout and a possible future opt-in.
- If the codec cannot be registered (the extension is absent at connection time, or a
  pooled connection does not support custom type codecs), registration is skipped;
  `to_python_value` still parses the text representation, so reads keep working. These
  skips are logged at `DEBUG`, never raised.

## Import

```python
import tortoise_extended  # noqa: F401 — apply patches (before Tortoise.init())

from tortoise_extended import VectorField
```

## Constructor

```python
VectorField(
    dimensions: int | None = None,
    vector_type: Literal["vector", "halfvec"] = "vector",
    null: bool = False,
    default: Any = None,
    description: str | None = None,
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `dimensions` | `int \| None` | `None` | Number of vector dimensions. When set, writes of any other length raise `VectorFieldError`; `None` leaves the length unconstrained. |
| `vector_type` | `Literal["vector", "halfvec"]` | `"vector"` | Column type — `halfvec` stores half-precision floats (~2x storage savings, pgvector 0.7+). |
| `null` | `bool` | `False` | Allow NULL values |
| `default` | `Any` | `None` | Default value |
| `description` | `str \| None` | `None` | Column comment |

### SQL Type

- **PostgreSQL:** `vector` / `halfvec`, or `vector(N)` / `halfvec(N)` when
  `dimensions` is set.
- **Other backends:** not supported — `VectorField` (pgvector) is PostgreSQL-only.

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

### Half-Precision (halfvec)

```python
from tortoise import fields, models
from tortoise_extended import VectorField, HNSWIndex


class CompactChunk(models.Model):
    id = fields.IntField(pk=True)
    content = fields.TextField()
    embedding = VectorField(dimensions=1536, vector_type="halfvec")

    class Meta:
        table = "compact_chunks"
        # halfvec columns use halfvec_* operator classes
        indexes = [
            HNSWIndex(
                fields=("embedding",),
                m=32,
                ef_construction=400,
                dist_metric="halfvec_cosine_ops",
            )
        ]
```

`halfvec` uses the same Python API (`list[float]`) and the same distance
filters (`__l2_distance`, `__cosine_distance`, `__inner_product`) as
`vector`; it halves storage at the cost of reduced precision. Requires
pgvector 0.7+.

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
chunk = await Chunk.create(
    content="Hello world",
    embedding=[0.1, 0.2, 0.3, ...],  # 1536 dimensions
)
```

A vector is written as a `list[float]`. Passing a string literal such as
`"[0.1,0.2,0.3]"` is **not** a write format: the field converts any sequence with
`list()`, which would turn the string into a list of characters. String parsing is
read-side only (see [Input Formats](#input-formats)).

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

### Writes — `list[float]`

```python
embedding = [0.1, 0.2, 0.3, ...]
chunk = await Chunk.create(embedding=embedding)
```

`to_db_value` converts any sequence to a plain float list and hands it to the
asyncpg text encoder.

### Reads — `list[float]` under the shipped text codec

The registered text decoder converts pgvector's `"[0.1,0.2,0.3]"` wire text to
`list[float]` inside asyncpg, so `to_python_value` receives a list and passes it
through — this is what an ordinary `await Chunk.get(...)` yields:

```python
chunk = await Chunk.get(id=1)
embedding = chunk.embedding  # list[float]
```

The field-side parser (used only when no codec was registered) ignores
whitespace around tokens, so `"[]"`, `"[ ]"`, and `"[0.1, 0.2]"` all decode. A
malformed literal raises `VectorFieldError`.

### Reads — `str` fallback

When the codec was not registered (no `vector` extension at connection time, or an
unsupported connection), asyncpg returns the same text representation and the field's
`to_python_value` parses it instead. This is the reason reads do not depend on the
codec being present.

### Reads — `bytes` / `memoryview` (binary fallback only)

If a driver or a binary codec hands the field raw bytes, `to_python_value` decodes
pgvector's binary wire layout (see [Binary Format](#binary-format)). The shipped
asyncpg codec is text, so normal ORM reads never take this path.

## Binary Format

pgvector's send/receive layout (what `vector_send` / `halfvec_send` produce) is:

```
Bytes 0-1:  int16  dimension count, big-endian
Bytes 2-3:  int16  unused (always 0)
Bytes 4..:  dimensions × element, big-endian
```

The element is a 4-byte big-endian `float32` for `vector` and a 2-byte big-endian
`float16` (Python `struct` format `e`) for `halfvec`. Total size is
`4 + dimensions × 4` bytes for `vector` and `4 + dimensions × 2` bytes for `halfvec`.

This layout is implemented by `VectorField._encode_binary` / `_decode_binary`, which
mirror pgvector's wire functions. It is **not** the active asyncpg codec — the live
codec is text (see [Requirements](#requirements)). A truncated payload (a header
declaring more elements than the bytes contain) raises `VectorFieldError`.

## Memory Usage

| Dimensions | Bytes per Vector | 1M Vectors | HNSW Index |
|------------|------------------|------------|------------|
| 384 | 1,540 | 1.5 GB | 3 GB |
| 768 | 3,076 | 3 GB | 6 GB |
| 1536 | 6,148 | 6 GB | 12 GB |

Half-precision (`halfvec`) columns halve the per-vector size: a 1536-dim
halfvec is 3,076 bytes (~3 GB per 1M rows).

## Validation

- **Dimensions are checked on write.** When `dimensions` is set, `to_db_value`
  rejects a value whose length differs and raises `VectorFieldError` naming the field,
  the expected dimension count, and the actual length — before any SQL is sent.
- **`dimensions=None` is unconstrained.** No client-side length check runs; the
  column is created as bare `vector` / `halfvec` and PostgreSQL enforces its own
  limits.
- **`None` passes through** as SQL `NULL` (allowed when `null=True`).
- **String parsing failures are wrapped.** `"[1,oops]"` and `"not a vector"` raise
  `VectorFieldError` (not a bare `ValueError`), naming the field and the offending
  token.
- **Reads are not length-checked** by the field; a value read back is returned as
  decoded. Dimension enforcement for stored data belongs to the PostgreSQL column.

## Error Handling

```python
from tortoise_extended import VectorFieldError

# Dimension mismatch — rejected before SQL is sent
try:
    chunk = await Chunk.create(embedding=[0.1, 0.2])  # Chunk expects 1536
except VectorFieldError as exc:
    print(f"Dimension mismatch: {exc}")
    # e.g. "embedding: expected a vector with 1536 dimensions, got 2"

# Malformed text payload (e.g. a value decoded from the database)
try:
    VectorField(dimensions=3).to_python_value("not a vector")
except VectorFieldError as exc:
    print(f"Invalid format: {exc}")
```

`VectorFieldError` derives from `FieldDefinitionError`, itself a subclass of
`TortoiseExtendedError`, so either base catch works.

## Notes

- Vectors are stored as `NULL` if not provided.
- pgvector rejects empty vectors — a `vector` column must have at least one
  dimension. The text codec encodes `[]` as `"[]"`, which PostgreSQL refuses.
- Maximum storage dimensions depend on the pgvector version: 16,000 for `vector`
  and 4,000 for `halfvec` on 0.7+. ANN indexes support fewer dimensions (2,000 for
  `vector`, 4,000 for `halfvec`).
- HNSW index creation requires the matching operator class (`vector_cosine_ops`,
  `halfvec_cosine_ops`, ...).
- IVFFlat requires data to exist before index creation.
