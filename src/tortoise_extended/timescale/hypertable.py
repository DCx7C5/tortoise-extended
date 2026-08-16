"""TimescaleDB hypertable manager.

Converts regular PostgreSQL tables to hypertables for time-series data.
Requires: TimescaleDB extension

Usage::

    from tortoise_extended.timescale import HypertableManager

    # Convert table to hypertable
    await HypertableManager.create_hypertable(
        "events",
        time_column="created_at",
        chunk_time_interval="7 days",
    )

    # Check if table is a hypertable
    is_hypertable = await HypertableManager.is_hypertable("events")
"""

from collections.abc import Sequence
from typing import cast

from tortoise import connections
from tortoise.backends.base.client import BaseDBAsyncClient

from tortoise_extended._quote import quote_ident, quote_literal
from tortoise_extended._types import RowMapping, RowValue
from tortoise_extended.exceptions import TimescaleError


def _split_schema_name(name: str) -> tuple[str | None, str]:
    """Split a possibly schema-qualified identifier into (schema, name).

    The split happens on the **last dot outside double quotes**, so quoted
    identifiers containing dots (``"a.b".t``) split into
    (``'"a.b"'``, ``'t'``) instead of mis-splitting on the embedded dot.
    """
    in_quote = False
    last_unquoted_dot = -1
    for index, char in enumerate(name):
        if char == '"':
            in_quote = not in_quote
        elif char == "." and not in_quote:
            last_unquoted_dot = index
    if last_unquoted_dot != -1:
        return name[:last_unquoted_dot], name[last_unquoted_dot + 1 :]
    return None, name


def _quote_qualified(name: str) -> str:
    """Quote a possibly schema-qualified identifier as ``"schema"."table"``.

    Quoting the whole ``schema.table`` string as one identifier would
    produce the broken ``"schema.table"``.
    """
    schema, table = _split_schema_name(name)
    if schema is None:
        return quote_ident(table)
    return f"{quote_ident(schema)}.{quote_ident(table)}"


class HypertableManager:
    """Manager for TimescaleDB hypertables.

    A hypertable is a table that is automatically partitioned into chunks
    based on time. TimescaleDB extends PostgreSQL with hypertables for
    efficient time-series data storage and querying.
    """

    @staticmethod
    async def create_hypertable(
        table_name: str,
        time_column: str = "created_at",
        chunk_time_interval: str = "7 days",
        if_not_exists: bool = True,
        migrate_data: bool = False,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Convert a regular table to a hypertable.

        Args:
            table_name: Name of the table to convert
            time_column: Name of the time column for partitioning
            chunk_time_interval: Interval for chunk creation (e.g., '7 days')
            if_not_exists: Don't error if already a hypertable
            migrate_data: Move existing data to new hypertable chunks
            using_db: Database connection to use (default: 'default')

        Raises:
            TimescaleError: If conversion fails

        Example::

            await HypertableManager.create_hypertable(
                "events",
                time_column="created_at",
                chunk_time_interval="7 days",
            )
        """
        conn = using_db or connections.get("default")

        sql = (
            "SELECT create_hypertable("
            f"{quote_literal(table_name)}, "
            f"{quote_literal(time_column)}, "
            f"chunk_time_interval => INTERVAL {quote_literal(chunk_time_interval)}, "
            f"if_not_exists => {str(if_not_exists).lower()}, "
            f"migrate_data => {str(migrate_data).lower()}"
            ")"
        )

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to create hypertable {table_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def drop_hypertable(
        table_name: str,
        if_exists: bool = True,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Drop a hypertable.

        Args:
            table_name: Name of the hypertable to drop (may be
                schema-qualified, e.g. ``"metrics.events"``)
            if_exists: Don't error if doesn't exist
            using_db: Database connection to use (default: 'default')

        Example::

            await HypertableManager.drop_hypertable("events")
        """
        conn = using_db or connections.get("default")

        sql = f"""
            DROP TABLE
            {"IF EXISTS" if if_exists else ""}
            {_quote_qualified(table_name)}
            CASCADE
        """

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to drop hypertable {table_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def is_hypertable(
        table_name: str,
        using_db: BaseDBAsyncClient | None = None,
    ) -> bool:
        """Check if a table is a hypertable.

        Args:
            table_name: Name of the table to check (may be schema-qualified,
                e.g. ``"metrics.events"``)
            using_db: Database connection to use (default: 'default')

        Returns:
            True if the table is a hypertable

        Example::

            is_hypertable = await HypertableManager.is_hypertable("events")
            if not is_hypertable:
                await HypertableManager.create_hypertable("events")
        """
        conn = using_db or connections.get("default")

        schema, table = _split_schema_name(table_name)
        if schema is None:
            sql = f"""
                SELECT EXISTS(
                    SELECT 1 FROM timescaledb_information.hypertables
                    WHERE hypertable_name = {quote_literal(table)}
                ) AS is_hypertable
            """
        else:
            # ``timescaledb_information.hypertables`` only exposes
            # hypertable_schema/hypertable_name as separate columns, so a
            # schema-qualified check must match both (an unqualified lookup
            # can false-positive when another schema owns a same-named table).
            sql = f"""
                SELECT EXISTS(
                    SELECT 1 FROM timescaledb_information.hypertables
                    WHERE hypertable_schema = {quote_literal(schema)}
                    AND hypertable_name = {quote_literal(table)}
                ) AS is_hypertable
            """

        try:
            result = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to inspect hypertable {table_name!r}: {exc}"
            raise TimescaleError(msg) from exc
        rows = cast(
            Sequence[RowMapping | tuple[RowValue, ...]],
            result[1] if isinstance(result, tuple) else result,
        )

        if rows:
            row = rows[0]
            if isinstance(row, dict):
                return bool(row.get("is_hypertable", False))
            # Tuple result
            return bool(row[0]) if row else False
        return False

    @staticmethod
    async def list_hypertables(
        extension_schema: str = "public",
        using_db: BaseDBAsyncClient | None = None,
    ) -> list[RowMapping]:
        """List all hypertables in the database.

        Args:
            extension_schema: Schema the TimescaleDB extension was installed
                into (defaults to ``public``); ``hypertable_size()`` is
                resolved relative to it.
            using_db: Database connection to use (default: 'default')

        Returns:
            List of dicts with hypertable information

        Example::

            hypertables = await HypertableManager.list_hypertables()
            for ht in hypertables:
                print(f"{ht['table_name']}: {ht['num_chunks']} chunks")
        """
        conn = using_db or connections.get("default")

        sql = f"""
            SELECT
                (h.hypertable_schema || '.' || h.hypertable_name) AS table_name,
                h.num_chunks,
                h.compression_enabled,
                {quote_ident(extension_schema)}.hypertable_size(
                    (h.hypertable_schema || '.' || h.hypertable_name)::regclass
                ) AS table_size
            FROM timescaledb_information.hypertables h
            ORDER BY h.hypertable_name
        """

        try:
            result = await conn.execute_query(sql)
        except Exception as exc:
            msg = "Failed to list hypertables"
            raise TimescaleError(msg) from exc
        rows = cast(
            Sequence[RowMapping | tuple[RowValue, ...]],
            result[1] if isinstance(result, tuple) else result,
        )

        return [dict(cast(RowMapping, row)) for row in rows]

    @staticmethod
    async def add_dimension(
        table_name: str,
        column_name: str,
        chunk_time_interval: str | None = None,
        number_partitions: int | None = None,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Add a dimension to a hypertable.

        Args:
            table_name: Name of the hypertable
            column_name: Name of the column to add as dimension
            chunk_time_interval: Interval for time-typed dimensions
                (e.g. ``"7 days"``).
            number_partitions: Partition count for space dimensions on
                integer columns. One of ``chunk_time_interval`` or
                ``number_partitions`` is required — TimescaleDB rejects a
                dimension without an explicit interval or partition count.
            using_db: Database connection to use (default: 'default')

        Raises:
            ValueError: If neither ``chunk_time_interval`` nor
                ``number_partitions`` is provided.
            TimescaleError: If the dimension cannot be added.

        Example::

            # Add space dimension for multi-tenant data
            await HypertableManager.add_dimension(
                "events",
                "tenant_id",
                number_partitions=4,
            )
        """
        conn = using_db or connections.get("default")

        if chunk_time_interval:
            sql = (
                "SELECT add_dimension("
                f"{quote_literal(table_name)}, "
                f"{quote_literal(column_name)}, "
                f"chunk_time_interval => INTERVAL {quote_literal(chunk_time_interval)}"
                ")"
            )
        elif number_partitions is not None:
            sql = (
                "SELECT add_dimension("
                f"{quote_literal(table_name)}, "
                f"{quote_literal(column_name)}, "
                f"number_partitions => {number_partitions}"
                ")"
            )
        else:
            raise ValueError(
                "add_dimension requires chunk_time_interval or "
                "number_partitions — TimescaleDB rejects a dimension "
                "without an explicit partition interval or count"
            )

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = (
                f"Failed to add dimension {column_name!r} to hypertable "
                f"{table_name!r}: {exc}"
            )
            raise TimescaleError(msg) from exc

    @staticmethod
    async def show_chunks(
        table_name: str,
        start_time: str | None = None,
        end_time: str | None = None,
        using_db: BaseDBAsyncClient | None = None,
    ) -> list[str]:
        """Show chunks for a hypertable.

        Args:
            table_name: Name of the hypertable
            start_time: Optional start time filter (e.g., '2024-01-01')
            end_time: Optional end time filter (e.g., '2024-02-01')
            using_db: Database connection to use (default: 'default')

        Returns:
            List of chunk names

        Example::

            chunks = await HypertableManager.show_chunks(
                "events",
                start_time="2024-01-01",
                end_time="2024-02-01",
            )
            print(f"Found {len(chunks)} chunks")
        """
        conn = using_db or connections.get("default")

        if start_time and end_time:
            sql = (
                "SELECT show_chunks("
                f"{quote_literal(table_name)}, "
                f"newer_than => TIMESTAMPTZ {quote_literal(start_time)}, "
                f"older_than => TIMESTAMPTZ {quote_literal(end_time)}"
                ")"
            )
        elif start_time:
            sql = (
                "SELECT show_chunks("
                f"{quote_literal(table_name)}, "
                f"newer_than => TIMESTAMPTZ {quote_literal(start_time)}"
                ")"
            )
        else:
            sql = f"SELECT show_chunks({quote_literal(table_name)})"

        try:
            result = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to show chunks for hypertable {table_name!r}: {exc}"
            raise TimescaleError(msg) from exc
        rows = cast(
            Sequence[tuple[RowValue, ...]],
            result[1] if isinstance(result, tuple) else result,
        )

        return [cast(str, row[0]) for row in rows]
