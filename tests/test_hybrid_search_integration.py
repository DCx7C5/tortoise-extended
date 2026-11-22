"""Integration tests for HybridSearch against live PostgreSQL.

Requires the docker stack (see ``docker-compose.dev.yml``): PostgreSQL 18 with
pgvector on ``127.0.0.1:5434``, database ``tortoise_test``.

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
    "postgres://postgres:postgres@localhost:5434/tortoise_test",
)


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5434), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not available on localhost:5434",
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


# ---------------------------------------------------------------------------
# Weighted combination behavior
# ---------------------------------------------------------------------------


class TestHybridSearchWeighting:
    """Verify vector_weight / text_weight actually affect result ordering."""

    @pytest.mark.asyncio
    async def test_high_vector_weight_prefers_vector_match(self) -> None:
        """When vector weight dominates, the vector-closest row wins even if
        its text is less relevant."""
        await SearchEntity.create(
            name="vector-close",
            embedding=[0.9, 0.1, 0.0],
            description="quantum physics article",
        )
        await SearchEntity.create(
            name="text-close",
            embedding=[0.1, 0.9, 0.0],
            description="machine learning tutorial",
        )
        search = HybridSearch(
            model=SearchEntity,
            vector_weight=0.9,
            text_weight=0.1,
        )
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            query_text="machine learning",
        )
        assert results[0]["name"] == "vector-close"

    @pytest.mark.asyncio
    async def test_high_text_weight_prefers_text_match(self) -> None:
        """When text weight dominates, the text-closer row wins even if its
        vector is farther from the query."""
        await SearchEntity.create(
            name="vector-close",
            embedding=[0.9, 0.1, 0.0],
            description="quantum physics article",
        )
        await SearchEntity.create(
            name="text-close",
            embedding=[0.1, 0.9, 0.0],
            description="machine learning tutorial",
        )
        search = HybridSearch(
            model=SearchEntity,
            vector_weight=0.1,
            text_weight=0.9,
        )
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            query_text="machine learning",
        )
        assert results[0]["name"] == "text-close"

    @pytest.mark.asyncio
    async def test_equal_weights_blend_scores(self) -> None:
        """Equal weights produce a balanced ranking that considers both signals."""
        await SearchEntity.create(
            name="ml",
            embedding=[0.9, 0.1, 0.0],
            description="machine learning framework",
        )
        await SearchEntity.create(
            name="db",
            embedding=[0.0, 0.1, 0.9],
            description="database systems",
        )
        search = HybridSearch(
            model=SearchEntity,
            vector_weight=0.5,
            text_weight=0.5,
        )
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            query_text="machine learning",
        )
        # ml wins on both vector proximity and text relevance
        assert results[0]["name"] == "ml"
        # scores are strictly ordered
        scores = [r["combined_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_zero_text_weight_degrades_to_vector_only(self) -> None:
        """text_weight=0.0 makes the combined score pure vector similarity,
        even when query_text is provided."""
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
        search = HybridSearch(
            model=SearchEntity,
            vector_weight=1.0,
            text_weight=0.0,
        )
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            query_text="machine learning",
        )
        assert results[0]["name"] == "ml"
        # text_score should be zero since text_weight is 0 — the combined
        # branch is not entered when text_weight == 0
        assert all(r["text_score"] == 0.0 for r in results)


# ---------------------------------------------------------------------------
# FTS contribution — text relevance surfaces distant-vector matches
# ---------------------------------------------------------------------------


class TestHybridSearchFTSContribution:
    """Verify full-text search actually contributes to ranking."""

    @pytest.mark.asyncio
    async def test_text_match_surfaces_distant_vector_row(self) -> None:
        """A row whose text matches the query but whose vector is far away
        still appears in results when text_weight > 0."""
        await SearchEntity.create(
            name="ml",
            embedding=[0.9, 0.1, 0.0],
            description="machine learning framework",
        )
        await SearchEntity.create(
            name="distant-ml",
            embedding=[0.0, 0.0, 1.0],
            description="machine learning algorithms for beginners",
        )
        search = HybridSearch(
            model=SearchEntity,
            vector_weight=0.5,
            text_weight=0.5,
        )
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            query_text="machine learning",
        )
        names = [r["name"] for r in results]
        assert "ml" in names
        assert "distant-ml" in names
        # ml is closer in vector space, so it ranks higher
        assert names.index("ml") < names.index("distant-ml")

    @pytest.mark.asyncio
    async def test_text_score_is_zero_when_no_text_query(self) -> None:
        """Without query_text, text_score must be 0 for all rows."""
        await _seed()
        search = HybridSearch(model=SearchEntity)
        results = await search.search(query_vector=[0.85, 0.15, 0.0])
        assert all(r["text_score"] == 0.0 for r in results)

    @pytest.mark.asyncio
    async def test_text_only_match_among_uniform_vectors(self) -> None:
        """When all vectors are equidistant, text_score determines ordering."""
        await SearchEntity.create(
            name="ml-match",
            embedding=[1.0, 0.0, 0.0],
            description="machine learning deep dive",
        )
        await SearchEntity.create(
            name="no-match",
            embedding=[1.0, 0.0, 0.0],
            description="cooking recipes for dinner",
        )
        search = HybridSearch(
            model=SearchEntity,
            vector_weight=0.5,
            text_weight=0.5,
        )
        results = await search.search(
            query_vector=[1.0, 0.0, 0.0],
            query_text="machine learning",
        )
        assert results[0]["name"] == "ml-match"
        assert results[0]["text_score"] > results[1]["text_score"]


# ---------------------------------------------------------------------------
# Empty / no-match handling
# ---------------------------------------------------------------------------


class TestHybridSearchEmptyResults:
    """Verify behavior when there is no data or no matches."""

    @pytest.mark.asyncio
    async def test_empty_table_returns_empty(self) -> None:
        """Searching an empty table returns an empty list."""
        search = HybridSearch(model=SearchEntity)
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            query_text="machine learning",
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_vector_only_empty_table_returns_empty(self) -> None:
        """Vector-only search on empty table also returns empty."""
        search = HybridSearch(model=SearchEntity)
        results = await search.search(query_vector=[0.85, 0.15, 0.0])
        assert results == []

    @pytest.mark.asyncio
    async def test_min_distance_filters_out_all_rows(self) -> None:
        """When min_distance excludes everything, result is empty."""
        await _seed()
        search = HybridSearch(model=SearchEntity)
        # cosine distance is 0 only for identical vectors; none of the seed
        # vectors exactly match [0.85, 0.15, 0.0], so distance=0.0
        # (combined branch uses <=) excludes every row.
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            min_distance=0.0,
        )
        assert results == []


# ---------------------------------------------------------------------------
# Distance metrics — l2 and inner_product end-to-end
# ---------------------------------------------------------------------------


class TestHybridSearchDistanceMetrics:
    """Exercise non-default distance metrics against real PG."""

    @pytest.mark.asyncio
    async def test_l2_metric_ranks_closest_first(self) -> None:
        """L2 metric should rank the nearest vector first."""
        await _seed()
        search = HybridSearch(
            model=SearchEntity,
            distance_metric="l2",
        )
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
        )
        assert results[0]["name"] == "ml"
        # distance should be L2 (Euclidean), not cosine
        assert results[0]["distance"] >= 0.0

    @pytest.mark.asyncio
    async def test_inner_product_metric_ranks_highest_dot_product_first(self) -> None:
        """Inner product metric returns the highest dot-product first."""
        await _seed()
        search = HybridSearch(
            model=SearchEntity,
            distance_metric="inner_product",
        )
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
        )
        assert results[0]["name"] == "ml"
        # For inner_product, larger distance = more similar
        distances = [r["distance"] for r in results]
        assert distances == sorted(distances, reverse=True)

    @pytest.mark.asyncio
    async def test_inner_product_with_min_distance(self) -> None:
        """min_distance with inner_product uses >= threshold (floor)."""
        await _seed()
        search = HybridSearch(
            model=SearchEntity,
            distance_metric="inner_product",
        )
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            min_distance=0.5,
        )
        assert all(r["distance"] >= 0.5 for r in results)

    @pytest.mark.asyncio
    async def test_l2_combined_search_with_text(self) -> None:
        """L2 metric works in the combined (vector + text) branch."""
        await _seed()
        search = HybridSearch(
            model=SearchEntity,
            distance_metric="l2",
        )
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            query_text="machine learning",
        )
        assert results[0]["name"] == "ml"
        assert "combined_score" in results[0]
        assert "text_score" in results[0]
        assert results[0]["text_score"] > 0.0


# ---------------------------------------------------------------------------
# Score invariants
# ---------------------------------------------------------------------------


class TestHybridSearchScoreInvariants:
    """Verify mathematical properties of the combined score."""

    @pytest.mark.asyncio
    async def test_combined_score_is_weighted_sum(self) -> None:
        """combined_score = vector_weight * similarity + text_weight * text_rank."""
        await SearchEntity.create(
            name="ml",
            embedding=[0.9, 0.1, 0.0],
            description="machine learning framework",
        )
        vw, tw = 0.6, 0.4
        search = HybridSearch(
            model=SearchEntity,
            vector_weight=vw,
            text_weight=tw,
        )
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            query_text="machine learning",
        )
        assert len(results) == 1
        r = results[0]
        # The similarity component is 1/(1+dist) for cosine
        expected_similarity = 1.0 / (1.0 + r["distance"])
        expected_combined = vw * expected_similarity + tw * r["text_score"]
        assert r["combined_score"] == pytest.approx(expected_combined, rel=1e-4)

    @pytest.mark.asyncio
    async def test_combined_score_bounded(self) -> None:
        """combined_score should be in [0, 1] for cosine metric with weights summing to 1."""
        await _seed()
        search = HybridSearch(
            model=SearchEntity,
            vector_weight=0.7,
            text_weight=0.3,
        )
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            query_text="machine learning",
        )
        for r in results:
            assert 0.0 <= r["combined_score"] <= 1.0

    @pytest.mark.asyncio
    async def test_result_dict_contains_all_expected_keys(self) -> None:
        """Each result dict has model fields plus score metadata."""
        await _seed()
        search = HybridSearch(model=SearchEntity)
        results = await search.search(
            query_vector=[0.85, 0.15, 0.0],
            query_text="machine learning",
        )
        for r in results:
            assert "name" in r
            assert "embedding" in r
            assert "description" in r
            assert "distance" in r
            assert "text_score" in r
            assert "combined_score" in r
