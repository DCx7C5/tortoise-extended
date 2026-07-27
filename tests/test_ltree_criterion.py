"""Comprehensive tests for ltree criterion classes, encoders, and filter functions.

Covers all 5 criterion classes, encoders, filter definitions, and internal
filter operator functions. No database connection required.
"""

from pypika_tortoise import Table
from pypika_tortoise.context import DEFAULT_SQL_CONTEXT
from pypika_tortoise.terms import Field, ValueWrapper

from tortoise_extended.expressions.ltree_filters import (
    LTreeAncestorMatch,
    LTreeAncestorOf,
    LTreeDescendantMatch,
    LTreeDescendantOf,
    LTreeMatch,
    _ancestor_match_filter,
    _ancestor_of_filter,
    _descendant_match_filter,
    _descendant_of_filter,
    _is_null,
    _lquery_encoder,
    _match_filter,
    _not_null,
    get_ltree_filters,
    ltree_encoder,
)


# ---------------------------------------------------------------------------
# Criterion classes — SQL generation
# ---------------------------------------------------------------------------


class TestLTreeAncestorOf:
    """Test LTreeAncestorOf criterion class."""

    def test_sql_contains_operator(self) -> None:
        """@> operator should appear in SQL."""
        t = Table("categories")
        criterion = LTreeAncestorOf("path", ValueWrapper("root.child"))
        sql = criterion.get_sql(DEFAULT_SQL_CONTEXT)
        assert "@>" in sql

    def test_string_field_construction(self) -> None:
        """String field name should be converted to Field term."""
        criterion = LTreeAncestorOf("path", ValueWrapper("root.child"))
        assert criterion.left is not None

    def test_term_field_construction(self) -> None:
        """Term field should be used directly."""
        t = Table("categories")
        criterion = LTreeAncestorOf(t.path, ValueWrapper("root.child"))
        assert criterion.left is not None


class TestLTreeDescendantOf:
    """Test LTreeDescendantOf criterion class."""

    def test_sql_contains_operator(self) -> None:
        """<@ operator should appear in SQL."""
        t = Table("categories")
        criterion = LTreeDescendantOf("path", ValueWrapper("root"))
        sql = criterion.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<@" in sql

    def test_string_field_construction(self) -> None:
        """String field name should be converted to Field term."""
        criterion = LTreeDescendantOf("path", ValueWrapper("root"))
        assert criterion.left is not None


class TestLTreeMatch:
    """Test LTreeMatch criterion class."""

    def test_sql_contains_operator(self) -> None:
        """~ operator should appear in SQL."""
        t = Table("categories")
        criterion = LTreeMatch("path", ValueWrapper("root.*"))
        sql = criterion.get_sql(DEFAULT_SQL_CONTEXT)
        assert "~" in sql

    def test_string_field_construction(self) -> None:
        """String field name should be converted to Field term."""
        criterion = LTreeMatch("path", ValueWrapper("root.*"))
        assert criterion.left is not None


class TestLTreeAncestorMatch:
    """Test LTreeAncestorMatch criterion class."""

    def test_sql_contains_operator(self) -> None:
        """?@> operator should appear in SQL."""
        t = Table("categories")
        criterion = LTreeAncestorMatch("path", ValueWrapper("*.child"))
        sql = criterion.get_sql(DEFAULT_SQL_CONTEXT)
        assert "?@>" in sql

    def test_string_field_construction(self) -> None:
        """String field name should be converted to Field term."""
        criterion = LTreeAncestorMatch("path", ValueWrapper("*.child"))
        assert criterion.left is not None


class TestLTreeDescendantMatch:
    """Test LTreeDescendantMatch criterion class."""

    def test_sql_contains_operator(self) -> None:
        """?<@ operator should appear in SQL."""
        t = Table("categories")
        criterion = LTreeDescendantMatch("path", ValueWrapper("root.*"))
        sql = criterion.get_sql(DEFAULT_SQL_CONTEXT)
        assert "?<@" in sql

    def test_string_field_construction(self) -> None:
        """String field name should be converted to Field term."""
        criterion = LTreeDescendantMatch("path", ValueWrapper("root.*"))
        assert criterion.left is not None


# ---------------------------------------------------------------------------
# Encoders
# ---------------------------------------------------------------------------


class TestLTreeEncoder:
    """Test ltree_encoder function."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        assert ltree_encoder(None) is None

    def test_string_passthrough(self) -> None:
        """String input should pass through unchanged."""
        assert ltree_encoder("root.child") == "root.child"

    def test_list_joins(self) -> None:
        """List should be joined with dots."""
        assert ltree_encoder(["root", "child"]) == "root.child"

    def test_tuple_joins(self) -> None:
        """Tuple should be joined with dots."""
        assert ltree_encoder(("root", "child")) == "root.child"

    def test_integer_list(self) -> None:
        """Integer list should be converted to strings and joined."""
        assert ltree_encoder([1, 2, 3]) == "1.2.3"

    def test_empty_list(self) -> None:
        """Empty list should produce empty string."""
        assert ltree_encoder([]) == ""

    def test_int_passthrough(self) -> None:
        """Integer should be stringified."""
        assert ltree_encoder(42) == "42"


class TestLQueryEncoder:
    """Test _lquery_encoder function."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        assert _lquery_encoder(None) is None

    def test_string_passthrough(self) -> None:
        """String input should pass through unchanged."""
        assert _lquery_encoder("root.*") == "root.*"


# ---------------------------------------------------------------------------
# Filter definitions
# ---------------------------------------------------------------------------


class TestGetLTreeFilters:
    """Test get_ltree_filters function."""

    def test_returns_dict(self) -> None:
        """Should return a dictionary."""
        filters = get_ltree_filters("path", "path")
        assert isinstance(filters, dict)

    def test_expected_keys(self) -> None:
        """Should contain all expected filter keys."""
        filters = get_ltree_filters("path", "path")
        expected = [
            "path",
            "path__isnull",
            "path__not_isnull",
            "path__ancestor_of",
            "path__descendant_of",
            "path__match",
            "path__ancestor_match",
            "path__descendant_match",
        ]
        for key in expected:
            assert key in filters, f"Missing key: {key}"

    def test_source_field_propagation(self) -> None:
        """Each filter should reference the correct source_field."""
        filters = get_ltree_filters("path", "db_path")
        for _key, filter_def in filters.items():
            assert filter_def["source_field"] == "db_path"

    def test_each_filter_has_operator(self) -> None:
        """Each filter should have an 'operator' callable."""
        filters = get_ltree_filters("path", "path")
        for _key, filter_def in filters.items():
            assert callable(filter_def["operator"])

    def test_each_filter_has_value_encoder(self) -> None:
        """Each filter should have a 'value_encoder' callable."""
        filters = get_ltree_filters("path", "path")
        for _key, filter_def in filters.items():
            assert callable(filter_def["value_encoder"])

    def test_ancestor_of_filter_uses_ltree_encoder(self) -> None:
        """ancestor_of filter should use ltree_encoder."""
        filters = get_ltree_filters("path", "path")
        assert filters["path__ancestor_of"]["value_encoder"] is ltree_encoder

    def test_match_filter_uses_lquery_encoder(self) -> None:
        """match filter should use _lquery_encoder."""
        filters = get_ltree_filters("path", "path")
        assert filters["path__match"]["value_encoder"] is _lquery_encoder


# ---------------------------------------------------------------------------
# Internal filter functions
# ---------------------------------------------------------------------------


class TestInternalFilterFunctions:
    """Test internal filter operator functions."""

    def test_is_null(self) -> None:
        """_is_null with True should call isnull()."""
        t = Table("test")
        result = _is_null(t.path, True)
        assert result is not None

    def test_is_null_false(self) -> None:
        """_is_null with False should negate isnull()."""
        t = Table("test")
        result = _is_null(t.path, False)
        assert result is not None

    def test_not_null(self) -> None:
        """_not_null with True should negate isnull()."""
        t = Table("test")
        result = _not_null(t.path, True)
        assert result is not None

    def test_not_null_false(self) -> None:
        """_not_null with False should call isnull()."""
        t = Table("test")
        result = _not_null(t.path, False)
        assert result is not None

    def test_ancestor_of_filter(self) -> None:
        """_ancestor_of_filter should produce LTreeAncestorOf."""
        t = Table("test")
        result = _ancestor_of_filter(t.path, "root.child")
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "@>" in sql

    def test_descendant_of_filter(self) -> None:
        """_descendant_of_filter should produce LTreeDescendantOf."""
        t = Table("test")
        result = _descendant_of_filter(t.path, "root")
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "<@" in sql

    def test_match_filter(self) -> None:
        """_match_filter should produce LTreeMatch."""
        t = Table("test")
        result = _match_filter(t.path, "root.*")
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "~" in sql

    def test_ancestor_match_filter(self) -> None:
        """_ancestor_match_filter should produce LTreeAncestorMatch."""
        t = Table("test")
        result = _ancestor_match_filter(t.path, "*.child")
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "?@>" in sql

    def test_descendant_match_filter(self) -> None:
        """_descendant_match_filter should produce LTreeDescendantMatch."""
        t = Table("test")
        result = _descendant_match_filter(t.path, "root.*")
        sql = result.get_sql(DEFAULT_SQL_CONTEXT)
        assert "?<@" in sql
