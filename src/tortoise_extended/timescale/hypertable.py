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

from tortoise import connections

from tortoise_extended._types import LibraryAny


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
    ) -> None:
        """Convert a regular table to a hypertable.

        Args:
            table_name: Name of the table to convert
            time_column: Name of the time column for partitioning
            chunk_time_interval: Interval for chunk creation (e.g., '7 days')
            if_not_exists: Don't error if already a hypertable
            migrate_data: Move existing data to new hypertable chunks

        Raises:
            Exception: If conversion fails (and if_not_exists=False)

        Example::

            await HypertableManager.create_hypertable(
                "events",
                time_column="created_at",
                chunk_time_interval="7 days",
            )
        """
        conn = connections.get("default")

        sql = f"""
            SELECT create_hypertable(
                '{table_name}',
                '{time_column}',
                chunk_time_interval => INTERVAL '{chunk_time_interval}',
                if_not_exists => {str(if_not_exists).lower()},
                migrate_data => {str(migrate_data).lower()}
            )
        """

        await conn.execute_query(sql)

    @staticmethod
    async def drop_hypertable(
        table_name: str,
        if_exists: bool = True,
    ) -> None:
        """Drop a hypertable.

        Args:
            table_name: Name of the hypertable to drop
            if_exists: Don't error if doesn't exist

        Example::

            await HypertableManager.drop_hypertable("events")
        """
        conn = connections.get("default")

        sql = f"""
            SELECT drop_hypertable(
                '{table_name}',
                if_exists => {str(if_exists).lower()}
            )
        """

        await conn.execute_query(sql)

    @staticmethod
    async def is_hypertable(table_name: str) -> bool:
        """Check if a table is a hypertable.

        Args:
            table_name: Name of the table to check

        Returns:
            True if the table is a hypertable

        Example::

            is_hypertable = await HypertableManager.is_hypertable("events")
            if not is_hypertable:
                await HypertableManager.create_hypertable("events")
        """
        conn = connections.get("default")

        sql = f"""
            SELECT EXISTS(
                SELECT 1 FROM _timescaledb_catalog.hypertable
                WHERE table_name = '{table_name}'
            ) AS is_hypertable
        """

        result = await conn.execute_query(sql)
        rows: list[LibraryAny] = result[1] if isinstance(result, tuple) else result  # pyright: ignore[reportExplicitAny, reportUnknownVariableType]

        if rows:
            row: LibraryAny = rows[0]  # pyright: ignore[reportExplicitAny, reportUnknownVariableType]
            if isinstance(row, dict):
                return row.get("is_hypertable", False)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            # Tuple result
            return bool(row[0]) if row else False  # pyright: ignore[reportUnknownArgumentType]
        return False

    @staticmethod
    async def list_hypertables() -> list[dict[str, LibraryAny]]:  # pyright: ignore[reportExplicitAny]
        """List all hypertables in the database.

        Returns:
            List of dicts with hypertable information

        Example::

            hypertables = await HypertableManager.list_hypertables()
            for ht in hypertables:
                print(f"{ht['table_name']}: {ht['num_chunks']} chunks")
        """
        conn = connections.get("default")

        sql = """
            SELECT
                table_name,
                num_chunks,
                compression_enabled,
                table_size
            FROM hypertable_stats()
            ORDER BY table_name
        """

        result = await conn.execute_query(sql)
        rows: list[LibraryAny] = result[1] if isinstance(result, tuple) else result  # pyright: ignore[reportExplicitAny, reportUnknownVariableType]

        return [dict(row) for row in rows]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]

    @staticmethod
    async def add_dimension(
        table_name: str,
        column_name: str,
        chunk_time_interval: str | None = None,
    ) -> None:
        """Add a dimension to a hypertable.

        Args:
            table_name: Name of the hypertable
            column_name: Name of the column to add as dimension
            chunk_time_interval: Optional interval for time-based dimension

        Example::

            # Add space dimension for multi-tenant data
            await HypertableManager.add_dimension(
                "events",
                "tenant_id",
            )
        """
        conn = connections.get("default")

        if chunk_time_interval:
            sql = f"""
                SELECT add_dimension(
                    '{table_name}',
                    '{column_name}',
                    chunk_time_interval => INTERVAL '{chunk_time_interval}'
                )
            """
        else:
            sql = f"""
                SELECT add_dimension(
                    '{table_name}',
                    '{column_name}'
                )
            """

        await conn.execute_query(sql)

    @staticmethod
    async def show_chunks(
        table_name: str,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> list[str]:
        """Show chunks for a hypertable.

        Args:
            table_name: Name of the hypertable
            start_time: Optional start time filter (e.g., '2024-01-01')
            end_time: Optional end time filter (e.g., '2024-02-01')

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
        conn = connections.get("default")

        if start_time and end_time:
            sql = f"""
                SELECT show_chunks(
                    '{table_name}',
                    TIMESTAMPTZ '{start_time}',
                    TIMESTAMPTZ '{end_time}'
                )
            """
        elif start_time:
            sql = f"""
                SELECT show_chunks(
                    '{table_name}',
                    TIMESTAMPTZ '{start_time}'
                )
            """
        else:
            sql = f"""
                SELECT show_chunks('{table_name}')
            """

        result = await conn.execute_query(sql)
        rows: list[LibraryAny] = result[1] if isinstance(result, tuple) else result  # pyright: ignore[reportExplicitAny, reportUnknownVariableType]

        return [row[0] for row in rows]  # pyright: ignore[reportUnknownVariableType]
