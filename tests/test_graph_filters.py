"""Tests for graph filters (pgvector similarity operators)."""

from tortoise_extended.expressions.graph_filters import (
    CosineDistance,
    InnerProduct,
    L2Distance,
    get_vector_filters,
    vector_encoder,
)


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

    def test_has_isnull(self) -> None:
        filters = get_vector_filters("embedding", "embedding")
        assert "embedding__isnull" in filters

    def test_field_name_prefix(self) -> None:
        filters = get_vector_filters("vec", "vec")
        assert "vec__l2_distance" in filters
        assert "vec__cosine_distance" in filters


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


class TestFilterPatchIdempotent:
    def test_reload_does_not_double_wrap(self) -> None:
        """Verify filter patch is idempotent across reloads."""
        import importlib

        import tortoise.filters as f

        before = f.get_filters_for_field
        importlib.reload(__import__("tortoise_extended"))
        after = f.get_filters_for_field
        assert before is after
