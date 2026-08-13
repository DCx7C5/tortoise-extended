# Benchmarks

Reproducible micro-benchmarks for the PostgreSQL recursive-CTE retrieval
patterns that `tortoise-extended` builds on.

## Graph traversal

`bench_graph_traversal.py` creates a synthetic directed graph (a chain plus
random edges) in the docker PostgreSQL and measures 0-hop / 1-hop / 3-hop
recursive-CTE retrieval latency and throughput:

```bash
docker compose -f docker-compose.dev.yml up -d
uv run python benchmarks/bench_graph_traversal.py --rows 100000
```

### Options

| Flag | Default | Purpose |
| --- | --- | --- |
| `--rows N` | `100000` | Number of synthetic nodes (2×N edges) |
| `--iterations N` | `500` (200 for 3-hop) | Iterations per query |
| `--output PATH` | — | Write results as JSON for later comparison |

The database URL can be overridden with the `BENCH_DB_URL` environment
variable (default: `postgres://postgres:postgres@localhost:5433/tortoise_test`).

## Provenance of documented numbers

The RPS/latency tables in `README.md` and `doc/architecture/*` (e.g.
"22,581 RPS, 4ms p95", "290x faster than AGE") are **illustrative**,
machine-dependent figures. The `22,581 RPS` figure predates this harness and
the AGE/Neo4j comparison rows cannot be reproduced without those systems
installed. Treat them as order-of-magnitude guidance; run this harness on
your own hardware for current numbers.
