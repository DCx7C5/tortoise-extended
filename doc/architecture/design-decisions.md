# Design Decisions

## Why Self-Contained VectorField?

### The Problem

`tortoise-embeddings` provides a `VectorField` implementation, but it monkey-patches the same functions we need to patch:

- `get_filters_for_field` — We need `__l2_distance`, `__cosine_distance`, `__inner_product`
- `MetaInfo.add_field` — We need `HNSWIndex` validation
- `Tortoise.init` — We need pgvector codec registration
- `OperationGenerator.generate` — We need `CreateHypertable`
- `MigrationWriter._format_operation` — We need custom operation imports

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

## Why Port 5432?

### The Problem

PostgreSQL defaults to port 5432. Most developers have a local PostgreSQL instance running.

### The Solution

Map container port 5432 to host port 5432.

```bash
docker run -p 5432:5432 tortoise-extended-pg
```

### Trade-offs

- Non-standard port requires explicit configuration
- May confuse new users
- Requires port documentation

**Decision:** Avoiding port conflicts is more important than following defaults.

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

## Why 12 Models?

### The Problem

GraphRAG requires multiple interconnected tables. Users need to define these tables correctly.

### Our Solution

Provide 12 pre-defined models that:
- Match the init.sql schema exactly
- Include proper foreign keys and indexes
- Document all fields and relationships
- Work out of the box

### Trade-offs

- Users must use our models (can't customize schema)
- Models may not fit all use cases
- Adds package size

**Decision:** Providing correct models saves users hours of debugging schema mismatches.

## Why Raw SQL for Graph Functions?

### The Problem

Tortoise ORM doesn't support:
- Recursive CTEs (`WITH RECURSIVE`)
- `DISTINCT ON`
- `UNION` in subqueries
- `ts_rank_cd` full-text search
- `ARRAY[]` literals

### Our Solution

Generate parameterized SQL strings for the 6 retrieval functions. Users execute them with `conn.execute_query()`.

### Trade-offs

- No type safety for generated SQL
- No query builder integration
- Requires raw SQL knowledge

**Decision:** Raw SQL is necessary for these features. The functions provide parameterization and documentation.

## Why Deterministic Lockfiles?

### The Problem

Package managers can resolve different dependency versions, causing:
- Works on my machine
- Reproducibility issues
- CI/CD failures

### Our Solution

Pin all dependencies with exact versions and hashes:

```toml
[[package]]
name = "tortoise-orm"
version = "1.1.7"
source = { registry = "https://pypi.org/simple" }
dependencies = [...]
]

[[package]]
name = "asyncpg"
version = "0.31.0"
source = { registry = "https://pypi.org/simple" }
```

### Trade-offs

- Requires manual dependency updates
- May miss security patches
- Larger lockfile

**Decision:** Deterministic builds are critical for production systems.

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
