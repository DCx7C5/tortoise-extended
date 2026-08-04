# Design Decisions

## Why Self-Contained VectorField?

### The Problem

`tortoise-embeddings` provides a `VectorField` implementation, but it monkey-patches the same functions we need to patch:

- `get_filters_for_field` — We need `__l2_distance`, `__cosine_distance`, `__inner_product`
- `MetaInfo.add_field` — We need `HNSWIndex` validation
- `Tortoise.init` — We need pgvector codec registration
- `MigrationWriter._format_operation` — We need custom operation imports and
  generic serialization of `CreateHypertable` / `CreateContinuousAggregate`

Only one monkey-patch can win per function. Using `tortoise-embeddings` would break our features.

### Our Solution

Implement VectorField ourselves (~50 lines) plus pgvector codec (~30 lines). This gives us:

- Full control over monkey-patches
- No dependency conflicts
- Simpler maintenance
- Smaller package footprint

### Trade-offs

- We don't get automatic embedding generation (must use OpenAI API directly)
- We don't get batch embedding support
- We don't get embedding caching

**Decision:** These features belong in the application layer, not the ORM extension.

## Why No Apache AGE?

### Benchmark Results

| Solution | RPS (u=50) | Latency (p95) |
|----------|------------|---------------|
| Plain PG recursive CTEs | 22,581 | 4ms |
| AGE `cypher()` wrapper | 78 | 640ms |
| Neo4j | 15,000 | 10ms |

### Analysis

AGE adds ~13ms overhead per `cypher()` call because:
1. `cypher()` is a function, not native SQL — requires query rewriting
2. AGE maintains its own graph storage alongside PostgreSQL
3. The wrapper translates Cypher to PostgreSQL operations

### 85% of GraphRAG is 1-Hop

Most GraphRAG retrieval patterns are:
- Entity lookup (0 hops)
- Direct neighbors (1 hop)
- Community members (1 hop)

Recursive CTEs are sub-millisecond for these patterns. AGE's overhead is unnecessary.

### Our Decision

Use plain PostgreSQL recursive CTEs. They're:
- 290x faster than AGE for typical workloads
- Simpler to maintain
- No extension compilation required
- Better SQL compatibility

## Why Port 5433?

### The Problem

PostgreSQL defaults to port 5432. Most developers already have a local
PostgreSQL instance running there — a second dev database on 5432 would
collide with it.

### The Solution

Map the container's internal 5432 to the host port **5433**, and Redis to
host port **6380**:

```bash
docker compose -f docker-compose.dev.yml up -d
# postgres-ext -> 127.0.0.1:5433, redis-ext -> 127.0.0.1:6380
```

### Trade-offs

- Non-standard port requires explicit configuration
- All examples must use `127.0.0.1:5433`

**Decision:** Avoiding conflicts with an existing local PostgreSQL is more
important than following defaults.

## Why Monkey-Patching?

### The Problem

Tortoise ORM doesn't provide plugin hooks for:
- Custom field types
- Custom query filters
- Custom index types
- Custom migration operations
- Custom codec registration

### Alternatives Considered

1. **Forking tortoise-orm** — Too much maintenance burden
2. **Wrapping tortoise-orm** — Would break user code
3. **Contributing upstream** — Slow, uncertain acceptance
4. **Monkey-patching** — Immediate, targeted, reversible

### Our Decision

Monkey-patching allows us to extend the ORM without maintaining a fork. The patches are:
- Applied once at import time
- Extending (not replacing) existing functions
- Documented in source code
- Reversible if needed

## Why Reusable Graph Base Classes?

### The Problem

Graph-style schemas (nodes + edges + hierarchies) repeat across projects:
`GraphNode` / `GraphEdge` for entity-relationship graphs, `HierarchyModel`
for ltree-path trees. Hand-rolling them each time leads to inconsistent
indexes and wrong query patterns.

### Our Solution

Provide three small, well-indexed abstract base models:

- `GraphNode` — `id`, `name`, `type`, `description`, `embedding`, `metadata`
- `GraphEdge` — `source_id`/`target_id` (plain columns, no FK — one edge
  table can link nodes of different types), `type`, `weight`, `metadata`,
  plus `outgoing(...)` / `incoming(...)` queryset helpers
- `HierarchyModel` — `path` `LTreeField`, `parent_id`, `depth`, `namespace`
  with a `GiSTIndex` on `path`

### Trade-offs

- Base classes are intentionally minimal — application models extend them
- No auto-generated schema: models still map to your own table names
- Single-table inheritance is not provided; compose with your own fields

**Decision:** Reusable, minimal base classes save setup time without
dictating the application schema.

## Why Raw SQL for Graph Expressions?

### The Problem

Tortoise ORM's QuerySet cannot express:
- Recursive CTEs (`WITH RECURSIVE`)
- `DISTINCT ON`
- `UNION` subqueries
- `ts_rank_cd` full-text ranking
- `ARRAY[]` literals

### Our Solution

These stay inside the library — `RecursiveCTE`, `GraphTraversal`,
`shortest_path` / `all_paths` / `find_cycles`, and `HybridSearch` build the
parameterized SQL for you and expose async Python APIs. Application code
never hand-writes SQL for these patterns; raw SQL remains reserved for
operations the library doesn't cover.

### Trade-offs

- SQL generation is internal, not a public query-builder language
- Postgres-specific features require the PostgreSQL backend

**Decision:** Keep the raw SQL encapsulated behind async Python APIs; users
get type-safe, parameterized access without writing CTE strings themselves.

## Why uv with Constraints?

### The Problem

Package managers can resolve different dependency versions, causing:
- Works on my machine
- Reproducibility issues
- CI/CD failures

### Our Solution

Use `uv` and pin the *ranges* that matter in `pyproject.toml`:

```toml
dependencies = [
    "tortoise-orm>=1.1.7,<1.2",
    "pypika-tortoise>=0.6.5,<0.7",
    "msgspec>=0.21.0",
]
```

Dependencies are added only via `uv add <pkg>` so `pyproject.toml` and
`uv.lock` stay in sync. (The lockfile is not committed by default — if one is
introduced deliberately, pin it to the project.)

### Trade-offs

- Range pins allow resolver freedom within a major version
- Exact-repro builds are the caller's choice via `uv sync --locked`

**Decision:** Constrain what matters (the ORM version and query builder),
leave the rest to the resolver.

## Future Considerations

### Potential Enhancements

1. **Vector quantization** — Scalar or product quantization for large vectors
2. **Graph algorithms** — PageRank, community detection, centrality
3. **Streaming ingestion** — Real-time document processing
4. **Multi-tenancy** — Row-level security for shared databases

### What We Won't Do

1. **Embedding generation** — Belongs in application layer
2. **Graph visualization** — Belongs in frontend
3. **Real-time sync** — Use websockets instead
