"""Unit tests for LTreeField and ltree filters.

Tests field serialization, filter generation, and query operators.
"""

import pytest

from tortoise_extended.expressions.ltree_filters import (
    LTreeAncestorOf,
    LTreeDescendantOf,
    LTreeMatch,
    _lquery_encoder,
    get_ltree_filters,
    ltree_encoder,
)
from tortoise_extended.fields.ltree import LTreeField


class TestLTreeField:
    """Tests for LTreeField definition and serialization."""

    def test_sql_type(self) -> None:
        """LTreeField should use 'ltree' SQL type."""
        field = LTreeField()
        assert field.SQL_TYPE == "ltree"

    def test_default_max_length(self) -> None:
        """Default max_length should be 256."""
        field = LTreeField()
        assert field.max_length == 256

    def test_custom_max_length(self) -> None:
        """Custom max_length should be respected."""
        field = LTreeField(max_length=1024)
        assert field.max_length == 1024

    def test_default_separator(self) -> None:
        """Default separator should be '.'."""
        field = LTreeField()
        assert field.separator == "."

    def test_custom_separator(self) -> None:
        """Custom separator should be respected."""
        field = LTreeField(separator="/")
        assert field.separator == "/"

    def test_to_python_value_none(self) -> None:
        """None should be returned as None."""
        field = LTreeField()
        assert field.to_python_value(None) is None

    def test_to_python_value_string(self) -> None:
        """String should be split into list."""
        field = LTreeField()
        result = field.to_python_value("root.child.grandchild")
        assert result == ["root", "child", "grandchild"]

    def test_to_python_value_list(self) -> None:
        """List should be returned as-is."""
        field = LTreeField()
        result = field.to_python_value(["root", "child"])
        assert result == ["root", "child"]

    def test_to_db_value_none(self) -> None:
        """None should be returned as None."""
        field = LTreeField()
        assert field.to_db_value(None, None) is None

    def test_to_db_value_list(self) -> None:
        """List should be joined into string."""
        field = LTreeField()
        result = field.to_db_value(["root", "child", "grandchild"], None)
        assert result == "root.child.grandchild"

    def test_to_db_value_string(self) -> None:
        """String should be returned as-is."""
        field = LTreeField()
        result = field.to_db_value("root.child", None)
        assert result == "root.child"

    def test_to_db_value_integers(self) -> None:
        """Integer list should be converted to string."""
        field = LTreeField()
        result = field.to_db_value([1, 2, 3], None)
        assert result == "1.2.3"

    def test_to_db_value_enforces_max_length(self) -> None:
        """G17 — max_length is a real guard: over-long paths raise."""
        field = LTreeField(max_length=10)
        with pytest.raises(ValueError, match="exceeds max_length=10"):
            field.to_db_value(["root", "child", "grandchild"], None)

    def test_to_db_value_respects_max_length_boundary(self) -> None:
        """Paths exactly at max_length are accepted."""
        field = LTreeField(max_length=7)
        assert field.to_db_value(["a.b.c"], None) == "a.b.c"

    def test_repr(self) -> None:
        """Repr should show field parameters."""
        field = LTreeField(max_length=1024, separator="/")
        assert "max_length=1024" in repr(field)
        assert "separator='/'" in repr(field)


class TestLTreeEncoder:
    """Tests for ltree_encoder function."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        assert ltree_encoder(None) is None

    def test_string_passthrough(self) -> None:
        """String input should pass through."""
        assert ltree_encoder("root.child") == "root.child"

    def test_list_join(self) -> None:
        """List should be joined with dots."""
        assert ltree_encoder(["root", "child"]) == "root.child"

    def test_tuple_join(self) -> None:
        """Tuple should be joined with dots."""
        assert ltree_encoder(("root", "child")) == "root.child"

    def test_integer_list(self) -> None:
        """Integer list should be converted to strings."""
        assert ltree_encoder([1, 2, 3]) == "1.2.3"

    def test_empty_list(self) -> None:
        """Empty list should return empty string."""
        assert ltree_encoder([]) == ""


class TestLQueryEncoder:
    """Tests for _lquery_encoder function."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        assert _lquery_encoder(None) is None

    def test_string_passthrough(self) -> None:
        """String input should pass through."""
        assert _lquery_encoder("root.*") == "root.*"


class TestLTreeFilters:
    """Tests for ltree filter definitions."""

    def test_get_filters_returns_dict(self) -> None:
        """get_ltree_filters should return a dictionary."""
        filters = get_ltree_filters("path", "path")
        assert isinstance(filters, dict)

    def test_get_filters_has_expected_keys(self) -> None:
        """Filter dict should have expected keys."""
        filters = get_ltree_filters("path", "path")
        assert "path" in filters
        assert "path__isnull" in filters
        assert "path__ancestor_of" in filters
        assert "path__descendant_of" in filters
        assert "path__match" in filters
        assert "path__ancestor_match" in filters
        assert "path__descendant_match" in filters

    def test_filter_source_field(self) -> None:
        """Each filter should reference the correct source field."""
        filters = get_ltree_filters("path", "db_path")
        for _key, filter_def in filters.items():
            assert filter_def["source_field"] == "db_path"


class TestLTreeCriterion:
    """Tests for ltree Criterion classes."""

    def test_ancestor_of_type(self) -> None:
        """LTreeAncestorOf should be a Criterion instance."""
        criterion = LTreeAncestorOf("path", "root.child")
        assert criterion is not None

    def test_descendant_of_type(self) -> None:
        """LTreeDescendantOf should be a Criterion instance."""
        criterion = LTreeDescendantOf("path", "root")
        assert criterion is not None

    def test_match_type(self) -> None:
        """LTreeMatch should be a Criterion instance."""
        criterion = LTreeMatch("path", "root.*")
        assert criterion is not None

    def test_ancestor_of_field_conversion(self) -> None:
        """String field name should be converted to Field term."""
        criterion = LTreeAncestorOf("path", "root.child")
        assert criterion.left is not None

    def test_descendant_of_field_conversion(self) -> None:
        """String field name should be converted to Field term."""
        criterion = LTreeDescendantOf("path", "root")
        assert criterion.left is not None
