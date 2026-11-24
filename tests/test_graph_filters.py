"""Tests for graph filters (pgvector similarity operators).

The bare-equality filter integration tests run against the live PostgreSQL test
database (see ``docker-compose.dev.yml``); the operator unit tests need no
database and keep running when PostgreSQL is not reachable.
"""

from collections.abc import AsyncGenerator

import pytest
from tortoise import Tortoise, fields
from tortoise.models import Model

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.exceptions import VectorFieldError
from tortoise_extended.fields import CharField
from tortoise_extended.expressions.graph_filters import (
    CosineDistance,
    HammingDistance,
    InnerProduct,
    JaccardDistance,
    L2Distance,
    _cosine_distance_lte,
    _hamming_distance_lte,
    _inner_product_gte,
    _jaccard_distance_lte,
    _l2_distance_lte,
    _parse_vector_threshold,
    _vector_eq_guard,
    get_vector_filters,
    vector_encoder,
)
from tortoise_extended.fields.vector import VectorField
from tests._pg_env import database_url, pg_skip_reason


class TestVectorEncoder:
    def test_list(self) -> None:
        result = vector_encoder([0.1, 0.2, 0.3], None, None)
        assert result == "[0.1,0.2,0.3]"

    def test_none(self) -> None:
        result = vector_encoder(None, None, None)
        assert result is None

    def test_tuple(self) -> None:
        result = vector_encoder((1.0, 2.0), None, None)
        assert result == "[1.0,2.0]"

    def test_non_iterable_scalar(self) -> None:
        """A scalar (e.g. float) falls back to str(value)."""
        result = vector_encoder(0.5, None, None)
        assert result == "0.5"


class TestGetVectorFilters:
    def test_returns_dict(self) -> None:
        filters = get_vector_filters("embedding", "embedding")
        assert isinstance(filters, dict)

    def test_has_l2_distance(self) -> None:
        filters = get_vector_filters("embedding", "embedding")
        assert "embedding__l2_distance" in filters

    def test_has_cosine_distance(self) -> None:
        filters = get_vector_filters("embedding", "embedding")
        assert "embedding__cosine_distance" in filters

    def test_has_inner_product(self) -> None:
        filters = get_vector_filters("embedding", "embedding")
        assert "embedding__inner_product" in filters

    def test_has_hamming_distance(self) -> None:
        filters = get_vector_filters("embedding", "embedding")
        assert "embedding__hamming_distance" in filters

    def test_has_jaccard_distance(self) -> None:
        filters = get_vector_filters("embedding", "embedding")
        assert "embedding__jaccard_distance" in filters

    def test_has_isnull(self) -> None:
        filters = get_vector_filters("embedding", "embedding")
        assert "embedding__isnull" in filters

    def test_field_name_prefix(self) -> None:
        filters = get_vector_filters("vec", "vec")
        assert "vec__l2_distance" in filters
        assert "vec__cosine_distance" in filters
        assert "vec__hamming_distance" in filters
        assert "vec__jaccard_distance" in filters


class TestDistanceOperators:
    def test_l2_distance(self) -> None:
        from pypika_tortoise import Table
        from pypika_tortoise.context import DEFAULT_SQL_CONTEXT
        from pypika_tortoise.terms import ValueWrapper

        t = Table("test")
        op = L2Distance(t.embedding, ValueWrapper("[0.1,0.2]"))
        sql = op.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<->" in sql

    def test_cosine_distance(self) -> None:
        from pypika_tortoise import Table
        from pypika_tortoise.context import DEFAULT_SQL_CONTEXT
        from pypika_tortoise.terms import ValueWrapper

        t = Table("test")
        op = CosineDistance(t.embedding, ValueWrapper("[0.1,0.2]"))
        sql = op.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<=>" in sql

    def test_inner_product(self) -> None:
        from pypika_tortoise import Table
        from pypika_tortoise.context import DEFAULT_SQL_CONTEXT
        from pypika_tortoise.terms import ValueWrapper

        t = Table("test")
        op = InnerProduct(t.embedding, ValueWrapper("[0.1,0.2]"))
        sql = op.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<#>" in sql

    def test_hamming_distance(self) -> None:
        from pypika_tortoise import Table
        from pypika_tortoise.context import DEFAULT_SQL_CONTEXT
        from pypika_tortoise.terms import ValueWrapper

        t = Table("test")
        op = HammingDistance(t.embedding, ValueWrapper("[1,0,1]"))
        sql = op.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<~>" in sql

    def test_jaccard_distance(self) -> None:
        from pypika_tortoise import Table
        from pypika_tortoise.context import DEFAULT_SQL_CONTEXT
        from pypika_tortoise.terms import ValueWrapper

        t = Table("test")
        op = JaccardDistance(t.embedding, ValueWrapper("[1,0,1]"))
        sql = op.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<%>" in sql

    def test_hamming_distance_lte_with_threshold(self) -> None:
        from pypika_tortoise import Table
        from pypika_tortoise.context import DEFAULT_SQL_CONTEXT

        t = Table("test")
        op = _hamming_distance_lte(t.embedding, [[1, 0, 1], 0.5])
        sql = op.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<~>" in sql
        assert "0.5" in sql

    def test_jaccard_distance_lte_with_threshold(self) -> None:
        from pypika_tortoise import Table
        from pypika_tortoise.context import DEFAULT_SQL_CONTEXT

        t = Table("test")
        op = _jaccard_distance_lte(t.embedding, [[1, 0, 1], 0.5])
        sql = op.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<%>" in sql
        assert "0.5" in sql

    def test_hamming_jaccard_reject_nested_vectors(self) -> None:
        from pypika_tortoise.terms import Field

        with pytest.raises(VectorFieldError):
            _hamming_distance_lte(Field("embedding"), [[0.1], [0.2]])
        with pytest.raises(VectorFieldError):
            _jaccard_distance_lte(Field("embedding"), [[0.1], [0.2]])


class TestFilterPatchIdempotent:
    def test_reload_does_not_double_wrap(self) -> None:
        """Verify filter patch is idempotent across reloads."""
        import importlib

        import tortoise.filters as f

        before = f.get_filters_for_field
        importlib.reload(__import__("tortoise_extended"))
        after = f.get_filters_for_field
        assert before is after


class TestBareEqualityGuard:
    """G4 regression — a bare non-None value on a VectorField must raise."""

    def test_guard_raises_for_non_none(self) -> None:
        from pypika_tortoise.terms import Field

        with pytest.raises(
            VectorFieldError, match="Bare equality filters are not supported"
        ):
            _vector_eq_guard(Field("embedding"), True)


class TestParseVectorThreshold:
    """G20 — compound value validation."""

    def test_plain_vector_uses_default(self) -> None:
        query_vector, threshold = _parse_vector_threshold(
            [0.1, 0.2], 1.0, "__cosine_distance"
        )
        assert query_vector == [0.1, 0.2]
        assert threshold == 1.0

    def test_compound_vector_uses_threshold(self) -> None:
        query_vector, threshold = _parse_vector_threshold(
            [[0.1, 0.2], 0.5], 1.0, "__cosine_distance"
        )
        assert query_vector == [0.1, 0.2]
        assert threshold == 0.5

    def test_nested_vectors_raise(self) -> None:
        """[[v1], [v2]] — two vectors misread as compound — must raise."""
        with pytest.raises(VectorFieldError, match="threshold must be a number"):
            _parse_vector_threshold([[0.1, 0.2], [0.3, 0.4]], 1.0, "__cosine_distance")

    def test_boolean_threshold_raises(self) -> None:
        with pytest.raises(VectorFieldError, match="threshold must be a number"):
            _parse_vector_threshold([[0.1, 0.2], True], 1.0, "__cosine_distance")

    def test_distance_operators_reject_nested_vectors(self) -> None:
        """End-to-end through the operator wrappers."""
        from pypika_tortoise.terms import Field

        with pytest.raises(VectorFieldError):
            _l2_distance_lte(Field("embedding"), [[0.1], [0.2]])
        with pytest.raises(VectorFieldError):
            _cosine_distance_lte(Field("embedding"), [[0.1], [0.2]])
        with pytest.raises(VectorFieldError):
            _inner_product_gte(Field("embedding"), [[0.1], [0.2]])

    def test_bare_filter_registered_with_guard(self) -> None:
        filters = get_vector_filters("embedding", "embedding")
        assert filters["embedding"]["operator"] is _vector_eq_guard


class _VecDoc(Model):
    """VectorField model for the bare-equality filter integration tests."""

    id = fields.IntField(primary_key=True)
    name = CharField(max_length=50)
    embedding = VectorField(dimensions=2, null=True)

    class Meta:
        table = "g4_vec_docs"


# --- Config — only TestBareEqualityFilterIntegration needs PG ----------------

DB_URL = database_url()


@pytest.fixture(scope="module")
async def _init_db() -> AsyncGenerator[None, None]:
    """Initialize Tortoise against the live PostgreSQL test database."""
    _ = await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_graph_filters"]},
    )
    conn = Tortoise.get_connection("default")
    await conn.execute_query("DROP TABLE IF EXISTS g4_vec_docs CASCADE")
    await Tortoise.generate_schemas()
    yield
    await conn.execute_query("DROP TABLE IF EXISTS g4_vec_docs CASCADE")
    await Tortoise.close_connections()


@pytest.fixture
async def _clean_table() -> AsyncGenerator[None, None]:
    """Truncate the vector table before each integration test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query("TRUNCATE g4_vec_docs CASCADE")
    yield


_PG_SKIP_REASON = pg_skip_reason()


@pytest.mark.skipif(
    _PG_SKIP_REASON is not None,
    reason=_PG_SKIP_REASON or "",
)
@pytest.mark.usefixtures("_init_db", "_clean_table")
class TestBareEqualityFilterIntegration:
    """G4 — end-to-end filter resolution through Tortoise on PostgreSQL.

    The G4 guard fires during query build, before SQL generation, so the
    assertion is dialect-free; the rows are seeded through the real vector
    column so the NULL/ISNULL branches hit a live pgvector table.
    """

    async def _seed(self, name: str, embedding: list[float] | None) -> None:
        await _VecDoc.create(name=name, embedding=embedding)

    @pytest.mark.asyncio
    async def test_bare_non_none_raises(self) -> None:
        await self._seed("a", [0.0, 0.0])
        with pytest.raises(
            VectorFieldError, match="Bare equality filters are not supported"
        ):
            await _VecDoc.filter(embedding=[0.1, 0.2]).all()

    @pytest.mark.asyncio
    async def test_bare_none_is_null(self) -> None:
        await self._seed("has_vec", [0.0, 0.0])
        await self._seed("no_vec", None)
        rows = await _VecDoc.filter(embedding=None)
        assert [r.name for r in rows] == ["no_vec"]

    @pytest.mark.asyncio
    async def test_isnull_and_not_isnull_unchanged(self) -> None:
        await self._seed("has_vec", [0.0, 0.0])
        await self._seed("no_vec", None)
        assert [r.name for r in await _VecDoc.filter(embedding__isnull=True)] == [
            "no_vec"
        ]
        assert [r.name for r in await _VecDoc.filter(embedding__not_isnull=True)] == [
            "has_vec"
        ]
