"""Tests for VectorField."""

import struct

import pytest

from tortoise import fields, models

from tortoise_extended.exceptions import VectorFieldError
from tortoise_extended.fields.vector import VectorField


class VectorChunk(models.Model):
    """SQLite round-trip model with a BLOB-backed vector column."""

    id = fields.IntField(primary_key=True)
    embedding = VectorField(dimensions=3)

    class Meta:
        table = "vector_chunks"


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

    def test_decode_binary_truncated_raises(self) -> None:
        """Header declaring more elements than the payload must raise."""
        header = struct.pack(">HH", 0, 3)
        data = struct.pack(">2f", 0.1, 0.2)  # only 2 of the declared 3 floats
        with pytest.raises(VectorFieldError, match="Truncated vector binary"):
            VectorField._decode_binary(header + data)

    def test_decode_binary_truncated_halfvec_raises(self) -> None:
        header = struct.pack(">HH", 0, 4)
        data = struct.pack(">3e", 1.0, 0.5, -2.0)  # only 3 of the declared 4 halves
        with pytest.raises(VectorFieldError, match="Truncated vector binary"):
            VectorField._decode_binary(header + data, vector_type="halfvec")

    def test_encode_binary_round_trip_vector(self) -> None:
        values = [0.1, 0.2, 0.3]
        encoded = VectorField._encode_binary(values)
        assert isinstance(encoded, bytes)
        decoded = VectorField._decode_binary(encoded)
        assert decoded == pytest.approx(values, abs=1e-6)

    def test_encode_binary_round_trip_halfvec(self) -> None:
        values = [1.0, 0.5, -2.0]
        encoded = VectorField._encode_binary(values, vector_type="halfvec")
        assert isinstance(encoded, bytes)
        assert VectorField._decode_binary(encoded, vector_type="halfvec") == values

    def test_to_db_value_returns_list_unbound(self) -> None:
        """Without a bound model, to_db_value keeps the PostgreSQL list form."""
        field = VectorField(dimensions=3)
        result = field.to_db_value([0.1, 0.2, 0.3], None)
        assert result == [0.1, 0.2, 0.3]

    def test_repr(self) -> None:
        field = VectorField(dimensions=1536)
        assert repr(field) == "VectorField(dimensions=1536, vector_type='vector')"


class TestVectorFieldSQLiteRoundTrip:
    """SQLite BLOB save/load round-trip through a real Tortoise model."""

    async def test_sqlite_save_load_round_trip(self, tmp_path) -> None:
        from tortoise import Tortoise

        db_file = tmp_path / "vectors.db"
        await Tortoise.init(
            db_url=f"sqlite://{db_file}",
            modules={"models": ["tests.test_vector_field"]},
        )
        await Tortoise.generate_schemas()
        try:
            chunk = await VectorChunk.create(embedding=[0.1, 0.2, 0.3])
            fetched = await VectorChunk.get(id=chunk.id)
            # BLOB stores float32 — compare with tolerance for rounding.
            assert fetched.embedding == pytest.approx([0.1, 0.2, 0.3], abs=1e-6)

            # Bound to a SQLite model, to_db_value emits bindable BLOB bytes.
            bound_field = VectorChunk._meta.fields_map["embedding"]
            bound_value = bound_field.to_db_value([0.9, 0.8, 0.7], None)
            assert isinstance(bound_value, bytes)
            assert VectorField._decode_binary(bound_value) == pytest.approx(
                [0.9, 0.8, 0.7], abs=1e-6
            )

            # Update path also round-trips through the binary encoder.
            chunk.embedding = [0.4, 0.5, 0.6]
            await chunk.save(update_fields=["embedding"])
            updated = await VectorChunk.get(id=chunk.id)
            assert updated.embedding == pytest.approx([0.4, 0.5, 0.6], abs=1e-6)
        finally:
            await Tortoise.close_connections()
