"""TimescaleDB continuous aggregate manager.

Provides continuous aggregates for materialized views that auto-refresh.
Requires: TimescaleDB extension

Usage::

    from tortoise_extended.timescale import ContinuousAggregateManager

    # Create continuous aggregate
    await ContinuousAggregateManager.create(
        "daily_events",
        "events",
        "SELECT time_bucket('1 day', created_at) AS bucket, "
        "COUNT(*) AS count FROM events GROUP BY bucket",
    )

    # Refresh continuous aggregate
    await ContinuousAggregateManager.refresh("daily_events")

    # Set refresh policy
    await ContinuousAggregateManager.set_refresh_policy(
        "daily_events",
        start_offset="1 hour",
        end_offset="1 minute",
        schedule_interval="1 hour",
    )
"""

from tortoise import connections

from tortoise_extended._types import LibraryAny


class ContinuousAggregateManager:
    """Manager for TimescaleDB continuous aggregates.

    Continuous aggregates are materialized views that automatically
    refresh as new data is inserted into the underlying hypertable.
    They're ideal for pre-computing aggregations like hourly/daily stats.
    """

    @staticmethod
    async def create(
        view_name: str,
        _source_table: str,
        query: str,
        with_data: bool = True,
    ) -> None:
        """Create a continuous aggregate view.

        Args:
            view_name: Name for the continuous aggregate
            source_table: Source hypertable name
            query: SQL query for the aggregate (must use time_bucket)
            with_data: Populate with existing data

        Example::

            await ContinuousAggregateManager.create(
                "daily_events",
                "events",
                "SELECT time_bucket('1 day', created_at) AS bucket, "
                "COUNT(*) AS count FROM events GROUP BY bucket",
            )
        """
        conn = connections.get("default")

        # Ensure CREATE CONTINUOUS AGGREGATE syntax
        if "CREATE CONTINUOUS AGGREGATE" not in query.upper():
            query = f"""
                CREATE CONTINUOUS AGGREGATE {view_name}
                AS ({query})
                WITH DATA = {str(with_data).lower()}
            """
        else:
            # User provided full CREATE statement
            pass

        await conn.execute_query(query)

    @staticmethod
    async def drop(view_name: str, if_exists: bool = True) -> None:
        """Drop a continuous aggregate.

        Args:
            view_name: Name of the continuous aggregate to drop
            if_exists: Don't error if doesn't exist

        Example::

            await ContinuousAggregateManager.drop("daily_events")
        """
        conn = connections.get("default")

        sql = f"""
            DROP MATERIALIZED VIEW {view_name}
            {"IF EXISTS" if if_exists else ""}
        """

        await conn.execute_query(sql)

    @staticmethod
    async def refresh(
        view_name: str,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> None:
        """Refresh a continuous aggregate.

        Args:
            view_name: Name of the continuous aggregate
            start_time: Optional start of refresh window
            end_time: Optional end of refresh window

        Example::

            # Refresh entire aggregate
            await ContinuousAggregateManager.refresh("daily_events")

            # Refresh specific time range
            await ContinuousAggregateManager.refresh(
                "daily_events",
                start_time="2024-01-01",
                end_time="2024-01-31",
            )
        """
        conn = connections.get("default")

        if start_time and end_time:
            sql = f"""
                CALL refresh_continuous_aggregate(
                    '{view_name}',
                    TIMESTAMPTZ '{start_time}',
                    TIMESTAMPTZ '{end_time}'
                )
            """
        else:
            # Refresh everything
            sql = f"""
                CALL refresh_continuous_aggregate(
                    '{view_name}',
                    NULL,
                    NULL
                )
            """

        await conn.execute_query(sql)

    @staticmethod
    async def set_refresh_policy(
        view_name: str,
        start_offset: str = "1 hour",
        end_offset: str = "1 minute",
        schedule_interval: str = "1 hour",
    ) -> None:
        """Set automatic refresh policy for a continuous aggregate.

        Args:
            view_name: Name of the continuous aggregate
            start_offset: How far back to refresh from now
            end_offset: How far back from now to stop refreshing
            schedule_interval: How often to refresh

        Example::

            await ContinuousAggregateManager.set_refresh_policy(
                "daily_events",
                start_offset="1 hour",
                end_offset="1 minute",
                schedule_interval="1 hour",
            )
        """
        conn = connections.get("default")

        sql = f"""
            SELECT add_continuous_aggregate_policy(
                '{view_name}',
                start_offset => INTERVAL '{start_offset}',
                end_offset => INTERVAL '{end_offset}',
                schedule_interval => INTERVAL '{schedule_interval}'
            )
        """

        await conn.execute_query(sql)

    @staticmethod
    async def remove_refresh_policy(view_name: str) -> None:
        """Remove refresh policy from a continuous aggregate.

        Args:
            view_name: Name of the continuous aggregate

        Example::

            await ContinuousAggregateManager.remove_refresh_policy("daily_events")
        """
        conn = connections.get("default")

        sql = f"""
            SELECT remove_continuous_aggregate_policy('{view_name}')
        """

        await conn.execute_query(sql)

    @staticmethod
    async def add_realtime_aggregate(view_name: str) -> None:
        """Enable realtime aggregation for a continuous aggregate.

        Args:
            view_name: Name of the continuous aggregate

        Example::

            await ContinuousAggregateManager.add_realtime_aggregate("daily_events")
        """
        conn = connections.get("default")

        sql = f"""
            ALTER MATERIALIZED VIEW {view_name}
            SET (timescaledb.materialized_only = false)
        """

        await conn.execute_query(sql)

    @staticmethod
    async def list() -> list[dict[str, LibraryAny]]:  # pyright: ignore[reportExplicitAny]
        """List all continuous aggregates.

        Returns:
            List of dicts with aggregate information

        Example::

            aggregates = await ContinuousAggregateManager.list()
            for agg in aggregates:
                print(f"{agg['view_name']}: {agg['query']}")
        """
        conn = connections.get("default")

        sql = """
            SELECT
                view_name,
                view_definition
            FROM _timescaledb_catalog.continuous_agg
            ORDER BY view_name
        """

        result = await conn.execute_query(sql)
        rows: list[LibraryAny] = result[1] if isinstance(result, tuple) else result  # pyright: ignore[reportExplicitAny, reportUnknownVariableType]

        return [dict(row) for row in rows]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
