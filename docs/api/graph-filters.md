# Graph Filters

pgvector distance operators for Tortoise ORM query filters.

## Overview

The graph filters module provides three distance operators as `Comparator` subclasses that integrate with pypika-tortoise's query builder.

## Operators

### L2Distance

Euclidean distance between vectors.

```python
from tortoise_extended import L2Distance
from pypika_tortoise import Table, ValueWrapper

t = Table("entities")
query = (
    PostgreSQLQuery.from_(t)
    .select(t.id, t.title)
    .where(L2Distance(t.embedding, ValueWrapper("[0.1,0.2,0.3]")).le(0.5))
)
```

**SQL:** `<->` operator

**Range:** 0 to √(2 × dimensions)

**Use case:** General similarity, when magnitude matters

**Example:**
```python
# Find entities within L2 distance 0.5
entities = (
    await Entity.filter(embedding__l2_distance=[[query_vec], 0.5])
    .order_by("embedding__l2_distance")
    .limit(10)
)
```

---

### CosineDistance

Cosine distance between vectors.

```python
from tortoise_extended import CosineDistance
from pypika_tortoise import Table, ValueWrapper

t = Table("entities")
query = (
    PostgreSQLQuery.from_(t)
    .select(t.id, t.title)
    .where(CosineDistance(t.embedding, ValueWrapper("[0.1,0.2,0.3]")).le(0.3))
)
```

**SQL:** `<=>` operator

**Range:** 0 to 2 (0 = identical, 2 = opposite)

**Use case:** Semantic similarity, normalized vectors

**Example:**
```python
# Find entities within cosine distance 0.3
entities = (
    await Entity.filter(embedding__cosine_distance=[[query_vec], 0.3])
    .order_by("embedding__cosine_distance")
    .limit(10)
)
```

---

### InnerProduct

Inner product (negated) between vectors.

```python
from tortoise_extended import InnerProduct
from pypika_tortoise import Table, ValueWrapper

t = Table("entities")
query = (
    PostgreSQLQuery.from_(t)
    .select(t.id, t.title)
    .where(InnerProduct(t.embedding, ValueWrapper("[0.1,0.2,0.3]")).ge(0.8))
)
```

**SQL:** `<#>` operator (negated)

**Range:** -∞ to √(dimensions) (higher = more similar)

**Use case:** When vectors are normalized, faster than cosine

**Example:**
```python
# Find entities with inner product >= 0.8
entities = (
    await Entity.filter(embedding__inner_product=[[query_vec], 0.8])
    .order_by("embedding__inner_product")
    .limit(10)
)
```

---

### HammingDistance

Hamming distance for binary vectors.

```python
from tortoise_extended import HammingDistance
from pypika_tortoise import Table, ValueWrapper

t = Table("entities")
query = (
    PostgreSQLQuery.from_(t)
    .select(t.id, t.title)
    .where(HammingDistance(t.embedding, ValueWrapper("10101")).le(2))
)
```

**SQL:** `<~>` operator

**Use case:** Binary vectors, exact matching

---

### JaccardDistance

Jaccard distance for binary vectors.

```python
from tortoise_extended import JaccardDistance
from pypika_tortoise import Table, ValueWrapper

t = Table("entities")
query = (
    PostgreSQLQuery.from_(t)
    .select(t.id, t.title)
    .where(JaccardDistance(t.embedding, ValueWrapper("10101")).le(0.3))
)
```

**SQL:** `<%>` operator

**Use case:** Binary vectors, set similarity

## Tortoise ORM Integration

The operators are auto-registered as query filters via monkey-patching.

### Filter Syntax

```python
# Format: field__operator_name=[vector, threshold]
entities = await Entity.filter(embedding__l2_distance=[[query_vec], 0.5])

entities = await Entity.filter(embedding__cosine_distance=[[query_vec], 0.3])

entities = await Entity.filter(embedding__inner_product=[[query_vec], 0.8])
```

### Ordering

```python
# Order by distance (ascending = closest first)
entities = (
    await Entity.filter(embedding__cosine_distance=[[query_vec], 0.5])
    .order_by("embedding__cosine_distance")
    .limit(10)
)

# Order by inner product (descending = most similar first)
entities = (
    await Entity.filter(embedding__inner_product=[[query_vec], 0.8])
    .order_by("-embedding__inner_product")
    .limit(10)
)
```

### Combined Filters

```python
# Filter by type + vector distance
entities = (
    await Entity.filter(
        type="TECHNOLOGY", embedding__cosine_distance=[[query_vec], 0.5]
    )
    .order_by("embedding__cosine_distance")
    .limit(10)
)

# Filter by multiple conditions
entities = (
    await Entity.filter(
        type__in=["TECHNOLOGY", "CONCEPT"],
        metadata__contains={"source": "arxiv"},
        embedding__cosine_distance=[[query_vec], 0.5],
    )
    .order_by("embedding__cosine_distance")
    .limit(10)
)
```

## Operator Selection Guide

| Use Case | Operator | Threshold | Notes |
|----------|----------|-----------|-------|
| General similarity | L2Distance | 0.5 | Good default |
| Semantic search | CosineDistance | 0.3 | Best for embeddings |
| Fast similarity | InnerProduct | 0.8 | Fastest |
| Binary matching | HammingDistance | 2 | For binary vectors |
| Set similarity | JaccardDistance | 0.3 | For binary vectors |

## Performance

| Operator | Index Support | Query Time |
|----------|---------------|------------|
| L2Distance | HNSW, IVFFlat | <5ms |
| CosineDistance | HNSW, IVFFlat | <5ms |
| InnerProduct | HNSW, IVFFlat | <5ms |
| HammingDistance | Brute force | <10ms |
| JaccardDistance | Brute force | <10ms |

## Notes

- Threshold is inclusive (`le` for distance, `ge` for inner product)
- Empty vectors return distance = ∞
- NULL vectors are excluded from results
- Distance metrics must match index type (cosine query → cosine index)
