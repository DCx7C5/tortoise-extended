# Hybrid Search

Combined vector similarity + full-text search with weighted scoring.

## Import

```python
from tortoise_extended import HybridSearch
```

## Constructor

```python
HybridSearch(
    model: type,
    vector_field: str = "embedding",
    text_field: str = "description",
    tsvector_field: str | None = None,
    distance_metric: str = "cosine",
    vector_weight: float = 0.7,
    text_weight: float = 0.3,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | `type` | Required | Tortoise ORM model |
| `vector_field` | `str` | `"embedding"` | VectorField column name |
| `text_field` | `str` | `"description"` | TextField for FTS |
| `tsvector_field` | `str \| None` | `None` | tsvector column (auto: `{text_field}_tsv`) |
| `distance_metric` | `str` | `"cosine"` | `"cosine"`, `"l2"`, or `"inner_product"` |
| `vector_weight` | `float` | `0.7` | Weight for vector similarity |
| `text_weight` | `float` | `0.3` | Weight for text ranking |

## Methods

### search

```python
await search.search(
    query_vector: list[float] | str,
    query_text: str | None = None,
    max_results: int = 20,
    min_distance: float | None = None,
) -> list[RowMapping]
```

Execute hybrid search. Returns list of row dicts with model fields + score metadata.

**Return fields:** All model columns + `distance`, `text_score`, `combined_score`.

## Usage

```python
from myapp.models import Entity
from tortoise_extended import HybridSearch

search = HybridSearch(
    model=Entity,
    vector_field="embedding",
    text_field="description",
    vector_weight=0.7,
    text_weight=0.3,
)

# Hybrid search
results = await search.search(
    query_vector=[0.1, 0.2, ...],
    query_text="machine learning framework",
    max_results=20,
)

for r in results:
    print(f"{r['name']}: score={r['combined_score']:.3f}")

# Vector-only search (no text)
results = await search.search(
    query_vector=[0.1, 0.2, ...],
    max_results=10,
)
```

## Requirements

- `vector` extension (pgvector)
- `pg_trgm` extension (optional, for fuzzy text search)
- tsvector column on the model (e.g., `description_tsv`)
- Text column must have a GIN index on the tsvector for performance

## Notes

- Scoring formula: `combined = vector_weight * (1 - distance) + text_weight * ts_rank_cd`
- Distance metrics: cosine (0=identical, 2=opposite), l2 (0=identical), inner_product (higher=more similar)
- Raw SQL via `connections.get("default")` — requires initialized Tortoise ORM
- The `tsvector_field` must exist on the table (add via trigger or computed column)
