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

> **Illustrative.** Machine-dependent figures — see
> `benchmarks/bench_graph_traversal.py` for a reproducible harness that
> measures the PG recursive-CTE side on your own hardware. The AGE/Neo4j
> comparison rows predate the harness and cannot be reproduced without those
> systems installed.

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
- ~290x faster than AGE for typical workloads (illustrative — see above)
- Simpler to maintain
- No extension compilation required
 - Better SQL compatibility

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

Research-informed outlook (web, 2026-08): best practices, anti-patterns, and
the "not now" boundary for each topic. These are forward-looking notes, not
commitments.

### Potential Enhancements

1. **Vector quantization** — pgvector 0.8 ships `halfvec` (16-bit scalar
   quantization) and binary `bit` quantization. Practice:
   - `halfvec` + HNSW: ~50x faster index build with negligible accuracy
     drop — the default for dense vectors at scale.
   - `bit` + HNSW: ~150x faster build, 5–10% recall loss unless you
     **re-rank the top-100 hits against the full vector** (store both the
     `bit` index column and the original `vector`).
   - IVFFlat: `lists` ≈ `sqrt(rows)` is the documented default; HNSW knobs
     are `m`, `ef_construction` (build) and `ef_search` (query).
   - **Anti-pattern:** quantizing only at write time with no re-rank path,
     or running a full-vector index at 50M+ rows "because accuracy".
     `VectorField` is storage-only today; a quantization-aware index would
     need dual-column modeling, so this stays application-side for now.

2. **Graph algorithms** — PageRank, community detection, and shortest-path
   variants are all expressible as recursive SQL (our `RecursiveCTE`
   already covers reachability/paths). Practice:
   - Iterative algorithms (PageRank, label propagation) converge in
     ~10–20 iterations in-PG; **pg_trickle**-style incremental maintenance
     exists but scans full edges+scores per iteration — a real cost concern
     at 50M edges.
   - **Anti-pattern:** export-to-Neo4j/JanusGraph + import-back when
     freshness matters; you pay ETL latency and two sources of truth for
     what recursive SQL does in place.
   - Decision: add algorithm helpers only if a concrete use case needs
     them; keep raw `RecursiveCTE` as the escape hatch.

3. **Streaming ingestion** — COPY is bulk-throughput king for well-formed
   batches; row-wise streaming gives low latency with per-row control.
   Practice: the safest production pattern is **both** — a streaming path
   for real-time events and COPY for staged backfill, with a
   validate → stage → cast/dedupe/business-rules → final-table pipeline.
   `EventStreamMixin.bulk_insert` already covers the COPY side.

4. **Multi-tenancy** — shared-pooled models need **DB-level RLS
   enforcement**, centralized in Postgres (9.5+; supported on RDS/Aurora),
   not app-level `WHERE tenant_id = ...` clauses. Practice:
   - RLS policies per tenant (`USING`/`WITH CHECK`) on every pooled table;
     `SET app.current_tenant` per connection or per-transaction.
   - **Anti-pattern:** "hoping the correct WHERE is written in every SQL
     query" — one missed filter leaks a tenant's rows; RLS is a last line
     of defense, not a replacement for correct queries.
   - Not built yet: this needs a `TenantMixin` + policy-generation helper,
     and `VectorField`/ltree columns must participate in policies too.

### What We Won't Do

1. **Embedding generation** — Belongs in application layer
2. **Graph visualization** — Belongs in frontend
3. **Real-time sync** — Use websockets instead
4. **Neo4j/JanusGraph interchange** — recursive SQL covers in-PG graph
   needs; a separate graph DB is a deployment choice, not a library feature
5. **Quantization storage formats** — pgvector's `halfvec`/`bit` need
   dual-column modeling and are application decisions; `VectorField`
   remains full-precision storage
