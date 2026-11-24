"""Tests for VectorField."""

import struct

import pytest

from tortoise_extended.exceptions import VectorFieldError
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
        # Real pgvector layout: int16 ndim, int16 unused (both big-endian).
        header = struct.pack(">HH", 3, 0)
        data = struct.pack(">3e", 1.0, 0.5, -2.0)  # half-precision floats
        result = VectorField._decode_binary(header + data, vector_type="halfvec")
        assert result == [1.0, 0.5, -2.0]

    def test_halfvec_to_python_value_memoryview(self) -> None:
        field = VectorField(dimensions=2, vector_type="halfvec")
        raw = VectorField._encode_binary([1.0, 2.0], vector_type="halfvec")
        result = field.to_python_value(memoryview(raw))
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
        raw = VectorField._encode_binary([1.0, 2.0])
        result = field.to_python_value(memoryview(raw))
        assert result == [1.0, 2.0]

    def test_decode_binary(self) -> None:
        header = struct.pack(">HH", 3, 0)
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
        header = struct.pack(">HH", 3, 0)
        data = struct.pack(">2f", 0.1, 0.2)  # only 2 of the declared 3 floats
        with pytest.raises(VectorFieldError, match="Truncated vector binary"):
            VectorField._decode_binary(header + data)

    def test_decode_binary_truncated_halfvec_raises(self) -> None:
        header = struct.pack(">HH", 4, 0)
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


# ---------------------------------------------------------------------------
# Binary wire layout — pgvector's real vector_send / halfvec_send header
# ---------------------------------------------------------------------------


class TestDecodeBinaryRealLayout:
    """Pin the real pgvector layout: int16 ndim, int16 unused, then elements.

    The payloads are built with ``struct`` directly rather than through
    ``_encode_binary``: a round-trip through the encoder cannot prove the
    layout, because a symmetric mistake in both helpers still round-trips.
    These bytes are the contract ``vector_send`` / ``halfvec_send`` emit.
    """

    def test_vector_payload(self) -> None:
        payload = struct.pack(">HH", 3, 0) + struct.pack(">3f", 1.0, 2.0, 3.0)
        assert VectorField._decode_binary(payload) == [1.0, 2.0, 3.0]

    def test_halfvec_payload(self) -> None:
        payload = struct.pack(">HH", 3, 0) + struct.pack(">3e", 1.0, 2.0, 3.0)
        assert VectorField._decode_binary(payload, vector_type="halfvec") == [
            1.0,
            2.0,
            3.0,
        ]

    def test_unused_header_component_is_ignored(self) -> None:
        """Only the first int16 is the dimension count; the second is unused."""
        payload = struct.pack(">HH", 2, 7) + struct.pack(">2f", 4.0, 5.0)
        assert VectorField._decode_binary(payload) == [4.0, 5.0]

    def test_truncated_real_layout_raises(self) -> None:
        payload = struct.pack(">HH", 3, 0) + struct.pack(">2f", 1.0, 2.0)
        with pytest.raises(VectorFieldError, match="Truncated vector binary"):
            VectorField._decode_binary(payload)


# ---------------------------------------------------------------------------
# Dimension guard — to_db_value rejects length mismatches before any SQL
# ---------------------------------------------------------------------------


class TestToDbValueDimensionGuard:
    """``dimensions`` is enforced on write, not silently accepted."""

    def test_matching_length_returns_list(self) -> None:
        field = VectorField(dimensions=3)
        assert field.to_db_value([0.5, 1.5, 2.5], None) == [0.5, 1.5, 2.5]

    def test_mismatch_raises_naming_expected_and_actual(self) -> None:
        field = VectorField(dimensions=3)
        with pytest.raises(
            VectorFieldError,
            match=r"^VectorField: expected a vector with 3 dimensions, got 2$",
        ):
            field.to_db_value([1.0, 2.0], None)

    def test_mismatch_names_the_bound_field(self) -> None:
        field = VectorField(dimensions=3)
        field.model_field_name = "embedding"
        with pytest.raises(
            VectorFieldError,
            match=r"^embedding: expected a vector with 3 dimensions, got 4$",
        ):
            field.to_db_value([1.0, 2.0, 3.0, 4.0], None)

    def test_empty_vector_against_dimensions_raises(self) -> None:
        field = VectorField(dimensions=3)
        with pytest.raises(VectorFieldError, match=r"got 0$"):
            field.to_db_value([], None)

    def test_unconstrained_field_accepts_any_length(self) -> None:
        field = VectorField()
        assert field.to_db_value([1.0], None) == [1.0]
        assert field.to_db_value([float(i) for i in range(300)], None) == [
            float(i) for i in range(300)
        ]

    def test_none_passes_through(self) -> None:
        assert VectorField(dimensions=3).to_db_value(None, None) is None

    def test_returned_list_is_a_copy(self) -> None:
        field = VectorField(dimensions=3)
        source = [1.0, 2.0, 3.0]
        result = field.to_db_value(source, None)
        assert result is not source
        assert result == source
        result.append(4.0)
        assert source == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------------------
# Text literal parsing — whitespace, empties and malformed input
# ---------------------------------------------------------------------------


class TestStringLiteralParsing:
    """The text codec path must tolerate whitespace and reject garbage clearly."""

    def test_empty_literal(self) -> None:
        assert VectorField(dimensions=3).to_python_value("[]") == []

    def test_whitespace_only_literal(self) -> None:
        assert VectorField(dimensions=3).to_python_value("[ ]") == []

    def test_single_element_literal(self) -> None:
        assert VectorField(dimensions=1).to_python_value("[0.5]") == [0.5]

    def test_padded_literal(self) -> None:
        field = VectorField(dimensions=2)
        assert field.to_python_value(" [ 0.5 , 1.5 ] ") == [0.5, 1.5]

    def test_trailing_comma_tolerated(self) -> None:
        assert VectorField(dimensions=2).to_python_value("[1.0,2.0,]") == [1.0, 2.0]

    def test_malformed_literal_raises_naming_literal(self) -> None:
        field = VectorField(dimensions=2)
        with pytest.raises(VectorFieldError) as excinfo:
            field.to_python_value("[a,b]")
        message = str(excinfo.value)
        assert "'[a,b]'" in message
        assert "'a'" in message
