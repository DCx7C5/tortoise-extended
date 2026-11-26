# tortoise-extended

Tortoise ORM extensions for PostgreSQL graph/vector workloads — pgvector, TimescaleDB, recursive CTEs, and graph traversal helpers.

## Quick Start

There is no PyPI release yet — install from git and pin a release tag (add `[redis]` for the
caching extras):

```bash
uv add "tortoise-extended @ git+https://github.com/DCx7C5/tortoise-extended@v0.2.0"
```

```python
import tortoise_extended  # noqa: F401 — apply patches (must be imported BEFORE Tortoise.init())

from tortoise import Tortoise, fields
from tortoise.models import Model
from tortoise_extended import VectorField, HNSWIndex


class Entity(Model):
    title = fields.CharField(max_length=255)
    embedding = VectorField(dimensions=1536)

    class Meta:
        table = "entities"
        indexes = [HNSWIndex(fields=("embedding",), m=32, ef_construction=400)]


await Tortoise.init(
    db_url="postgres://user:pass@localhost:5432/graphrag",
    modules={"models": ["__main__"]},
)

# Query with vector similarity
entities = (
    await Entity.filter(embedding__cosine_distance=[query_vec, 0.3])
    .order_by("embedding__cosine_distance")
    .limit(10)
)
```

## Import order and patching

`import tortoise_extended` must run **before** `Tortoise.init()`. The import applies every
monkey-patch — the field and index types, the query filters, and the asyncpg pgvector codec hook —
and Tortoise reads those registrations when it initializes. Importing after `Tortoise.init()` raises
`TortoiseExtendedError` instead of leaving `VectorField` silently unregistered.

- **Patching is idempotent.** `tortoise_extended.patch()` may be called any number of times; a
  second call is a no-op and does not double-register filters or codecs.
- **The tortoise-orm version is checked at import.** The supported range is `>=1.1.7,<1.2` — the
  same range `pyproject.toml` declares. An unsupported tortoise-orm raises `TortoiseVersionError`
  naming that range, so a monkey-patch conflict surfaces at import instead of deep inside Tortoise.

## What This Package Does

`tortoise-extended` monkey-patches [tortoise-orm](https://github.com/tortoise/tortoise-orm) at import time to add:

- **`VectorField`** — pgvector `vector` column type (self-contained, no `tortoise-embeddings` dependency)
- **`CharField`** — validated `varchar` column (`tortoise_extended.fields.char`)
- **`HNSWIndex` / `IVFFlatIndex` / `GiSTIndex`** — pgvector and GiST index types
- **pgvector filters** — `__l2_distance`, `__cosine_distance`, `__inner_product` query filters
- **`RecursiveCTE`** — builder for recursive Common Table Expressions
- **`GraphTraversal` + pathfinding helpers** — ancestors, descendants, neighbors, shortest path, all paths, cycle detection
- **`GraphVectorSearch`** — single-query graph + vector compositor with typed results
- **`HybridSearch`** — weighted vector + full-text search scoring
- **`Graph` facade** — model-class API over traversal, pathfinding and graph-scoped vector search
  (`link` / `unlink` / `neighbours` / `shortest_path` / `find_cycles` / `search(seed=...)`)
- **Fusion utilities** — reciprocal rank fusion, weighted score fusion, MMR diversity re-ranking
- **`QuerySet.paginate(page, page_size)`** — offset pagination returning a `PageInfo`
  (`items`, `total`, `total_pages`, `has_next`, `has_prev`)
- **Startup helpers** — `ensure_extensions()` and `setup(models=None, *, generate_schemas=False)`:
  extensions → schemas → each stream model's `setup()`, in that order
- **`Model.bulk_insert(rows)`** — COPY ingestion: one asyncpg `COPY` per batch for any model,
  refusing relation-bearing models and requiring explicit primary keys
- **Model base classes** — `BaseModel` (BigInt pk), `UnifiedIdModel` (BigInt pk + UUID7 `uid`
  for cross-table refs), `TimestampMixin` (`created_at`/`updated_at`), `BaseSoftDeleteModel` +
  `SoftDeleteQuerySet` (auto-filtered soft delete), `BaseUserModel` (Django-style email/password
  auth with argon2id hashing), `NodeMixin` / `BaseNode` (pk-free adjacency list + UUID7 identity),
  `BaseGraphEdgeModel` / `BaseHierarchyModel`, `BaseCacheableModel`, `SampleMixin` / `BaseSample`
  (TimescaleDB streams), `BaseUnifiedNode` (integer `parent_id` + UUID7 `uid`),
  `BaseSoftDeleteNode` (subtree soft delete); `BaseGraphNodeModel` / `BaseEventStreamModel` are
  deprecated aliases
- **`LTreeField` + ltree filters** — PostgreSQL `ltree` hierarchical column type
- **TimescaleDB** — hypertable/compression/retention/continuous-aggregate managers, `BaseSample`, migration operations
- **pgcrypto query helpers** — `Digest`/`DigestHex`/`HMACDigest` (hash and HMAC expressions)
- **`PGPEncryptedField`** — `bytea` column for pgcrypto ciphertext, plus `PGPSymEncrypt` /
  `PGPSymDecrypt` expression helpers (deliberately not indexable)
- **Redis caching** (optional `redis` extra) — `RedisCache`, `BaseCacheableModel`, `CachedQuerySet`, `@cached`
- **Cached fields** (optional `redis` extra) — `CachedField` / `RedisPrimaryField`, `CachedFieldsMixin`, `cached_get` / `cached_set` / `cached_invalidate` / `prefetch_cached`

## Architecture

```
src/tortoise_extended/
├── __init__.py              # Public API + monkey-patch application (_apply_patches)
├── _quote.py                # shared SQL identifier/literal quoting
├── _types.py                # concrete type aliases + duck-typed protocols
├── bulk.py                  # bulk_insert — asyncpg COPY ingestion for any model
├── exceptions.py            # exception hierarchy
├── lifecycle.py             # ensure_extensions / setup — extension, schema and hypertable fan-out
├── pagination.py            # QuerySet.paginate + PageInfo
├── queryset.py              # QuerySet.random + random_ordering
├── py.typed                 # PEP 561 typed-package marker
├── models/
│   ├── base.py                # BaseModel (BigInt pk)
│   ├── unified.py             # UnifiedIdModel (BigInt pk + UUID7 uid)
│   ├── unified_node.py        # BaseUnifiedNode (UnifiedIdModel + NodeMixin)
│   ├── versioned.py           # VersionMixin (optimistic locking: version + guarded save)
│   ├── user.py                # BaseUserModel (Django-style email/password auth)
│   ├── mixins.py              # TimestampMixin / TimestampEndMixin (created_at/updated_at)
│   ├── soft_delete.py         # BaseSoftDeleteModel + SoftDeleteQuerySet
│   ├── soft_delete_node.py    # BaseSoftDeleteNode + SoftDeleteNodeQuerySet (subtree soft delete)
│   ├── node.py                # NodeMixin + BaseNode (pk-free adjacency list, UUID7 identity)
│   ├── graph_node.py          # BaseGraphNodeModel (deprecated alias of BaseNode, UUID4 pk)
│   ├── graph_edge.py          # BaseGraphEdgeModel (typed/weighted edges, UUID pks)
│   ├── hierarchy_model.py     # BaseHierarchyModel (ltree-path hierarchy)
│   ├── cacheable_model.py     # BaseCacheableModel (model-level Redis caching)
│   ├── cached_mixin.py        # CachedFieldsMixin (write-through/delete for cache fields)
│   ├── sample.py              # SampleMixin + BaseSample (TimescaleDB streams)
│   └── event_stream.py        # BaseEventStreamModel (deprecated alias of BaseSample)
├── fields/
│   ├── char.py            # CharField (validated varchar column)
│   ├── cached.py          # CachedField / RedisPrimaryField + cached_get/set/invalidate
│   ├── vector.py          # VectorField (pgvector vector type)
│   ├── ltree.py           # LTreeField (PostgreSQL ltree type)
│   ├── ipv4.py            # IPv4Field (PostgreSQL inet type)
│   ├── inet.py            # InetField (PostgreSQL inet type, v4 + v6)
│   ├── macaddr.py         # MacAddrField / MacAddr8Field (macaddr / macaddr8)
│   ├── citext.py          # CitextField (citext, case-insensitive text)
│   ├── path.py            # PathField (text path column)
│   ├── pgcrypto.py        # PGPEncryptedField (bytea ciphertext, not indexable)
│   ├── url.py             # URLField (validated URL column)
│   └── uuid.py            # UUID4Field / UUID7Field
├── indexes/
│   ├── hnsw_index.py        # HNSWIndex + IVFFlatIndex (pgvector DDL)
│   └── ltree_index.py       # GiSTIndex
├── expressions/
│   ├── recursive_cte.py     # RecursiveCTE builder
│   ├── graph_filters.py     # pgvector distance operators
│   ├── graph_traversal.py   # GraphTraversal (ancestors/descendants/neighbors)
│   ├── graph.py             # Graph facade over traversal, pathfinding and vector search
│   ├── graph_vector_search.py  # GraphVectorSearch (graph + vector compositor)
│   ├── fusion.py            # reciprocal_rank_fusion, weighted_score_fusion, diversity_rerank
│   ├── distinct_on.py       # PostgreSQL DISTINCT ON as hydrated models
│   ├── pathfinding.py       # shortest_path, all_paths, find_cycles
│   ├── hybrid_search.py     # HybridSearch (vector + FTS weighted scoring)
│   ├── ltree_filters.py     # ltree query operators
│   ├── windows.py           # row_number / rank / dense_rank / ntile + windowed aggregates
│   └── pgcrypto.py          # pgcrypto helpers (Digest, DigestHex, HMACDigest)
├── migrations/
│   └── operations.py        # CreateHypertable, CreateContinuousAggregate
├── cache/                   # RedisCache, BaseCacheableModel, CachedQuerySet, decorators
├── timescale/               # HypertableManager, CompressionManager, RetentionPolicy,
│                            #   ContinuousAggregateManager, stream helpers
└── stubs/tortoise-stubs/    # local typing overlay for tortoise-orm
```

## Graph Model Bases

The package ships reusable base classes rather than a fixed GraphRAG schema — compose them into your own models:

- **`BaseNode`** (+ `NodeMixin`) — the primary graph-node base: a pk-free adjacency-list mixin plus a UUID7 identity base, with `parent_id` traversal, `depth`, `is_root`, and a `child_count` denormalized degree counter.
- **`BaseGraphNodeModel`** — deprecated alias of `BaseNode` (UUID4 pk); kept for compatibility, do not build new models on it.
- **`BaseGraphEdgeModel`** — a `Model` base with `source_id` / `target_id` directed edges, `edge_type`, and `weight`.
- **`BaseHierarchyModel`** — a node base using an `ltree` path column for O(1) ancestor/descendant checks (requires `LTreeField`).

```python
from tortoise import fields
from tortoise_extended import BaseGraphNodeModel, BaseGraphEdgeModel

# `BaseGraphNodeModel` is the deprecated alias shown here for brevity; new code
# should derive from `BaseNode` (see the module docstring in models/node.py).


class Category(BaseGraphNodeModel):
    name = fields.CharField(max_length=100)


class CategoryLink(BaseGraphEdgeModel):
    class Meta:
        table = "category_links"
        # Tortoise does NOT inherit Meta.indexes from the abstract base —
        # redeclare them on every concrete subclass.
        indexes = (
            ("source_id", "edge_type"),
            ("target_id", "edge_type"),
            ("source_id", "target_id", "edge_type"),
        )


# Create a tree
root = await Category.create(name="Electronics")
laptops = await Category.create(name="Laptops", parent=root)

# Create a typed, weighted edge
await CategoryLink.create(source=root, target=laptops, edge_type="contains", weight=1.0)
```

`Meta.indexes` is not inherited from an abstract base. Tortoise does not propagate it, so every
concrete subclass must redeclare it — and the three graph bases enforce that at class-creation
time: `NodeMixin`, `BaseGraphEdgeModel`, and `BaseHierarchyModel` raise `NotImplementedError` when a
concrete subclass omits `Meta.indexes`. Opt out deliberately with `indexes = ()`.

## Module Reference

### `tortoise_extended.fields.vector.VectorField`

Self-contained pgvector field. Does **not** depend on `tortoise-embeddings`.

```python
from tortoise import fields, models
from tortoise_extended import VectorField


class Chunk(models.Model):
    embedding = VectorField(dimensions=1536, null=True)

    class Meta:
        table = "chunks"
```

**Parameters:**
- `dimensions: int | None` — Number of vector dimensions (e.g., 1536 for OpenAI embeddings); when set, writes of any other length raise `VectorFieldError` before SQL is sent
- `vector_type: Literal["vector", "halfvec"]` — Column type; `halfvec` stores half-precision floats (~2x storage savings)
- `null: bool` — Allow NULL values
- `default: Any` — Default value
- `description: str | None` — Column comment

**SQL type:** `vector` / `halfvec` (PostgreSQL only — requires `CREATE EXTENSION vector`)

**Python type:** `list[float] | None`

The shipped asyncpg codec is **text**: reads return `list[float]` and writes take `list[float]`.
- `list[float]` — the write format; length-checked when `dimensions` is set
- `str` — parsed only as a fallback when no codec was registered (e.g. the extension was created later)
- `bytes` / `memoryview` — decoded from the `int16 dim, int16 unused, big-endian elements` layout only when a binary codec is registered (none ships; the field keeps the decoder for that opt-in)

---

### `tortoise_extended.indexes.hnsw_index.HNSWIndex`

HNSW (Hierarchical Navigable Small World) index for approximate nearest-neighbor search.

```python
from tortoise_extended import VectorField, HNSWIndex


class Chunk(models.Model):
    embedding = VectorField(dimensions=1536)

    class Meta:
        table = "chunks"
        indexes = [HNSWIndex(fields=("embedding",), m=32, ef_construction=400)]
```

**Parameters:**
- `fields: tuple[str, ...]` — Field names to index
- `m: int = 16` — Max connections per layer
- `ef_construction: int = 200` — Candidate list size during build
- `dist_metric: str = "vector_l2_ops"` — Distance metric (`vector_l2_ops`, `vector_ip_ops`, `vector_cosine_ops`)
- `name: str | None` — Custom index name

**Generated DDL:**
```sql
CREATE INDEX "hnsw_chunks_embedding_abc123" ON chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 32, ef_construction = 400);
```

---

### `tortoise_extended.indexes.hnsw_index.IVFFlatIndex`

IVFFlat (Inverted File with Flat quantization) index. Requires an existing table with data.

```python
class Chunk(models.Model):
    embedding = VectorField(dimensions=1536)

    class Meta:
        table = "chunks"
        indexes = [IVFFlatIndex(fields=("embedding",), lists=100)]
```

**Parameters:**
- `fields: tuple[str, ...]` — Field names to index
- `lists: int = 100` — Number of lists (recommended: rows / 1000)
- `dist_metric: str = "vector_l2_ops"` — Distance metric
- `name: str | None` — Custom index name

---

### `tortoise_extended.expressions.graph_filters`

pgvector distance operators as pypika-tortoise `BasicCriterion` subclasses.

```python
from tortoise_extended import L2Distance, CosineDistance, InnerProduct
from pypika_tortoise import Table

t = Table("entities")
# Use in queries:
# L2Distance(t.embedding, ValueWrapper("[0.1,0.2]")).le(0.5)
```

**Operators:**
| Class | SQL Operator | Description |
|-------|-------------|-------------|
| `L2Distance` | `<->` | Euclidean distance |
| `CosineDistance` | `<=>` | Cosine distance |
| `InnerProduct` | `<#>` | Inner product (negative) |
| `HammingDistance` | `<~>` | Hamming distance |
| `JaccardDistance` | `<%>` | Jaccard distance |

**Query filters** (auto-registered via monkey-patch):
```python
# Find entities within L2 distance 0.5 of a query vector
entities = await Entity.filter(embedding__l2_distance=([query_vec, 0.5]))

# Find entities within cosine distance 0.3
entities = await Entity.filter(embedding__cosine_distance=([query_vec, 0.3]))

# Find entities with inner product >= 0.8
entities = await Entity.filter(embedding__inner_product=([query_vec, 0.8]))
```

---

### `tortoise_extended.expressions.recursive_cte.RecursiveCTE`

Builder for recursive Common Table Expressions. Works with pypika-tortoise's native CTE detection.

```python
from pypika_tortoise import PostgreSQLQuery, Table
from tortoise_extended import RecursiveCTE

entities = Table("entities")
relationships = Table("relationships")

# Find all ancestors of entity_id=42
cte = (
    RecursiveCTE("ancestors")
    .anchor(
        PostgreSQLQuery.from_(entities)
        .select(entities.id, entities.title, RawSQL("0").as_("depth"))
        .where(entities.id == 42)
    )
    .union(
        PostgreSQLQuery.from_(entities)
        .join(relationships)
        .on(entities.id == relationships.source_entity_id)
        .select(
            entities.id, entities.title, (RawSQL("ancestors.depth") + 1).as_("depth")
        )
    )
    .build()
)
```

---

### `tortoise_extended.expressions.graph_traversal.GraphTraversal`

CTE-based graph traversal over any `BaseGraphNodeModel`-style model + `BaseGraphEdgeModel`-style model pair:

```python
from tortoise_extended import GraphTraversal

traversal = GraphTraversal(node_model=Node, edge_model=Edge)

# Ancestors of node 42 (up to 2 hops)
ancestors = await traversal.ancestors(node_id=42, max_depth=2)

# Descendants and neighbors
descendants = await traversal.descendants(node_id=42)
neighbors = await traversal.neighbors(node_id=42, direction="outgoing", max_depth=1)
```

### `tortoise_extended.expressions.pathfinding`

Shortest-path, all-paths, and cycle detection helpers:

```python
from tortoise_extended import shortest_path, all_paths, find_cycles

path = await shortest_path(Node, Edge, from_id=a, to_id=b, max_hops=5)
paths = await all_paths(Node, Edge, from_id=a, to_id=b, max_hops=3)
cycles = await find_cycles(Node, Edge, max_depth=10)
```

### `tortoise_extended.expressions.hybrid_search.HybridSearch`

Weighted vector + full-text search with `ts_rank_cd` ranking:

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
    query_text="machine learning",
    max_results=10,
)
```

---

### `tortoise_extended.migrations.operations`

Custom migration operations for TimescaleDB.

```python
from tortoise_extended import CreateHypertable, CreateContinuousAggregate

# In your migration file:
operations = [
    CreateHypertable(
        table_name="events",
        time_column="created_at",
        chunk_time_interval="7 days",
    ),
    CreateContinuousAggregate(
        view_name="daily_entity_stats",
        query="SELECT time_bucket('1 day', created_at) AS bucket, COUNT(*) FROM events GROUP BY 1",
        refresh_interval="1 hour",
    ),
]
```

| Operation | Purpose |
|-----------|---------|
| `CreateHypertable` | Convert table to TimescaleDB hypertable |
| `CreateContinuousAggregate` | Create continuous aggregate view with refresh policy |

---

## Docker

A development database stack is provided (PostgreSQL 18 + pgvector + TimescaleDB, and Redis 7):

```bash
cp .env.example .env          # set POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
docker compose -f docker-compose.dev.yml up -d       # postgres-ext (127.0.0.1:5434) + redis-ext (127.0.0.1:6381)
docker compose -f docker-compose.dev.yml logs -f postgres-ext
docker compose -f docker-compose.dev.yml down
```

**`postgres-ext` image contents:**
- PostgreSQL 18 (digest-pinned base image)
- pgvector 0.8.6 + TimescaleDB 2.29.2 (commit-pinned via ARG, tag-verified at build)
- Init script runs from `docker/postgres-ext/scripts/`: `00-extensions.sql` (creates `vector`, `ltree`, `timescaledb`, `pg_trgm`, `uuid-ossp`)

## Dependencies

```
tortoise-orm[asyncpg] >=1.1.7,<1.2  (asyncpg driver comes via this extra)
pypika-tortoise >=0.6.5,<0.7
msgspec >=0.21.0
argon2-cffi >=25.1.0                (argon2id hashing for BaseUserModel)
pglast >=8.9,<9                     (PostgreSQL grammar for continuous aggregates)
redis[hiredis] >=7.0.0              (optional `redis` extra)
```

There is no `tortoise-orm-stubs` dependency: `tortoise-orm` ships its own `py.typed` marker, so
consumers get `tortoise.*` typing inline. The overlay under `src/tortoise_extended/stubs/` is a
development artifact excluded from the built wheel.

Add new dependencies with `uv add <pkg>` so `pyproject.toml` stays the single source of truth.

## Testing

```bash
uv sync --all-extras
uv run ruff format --check src tests
uv run ruff check src tests
uv run basedpyright
uv run pytest tests/ -v
```

The suite is configured to fail on warnings (`filterwarnings = ["error"]`) and covers every feature area.

## Design Decisions

### Why self-contained VectorField?

`tortoise-embeddings` monkey-patches functions we also patch (`get_filters_for_field`, `MetaInfo.add_field`, `Tortoise.init`, `MigrationWriter._format_operation`). Only one monkey-patch can win per function. Our self-contained ~185-line `VectorField` avoids the conflict entirely.

### Why raw SQL for graph helpers?

Tortoise ORM's QuerySet still cannot express these, so the package ships
parameterized builders/helpers instead of hand-written SQL:

| Capability | QuerySet support | tortoise-extended |
|---|---|---|
| Recursive CTEs (`WITH RECURSIVE`) | none | `RecursiveCTE` builder + `GraphTraversal`/`pathfinding` |
| `DISTINCT ON` | none | `BaseEventStreamModel.latest_per_stream` |
| `UNION` subqueries | none | recursive steps in `RecursiveCTE` + traversal queries |
| `ts_rank_cd` ranking | none | `HybridSearch` weighted scoring |
| `ARRAY[]` literals | none | `pathfinding` path aggregation |

Everything else goes through the Tortoise QuerySet API.

### Why no Apache AGE?

Recursive-CTE traversal is **illustratively** ~290x faster than the AGE
`cypher()` wrapper for GraphRAG retrieval patterns (22,581 RPS vs 78 RPS —
machine-dependent, reproduced by `benchmarks/bench_graph_traversal.py`;
AGE/Neo4j comparison rows cannot be reproduced without those systems):
- AGE adds ~13ms overhead per `cypher()` call
- 85% of GraphRAG retrieval is 1-hop (point lookups + direct edges)
- Recursive CTEs on indexed tables are sub-millisecond
- No extension compilation or maintenance burden

Run `uv run python benchmarks/bench_graph_traversal.py` for current numbers
on your hardware.

## Documentation

Detailed documentation is available in the [`docs/`](docs/) directory:

- **[Getting Started](docs/getting-started/)** — Installation, quickstart, configuration
- **[Architecture](docs/architecture/)** — Overview, schema, vector search, graph traversal, design decisions
- **[API Reference](docs/api/)** — API docs for the public modules
- **[Guides](docs/guides/)** — Migration, performance tuning, troubleshooting
- **[Docker](docs/docker/)** — Setup and configuration

## License

MIT
