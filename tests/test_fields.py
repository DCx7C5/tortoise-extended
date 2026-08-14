"""Comprehensive tests for LTreeField and VectorField.

Covers serialization, deserialization, SQL types, repr, binary decoding,
and all edge cases. No database connection required.
"""

import struct

from tortoise_extended.fields.ltree_field import LTreeField
from tortoise_extended.fields.vector_field import VectorField


# ---------------------------------------------------------------------------
# LTreeField tests
# ---------------------------------------------------------------------------


class TestLTreeFieldSQLType:
    """Verify LTreeField SQL type and indexability."""

    def test_sql_type(self) -> None:
        """SQL_TYPE should be 'ltree'."""
        assert LTreeField.SQL_TYPE == "ltree"

    def test_indexable(self) -> None:
        """LTreeField should be indexable."""
        assert LTreeField.indexable is True


class TestLTreeFieldInit:
    """Verify LTreeField constructor parameters."""

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

    def test_null_param(self) -> None:
        """null=True should be stored."""
        field = LTreeField(null=True)
        assert field.null is True


class TestLTreeFieldToPythonValue:
    """Verify to_python_value conversions."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        field = LTreeField()
        assert field.to_python_value(None) is None

    def test_string_splits(self) -> None:
        """String should be split into list by separator."""
        field = LTreeField()
        result = field.to_python_value("root.child.grandchild")
        assert result == ["root", "child", "grandchild"]

    def test_list_passthrough(self) -> None:
        """List should be returned as-is."""
        field = LTreeField()
        result = field.to_python_value(["root", "child"])
        assert result == ["root", "child"]

    def test_custom_separator(self) -> None:
        """Custom separator should be used for splitting."""
        field = LTreeField(separator="/")
        result = field.to_python_value("a/b/c")
        assert result == ["a", "b", "c"]

    def test_single_label(self) -> None:
        """Single label path returns single-element list."""
        field = LTreeField()
        result = field.to_python_value("root")
        assert result == ["root"]


class TestLTreeFieldToDbValue:
    """Verify to_db_value conversions."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        field = LTreeField()
        assert field.to_db_value(None, None) is None

    def test_list_joins(self) -> None:
        """List should be joined into string."""
        field = LTreeField()
        result = field.to_db_value(["root", "child", "grandchild"], None)
        assert result == "root.child.grandchild"

    def test_string_passthrough(self) -> None:
        """String should be returned as-is."""
        field = LTreeField()
        result = field.to_db_value("root.child", None)
        assert result == "root.child"

    def test_integer_list(self) -> None:
        """Integer list should be converted to string with dots."""
        field = LTreeField()
        result = field.to_db_value([1, 2, 3], None)
        assert result == "1.2.3"

    def test_custom_separator(self) -> None:
        """Custom separator should be used for joining."""
        field = LTreeField(separator="/")
        result = field.to_db_value(["a", "b", "c"], None)
        assert result == "a/b/c"

    def test_empty_list(self) -> None:
        """Empty list should produce empty string."""
        field = LTreeField()
        result = field.to_db_value([], None)
        assert result == ""


class TestLTreeFieldRepr:
    """Verify __repr__ output."""

    def test_repr_default(self) -> None:
        """Repr should show default params."""
        field = LTreeField()
        r = repr(field)
        assert "max_length=256" in r
        assert "separator='.'" in r

    def test_repr_custom(self) -> None:
        """Repr should show custom params."""
        field = LTreeField(max_length=1024, separator="/")
        r = repr(field)
        assert "max_length=1024" in r
        assert "separator='/'" in r


# ---------------------------------------------------------------------------
# VectorField tests
# ---------------------------------------------------------------------------


class TestVectorFieldSQLType:
    """Verify VectorField SQL type and indexability."""

    def test_sql_type(self) -> None:
        """SQL_TYPE should be 'vector'."""
        assert VectorField.SQL_TYPE == "vector"

    def test_indexable(self) -> None:
        """VectorField should be indexable."""
        assert VectorField.indexable is True

    def test_dimensions_stored(self) -> None:
        """Dimensions parameter should be stored."""
        field = VectorField(dimensions=1536)
        assert field.dimensions == 1536

    def test_dimensions_none(self) -> None:
        """Dimensions can be None."""
        field = VectorField()
        assert field.dimensions is None


class TestVectorFieldDbPostgresSqlType:
    """Verify _db_postgres SQL_TYPE property."""

    def test_with_dimensions(self) -> None:
        """With dimensions should return vector(N)."""
        field = VectorField(dimensions=1536)
        sql_type = field.get_for_dialect("postgres", "SQL_TYPE")
        assert sql_type == "vector(1536)"

    def test_without_dimensions(self) -> None:
        """Without dimensions should return bare 'vector'."""
        field = VectorField()
        sql_type = field.get_for_dialect("postgres", "SQL_TYPE")
        assert sql_type == "vector"


class TestVectorFieldDbSqliteSqlType:
    """Verify _db_sqlite SQL_TYPE."""

    def test_sqlite_type(self) -> None:
        """SQLite type should be 'BLOB'."""
        assert VectorField._db_sqlite.SQL_TYPE == "BLOB"


class TestVectorFieldToPythonValue:
    """Verify to_python_value conversions."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        field = VectorField(dimensions=3)
        assert field.to_python_value(None) is None

    def test_list_passthrough(self) -> None:
        """List should be returned as-is."""
        field = VectorField(dimensions=3)
        result = field.to_python_value([0.1, 0.2, 0.3])
        assert result == [0.1, 0.2, 0.3]

    def test_string_parse(self) -> None:
        """PostgreSQL string '[0.1,0.2]' should be parsed."""
        field = VectorField(dimensions=3)
        result = field.to_python_value("[0.1,0.2,0.3]")
        assert result == [0.1, 0.2, 0.3]

    def test_memoryview_decode(self) -> None:
        """Memoryview binary should be decoded to float list."""
        field = VectorField(dimensions=2)
        header = struct.pack(">HH", 0, 2)
        data = struct.pack(">2f", 1.0, 2.0)
        result = field.to_python_value(memoryview(header + data))
        assert result == [1.0, 2.0]

    def test_empty_binary(self) -> None:
        """Empty binary should return empty list."""
        field = VectorField(dimensions=2)
        result = field.to_python_value(b"")
        assert result == []

    def test_tuple_passthrough(self) -> None:
        """Tuple should be converted to list."""
        field = VectorField(dimensions=3)
        result = field.to_python_value((1.0, 2.0, 3.0))
        assert result == [1.0, 2.0, 3.0]

    def test_empty_string(self) -> None:
        """Empty string '[]' should return empty list."""
        field = VectorField(dimensions=3)
        result = field.to_python_value("[]")
        assert result == []


class TestVectorFieldToDbValue:
    """Verify to_db_value conversions."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        field = VectorField(dimensions=3)
        assert field.to_db_value(None, None) is None

    def test_list_returns_list(self) -> None:
        """List should be returned as a list."""
        field = VectorField(dimensions=3)
        result = field.to_db_value([0.1, 0.2, 0.3], None)
        assert result == [0.1, 0.2, 0.3]

    def test_tuple_returns_list(self) -> None:
        """Tuple should be converted to list."""
        field = VectorField(dimensions=3)
        result = field.to_db_value((1.0, 2.0, 3.0), None)
        assert result == [1.0, 2.0, 3.0]
        assert isinstance(result, list)


class TestVectorFieldDecodeBinary:
    """Verify _decode_binary static method."""

    def test_normal_decode(self) -> None:
        """Normal binary should decode correctly."""
        header = struct.pack(">HH", 0, 3)
        data = struct.pack(">3f", 0.1, 0.2, 0.3)
        result = VectorField._decode_binary(header + data)
        assert len(result) == 3
        assert abs(result[0] - 0.1) < 1e-6
        assert abs(result[1] - 0.2) < 1e-6
        assert abs(result[2] - 0.3) < 1e-6

    def test_empty_data(self) -> None:
        """Empty bytes should return empty list."""
        assert VectorField._decode_binary(b"") == []

    def test_zero_dim(self) -> None:
        """Zero dimensions should return empty list."""
        header = struct.pack(">HH", 0, 0)
        result = VectorField._decode_binary(header)
        assert result == []

    def test_short_header(self) -> None:
        """Data shorter than 4 bytes should return empty list."""
        assert VectorField._decode_binary(b"\x00\x01") == []


class TestVectorFieldRepr:
    """Verify __repr__ output."""

    def test_repr_with_dimensions(self) -> None:
        """Repr should show dimensions."""
        field = VectorField(dimensions=1536)
        assert repr(field) == "VectorField(dimensions=1536, vector_type='vector')"

    def test_repr_without_dimensions(self) -> None:
        """Repr should show None dimensions."""
        field = VectorField()
        assert repr(field) == "VectorField(dimensions=None, vector_type='vector')"

    def test_repr_halfvec(self) -> None:
        """Repr should show the halfvec type."""
        field = VectorField(dimensions=1536, vector_type="halfvec")
        assert repr(field) == "VectorField(dimensions=1536, vector_type='halfvec')"
