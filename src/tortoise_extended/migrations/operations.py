"""Custom migration operations for TimescaleDB.

These operations are used in migration files to create hypertables
and continuous aggregates.
"""

from typing import TYPE_CHECKING

from tortoise.migrations.operations import Operation
from tortoise.migrations.writer import MigrationWriter

if TYPE_CHECKING:
    from tortoise.migrations.schema_editor.base import BaseSchemaEditor
    from tortoise.migrations.schema_generator.state import State
    from tortoise.migrations.writer import ImportManager


def _patch_format_operation() -> None:
    """Patch MigrationWriter to serialize custom operations generically."""
    _original = MigrationWriter._format_operation

    def _patched(
        self: MigrationWriter,
        operation: Operation,
        imports: ImportManager,
        *,
        indent: str,
    ) -> list[str]:
        try:
            return _original(self, operation, imports, indent=indent)
        except ValueError:
            pass

        if not hasattr(operation, "deconstruct"):
            raise TypeError(f"Operation {type(operation).__name__} has no deconstruct method")

        class_name, args, kwargs = operation.deconstruct()
        imports.add_from("tortoise_extended.migrations.operations", class_name)

        parts: list[str] = [repr(a) for a in args]
        parts += [f"{k}={v!r}" for k, v in kwargs.items()]
        joined = ", ".join(parts)

        return [f"{indent}{class_name}({joined}),"]

    MigrationWriter._format_operation = _patched  # type: ignore[method-assign]


_patch_format_operation()


class CreateHypertable(Operation):
    """Convert a regular table to a TimescaleDB hypertable.

    :param table_name: Name of the table to convert.
    :param time_column: Name of the time column for partitioning.
    :param chunk_time_interval: Interval for chunk creation (e.g., '7 days').
    :param migrate_data: Whether to migrate existing data (default: True).
    """

    def __init__(
        self,
        table_name: str,
        time_column: str = "created_at",
        chunk_time_interval: str = "7 days",
        migrate_data: bool = True,
    ) -> None:
        self.table_name = table_name
        self.time_column = time_column
        self.chunk_time_interval = chunk_time_interval
        self.migrate_data = migrate_data

    def describe(self) -> str:
        return (
            f"CreateHypertable(table_name={self.table_name!r}, "
            f"time_column={self.time_column!r}, "
            f"chunk_time_interval={self.chunk_time_interval!r})"
        )

    def deconstruct(self) -> tuple[str, tuple[()], dict[str, str | bool]]:
        return (
            "CreateHypertable",
            (),
            {
                "table_name": self.table_name,
                "time_column": self.time_column,
                "chunk_time_interval": self.chunk_time_interval,
                "migrate_data": self.migrate_data,
            },
        )

    async def run(
        self,
        app_label: str,
        state: State,
        dry_run: bool,
        state_editor: BaseSchemaEditor | None = None,
    ) -> None:
        if dry_run or state_editor is None:
            return

        sql = (
            f"SELECT create_hypertable('{self.table_name}', '{self.time_column}', "
            f"if_not_exists => TRUE, migrate_data => {str(self.migrate_data).upper()})"
        )
        await state_editor._run_sql(sql)

        if self.chunk_time_interval != "7 days":
            interval_sql = (
                f"ALTER TABLE {self.table_name} "
                f"SET (timescaledb.chunk_time_interval = '{self.chunk_time_interval}')"
            )
            await state_editor._run_sql(interval_sql)

    def state_forward(self, app_label: str, state: State) -> None:
        pass

    async def database_forward(
        self,
        app_label: str,
        old_state: State,
        new_state: State,
        state_editor: BaseSchemaEditor | None = None,
    ) -> None:
        await self.run(app_label, new_state, False, state_editor)

    async def database_backward(
        self,
        app_label: str,
        old_state: State,
        new_state: State,
        state_editor: BaseSchemaEditor | None = None,
    ) -> None:
        if state_editor is None:
            return
        sql = (
            f"SELECT convert_from_hypertable('{self.table_name}', if_exists => TRUE)"
        )
        await state_editor._run_sql(sql)


class CreateContinuousAggregate(Operation):
    """Create a TimescaleDB continuous aggregate view.

    :param view_name: Name of the materialized view.
    :param query: The SELECT query for the aggregate.
    :param time_column: Name of the time bucket column.
    :param refresh_interval: How often to refresh (e.g., '1 hour').
    """

    def __init__(
        self,
        view_name: str,
        query: str,
        time_column: str = "time_bucket",
        refresh_interval: str = "1 hour",
    ) -> None:
        self.view_name = view_name
        self.query = query
        self.time_column = time_column
        self.refresh_interval = refresh_interval

    def describe(self) -> str:
        return (
            f"CreateContinuousAggregate(view_name={self.view_name!r}, "
            f"refresh_interval={self.refresh_interval!r})"
        )

    def deconstruct(self) -> tuple[str, tuple[()], dict[str, str]]:
        return (
            "CreateContinuousAggregate",
            (),
            {
                "view_name": self.view_name,
                "query": self.query,
                "time_column": self.time_column,
                "refresh_interval": self.refresh_interval,
            },
        )

    async def run(
        self,
        app_label: str,
        state: State,
        dry_run: bool,
        state_editor: BaseSchemaEditor | None = None,
    ) -> None:
        if dry_run or state_editor is None:
            return

        # Create the continuous aggregate view
        create_sql = (
            f"CREATE MATERIALIZED VIEW IF NOT EXISTS {self.view_name} "
            f"WITH (timescaledb.continuous) AS {self.query}"
        )
        await state_editor._run_sql(create_sql)

        # Add refresh policy
        refresh_sql = (
            f"SELECT add_continuous_aggregate_policy('{self.view_name}', "
            f"start_offset => INTERVAL '1 hour', "
            f"end_offset => INTERVAL '0', "
            f"schedule_interval => INTERVAL '{self.refresh_interval}')"
        )
        await state_editor._run_sql(refresh_sql)

    def state_forward(self, app_label: str, state: State) -> None:
        pass

    async def database_forward(
        self,
        app_label: str,
        old_state: State,
        new_state: State,
        state_editor: BaseSchemaEditor | None = None,
    ) -> None:
        await self.run(app_label, new_state, False, state_editor)

    async def database_backward(
        self,
        app_label: str,
        old_state: State,
        new_state: State,
        state_editor: BaseSchemaEditor | None = None,
    ) -> None:
        if state_editor is None:
            return
        sql = f"DROP MATERIALIZED VIEW IF EXISTS {self.view_name}"
        await state_editor._run_sql(sql)
