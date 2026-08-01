"""Comprehensive tests for migration operations: CreateHypertable and CreateContinuousAggregate.

Covers describe(), deconstruct() roundtrip, default params, custom params,
and state_forward no-op. No database connection required.
"""

import pytest
from tortoise.migrations.writer import ImportManager, MigrationWriter

from tortoise_extended.exceptions import MigrationOperationError
from tortoise_extended.migrations.operations import (
    CreateContinuousAggregate,
    CreateHypertable,
    _quote_ident,
    _quote_literal,
    _run_sql,
)


class FakeSchemaEditor:
    """Records every ``_run_sql`` call."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def _run_sql(self, sql: str) -> None:
        self.statements.append(sql)


class FakeState:
    """State stand-in — operations never inspect it."""

    pass


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


# ---------------------------------------------------------------------------
# Execution paths (run / database_forward / database_backward)
# ---------------------------------------------------------------------------


class TestRunSql:
    """_run_sql() forwards DDL to the schema editor."""

    @pytest.mark.asyncio
    async def test_run_sql_executes(self) -> None:
        editor = FakeSchemaEditor()
        await _run_sql(editor, "SELECT 1")
        assert editor.statements == ["SELECT 1"]


class TestCreateHypertableRun:
    """CreateHypertable.run() SQL generation branches."""

    @pytest.mark.asyncio
    async def test_run_dry_run_noop(self) -> None:
        op = CreateHypertable(table_name="events")
        editor = FakeSchemaEditor()
        await op.run("app", FakeState(), dry_run=True, state_editor=editor)
        assert editor.statements == []

    @pytest.mark.asyncio
    async def test_run_no_editor_noop(self) -> None:
        op = CreateHypertable(table_name="events")
        await op.run("app", FakeState(), dry_run=False, state_editor=None)
        assert True  # no exception, no SQL

    @pytest.mark.asyncio
    async def test_run_default_interval_single_statement(self) -> None:
        op = CreateHypertable(table_name="events")
        editor = FakeSchemaEditor()
        await op.run("app", FakeState(), dry_run=False, state_editor=editor)
        assert len(editor.statements) == 1
        assert "create_hypertable" in editor.statements[0]
        assert "'events'" in editor.statements[0]
        assert "migrate_data => TRUE" in editor.statements[0]
        assert "timescaledb.chunk_time_interval" not in editor.statements[0]

    @pytest.mark.asyncio
    async def test_run_custom_interval_adds_alter(self) -> None:
        op = CreateHypertable(
            table_name="events", chunk_time_interval="1 day", migrate_data=False
        )
        editor = FakeSchemaEditor()
        await op.run("app", FakeState(), dry_run=False, state_editor=editor)
        assert len(editor.statements) == 2
        assert "migrate_data => FALSE" in editor.statements[0]
        assert "timescaledb.chunk_time_interval" in editor.statements[1]
        assert "'1 day'" in editor.statements[1]

    @pytest.mark.asyncio
    async def test_database_forward(self) -> None:
        op = CreateHypertable(table_name="events")
        editor = FakeSchemaEditor()
        await op.database_forward("app", FakeState(), FakeState(), editor)
        assert len(editor.statements) == 1

    @pytest.mark.asyncio
    async def test_database_backward_no_editor(self) -> None:
        op = CreateHypertable(table_name="events")
        await op.database_backward("app", FakeState(), FakeState(), None)
        assert True  # no-op

    @pytest.mark.asyncio
    async def test_database_backward_removes_hypertable(self) -> None:
        op = CreateHypertable(table_name="events")
        editor = FakeSchemaEditor()
        await op.database_backward("app", FakeState(), FakeState(), editor)
        assert len(editor.statements) == 1
        assert "remove_hypertable" in editor.statements[0]
        assert "if_exists => TRUE" in editor.statements[0]


class TestCreateContinuousAggregateRun:
    """CreateContinuousAggregate.run() SQL generation branches."""

    @pytest.mark.asyncio
    async def test_run_dry_run_noop(self) -> None:
        op = CreateContinuousAggregate(view_name="v", query="SELECT 1")
        editor = FakeSchemaEditor()
        await op.run("app", FakeState(), dry_run=True, state_editor=editor)
        assert editor.statements == []

    @pytest.mark.asyncio
    async def test_run_no_editor_noop(self) -> None:
        op = CreateContinuousAggregate(view_name="v", query="SELECT 1")
        await op.run("app", FakeState(), dry_run=False, state_editor=None)
        assert True

    @pytest.mark.asyncio
    async def test_run_creates_view_and_policy(self) -> None:
        op = CreateContinuousAggregate(
            view_name="hourly", query="SELECT 1", refresh_interval="30 minutes"
        )
        editor = FakeSchemaEditor()
        await op.run("app", FakeState(), dry_run=False, state_editor=editor)
        assert len(editor.statements) == 2
        assert "CREATE MATERIALIZED VIEW IF NOT EXISTS \"hourly\"" in editor.statements[0]
        assert "AS SELECT 1" in editor.statements[0]
        assert "add_continuous_aggregate_policy" in editor.statements[1]
        assert "INTERVAL '30 minutes'" in editor.statements[1]

    @pytest.mark.asyncio
    async def test_database_forward(self) -> None:
        op = CreateContinuousAggregate(view_name="v", query="SELECT 1")
        editor = FakeSchemaEditor()
        await op.database_forward("app", FakeState(), FakeState(), editor)
        assert len(editor.statements) == 2

    @pytest.mark.asyncio
    async def test_database_backward_no_editor(self) -> None:
        op = CreateContinuousAggregate(view_name="v", query="SELECT 1")
        await op.database_backward("app", FakeState(), FakeState(), None)
        assert True

    @pytest.mark.asyncio
    async def test_database_backward_drops_view(self) -> None:
        op = CreateContinuousAggregate(view_name="v", query="SELECT 1")
        editor = FakeSchemaEditor()
        await op.database_backward("app", FakeState(), FakeState(), editor)
        assert editor.statements == ["DROP MATERIALIZED VIEW IF EXISTS \"v\""]


# ---------------------------------------------------------------------------
# MigrationWriter._format_operation patched path
# ---------------------------------------------------------------------------


class NoDeconstructOp:
    """Operation-like object lacking ``deconstruct``."""


class TestFormatOperationPatch:
    """The MigrationWriter patch serializes custom operations generically."""

    @staticmethod
    def _writer() -> MigrationWriter:
        return MigrationWriter("0001_test", "models", [])

    def test_known_operation_passes_through(self) -> None:
        writer = self._writer()
        op = CreateHypertable(table_name="events")
        # A custom op is NOT handled by the original writer, so the patched
        # path must serialize it via deconstruct().
        lines = writer._format_operation(op, ImportManager(), indent="    ")
        assert lines == [
            "    CreateHypertable(table_name='events', "
            "time_column='created_at', chunk_time_interval='7 days', "
            "migrate_data=True),"
        ]

    def test_continuous_aggregate_serialized(self) -> None:
        writer = self._writer()
        op = CreateContinuousAggregate(view_name="v", query="SELECT 1")
        lines = writer._format_operation(op, ImportManager(), indent="    ")
        assert lines == [
            "    CreateContinuousAggregate(view_name='v', query='SELECT 1', "
            "time_column='time_bucket', refresh_interval='1 hour'),"
        ]

    def test_no_deconstruct_raises(self) -> None:
        writer = self._writer()
        with pytest.raises(MigrationOperationError, match="has no deconstruct"):
            writer._format_operation(NoDeconstructOp(), ImportManager(), indent="    ")  # type: ignore[arg-type]
