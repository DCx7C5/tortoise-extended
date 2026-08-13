#!/usr/bin/env python3
"""Reproducible benchmark for recursive-CTE graph traversal.

Measures the PostgreSQL recursive-CTE retrieval patterns that
tortoise-extended's ``graph_traversal`` / ``pathfinding`` modules compile to:

- 0-hop: point lookup of a node
- 1-hop: direct neighbors
- N-hop: bounded BFS through the edge table

The harness talks to the live docker PostgreSQL (``postgres-ext`` on
127.0.0.1:5433) directly with raw SQL so the numbers reflect the database
engine, not the ORM wrapper.

Usage::

    docker compose -f docker-compose.dev.yml up -d
    uv run python benchmarks/bench_graph_traversal.py [--rows 100000]

Options:
    --rows N          Number of synthetic nodes (default: 100000)
    --iterations N    Iterations per query (default: 500, 200 for 3-hop)
    --output PATH     Write results as JSON for later comparison

The database URL can be overridden with the ``BENCH_DB_URL`` environment
variable (default: ``postgres://postgres:postgres@localhost:5433/tortoise_test``).

The README/doc tables quoting "22,581 RPS / 4ms p95" are *illustrative* and
machine-dependent; run this script on your own hardware for current numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time

import asyncpg

DB_URL = os.environ.get(
    "BENCH_DB_URL",
    "postgres://postgres:postgres@localhost:5433/tortoise_test",
)


async def _setup(conn: asyncpg.Connection, rows: int) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bench_nodes (
            id BIGINT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS bench_edges (
            source_id BIGINT NOT NULL,
            target_id BIGINT NOT NULL,
            PRIMARY KEY (source_id, target_id)
        );
        CREATE INDEX IF NOT EXISTS bench_edges_target ON bench_edges (target_id);
        TRUNCATE bench_nodes, bench_edges;
        """
    )
    await conn.copy_records_to_table("bench_nodes", records=[(i,) for i in range(rows)])
    chain = {(i, (i + 1) % rows) for i in range(rows)}
    random_edges = set()
    while len(random_edges) < rows:
        random_edges.add((random.randrange(rows), random.randrange(rows)))
    edges = chain | (random_edges - chain)
    await conn.copy_records_to_table("bench_edges", records=edges)


_QUERIES = {
    "0-hop": "SELECT id FROM bench_nodes WHERE id = $1;",
    "1-hop": """
        SELECT e.target_id FROM bench_edges e WHERE e.source_id = $1;
    """,
    "3-hop": """
        WITH RECURSIVE reach AS (
            SELECT e.target_id, 1 AS depth
            FROM bench_edges e WHERE e.source_id = $1
            UNION ALL
            SELECT e.target_id, r.depth + 1
            FROM reach r
            JOIN bench_edges e ON e.source_id = r.target_id
            WHERE r.depth < 3
        )
        SELECT DISTINCT target_id FROM reach;
    """,
}


def _percentile(sorted_latencies: list[float], percentile: float) -> float:
    """Return the p-th percentile of sorted latencies (ms)."""
    if not sorted_latencies:
        return 0.0
    return sorted_latencies[
        min(len(sorted_latencies) - 1, int(len(sorted_latencies) * percentile))
    ]


async def _bench(
    conn: asyncpg.Connection, rows: int, iterations: int | None
) -> dict[str, dict[str, float]]:
    print(f"rows={rows:,}  ({2 * rows:,} edges)")
    print(f"{'query':<8} {'iterations':<12} {'p50 ms':<10} {'p95 ms':<10} {'RPS':<10}")
    results: dict[str, dict[str, float]] = {}
    for name, sql in _QUERIES.items():
        count = iterations or (500 if name != "3-hop" else 200)
        start_id = random.randrange(rows)
        # warm up
        for _ in range(10):
            await conn.fetch(sql, start_id)
        latencies: list[float] = []
        for _ in range(count):
            t0 = time.perf_counter()
            await conn.fetch(sql, start_id)
            latencies.append((time.perf_counter() - t0) * 1000)
        latencies.sort()
        p50 = _percentile(latencies, 0.5)
        p95 = _percentile(latencies, 0.95)
        rps = count / (sum(latencies) / 1000)
        print(f"{name:<8} {count:<12} {p50:<10.3f} {p95:<10.3f} {rps:<10.0f}")
        results[name] = {
            "iterations": float(count),
            "p50_ms": p50,
            "p95_ms": p95,
            "rps": rps,
        }
    return results


async def main(rows: int, iterations: int | None, output: str | None) -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        await _setup(conn, rows)
        results = await _bench(conn, rows, iterations)
    finally:
        await conn.close()

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(
                {"rows": rows, "db_url": DB_URL, "results": results},
                f,
                indent=2,
            )
            f.write("\n")
        print(f"wrote results to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--iterations", type=int, default=None)
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()
    asyncio.run(main(args.rows, args.iterations, args.output))
