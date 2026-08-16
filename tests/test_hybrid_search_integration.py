"""Integration tests for HybridSearch against live PostgreSQL.

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL 18 with
pgvector on ``127.0.0.1:5433``, database ``tortoise_test``.

Run with: uv run pytest tests/test_hybrid_search_integration.py -v
"""

import os
import socket

import pytest
from tortoise import Tortoise

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.expressions.hybrid_search import HybridSearch
from tests.test_hybrid_search import SearchEntity


# ---------------------------------------------------------------------------
# Config — skip entire module if PG is not available
# ---------------------------------------------------------------------------

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5433/tortoise_test",
)


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5433), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not available on localhost:5433",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
async def _init_db():
    """Initialize Tortoise ORM and add the generated tsvector column."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_hybrid_search_integration"]},
    )
    conn = Tortoise.get_connection("default")
    await conn.execute_query("DROP TABLE IF EXISTS hybrid_it_entities CASCADE")
    await Tortoise.generate_schemas()
    await conn.execute_query(
        "ALTER TABLE hybrid_it_entities ADD COLUMN description_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', description)) STORED"
    )
    yield
    await conn.execute_query("DROP TABLE IF EXISTS hybrid_it_entities CASCADE")
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
async def _clean_table():
    """Truncate the search table before every test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query("TRUNCATE hybrid_it_entities CASCADE")
    yield


async def _seed() -> None:
    """Seed three rows: one ML document and two unrelated ones."""
    await SearchEntity.create(
        name="ml",
        embedding=[0.9, 0.1, 0.0],
        description="machine learning framework",
    )
    await SearchEntity.create(
        name="ui",
        embedding=[0.1, 0.9, 0.0],
        description="graphical user interface",
    )
    await SearchEntity.create(
        name="db",
        embedding=[0.0, 0.1, 0.9],
        description="database systems",
    )


# ---------------------------------------------------------------------------
# HybridSearch against real PostgreSQL
# ---------------------------------------------------------------------------


class TestHybridSearchIntegration:
    """HybridSearch.search() end-to-end (C6 regression: parameterized vector)."""

    @pytest.mark.asyncio
    async def test_combined_search_ranks_vector_match_first(self) -> None:
        await _seed()
        search = HybridSearch(
            model=SearchEntity,
            vector_field="embedding",
            text_field="description",
        )
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            query_text="machine learning",
        )
        assert results[0]["name"] == "ml"
        assert "combined_score" in results[0]
        assert "distance" in results[0]
        assert "text_score" in results[0]
        scores = [r["combined_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_combined_search_with_min_distance(self) -> None:
        """Combined branch with min_distance bound as a third parameter."""
        await _seed()
        search = HybridSearch(
            model=SearchEntity,
            vector_field="embedding",
            text_field="description",
        )
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            query_text="machine learning",
            min_distance=0.5,
        )
        assert all(r["distance"] <= 0.5 for r in results)
        assert all(r["text_score"] > 0.0 for r in results)

    @pytest.mark.asyncio
    async def test_vector_only_search(self) -> None:
        await _seed()
        search = HybridSearch(model=SearchEntity)
        results = await search.search(query_vector=[0.85, 0.15, 0.0])
        assert results[0]["name"] == "ml"
        assert all(r["text_score"] == 0.0 for r in results)
        # combined_score is 1/(1 + cosine distance) in the vector-only branch
        # (F6 — normalized so the score stays bounded for any distance)
        assert results[0]["combined_score"] == pytest.approx(
            1.0 / (1.0 + results[0]["distance"])
        )

    @pytest.mark.asyncio
    async def test_min_distance_filter(self) -> None:
        await _seed()
        search = HybridSearch(model=SearchEntity)
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            min_distance=0.5,
        )
        assert len(results) < 3
        assert all(r["distance"] <= 0.5 for r in results)

    @pytest.mark.asyncio
    async def test_max_results_limit(self) -> None:
        await _seed()
        search = HybridSearch(model=SearchEntity)
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            max_results=1,
        )
        assert len(results) == 1
        assert results[0]["name"] == "ml"

    @pytest.mark.asyncio
    async def test_string_query_vector(self) -> None:
        """A pgvector string literal should be bound as a parameter."""
        await _seed()
        search = HybridSearch(model=SearchEntity)
        results = await search.search(
            query_vector="[0.85,0.15,0.0]",
            query_text="machine learning",
        )
        assert results[0]["name"] == "ml"
