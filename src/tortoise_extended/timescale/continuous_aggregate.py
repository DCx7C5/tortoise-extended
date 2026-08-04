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
        start_offset="1 week",
        end_offset="1 day",
        schedule_interval="1 hour",
    )
"""

from typing import cast

from tortoise import connections

from tortoise_extended._quote import quote_ident, quote_literal
from tortoise_extended._types import LibraryAny, RowMapping


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
            _source_table: Source hypertable name
            query: SQL query for the aggregate (must use time_bucket)
            with_data: Populate with existing data

        .. warning::

            ``query`` is interpolated verbatim into
            ``CREATE MATERIALIZED VIEW ... AS ({query})``. It must be a
            single bare ``SELECT`` (or ``WITH ... SELECT``) statement —
            anything containing ``;`` or starting with another keyword
            raises :class:`ValueError` (G23). Passing a full
            ``CREATE MATERIALIZED VIEW`` statement is also accepted and
            passed through unvalidated, so treat that input as trusted.

        Example::

            await ContinuousAggregateManager.create(
                "daily_events",
                "events",
                "SELECT time_bucket('1 day', created_at) AS bucket, "
                "COUNT(*) AS count FROM events GROUP BY bucket",
            )
        """
        conn = connections.get("default")

        # Ensure CREATE CONTINUOUS AGGREGATE syntax.
        # Note: we build the canonical `CREATE MATERIALIZED VIEW ... WITH
        # (timescaledb.continuous)` form. The older `CREATE CONTINUOUS
        # AGGREGATE` spelling is equivalent on TimescaleDB 2.x but relies on a
        # parser hook that is not registered in some 2.28.x builds.
        if "CREATE MATERIALIZED VIEW" not in query.upper():
            inner = query.strip()
            first_keyword = inner.upper().split(None, 1)[0] if inner else ""
            if ";" in inner or first_keyword not in ("SELECT", "WITH"):
                raise ValueError(
                    "Continuous aggregate query must be a single bare "
                    "SELECT (or WITH ... SELECT) statement without ';'; "
                    f"got {first_keyword!r}..."
                )
            data_clause = "DATA" if with_data else "NO DATA"
            query = f"""
                CREATE MATERIALIZED VIEW {quote_ident(view_name)}
                WITH (timescaledb.continuous)
                AS ({query})
                WITH {data_clause}
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
            DROP MATERIALIZED VIEW
            {"IF EXISTS" if if_exists else ""}
            {quote_ident(view_name)}
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
            sql = (
                "CALL refresh_continuous_aggregate("
                f"{quote_literal(view_name)}, "
                f"TIMESTAMPTZ {quote_literal(start_time)}, "
                f"TIMESTAMPTZ {quote_literal(end_time)}"
                ")"
            )
        else:
            # Refresh everything
            sql = (
                "CALL refresh_continuous_aggregate("
                f"{quote_literal(view_name)}, NULL, NULL)"
            )

        await conn.execute_query(sql)

    @staticmethod
    async def set_refresh_policy(
        view_name: str,
        start_offset: str = "1 week",
        end_offset: str = "1 day",
        schedule_interval: str = "1 hour",
    ) -> None:
        """Set automatic refresh policy for a continuous aggregate.

        Args:
            view_name: Name of the continuous aggregate
            start_offset: How far back to refresh from now
            end_offset: How far back from now to stop refreshing
            schedule_interval: How often to refresh

        The refresh window (``start_offset`` minus ``end_offset``) must cover
        at least two buckets of the aggregate, otherwise TimescaleDB rejects
        the policy with "policy refresh window too small". For a daily
        aggregate, offsets of ``"1 week"`` / ``"1 day"`` are safe defaults.

        Example::

            await ContinuousAggregateManager.set_refresh_policy(
                "daily_events",
                start_offset="1 week",
                end_offset="1 day",
                schedule_interval="1 hour",
            )
        """
        conn = connections.get("default")

        sql = (
            "SELECT add_continuous_aggregate_policy("
            f"{quote_literal(view_name)}, "
            f"start_offset => INTERVAL {quote_literal(start_offset)}, "
            f"end_offset => INTERVAL {quote_literal(end_offset)}, "
            f"schedule_interval => INTERVAL {quote_literal(schedule_interval)}"
            ")"
        )

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

        sql = f"SELECT remove_continuous_aggregate_policy({quote_literal(view_name)})"

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
            ALTER MATERIALIZED VIEW {quote_ident(view_name)}
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
            FROM timescaledb_information.continuous_aggregates
            ORDER BY view_name
        """

        result = await conn.execute_query(sql)
        rows: list[LibraryAny] = result[1] if isinstance(result, tuple) else result  # pyright: ignore[reportExplicitAny, reportUnknownVariableType]

        return [cast(RowMapping, dict(row)) for row in rows]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
