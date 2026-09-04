"""Unit tests for UUID4Field and UUID7Field.

Covers SQL type inheritance, default injection (uuid4 / uuid7), explicit
default override, null handling, primary-key mode, and time-ordered UUID7
monotonicity. No database connection required.
"""

import time
import uuid

from tortoise_extended.fields.uuid import UUID4Field, UUID7Field


# ---------------------------------------------------------------------------
# UUID4Field
# ---------------------------------------------------------------------------


class TestUUID4FieldSQLType:
    """Verify UUID4Field inherits tortoise's UUIDField SQL types."""

    def test_sql_type(self) -> None:
        """Default SQL_TYPE (SQLite/CHAR fallback) should be CHAR(36)."""
        assert UUID4Field.SQL_TYPE == "CHAR(36)"

    def test_db_postgres_sql_type(self) -> None:
        """PostgreSQL-specific type should be UUID."""
        assert UUID4Field._db_postgres.SQL_TYPE == "UUID"


class TestUUID4FieldDefaultInjection:
    """UUID4Field must inject uuid4 when no explicit default is given."""

    def test_default_is_uuid4(self) -> None:
        """Default should be uuid.uuid4 (the function, not a value)."""
        field = UUID4Field()
        assert field.default is uuid.uuid4

    def test_explicit_default_preserved(self) -> None:
        """A user-supplied default must not be overridden."""
        sentinel = uuid.UUID("00000000-0000-0000-0000-000000000001")
        field = UUID4Field(default=sentinel)
        assert field.default == sentinel

    def test_callable_default_preserved(self) -> None:
        """A user-supplied callable default must not be overridden."""

        def my_gen() -> uuid.UUID:
            return uuid.UUID("00000000-0000-0000-0000-000000000002")

        field = UUID4Field(default=my_gen)
        assert field.default is my_gen

    def test_default_generates_valid_uuid4(self) -> None:
        """Calling the default must produce a valid UUID4 value."""
        field = UUID4Field()
        assert callable(field.default)
        value = field.default()
        assert isinstance(value, uuid.UUID)
        assert value.version == 4

    def test_consecutive_defaults_are_unique(self) -> None:
        """Two calls to the default must produce distinct UUID4s."""
        field = UUID4Field()
        assert callable(field.default)
        values = {field.default() for _ in range(50)}
        assert len(values) == 50


class TestUUID4FieldNullMode:
    """UUID4Field with null=True."""

    def test_null_allowed(self) -> None:
        """null=True should set null on the field."""
        field = UUID4Field(null=True)
        assert field.null is True

    def test_not_null_by_default(self) -> None:
        """Default is not-null."""
        field = UUID4Field()
        assert field.null is False


class TestUUID4FieldPrimaryKey:
    """UUID4Field used as a primary key."""

    def test_pk_mode(self) -> None:
        """primary_key=True should set pk on the field."""
        field = UUID4Field(primary_key=True)
        assert field.pk is True


class TestUUID4FieldValueConversion:
    """UUID4Field value round-trips."""

    def test_to_python_value_passthrough(self) -> None:
        """UUID input should pass through unchanged."""
        field = UUID4Field()
        val = uuid.uuid4()
        assert field.to_python_value(val) is val

    def test_to_python_value_from_string(self) -> None:
        """String input should be converted to UUID."""
        field = UUID4Field()
        val = uuid.uuid4()
        result = field.to_python_value(str(val))
        assert result == val

    def test_to_db_value_returns_string(self) -> None:
        """UUID input should be converted to a string for DB storage."""
        field = UUID4Field()
        val = uuid.uuid4()
        result = field.to_db_value(val, None)
        assert isinstance(result, str)
        assert result == str(val)

    def test_null_passthrough(self) -> None:
        """None input should return None."""
        field = UUID4Field()
        assert field.to_python_value(None) is None


# ---------------------------------------------------------------------------
# UUID7Field
# ---------------------------------------------------------------------------


class TestUUID7FieldSQLType:
    """Verify UUID7Field inherits tortoise's UUIDField SQL types."""

    def test_sql_type(self) -> None:
        """Default SQL_TYPE (SQLite/CHAR fallback) should be CHAR(36)."""
        assert UUID7Field.SQL_TYPE == "CHAR(36)"

    def test_db_postgres_sql_type(self) -> None:
        """PostgreSQL-specific type should be UUID."""
        assert UUID7Field._db_postgres.SQL_TYPE == "UUID"


class TestUUID7FieldDefaultInjection:
    """UUID7Field must inject uuid7 when no explicit default is given."""

    def test_default_is_uuid7(self) -> None:
        """Default should be uuid.uuid7 (the function, not a value)."""
        field = UUID7Field()
        assert field.default is uuid.uuid7

    def test_explicit_default_preserved(self) -> None:
        """A user-supplied default must not be overridden."""
        sentinel = uuid.UUID("00000000-0000-0000-0000-000000000001")
        field = UUID7Field(default=sentinel)
        assert field.default == sentinel

    def test_default_generates_valid_uuid7(self) -> None:
        """Calling the default must produce a valid UUID7 value."""
        field = UUID7Field()
        assert callable(field.default)
        value = field.default()
        assert isinstance(value, uuid.UUID)
        assert value.version == 7

    def test_uuid7_is_time_ordered(self) -> None:
        """Consecutive UUID7 values must be monotonically increasing."""
        field = UUID7Field()
        assert callable(field.default)
        a = field.default()
        b = field.default()
        assert b > a

    def test_uuid7_timestamp_encodes_recent_time(self) -> None:
        """UUID7 timestamp must be within a few seconds of now.

        UUID7 embeds the Unix timestamp in the top 48 bits of the hex
        representation (``hex[:12]``).
        """
        field = UUID7Field()
        assert callable(field.default)
        value = field.default()
        unix_ts_ms = int(value.hex[:12], 16)
        now_ms = int(time.time() * 1000)
        assert abs(now_ms - unix_ts_ms) < 5_000  # within 5 seconds


class TestUUID7FieldNullMode:
    """UUID7Field with null=True."""

    def test_null_allowed(self) -> None:
        """null=True should set null on the field."""
        field = UUID7Field(null=True)
        assert field.null is True

    def test_not_null_by_default(self) -> None:
        """Default is not-null."""
        field = UUID7Field()
        assert field.null is False


class TestUUID7FieldPrimaryKey:
    """UUID7Field used as a primary key."""

    def test_pk_mode(self) -> None:
        """primary_key=True should set pk on the field."""
        field = UUID7Field(primary_key=True)
        assert field.pk is True


class TestUUID7FieldValueConversion:
    """UUID7Field value round-trips."""

    def test_to_python_value_passthrough(self) -> None:
        """UUID input should pass through unchanged."""
        field = UUID7Field()
        val = uuid.uuid7()
        assert field.to_python_value(val) is val

    def test_to_db_value_returns_string(self) -> None:
        """UUID input should be converted to a string for DB storage."""
        field = UUID7Field()
        val = uuid.uuid7()
        result = field.to_db_value(val, None)
        assert isinstance(result, str)
        assert result == str(val)

    def test_null_passthrough(self) -> None:
        """None input should return None."""
        field = UUID7Field()
        assert field.to_python_value(None) is None
