"""Tests for VectorField."""

import struct

from tortoise_extended.fields.vector import VectorField


class TestVectorField:
    def test_sql_type(self) -> None:
        field = VectorField(dimensions=1536)
        assert field.SQL_TYPE == "vector"

    def test_postgres_sql_type(self) -> None:
        field = VectorField(dimensions=1536)
        # _db_postgres.SQL_TYPE is a property — accessed via get_for_dialect
        sql_type = field.get_for_dialect("postgres", "SQL_TYPE")
        assert sql_type == "vector(1536)"

    def test_postgres_sql_type_no_dimensions(self) -> None:
        field = VectorField()
        sql_type = field.get_for_dialect("postgres", "SQL_TYPE")
        assert sql_type == "vector"

    def test_to_db_value_list(self) -> None:
        field = VectorField(dimensions=3)
        result = field.to_db_value([0.1, 0.2, 0.3], None)
        assert result == [0.1, 0.2, 0.3]

    def test_halfvec_sql_type(self) -> None:
        field = VectorField(dimensions=1536, vector_type="halfvec")
        assert field.get_for_dialect("postgres", "SQL_TYPE") == "halfvec(1536)"

    def test_halfvec_sql_type_no_dimensions(self) -> None:
        field = VectorField(vector_type="halfvec")
        assert field.get_for_dialect("postgres", "SQL_TYPE") == "halfvec"

    def test_halfvec_binary_decode(self) -> None:
        header = struct.pack(">HH", 0, 3)
        data = struct.pack(">3e", 1.0, 0.5, -2.0)  # half-precision floats
        result = VectorField._decode_binary(header + data, vector_type="halfvec")
        assert result == [1.0, 0.5, -2.0]

    def test_halfvec_to_python_value_memoryview(self) -> None:
        field = VectorField(dimensions=2, vector_type="halfvec")
        header = struct.pack(">HH", 0, 2)
        data = struct.pack(">2e", 1.0, 2.0)
        result = field.to_python_value(memoryview(header + data))
        assert result == [1.0, 2.0]

    def test_halfvec_repr(self) -> None:
        field = VectorField(dimensions=1536, vector_type="halfvec")
        assert repr(field) == "VectorField(dimensions=1536, vector_type='halfvec')"

    def test_to_db_value_none(self) -> None:
        field = VectorField(dimensions=3)
        result = field.to_db_value(None, None)
        assert result is None

    def test_to_python_value_list(self) -> None:
        field = VectorField(dimensions=3)
        result = field.to_python_value([0.1, 0.2, 0.3])
        assert result == [0.1, 0.2, 0.3]

    def test_to_python_value_string(self) -> None:
        field = VectorField(dimensions=3)
        result = field.to_python_value("[0.1,0.2,0.3]")
        assert result == [0.1, 0.2, 0.3]

    def test_to_python_value_none(self) -> None:
        field = VectorField(dimensions=3)
        result = field.to_python_value(None)
        assert result is None

    def test_to_python_value_memoryview(self) -> None:
        field = VectorField(dimensions=2)
        # 4-byte header: 2 reserved + 2 ndim, then 2 * 4-byte floats
        header = struct.pack(">HH", 0, 2)  # reserved=0, ndim=2
        data = struct.pack(">2f", 1.0, 2.0)
        result = field.to_python_value(memoryview(header + data))
        assert result == [1.0, 2.0]

    def test_decode_binary(self) -> None:
        header = struct.pack(">HH", 0, 3)
        data = struct.pack(">3f", 0.1, 0.2, 0.3)
        result = VectorField._decode_binary(header + data)
        assert len(result) == 3
        assert abs(result[0] - 0.1) < 1e-6
        assert abs(result[1] - 0.2) < 1e-6
        assert abs(result[2] - 0.3) < 1e-6

    def test_decode_binary_empty(self) -> None:
        result = VectorField._decode_binary(b"")
        assert result == []

    def test_repr(self) -> None:
        field = VectorField(dimensions=1536)
        assert repr(field) == "VectorField(dimensions=1536, vector_type='vector')"
