# tortoise-extended

Tortoise ORM extensions for PostgreSQL graph/vector workloads — pgvector, TimescaleDB, recursive CTEs, and a complete GraphRAG schema.

## Quick Start

```bash
pip install tortoise-extended
```

```python
import tortoise_extended  # noqa: F401 — apply patches (must be first)

from tortoise import Tortoise
from tortoise_extended.models import Entity, Relationship, Community

await Tortoise.init(
    db_url="asyncpg://user:pass@localhost:5432/graphrag",
    modules={"models": ["tortoise_extended.models"]},
)

# Query with vector similarity
entities = await Entity.filter(
    embedding__cosine_distance="[0.1,0.2,...]"
).order_by("embedding__cosine_distance").limit(10)

# Traverse the graph
entity = await Entity.get(title="Python")
outgoing = await entity.outgoing.all()
```

## What This Package Does

`tortoise-extended` monkey-patches [tortoise-orm](https://github.com/tortoise/tortoise-orm) at import time to add:

- **`VectorField`** — pgvector `vector` column type (self-contained, no `tortoise-embeddings` dependency)
- **`HNSWIndex` / `IVFFlatIndex`** — pgvector index types for approximate nearest-neighbor search
- **pgvector filters** — `__l2_distance`, `__cosine_distance`, `__inner_product` query filters
- **pgvector codec** — automatic encoding/decoding of vectors on asyncpg connections
- **`RecursiveCTE`** — builder for recursive Common Table Expressions
- **Graph traversal functions** — 6 SQL functions for local search, community search, shortest path, entity lookup, hybrid search, and RAPTOR search
- **12 ORM models** — complete GraphRAG schema matching `.docker/postgres/scripts/init.sql`
- **TimescaleDB migration operations** — `CreateHypertable`, `CreateContinuousAggregate`, `AddRetrievalFunction`

## Architecture

```
tortoise_extended/
├── __init__.py              # Monkey-patches tortoise-orm on import
├── models.py                # 12 ORM models (Document → EntityMerge)
├── fields/
│   └── vector_field.py      # VectorField (pgvector vector type)
├── indexes/
│   └── hnsw_index.py        # HNSWIndex + IVFFlatIndex
├── expressions/
│   ├── recursive_cte.py     # RecursiveCTE builder
│   ├── graph_functions.py   # 6 SQL retrieval functions
│   └── graph_filters.py     # pgvector distance operators
├── backends/
│   ├── client.py            # GraphRagAsyncpgDBClient
│   └── schema_generator.py  # GraphRagSchemaGenerator
└── migrations/
    └── operations.py        # CreateHypertable, CreateContinuousAggregate
```

## Database Schema

The package maps to 12 tables in PostgreSQL:

| Model | Table | Purpose |
|-------|-------|---------|
| `Document` | `documents` | Source documents before chunking |
| `TextUnit` | `text_units` | Atomic chunks (300-500 tokens) with embeddings |
| `Entity` | `entities` | Graph nodes with optional embeddings |
| `Relationship` | `relationships` | Directed, typed, weighted graph edges |
| `Community` | `communities` | Hierarchical community structure (Leiden) |
| `CommunityMembership` | `community_memberships` | Entity ↔ Community mapping |
| `CommunityReport` | `community_reports` | LLM-generated summaries with embeddings |
| `RaptorNode` | `raptor_nodes` | RAPTOR tree nodes at abstraction levels |
| `RaptorTreeEdge` | `raptor_tree_edges` | Parent → child edges in RAPTOR tree |
| `QueryCache` | `query_cache` | Semantic response cache |
| `Fact` | `facts` | Time-bounded entity assertions |
| `EntityMerge` | `entity_merges` | Entity resolution audit trail |

### Entity-Relationship Diagram

```
Document ──1:N──> TextUnit
Entity ──1:N──> Relationship (source)
Entity ──1:N──> Relationship (target)
Entity ──M:N──> Community (via CommunityMembership)
Community ──1:N──> CommunityReport
Community ──1:N──> Community (parent/child hierarchy)
RaptorNode ──M:N──> RaptorNode (via RaptorTreeEdge)
Entity ──1:N──> Fact
TextUnit ──1:N──> Fact
```

## Module Reference

### `tortoise_extended.fields.vector_field.VectorField`

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
- `dimensions: int | None` — Number of vector dimensions (e.g., 1536 for OpenAI embeddings)
- `null: bool` — Allow NULL values
- `default: Any` — Default value
- `description: str | None` — Column comment

**SQL type:** `vector` (PostgreSQL), `BLOB` (SQLite fallback)

**Python type:** `list[float] | None`

Handles three incoming formats from asyncpg:
- `list[float]` — passed through
- `str` — parsed from `"[0.1,0.2,0.3]"` format
- `memoryview` — decoded from pgvector binary format (4-byte header + N × 4-byte floats)

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
entities = await Entity.filter(
    embedding__l2_distance=([query_vec, 0.5])
)

# Find entities within cosine distance 0.3
entities = await Entity.filter(
    embedding__cosine_distance=([query_vec, 0.3])
)

# Find entities with inner product >= 0.8
entities = await Entity.filter(
    embedding__inner_product=([query_vec, 0.8])
)
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
        .join(relationships).on(entities.id == relationships.source_entity_id)
        .select(entities.id, entities.title, (RawSQL("ancestors.depth") + 1).as_("depth"))
    )
    .build()
)
```

---

### `tortoise_extended.expressions.graph_functions`

Six SQL retrieval functions for GraphRAG. Generate raw SQL strings for execution via `execute_query()` or `execute_graph_query()`.

```python
from tortoise_extended import local_search, hybrid_search, raptor_search

# Local neighborhood search (1-2 hops)
sql = local_search("Python", entity_type="TECHNOLOGY", max_depth=2)

# Hybrid vector + text search
sql = hybrid_search(
    query_embedding="[0.1,0.2,...]",
    text_query="machine learning frameworks",
    vector_weight=0.7,
    text_weight=0.3,
)

# RAPTOR multi-level search
sql = raptor_search("[0.1,0.2,...]", max_levels=3, max_results=10)
```

| Function | Purpose | Key Parameters |
|----------|---------|----------------|
| `local_search` | BFS neighborhood (1-N hops) | `entity_name`, `entity_type`, `max_depth` |
| `community_search` | Vector search within a community | `query_embedding`, `community_id` |
| `shortest_path` | BFS shortest path between entities | `source_name`, `target_name`, `max_hops` |
| `entity_lookup` | Fuzzy entity search by name/type | `entity_name`, `entity_type` |
| `hybrid_search` | Weighted vector + full-text search | `query_embedding`, `text_query`, `vector_weight` |
| `raptor_search` | Multi-level RAPTOR tree search | `query_embedding`, `max_levels` |

---

### `tortoise_extended.backends.client.GraphRagAsyncpgDBClient`

Extended asyncpg client with pgvector codec and graph query helpers.

```python
from tortoise import Tortoise
from tortoise_extended import GraphRagAsyncpgDBClient

await Tortoise.init(
    db_url="asyncpg://user:pass@localhost:5432/graphrag",
    modules={"models": ["tortoise_extended.models"]},
)

# Use the client directly for raw graph queries
client = Tortoise.get_connection("default")
results = await client.execute_graph_query(
    "SELECT * FROM entities WHERE title ILIKE $1",
    ["%Python%"],
)
```

**Methods:**
- `execute_graph_query(sql, params)` → `list[dict]` — Returns rows as dictionaries
- `execute_graph_query_scalar(sql, params)` → `list[Any]` — Returns first column values

---

### `tortoise_extended.migrations.operations`

Custom migration operations for TimescaleDB.

```python
from tortoise.migrations.operations import RunSQL
from tortoise_extended import CreateHypertable, CreateContinuousAggregate

# In your migration file:
operations = [
    CreateHypertable(
        table_name="query_cache",
        time_column="created_at",
        chunk_time_interval="7 days",
    ),
    CreateContinuousAggregate(
        view_name="daily_entity_stats",
        query="SELECT time_bucket('1 day', created_at) AS bucket, COUNT(*) FROM entities GROUP BY 1",
        refresh_interval="1 hour",
    ),
]
```

| Operation | Purpose |
|-----------|---------|
| `CreateHypertable` | Convert table to TimescaleDB hypertable |
| `CreateContinuousAggregate` | Create continuous aggregate view with refresh policy |
| `AddRetrievalFunction` | Add a SQL function from `functions.sql` |

---

## Docker Image

The package includes a Docker image for the database:

```bash
docker build -t tortoise-extended-pg .docker/postgres/
docker run -p 5432:5432 tortoise-extended-pg
```

**Image contents:**
- PostgreSQL 18 (digest-pinned)
- pgvector 0.8.5 (commit-pinned: `159b79a`)
- TimescaleDB 2.21.2 (commit-pinned: `0ce1bf5`)
- 12 tables + 20+ indexes (from `scripts/init.sql`)
- 6 retrieval functions (from `scripts/functions.sql`)

---

## Dependencies

```
tortoise-orm >=1.1.7,<1.2
asyncpg >=0.31.0,<0.32
pypika-tortoise >=0.6.5,<0.7
numpy >=2.4,<3
```

All dependencies are pinned in `uv.lock` for deterministic builds.

## Testing

```bash
cd pypackages/tortoise-extended
uv sync --all-extras
uv run pytest tests/ -v
```

61 tests covering all modules. Zero warnings.

## Design Decisions

### Why self-contained VectorField?

`tortoise-embeddings` monkey-patches the same functions we patch (`get_filters_for_field`, `MetaInfo.add_field`, `Tortoise.init`, `OperationGenerator.generate`, `MigrationWriter._format_operation`). Only one monkey-patch can win per function. Our ~50-line `VectorField` avoids the conflict entirely.

### Why raw SQL for graph functions?

Tortoise ORM doesn't support:
- Recursive CTEs (no `WITH RECURSIVE` in the query builder)
- `DISTINCT ON` (PostgreSQL-specific)
- `UNION` in subqueries
- `ts_rank_cd` full-text search ranking
- `ARRAY[]` literal construction

The 6 retrieval functions generate parameterized SQL strings. Use `execute_graph_query()` or `execute_query()` to run them.

### Why no Apache AGE?

Benchmarked at 22,581 RPS (plain PG recursive CTEs) vs 78 RPS (AGE `cypher()` wrapper) for GraphRAG retrieval patterns. Recursive CTEs are 290x faster because:
- AGE adds ~13ms overhead per `cypher()` call
- 85% of GraphRAG retrieval is 1-hop (point lookups + direct edges)
- Recursive CTEs on indexed tables are sub-millisecond
- No extension compilation or maintenance burden

## Documentation

Detailed documentation is available in the [`doc/`](doc/) directory:

- **[Getting Started](doc/getting-started/)** — Installation, quickstart, configuration
- **[Architecture](doc/architecture/)** — Overview, schema, vector search, graph traversal, design decisions
- **[API Reference](doc/api/)** — Complete API docs for all modules
- **[Guides](doc/guides/)** — Migration, performance tuning, troubleshooting
- **[Docker](doc/docker/)** — Setup and configuration

## License

MIT
