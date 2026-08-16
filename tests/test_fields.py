"""Comprehensive tests for LTreeField, VectorField, and the typed fields.

Covers serialization, deserialization, SQL types, repr, binary decoding,
validation, and all edge cases. No database connection required.
"""

import ipaddress
import struct
import uuid
from pathlib import Path, PurePosixPath

import pytest

from tortoise_extended.fields.ipv4 import IPv4Field
from tortoise_extended.fields.ltree import LTreeField
from tortoise_extended.fields.path import PathField
from tortoise_extended.fields.url import URLField
from tortoise_extended.fields.uuid import UUID4Field, UUID7Field
from tortoise_extended.fields.vector import VectorField


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


# ---------------------------------------------------------------------------
# IPv4Field tests
# ---------------------------------------------------------------------------


class TestIPv4FieldSQLType:
    """Verify IPv4Field SQL type and indexability."""

    def test_sql_type(self) -> None:
        """SQL_TYPE should be 'inet'."""
        assert IPv4Field.SQL_TYPE == "inet"

    def test_indexable(self) -> None:
        """IPv4Field should be indexable."""
        assert IPv4Field.indexable is True


class TestIPv4FieldToPythonValue:
    """Verify to_python_value conversions."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        field = IPv4Field()
        assert field.to_python_value(None) is None

    def test_ipv4_address_passthrough(self) -> None:
        """IPv4Address input should be returned as-is."""
        field = IPv4Field()
        addr = ipaddress.IPv4Address("192.168.1.1")
        assert field.to_python_value(addr) is addr

    def test_valid_string(self) -> None:
        """Valid string should be converted to IPv4Address."""
        field = IPv4Field()
        result = field.to_python_value("192.168.1.1")
        assert result == ipaddress.IPv4Address("192.168.1.1")

    def test_bytes_decoded(self) -> None:
        """Bytes input should be decoded then converted."""
        field = IPv4Field()
        result = field.to_python_value(b"10.0.0.1")
        assert result == ipaddress.IPv4Address("10.0.0.1")

    def test_invalid_string_raises(self) -> None:
        """Invalid string should raise ValueError."""
        field = IPv4Field()
        with pytest.raises(ValueError):
            field.to_python_value("999.1.1.1")


class TestIPv4FieldToDbValue:
    """Verify to_db_value conversions."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        field = IPv4Field()
        assert field.to_db_value(None, None) is None

    def test_ipv4_address_to_string(self) -> None:
        """IPv4Address should be converted to dotted-quad string."""
        field = IPv4Field()
        result = field.to_db_value(ipaddress.IPv4Address("192.168.1.1"), None)
        assert result == "192.168.1.1"

    def test_valid_string_passthrough(self) -> None:
        """Valid string should be returned unchanged."""
        field = IPv4Field()
        result = field.to_db_value("10.0.0.1", None)
        assert result == "10.0.0.1"

    def test_invalid_string_raises(self) -> None:
        """Invalid string should raise ValueError."""
        field = IPv4Field()
        with pytest.raises(ValueError):
            field.to_db_value("not-an-ip", None)


class TestIPv4FieldDbSqliteSqlType:
    """Verify _db_sqlite SQL_TYPE."""

    def test_sqlite_type(self) -> None:
        """SQLite type should be 'VARCHAR(15)'."""
        assert IPv4Field._db_sqlite.SQL_TYPE == "VARCHAR(15)"


# ---------------------------------------------------------------------------
# PathField tests
# ---------------------------------------------------------------------------


class TestPathFieldSQLType:
    """Verify PathField SQL type and indexability."""

    def test_sql_type(self) -> None:
        """SQL_TYPE should be 'TEXT'."""
        assert PathField.SQL_TYPE == "TEXT"

    def test_indexable(self) -> None:
        """PathField should be indexable."""
        assert PathField.indexable is True


class TestPathFieldToDbValue:
    """Verify to_db_value conversions."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        field = PathField()
        assert field.to_db_value(None, None) is None

    def test_string_passthrough(self) -> None:
        """String should be returned unchanged."""
        field = PathField()
        result = field.to_db_value("docs/readme.md", None)
        assert result == "docs/readme.md"

    def test_pathlib_path_to_string(self) -> None:
        """pathlib.Path should be converted to a string."""
        field = PathField()
        result = field.to_db_value(Path("docs/readme.md"), None)
        assert result == "docs/readme.md"

    def test_nul_byte_raises(self) -> None:
        """String containing a NUL byte should raise ValueError."""
        field = PathField()
        with pytest.raises(ValueError):
            field.to_db_value("bad\x00path", None)


class TestPathFieldToPythonValue:
    """Verify to_python_value conversions."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        field = PathField()
        assert field.to_python_value(None) is None

    def test_string_round_trips_to_pure_posix_path(self) -> None:
        """A driver string should load as a PurePosixPath."""
        field = PathField()
        result = field.to_python_value("docs/readme.md")
        assert isinstance(result, PurePosixPath)
        assert result == PurePosixPath("docs/readme.md")

    def test_pure_posix_path_passthrough(self) -> None:
        """PurePosixPath input should be returned unchanged."""
        field = PathField()
        path = PurePosixPath("docs/readme.md")
        result = field.to_python_value(path)
        assert result is path


# ---------------------------------------------------------------------------
# URLField tests
# ---------------------------------------------------------------------------


class TestURLFieldSQLType:
    """Verify URLField SQL type and indexability."""

    def test_sql_type(self) -> None:
        """SQL_TYPE should be 'TEXT'."""
        assert URLField.SQL_TYPE == "TEXT"

    def test_indexable(self) -> None:
        """URLField should be indexable."""
        assert URLField.indexable is True


class TestURLFieldToDbValue:
    """Verify to_db_value conversions."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        field = URLField()
        assert field.to_db_value(None, None) is None

    def test_valid_url_passthrough(self) -> None:
        """Valid URL should be returned unchanged."""
        field = URLField()
        result = field.to_db_value("https://example.com/path", None)
        assert result == "https://example.com/path"

    def test_missing_scheme_raises(self) -> None:
        """URL without a scheme should raise ValueError."""
        field = URLField()
        with pytest.raises(ValueError):
            field.to_db_value("example.com/path", None)

    def test_missing_netloc_raises(self) -> None:
        """URL without a netloc should raise ValueError."""
        field = URLField()
        with pytest.raises(ValueError):
            field.to_db_value("not-a-url", None)

    def test_surrounding_whitespace_raises(self) -> None:
        """URL with leading/trailing whitespace should raise ValueError."""
        field = URLField()
        with pytest.raises(ValueError):
            field.to_db_value(" https://example.com ", None)

    def test_whitespace_in_netloc_raises(self) -> None:
        """URL with whitespace inside the netloc should raise ValueError."""
        field = URLField()
        with pytest.raises(ValueError):
            field.to_db_value("https://exa mple.com", None)

    def test_control_char_in_netloc_raises(self) -> None:
        """URL with a control character in the netloc should raise ValueError."""
        field = URLField()
        with pytest.raises(ValueError):
            field.to_db_value("https://example.com\nx", None)


class TestURLFieldToPythonValue:
    """Verify to_python_value conversions."""

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        field = URLField()
        assert field.to_python_value(None) is None

    def test_string_passthrough(self) -> None:
        """String should be returned unchanged."""
        field = URLField()
        result = field.to_python_value("https://example.com/path")
        assert result == "https://example.com/path"


# ---------------------------------------------------------------------------
# UUID4Field / UUID7Field tests
# ---------------------------------------------------------------------------


class TestUUID4Field:
    """Verify UUID4Field default injection and conversions."""

    def test_default_injected(self) -> None:
        """Default should be uuid4 when none is given."""
        field = UUID4Field()
        assert field.default is uuid.uuid4

    def test_custom_default_respected(self) -> None:
        """Custom default should be respected."""
        custom = uuid.UUID("12345678-1234-5678-1234-567812345678")
        field = UUID4Field(default=custom)
        assert field.default == custom

    def test_null_param(self) -> None:
        """null=True should be stored."""
        field = UUID4Field(null=True)
        assert field.null is True

    def test_to_python_value(self) -> None:
        """String UUID should be converted to a UUID instance."""
        field = UUID4Field()
        result = field.to_python_value("12345678-1234-5678-1234-567812345678")
        assert result == uuid.UUID("12345678-1234-5678-1234-567812345678")

    def test_to_python_value_none(self) -> None:
        """None input should return None."""
        field = UUID4Field()
        assert field.to_python_value(None) is None

    def test_to_db_value(self) -> None:
        """UUID instance should be converted to a string."""
        field = UUID4Field()
        value = uuid.UUID("12345678-1234-5678-1234-567812345678")
        result = field.to_db_value(value, None)
        assert result == "12345678-1234-5678-1234-567812345678"

    def test_postgres_sql_type(self) -> None:
        """PostgreSQL SQL type should be 'UUID'."""
        field = UUID4Field()
        assert field.get_for_dialect("postgres", "SQL_TYPE") == "UUID"

    def test_indexable(self) -> None:
        """UUID4Field should be indexable."""
        assert UUID4Field.indexable is True


class TestUUID7Field:
    """Verify UUID7Field default injection and conversions."""

    def test_default_injected(self) -> None:
        """Default should be uuid7 when none is given."""
        field = UUID7Field()
        assert field.default is uuid.uuid7

    def test_custom_default_respected(self) -> None:
        """Custom default should be respected."""
        custom = uuid.UUID("12345678-1234-5678-1234-567812345678")
        field = UUID7Field(default=custom)
        assert field.default == custom

    def test_null_param(self) -> None:
        """null=True should be stored."""
        field = UUID7Field(null=True)
        assert field.null is True

    def test_to_python_value(self) -> None:
        """String UUID should be converted to a UUID instance."""
        field = UUID7Field()
        result = field.to_python_value("12345678-1234-5678-1234-567812345678")
        assert result == uuid.UUID("12345678-1234-5678-1234-567812345678")

    def test_to_python_value_none(self) -> None:
        """None input should return None."""
        field = UUID7Field()
        assert field.to_python_value(None) is None

    def test_to_db_value(self) -> None:
        """UUID instance should be converted to a string."""
        field = UUID7Field()
        value = uuid.UUID("12345678-1234-5678-1234-567812345678")
        result = field.to_db_value(value, None)
        assert result == "12345678-1234-5678-1234-567812345678"

    def test_postgres_sql_type(self) -> None:
        """PostgreSQL SQL type should be 'UUID'."""
        field = UUID7Field()
        assert field.get_for_dialect("postgres", "SQL_TYPE") == "UUID"

    def test_indexable(self) -> None:
        """UUID7Field should be indexable."""
        assert UUID7Field.indexable is True
