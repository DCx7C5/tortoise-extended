"""Custom migration operations for TimescaleDB.

These operations are used in migration files to create hypertables
and continuous aggregates.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast, override

from tortoise.migrations.operations import Operation
from tortoise.migrations.writer import MigrationWriter
from tortoise_extended._quote import quote_ident as _quote_ident
from tortoise_extended._quote import quote_literal as _quote_literal
from tortoise_extended._types import SchemaEditorLike
from tortoise_extended.exceptions import MigrationOperationError

if TYPE_CHECKING:
    from tortoise.migrations.schema_editor.base import BaseSchemaEditor
    from tortoise.migrations.schema_generator.state import State
    from tortoise.migrations.writer import ImportManager


async def _run_sql(editor: SchemaEditorLike, sql: str) -> None:
    """Execute DDL on a schema editor.

    Uses ``getattr`` so ``reportPrivateUsage`` is not triggered: pyright
    checks protected-member access against the declaring class, and casting
    to a Protocol does not change that.
    """
    run_sql = cast(Callable[..., Awaitable[None]], getattr(editor, "_run_sql"))
    await run_sql(sql)


def _patch_format_operation() -> None:
    """Patch MigrationWriter to serialize custom operations generically."""
    if getattr(MigrationWriter, "_tortoise_extended_format_patched", False):
        return
    _original = cast(
        Callable[..., list[str]], getattr(MigrationWriter, "_format_operation")
    )

    def _patched(
        self: MigrationWriter,
        operation: Operation,
        imports: ImportManager,
        *,
        indent: str,
    ) -> list[str]:
        try:
            return _original(self, operation, imports, indent=indent)
        except ValueError as exc:
            # Only the stock writer's terminal "unsupported operation type"
            # ValueError routes to the generic deconstruct-based serializer.
            # Any other ValueError (e.g. from a field/index deconstruct or a
            # render helper) is a real error and must propagate unmasked.
            if "Unsupported operation type" not in str(exc):
                raise

        if not hasattr(operation, "deconstruct"):
            raise MigrationOperationError(
                f"Operation {type(operation).__name__} has no deconstruct method"
            )

        # ``Operation`` does not statically declare ``deconstruct``, and a
        # cast to a deconstruct protocol would require the ``object`` bridge
        # pyright rejects (the union member ``Operation`` never overlaps the
        # protocol).  Dispatch through ``getattr`` — the ``hasattr`` guard
        # above guarantees the method exists at runtime.
        dc_method = cast(
            Callable[
                [], tuple[str, tuple[()], dict[str, str | int | float | bool | None]]
            ],
            getattr(operation, "deconstruct"),
        )
        class_name, args, kwargs = dc_method()
        imports.add_from("tortoise_extended.migrations.operations", class_name)

        parts: list[str] = [repr(a) for a in args]
        parts += [f"{k}={v!r}" for k, v in kwargs.items()]
        joined = ", ".join(parts)

        return [f"{indent}{class_name}({joined}),"]

    setattr(MigrationWriter, "_format_operation", _patched)
    setattr(MigrationWriter, "_tortoise_extended_format_patched", True)


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

    @override
    def describe(self) -> str:
        """Return a human-readable description of the operation.

        :returns: Description string with the table and time-column config.
        """
        return (
            f"CreateHypertable(table_name={self.table_name!r}, "
            f"time_column={self.time_column!r}, "
            f"chunk_time_interval={self.chunk_time_interval!r})"
        )

    def deconstruct(self) -> tuple[str, tuple[()], dict[str, str | bool]]:
        """Serialize the operation for migration files.

        :returns: ``(class_name, args, kwargs)`` tuple used by the migration
            writer's generic serializer.
        """
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

    @override
    async def run(
        self,
        app_label: str,
        state: State,
        dry_run: bool,
        state_editor: BaseSchemaEditor | None = None,
    ) -> None:
        """Execute the hypertable conversion on the live database.

        Calls ``create_hypertable`` and, when a non-default chunk interval is
        configured, adjusts ``timescaledb.chunk_time_interval`` afterwards.

        :param app_label: App the migration belongs to (unused).
        :param state: Current model state (unused).
        :param dry_run: Skip execution when ``True``.
        :param state_editor: Active schema editor for DDL execution.
        """
        if dry_run or state_editor is None:
            return

        sql = (
            f"SELECT create_hypertable({_quote_literal(self.table_name)}, "
            f"{_quote_literal(self.time_column)}, "
            f"if_not_exists => TRUE, migrate_data => {str(self.migrate_data).upper()})"
        )
        await _run_sql(state_editor, sql)

        if self.chunk_time_interval != "7 days":
            interval_sql = (
                f"ALTER TABLE {_quote_ident(self.table_name)} "
                f"SET (timescaledb.chunk_time_interval = "
                f"{_quote_literal(self.chunk_time_interval)})"
            )
            await _run_sql(state_editor, interval_sql)

    @override
    def state_forward(self, app_label: str, state: State) -> None:
        """Update the model state for the forward direction.

        The hypertable conversion does not change the model schema, so the
        state is left untouched.

        :param app_label: App the migration belongs to (unused).
        :param state: Current model state (unused).
        """

    @override
    async def database_forward(
        self,
        app_label: str,
        old_state: State,
        new_state: State,
        state_editor: BaseSchemaEditor | None = None,
    ) -> None:
        """Apply the hypertable conversion during a forward migration.

        :param app_label: App the migration belongs to (unused).
        :param old_state: State before the migration (unused).
        :param new_state: State after the migration (unused).
        :param state_editor: Active schema editor for DDL execution.
        """
        await self.run(app_label, new_state, False, state_editor)

    @override
    async def database_backward(
        self,
        app_label: str,
        old_state: State,
        new_state: State,
        state_editor: BaseSchemaEditor | None = None,
    ) -> None:
        """Reverse the conversion by removing the hypertable.

        ``remove_hypertable`` keeps the underlying table and data intact.

        :param app_label: App the migration belongs to (unused).
        :param old_state: State before the migration (unused).
        :param new_state: State after the migration (unused).
        :param state_editor: Active schema editor for DDL execution.
        """
        if state_editor is None:
            return
        sql = (
            f"SELECT remove_hypertable("
            f"{_quote_literal(self.table_name)}, if_exists => TRUE)"
        )
        await _run_sql(state_editor, sql)


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

    @override
    def describe(self) -> str:
        """Return a human-readable description of the operation.

        :returns: Description string with the view and refresh config.
        """
        return (
            f"CreateContinuousAggregate(view_name={self.view_name!r}, "
            f"refresh_interval={self.refresh_interval!r})"
        )

    def deconstruct(self) -> tuple[str, tuple[()], dict[str, str]]:
        """Serialize the operation for migration files.

        :returns: ``(class_name, args, kwargs)`` tuple used by the migration
            writer's generic serializer.
        """
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

    @override
    async def run(
        self,
        app_label: str,
        state: State,
        dry_run: bool,
        state_editor: BaseSchemaEditor | None = None,
    ) -> None:
        """Create the continuous aggregate view and its refresh policy.

        Creates the materialized view with ``timescaledb.continuous`` and
        registers an ``add_continuous_aggregate_policy`` refresh schedule.

        :param app_label: App the migration belongs to (unused).
        :param state: Current model state (unused).
        :param dry_run: Skip execution when ``True``.
        :param state_editor: Active schema editor for DDL execution.
        """
        if dry_run or state_editor is None:
            return

        # Create the continuous aggregate view
        create_sql = (
            f"CREATE MATERIALIZED VIEW IF NOT EXISTS {_quote_ident(self.view_name)} "
            f"WITH (timescaledb.continuous) AS {self.query}"
        )
        await _run_sql(state_editor, create_sql)

        # Add refresh policy
        refresh_sql = (
            f"SELECT add_continuous_aggregate_policy("
            f"{_quote_literal(self.view_name)}, "
            f"start_offset => INTERVAL '1 hour', "
            f"end_offset => INTERVAL '0', "
            f"schedule_interval => INTERVAL {_quote_literal(self.refresh_interval)})"
        )
        await _run_sql(state_editor, refresh_sql)

    @override
    def state_forward(self, app_label: str, state: State) -> None:
        """Update the model state for the forward direction.

        The view creation does not change the model schema, so the state is
        left untouched.

        :param app_label: App the migration belongs to (unused).
        :param state: Current model state (unused).
        """

    @override
    async def database_forward(
        self,
        app_label: str,
        old_state: State,
        new_state: State,
        state_editor: BaseSchemaEditor | None = None,
    ) -> None:
        """Create the continuous aggregate during a forward migration.

        :param app_label: App the migration belongs to (unused).
        :param old_state: State before the migration (unused).
        :param new_state: State after the migration (unused).
        :param state_editor: Active schema editor for DDL execution.
        """
        await self.run(app_label, new_state, False, state_editor)

    @override
    async def database_backward(
        self,
        app_label: str,
        old_state: State,
        new_state: State,
        state_editor: BaseSchemaEditor | None = None,
    ) -> None:
        """Reverse the operation by dropping the materialized view.

        :param app_label: App the migration belongs to (unused).
        :param old_state: State before the migration (unused).
        :param new_state: State after the migration (unused).
        :param state_editor: Active schema editor for DDL execution.
        """
        if state_editor is None:
            return
        sql = f"DROP MATERIALIZED VIEW IF EXISTS {_quote_ident(self.view_name)}"
        await _run_sql(state_editor, sql)
