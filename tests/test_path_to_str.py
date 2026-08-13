"""Tests for _path_to_str helper function.

Covers all input types: None, empty string, empty list, string, list of
strings, and falsy values.
"""

from tortoise_extended.models.hierarchy_model import _path_to_str


class TestPathToStr:
    """Test _path_to_str normalization helper."""

    def test_none_returns_empty(self) -> None:
        """None input should return empty string."""
        assert _path_to_str(None) == ""

    def test_empty_string_returns_empty(self) -> None:
        """Empty string should return empty string."""
        assert _path_to_str("") == ""

    def test_empty_list_returns_empty(self) -> None:
        """Empty list should return empty string."""
        assert _path_to_str([]) == ""

    def test_string_passthrough(self) -> None:
        """String path should pass through unchanged."""
        assert _path_to_str("root.child.grandchild") == "root.child.grandchild"

    def test_list_of_strings_joins(self) -> None:
        """List of strings should be joined with dots."""
        assert _path_to_str(["root", "child", "grandchild"]) == "root.child.grandchild"

    def test_single_element_list(self) -> None:
        """Single-element list should produce no dots."""
        assert _path_to_str(["root"]) == "root"

    def test_boolean_false_returns_empty(self) -> None:
        """False is falsy, should return empty string."""
        assert _path_to_str(False) == ""

    def test_int_zero_returns_empty(self) -> None:
        """0 is falsy, should return empty string."""
        assert _path_to_str(0) == ""
