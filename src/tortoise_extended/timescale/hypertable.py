"""TimescaleDB hypertable manager.

Converts regular PostgreSQL tables to hypertables for time-series data.
Requires: TimescaleDB extension

Usage (illustrative; needs async context + configured DB)::

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

import msgspec
from tortoise import connections
from tortoise.backends.base.client import BaseDBAsyncClient

from tortoise_extended._quote import quote_ident, quote_literal
from tortoise_extended._types import RowMapping, RowValue
from tortoise_extended.exceptions import TimescaleError


class HypertableInfo(msgspec.Struct):
    """One hypertable returned by :meth:`HypertableManager.list_hypertables`.

    Attributes:
        table_name: Schema-qualified hypertable name (``schema.table``).
        num_chunks: Number of chunks currently stored for this hypertable.
        compression_enabled: Whether columnstore/compression is enabled on
            the hypertable.
        table_size: Human-readable total size of the hypertable as reported
            by ``hypertable_size()`` (e.g. ``"8192 kB"``).
    """

    table_name: str
    num_chunks: int
    compression_enabled: bool
    table_size: str


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


def _unquote_ident(part: str) -> str:
    """Strip one level of double quotes from an identifier part.

    Catalog views (``timescaledb_information.jobs``) store bare names, while a
    caller may pass a quoted identifier (``'"a.b"'``) whose dots must not be
    read as qualification.
    """
    if len(part) >= 2 and part.startswith('"') and part.endswith('"'):
        return part[1:-1].replace('""', '"')
    return part


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
        validate_time_column: bool = True,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Convert a regular table to a hypertable.

        Before converting, the partitioning (time) column is validated to be
        part of the table's primary key or a unique index — TimescaleDB's
        hard requirement — and a clear :class:`TimescaleError` is raised when
        it is not, instead of surfacing a cryptic server error from
        ``create_hypertable()``. Set ``validate_time_column=False`` to skip
        the pre-check (e.g. when the schema is unusual and introspection can't
        model it, or to avoid the extra introspection query).

        Args:
            table_name: Name of the table to convert
            time_column: Name of the time column for partitioning
            chunk_time_interval: Interval for chunk creation (e.g., '7 days')
            if_not_exists: Don't error if already a hypertable
            migrate_data: Move existing data to new hypertable chunks
            validate_time_column: Run the cheap PK/unique-index introspection
                pre-check on ``time_column`` before creating (default True).
                If introspection itself fails the error is swallowed and the
                hypertable is still attempted.
            using_db: Database connection to use (default: 'default')

        Raises:
            TimescaleError: If conversion fails, or if ``time_column`` is not
                covered by the table's primary key / a unique index (when
                ``validate_time_column`` is True).

        Example (illustrative; needs async context + configured DB)::

            await HypertableManager.create_hypertable(
                "events",
                time_column="created_at",
                chunk_time_interval="7 days",
            )
        """
        conn = using_db or connections.get("default")

        if validate_time_column:
            try:
                covered = await HypertableManager._time_column_in_unique_index(
                    table_name, time_column, using_db=conn
                )
            except Exception:
                # Introspection itself failed (e.g. an older PG without
                # pg_index it can read) — don't mask the real create; still
                # attempt the hypertable so the original error surfaces.
                covered = True
            if not covered:
                raise TimescaleError(
                    f"Cannot create hypertable on table {table_name!r}: the "
                    f"time/partitioning column {time_column!r} must be part "
                    "of the table's PRIMARY KEY or a UNIQUE index. "
                    "TimescaleDB requires the partitioning column to be "
                    "covered by a primary key or unique constraint; without "
                    "it create_hypertable() fails."
                )

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
    async def _time_column_in_unique_index(
        table_name: str,
        time_column: str,
        using_db: BaseDBAsyncClient | None = None,
    ) -> bool:
        """Return whether ``time_column`` is covered by a PK or unique index.

        TimescaleDB requires the hypertable partitioning (time) column to be
        part of the primary key or a unique index, otherwise
        ``create_hypertable()`` fails. This runs a cheap introspection against
        ``pg_index``/``pg_class`` to pre-empt that error.

        Args:
            table_name: Name of the table (may be schema-qualified).
            time_column: Name of the partitioning column.
            using_db: Database connection to use (default: 'default').

        Returns:
            True if the column is a key column of some unique index (or a
            primary key, which is an implicit unique index), else False.
        """
        conn = using_db or connections.get("default")

        schema, table = _split_schema_name(table_name)
        if schema is None:
            schema_clause = "n.nspname = ANY(current_schemas(false))"
        else:
            schema_clause = f"n.nspname = {quote_literal(schema)}"

        sql = f"""
            SELECT EXISTS(
                SELECT 1
                FROM pg_index i
                JOIN pg_class c ON c.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_attribute a
                  ON a.attrelid = c.oid
                 AND a.attnum = ANY(i.indkey)
                WHERE c.relname = {quote_literal(table)}
                  AND a.attname = {quote_literal(time_column)}
                  AND i.indisunique
                  AND {schema_clause}
            ) AS is_covered
        """

        result = await conn.execute_query(sql)
        rows = cast(
            Sequence[RowMapping | tuple[RowValue, ...]],
            result[1] if isinstance(result, tuple) else result,
        )

        if rows:
            row = rows[0]
            if isinstance(row, dict):
                return bool(row.get("is_covered", False))
            return bool(row[0]) if row else False
        return False

    @staticmethod
    async def drop_hypertable(
        table_name: str,
        if_exists: bool = True,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Drop a hypertable.

        Retention and columnstore (compression) policies are TimescaleDB
        scheduler **jobs**, not table constraints, so a bare
        ``DROP TABLE ... CASCADE`` would leave them registered and they would
        keep failing against a hypertable that no longer exists. Every job
        whose ``timescaledb_information.jobs.hypertable_name`` (plus schema,
        for a schema-qualified name) matches is deleted first; a plain table
        or a policy-less hypertable matches no rows, so the cleanup is a
        no-op for them.

        Args:
            table_name: Name of the hypertable to drop (may be
                schema-qualified, e.g. ``"metrics.events"``)
            if_exists: Don't error if doesn't exist
            using_db: Database connection to use (default: 'default')

        Example (illustrative; needs async context + configured DB)::

            await HypertableManager.drop_hypertable("events")
        """
        conn = using_db or connections.get("default")

        # Policies (retention, columnstore) are scheduler JOBS, not table
        # constraints: `DROP TABLE ... CASCADE` leaves them registered, and the
        # scheduler then keeps failing against a hypertable that no longer
        # exists. Delete them first; a plain table or a policy-less hypertable
        # matches no rows, so this stays a no-op for them.
        schema, bare = _split_schema_name(table_name)
        job_filter = f"hypertable_name = {quote_literal(_unquote_ident(bare))}"
        if schema is not None:
            job_filter += (
                f" AND hypertable_schema = {quote_literal(_unquote_ident(schema))}"
            )

        try:
            _ = await conn.execute_query(
                "SELECT delete_job(job_id) "
                f"FROM timescaledb_information.jobs WHERE {job_filter}"
            )
        except Exception as exc:
            msg = f"Failed to remove policy jobs for hypertable {table_name!r}: {exc}"
            raise TimescaleError(msg) from exc

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

        Example (illustrative; needs async context + configured DB)::

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
    ) -> list[HypertableInfo]:
        """List all hypertables in the database.

        Args:
            extension_schema: Schema the TimescaleDB extension was installed
                into (defaults to ``public``); ``hypertable_size()`` is
                resolved relative to it.
            using_db: Database connection to use (default: 'default')

        Returns:
            List of :class:`HypertableInfo` structs describing each
            hypertable.

        Example (illustrative; needs async context + configured DB)::

            hypertables = await HypertableManager.list_hypertables()
            for ht in hypertables:
                print(f"{ht.table_name}: {ht.num_chunks} chunks")
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

        infos: list[HypertableInfo] = []
        for row in rows:
            if isinstance(row, dict):
                infos.append(
                    HypertableInfo(
                        table_name=cast(str, row["table_name"]),
                        num_chunks=cast(int, row["num_chunks"]),
                        compression_enabled=cast(bool, row["compression_enabled"]),
                        table_size=cast(str, row["table_size"]),
                    )
                )
            else:
                infos.append(
                    HypertableInfo(
                        cast(str, row[0]),
                        cast(int, row[1]),
                        cast(bool, row[2]),
                        cast(str, row[3]),
                    )
                )
        return infos

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

        Example (illustrative; needs async context + configured DB)::

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
            # ``int`` validates as well as coerces: the annotation alone does
            # not stop an untyped caller from passing a string straight into
            # the SQL text. A non-numeric value raises ValueError before any
            # statement runs.
            partitions = int(number_partitions)
            sql = (
                "SELECT add_dimension("
                f"{quote_literal(table_name)}, "
                f"{quote_literal(column_name)}, "
                f"number_partitions => {partitions}"
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

        Example (illustrative; needs async context + configured DB)::

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
            cast(
                object,
                result[1] if isinstance(result, tuple) else result,
            ),
        )

        return [cast(str, row[0]) for row in rows]
