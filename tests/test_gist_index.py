"""Unit tests for GiSTIndex — initialization and type verification.

No database connection required.
"""

from tortoise_extended.indexes.ltree_index import GiSTIndex


class TestGiSTIndex:
    """Test GiSTIndex class."""

    def test_index_type(self) -> None:
        idx = GiSTIndex(fields=("path",))
        assert idx.INDEX_TYPE == "gist"

    def test_fields(self) -> None:
        idx = GiSTIndex(fields=("path", "name"))
        assert idx.field_names == ["path", "name"]

    def test_custom_name(self) -> None:
        idx = GiSTIndex(fields=("path",), name="my_gist_idx")
        assert idx.name == "my_gist_idx"

    def test_describe(self) -> None:
        idx = GiSTIndex(fields=("path",))
        desc = idx.describe()
        assert "fields" in desc
        assert desc["fields"] == ["path"]

    def test_deconstruct(self) -> None:
        idx = GiSTIndex(fields=("path",), name="test_idx")
        path, args, kwargs = idx.deconstruct()
        assert "GiSTIndex" in path
        assert kwargs["fields"] == ["path"]
        assert kwargs["name"] == "test_idx"
