"""Comprehensive tests for migration operations: CreateHypertable and CreateContinuousAggregate.

Covers describe(), deconstruct() roundtrip, default params, custom params,
and state_forward no-op. No database connection required.
"""

from tortoise_extended.migrations.operations import (
    CreateContinuousAggregate,
    CreateHypertable,
    _quote_ident,
    _quote_literal,
)


# ---------------------------------------------------------------------------
# SQL quoting helpers
# ---------------------------------------------------------------------------


class TestSqlQuoting:
    """Tests for SQL quoting helpers used by migration operations."""

    def test_quote_ident(self) -> None:
        """Identifiers should be double-quoted."""
        assert _quote_ident("events") == '"events"'

    def test_quote_ident_escapes_double_quote(self) -> None:
        """Embedded double quotes should be doubled."""
        assert _quote_ident('weird"name') == '"weird""name"'

    def test_quote_literal(self) -> None:
        """Literals should be single-quoted."""
        assert _quote_literal("7 days") == "'7 days'"

    def test_quote_literal_escapes_quote(self) -> None:
        """Embedded single quotes should be doubled."""
        assert _quote_literal("it's") == "'it''s'"


# ---------------------------------------------------------------------------
# CreateHypertable tests
# ---------------------------------------------------------------------------


class TestCreateHypertable:
    """Tests for CreateHypertable migration operation."""

    def test_describe(self) -> None:
        """describe() should include class name and table_name."""
        op = CreateHypertable(table_name="test_table", time_column="created_at")
        desc = op.describe()
        assert "CreateHypertable" in desc
        assert "test_table" in desc

    def test_default_params(self) -> None:
        """Default params should be time_column='created_at', chunk='7 days', migrate=True."""
        op = CreateHypertable(table_name="test_table")
        assert op.time_column == "created_at"
        assert op.chunk_time_interval == "7 days"
        assert op.migrate_data is True

    def test_custom_params(self) -> None:
        """Custom params should be stored."""
        op = CreateHypertable(
            table_name="events",
            time_column="ts",
            chunk_time_interval="1 day",
            migrate_data=False,
        )
        assert op.table_name == "events"
        assert op.time_column == "ts"
        assert op.chunk_time_interval == "1 day"
        assert op.migrate_data is False

    def test_deconstruct(self) -> None:
        """deconstruct() should return correct class_name, args, kwargs."""
        op = CreateHypertable(
            table_name="events", time_column="ts", chunk_time_interval="1 day"
        )
        class_name, args, kwargs = op.deconstruct()
        assert class_name == "CreateHypertable"
        assert args == ()
        assert kwargs["table_name"] == "events"
        assert kwargs["time_column"] == "ts"
        assert kwargs["chunk_time_interval"] == "1 day"
        assert kwargs["migrate_data"] is True

    def test_roundtrip_via_deconstruct(self) -> None:
        """Deconstruct → reconstruct should produce equivalent operation."""
        original = CreateHypertable(
            table_name="logs",
            time_column="recorded_at",
            chunk_time_interval="3 days",
            migrate_data=False,
        )
        _class_name, args, kwargs = original.deconstruct()
        restored = CreateHypertable(*args, **kwargs)
        assert restored.table_name == original.table_name
        assert restored.time_column == original.time_column
        assert restored.chunk_time_interval == original.chunk_time_interval
        assert restored.migrate_data == original.migrate_data

    def test_state_forward_noop(self) -> None:
        """state_forward should be a no-op (returns None)."""
        op = CreateHypertable(table_name="test_table")
        result = op.state_forward("app", None)
        assert result is None


# ---------------------------------------------------------------------------
# CreateContinuousAggregate tests
# ---------------------------------------------------------------------------


class TestCreateContinuousAggregate:
    """Tests for CreateContinuousAggregate migration operation."""

    def test_describe(self) -> None:
        """describe() should include class name and view_name."""
        op = CreateContinuousAggregate(
            view_name="my_view",
            query="SELECT id, sum(amount) as total FROM sales GROUP BY id",
        )
        desc = op.describe()
        assert "CreateContinuousAggregate" in desc
        assert "my_view" in desc

    def test_default_params(self) -> None:
        """Default params should be time_column='time_bucket', refresh='1 hour'."""
        op = CreateContinuousAggregate(view_name="v", query="SELECT 1")
        assert op.time_column == "time_bucket"
        assert op.refresh_interval == "1 hour"

    def test_custom_params(self) -> None:
        """Custom params should be stored."""
        op = CreateContinuousAggregate(
            view_name="hourly",
            query="SELECT 1",
            time_column="bucket",
            refresh_interval="30 minutes",
        )
        assert op.view_name == "hourly"
        assert op.query == "SELECT 1"
        assert op.time_column == "bucket"
        assert op.refresh_interval == "30 minutes"

    def test_deconstruct(self) -> None:
        """deconstruct() should return correct class_name, args, kwargs."""
        op = CreateContinuousAggregate(
            view_name="hourly",
            query="SELECT 1",
            time_column="bucket",
            refresh_interval="30 minutes",
        )
        class_name, args, kwargs = op.deconstruct()
        assert class_name == "CreateContinuousAggregate"
        assert args == ()
        assert kwargs["view_name"] == "hourly"
        assert kwargs["query"] == "SELECT 1"
        assert kwargs["time_column"] == "bucket"
        assert kwargs["refresh_interval"] == "30 minutes"

    def test_roundtrip_via_deconstruct(self) -> None:
        """Deconstruct → reconstruct should produce equivalent operation."""
        original = CreateContinuousAggregate(
            view_name="daily",
            query="SELECT time_bucket('1 day', created_at) AS bucket, COUNT(*) AS cnt FROM events GROUP BY bucket",
            time_column="bucket",
            refresh_interval="6 hours",
        )
        _class_name, args, kwargs = original.deconstruct()
        restored = CreateContinuousAggregate(*args, **kwargs)
        assert restored.view_name == original.view_name
        assert restored.query == original.query
        assert restored.time_column == original.time_column
        assert restored.refresh_interval == original.refresh_interval

    def test_state_forward_noop(self) -> None:
        """state_forward should be a no-op (returns None)."""
        op = CreateContinuousAggregate(view_name="v", query="SELECT 1")
        result = op.state_forward("app", None)
        assert result is None
