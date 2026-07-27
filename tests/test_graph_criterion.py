"""Comprehensive tests for pgvector graph filter classes and filter functions.

Covers all 5 distance criterion classes, vector encoders, filter definitions,
and internal filter operator functions. No database connection required.
"""

from pypika_tortoise import Table
from pypika_tortoise.context import DEFAULT_SQL_CONTEXT
from pypika_tortoise.terms import ValueWrapper

from tortoise_extended.expressions.graph_filters import (
    CosineDistance,
    HammingDistance,
    InnerProduct,
    JaccardDistance,
    L2Distance,
    _cosine_distance_lte,
    _inner_product_gte,
    _l2_distance_lte,
    _vector_value_passthrough,
    get_vector_filters,
    vector_encoder,
)


# ---------------------------------------------------------------------------
# Criterion classes — SQL generation
# ---------------------------------------------------------------------------


class TestL2Distance:
    """Test L2Distance criterion class."""

    def test_sql_contains_operator(self) -> None:
        """<-> operator should appear in SQL."""
        t = Table("test")
        criterion = L2Distance(t.embedding, ValueWrapper("[0.1,0.2]"))
        sql = criterion.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<->" in sql

    def test_string_field_construction(self) -> None:
        """String field name should be converted to Field term."""
        criterion = L2Distance("embedding", ValueWrapper("[0.1,0.2]"))
        assert criterion.left is not None


class TestInnerProduct:
    """Test InnerProduct criterion class."""

    def test_sql_contains_operator(self) -> None:
        """<#> operator should appear in SQL."""
        t = Table("test")
        criterion = InnerProduct(t.embedding, ValueWrapper("[0.1,0.2]"))
        sql = criterion.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<#>" in sql

    def test_string_field_construction(self) -> None:
        """String field name should be converted to Field term."""
        criterion = InnerProduct("embedding", ValueWrapper("[0.1,0.2]"))
        assert criterion.left is not None


class TestCosineDistance:
    """Test CosineDistance criterion class."""

    def test_sql_contains_operator(self) -> None:
        """<=> operator should appear in SQL."""
        t = Table("test")
        criterion = CosineDistance(t.embedding, ValueWrapper("[0.1,0.2]"))
        sql = criterion.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<=>" in sql

    def test_string_field_construction(self) -> None:
        """String field name should be converted to Field term."""
        criterion = CosineDistance("embedding", ValueWrapper("[0.1,0.2]"))
        assert criterion.left is not None


class TestHammingDistance:
    """Test HammingDistance criterion class."""

    def test_sql_contains_operator(self) -> None:
        """<~> operator should appear in SQL."""
        t = Table("test")
        criterion = HammingDistance(t.embedding, ValueWrapper("[1,0,1]"))
        sql = criterion.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<~>" in sql

    def test_string_field_construction(self) -> None:
        """String field name should be converted to Field term."""
        criterion = HammingDistance("embedding", ValueWrapper("[1,0,1]"))
        assert criterion.left is not None


class TestJaccardDistance:
    """Test JaccardDistance criterion class."""

    def test_sql_contains_operator(self) -> None:
        """<%> operator should appear in SQL."""
        t = Table("test")
        criterion = JaccardDistance(t.embedding, ValueWrapper("[1,0,1]"))
        sql = criterion.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<%>" in sql

    def test_string_field_construction(self) -> None:
        """String field name should be converted to Field term."""
        criterion = JaccardDistance("embedding", ValueWrapper("[1,0,1]"))
        assert criterion.left is not None


# ---------------------------------------------------------------------------
# Encoders
# ---------------------------------------------------------------------------


class TestVectorEncoder:
    """Test vector_encoder function."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        assert vector_encoder(None) is None

    def test_string_passthrough(self) -> None:
        """String input should pass through unchanged."""
        assert vector_encoder("[0.1,0.2]") == "[0.1,0.2]"

    def test_list_joins(self) -> None:
        """List should be formatted as pgvector string."""
        result = vector_encoder([0.1, 0.2, 0.3])
        assert result == "[0.1,0.2,0.3]"

    def test_tuple_joins(self) -> None:
        """Tuple should be formatted as pgvector string."""
        result = vector_encoder((1.0, 2.0))
        assert result == "[1.0,2.0]"

    def test_mixed_int_float(self) -> None:
        """Mixed int/float list should produce float string."""
        result = vector_encoder([1, 2, 3])
        assert result == "[1.0,2.0,3.0]"

    def test_with_explicit_args(self) -> None:
        """Extra args should be ignored."""
        result = vector_encoder([1.0, 2.0], None, None)
        assert result == "[1.0,2.0]"


class TestVectorValuePassthrough:
    """Test _vector_value_passthrough function."""

    def test_passthrough(self) -> None:
        """Should return value unchanged."""
        val = [[0.1, 0.2], 0.5]
        assert _vector_value_passthrough(val) is val

    def test_passthrough_string(self) -> None:
        """String should pass through unchanged."""
        assert _vector_value_passthrough("test") == "test"

    def test_passthrough_none(self) -> None:
        """None should pass through."""
        assert _vector_value_passthrough(None) is None


# ---------------------------------------------------------------------------
# Filter definitions
# ---------------------------------------------------------------------------


class TestGetVectorFilters:
    """Test get_vector_filters function."""

    def test_returns_dict(self) -> None:
        """Should return a dictionary."""
        filters = get_vector_filters("embedding", "embedding")
        assert isinstance(filters, dict)

    def test_expected_keys(self) -> None:
        """Should contain all expected filter keys."""
        filters = get_vector_filters("embedding", "embedding")
        expected = [
            "embedding",
            "embedding__isnull",
            "embedding__not_isnull",
            "embedding__l2_distance",
            "embedding__cosine_distance",
            "embedding__inner_product",
        ]
        for key in expected:
            assert key in filters, f"Missing key: {key}"

    def test_source_field_propagation(self) -> None:
        """Each filter should reference the correct source_field."""
        filters = get_vector_filters("emb", "db_emb")
        for _key, filter_def in filters.items():
            assert filter_def["source_field"] == "db_emb"

    def test_each_filter_has_operator(self) -> None:
        """Each filter should have an 'operator' callable."""
        filters = get_vector_filters("embedding", "embedding")
        for _key, filter_def in filters.items():
            assert callable(filter_def["operator"])

    def test_each_filter_has_value_encoder(self) -> None:
        """Each filter should have a 'value_encoder' callable."""
        filters = get_vector_filters("embedding", "embedding")
        for _key, filter_def in filters.items():
            assert callable(filter_def["value_encoder"])

    def test_distance_filters_use_passthrough_encoder(self) -> None:
        """Distance filters should use _vector_value_passthrough."""
        filters = get_vector_filters("embedding", "embedding")
        for key in ("embedding__l2_distance", "embedding__cosine_distance", "embedding__inner_product"):
            assert filters[key]["value_encoder"] is _vector_value_passthrough


# ---------------------------------------------------------------------------
# Internal filter operator functions
# ---------------------------------------------------------------------------


class TestInternalFilterFunctions:
    """Test internal distance filter operator functions."""

    def test_l2_distance_lte_with_threshold(self) -> None:
        """_l2_distance_lte with [vector, threshold] should use threshold."""
        t = Table("test")
        result = _l2_distance_lte(t.embedding, [[0.1, 0.2], 0.5])
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<->" in sql
        assert "0.5" in sql

    def test_l2_distance_lte_bare_list(self) -> None:
        """_l2_distance_lte with bare list should use default threshold 1.0."""
        t = Table("test")
        result = _l2_distance_lte(t.embedding, [0.1, 0.2])
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<->" in sql

    def test_l2_distance_lte_bare_value(self) -> None:
        """_l2_distance_lte with non-list value should use default threshold."""
        t = Table("test")
        result = _l2_distance_lte(t.embedding, 0.5)
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<->" in sql

    def test_cosine_distance_lte_with_threshold(self) -> None:
        """_cosine_distance_lte with [vector, threshold] should use threshold."""
        t = Table("test")
        result = _cosine_distance_lte(t.embedding, [[0.1, 0.2], 0.3])
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<=>" in sql
        assert "0.3" in sql

    def test_cosine_distance_lte_bare_list(self) -> None:
        """_cosine_distance_lte with bare list should use default threshold 1.0."""
        t = Table("test")
        result = _cosine_distance_lte(t.embedding, [0.1, 0.2])
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<=>" in sql

    def test_cosine_distance_lte_bare_value(self) -> None:
        """_cosine_distance_lte with non-list value should use default threshold."""
        t = Table("test")
        result = _cosine_distance_lte(t.embedding, 0.5)
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<=>" in sql

    def test_inner_product_gte_with_threshold(self) -> None:
        """_inner_product_gte with [vector, threshold] should use threshold."""
        t = Table("test")
        result = _inner_product_gte(t.embedding, [[0.1, 0.2], 0.5])
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<#>" in sql

    def test_inner_product_gte_bare_list(self) -> None:
        """_inner_product_gte with bare list should use default threshold 0.0."""
        t = Table("test")
        result = _inner_product_gte(t.embedding, [0.1, 0.2])
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<#>" in sql

    def test_inner_product_gte_bare_value(self) -> None:
        """_inner_product_gte with non-list value should use default threshold."""
        t = Table("test")
        result = _inner_product_gte(t.embedding, 0.5)
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<#>" in sql
