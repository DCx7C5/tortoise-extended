# Graph Vector Search

Single-query graph + vector compositor: find nodes that are both
vector-similar to a query **and** reachable from a seed node within a bounded
number of graph hops. Executes one parameterized SQL statement (recursive CTE
+ pgvector distance predicate) and hydrates the results into typed Tortoise
model instances.

## Import

```python
from tortoise_extended import GraphVectorSearch, GraphVectorHit
```

## Constructor

```python
GraphVectorSearch(
    node_model: type[Model],
    edge_model: type[Model],
    *,
    query_vector: list[float] | str,
    seed_id: int | str | UUID,
    vector_field: str = "embedding",
    max_hops: int = 2,
    direction: str = "both",
    edge_type: str | None = None,
    distance_metric: str = "l2",
    max_results: int = 20,
    min_distance: float | None = None,
    source_field: str = "source_id",
    target_field: str = "target_id",
)
```

Raises `HybridSearchError` for unsupported `distance_metric` (`"l2"`,
`"cosine"`, `"inner_product"`) or `direction` (`"outgoing"`, `"incoming"`,
`"both"`).

## Methods

### search

```python
await search.search() -> list[GraphVectorHit[Model]]
```

Execute the single-query search. Returns typed hits ordered by similarity
(best first). `GraphVectorHit` is a `msgspec.Struct` with:

| Field      | Type            | Meaning                                             |
|------------|-----------------|-----------------------------------------------------|
| `node`     | the node model  | Hydrated Tortoise model instance (fully typed)      |
| `distance` | `float`         | L2/cosine distance, or positive inner product       |
| `hops`     | `int`           | Graph hops from the seed (0 = the seed itself)      |

The typing layer maps each raw row's DB column names back to model field names
and hydrates via `Model._init_from_db`, so `hit.node` is a real, typed model
instance — not a dict.

## Usage

```python
from tortoise_extended import GraphVectorSearch

results = await GraphVectorSearch(
    node_model=Entity,
    edge_model=Relationship,
    query_vector=[0.1, 0.2, 0.3],
    seed_id="uuid-of-seed",
    max_hops=2,
    distance_metric="cosine",
    max_results=10,
).search()

for hit in results:
    entity: Entity = hit.node
    print(entity.title, f"distance={hit.distance:.3f}", f"hops={hit.hops}")
```

## Notes

- **Requires PostgreSQL** with the `vector` extension; the module is skipped in
  non-PG test runs.
- Edge model must declare `source_id` / `target_id` (customizable via
  `source_field` / `target_field`). An `is_bidirectional` boolean column is
  optional — it is detected and used only when present.
- The node primary key column is resolved from `Model._meta.db_pk_column`
  (not hardcoded to `id`).
- `min_distance` semantics follow `HybridSearch`: distance `<=` threshold for
  `l2`/`cosine`, inner product `>=` threshold for `inner_product`.
- Uses raw SQL via `connections.get("default")` — requires initialized
  Tortoise ORM. All values are bound as query parameters (`$1`–`$6`), never
  interpolated.
- The vector predicate runs over reachable nodes only (the recursive CTE
  bounds the work), so it scales to huge graphs better than fetch-then-filter
  in Python.
