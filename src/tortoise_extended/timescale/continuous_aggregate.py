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

from collections.abc import Sequence
import re
from typing import cast

from tortoise import connections
from tortoise.backends.base.client import BaseDBAsyncClient

from tortoise_extended._quote import quote_ident, quote_literal
from tortoise_extended._types import RowMapping, RowValue
from tortoise_extended.exceptions import TimescaleError


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
        allow_full_statement: bool = False,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Create a continuous aggregate view.

        Args:
            view_name: Name for the continuous aggregate
            _source_table: Source hypertable name
            query: SQL query for the aggregate (must use time_bucket)
            with_data: Populate with existing data
            allow_full_statement: Pass *query* through verbatim as a
                complete ``CREATE MATERIALIZED VIEW`` statement. This
                bypasses validation, so only use it with trusted input.
            using_db: Database connection to use (default: 'default')

        Raises:
            ValueError: If *query* is not a bare ``SELECT``/``WITH``
                statement (when ``allow_full_statement`` is False), or if it
                does not reference ``_source_table``.
            TimescaleError: If the aggregate cannot be created.

        .. warning::

            Unless ``allow_full_statement=True``, ``query`` is validated and
            interpolated into ``CREATE MATERIALIZED VIEW ... AS ({query})``.
            It must be a single bare ``SELECT`` (or ``WITH ... SELECT``)
            statement — anything containing ``;`` or starting with another
            keyword raises :class:`ValueError`. The pre-fix auto-detection
            of ``"CREATE MATERIALIZED VIEW"`` prefixes was removed because
            a bare SELECT may legitimately contain that literal text,
            silently bypassing validation (G23).

        Example::

            await ContinuousAggregateManager.create(
                "daily_events",
                "events",
                "SELECT time_bucket('1 day', created_at) AS bucket, "
                "COUNT(*) AS count FROM events GROUP BY bucket",
            )
        """
        conn = using_db or connections.get("default")

        if allow_full_statement:
            # Trusted verbatim passthrough (caller-supplied complete
            # CREATE MATERIALIZED VIEW statement).
            query = query.strip()
        else:
            inner = query.strip()
            first_keyword = inner.upper().split(None, 1)[0] if inner else ""
            if ";" in inner or first_keyword not in ("SELECT", "WITH"):
                raise ValueError(
                    "Continuous aggregate query must be a single bare "
                    "SELECT (or WITH ... SELECT) statement without ';'; "
                    f"got {first_keyword!r}..."
                )
            # The query must actually read from the declared source
            # hypertable — validating it keeps a mismatched _source_table
            # from silently creating an aggregate over the wrong table.
            # ``_source_table`` may be schema-qualified (``metrics.events``);
            # single-quoted string literals are stripped first, then the
            # table is matched on word boundaries with an optional schema
            # prefix — so a schema-qualified source is accepted and a
            # same-named table nested inside another identifier
            # (``my_events``) or a string literal (``'events'``) is not
            # falsely accepted.
            no_literals = re.sub(r"'[^']*(?:''[^']*)*'", "", inner)
            if "." in _source_table:
                source_pattern = re.escape(_source_table)
            else:
                source_pattern = rf"(?:\w+\.)?{re.escape(_source_table)}"
            if re.search(rf"\b{source_pattern}\b", no_literals) is None:
                raise ValueError(
                    f"Continuous aggregate query must reference the source "
                    f"table {_source_table!r}"
                )
            data_clause = "DATA" if with_data else "NO DATA"
            query = f"""
                CREATE MATERIALIZED VIEW {quote_ident(view_name)}
                WITH (timescaledb.continuous)
                AS ({query})
                WITH {data_clause}
            """

        try:
            _ = await conn.execute_query(query)
        except Exception as exc:
            msg = f"Failed to create continuous aggregate {view_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def drop(
        view_name: str,
        if_exists: bool = True,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Drop a continuous aggregate.

        Args:
            view_name: Name of the continuous aggregate to drop
            if_exists: Don't error if doesn't exist
            using_db: Database connection to use (default: 'default')

        Example::

            await ContinuousAggregateManager.drop("daily_events")
        """
        conn = using_db or connections.get("default")

        sql = f"""
            DROP MATERIALIZED VIEW
            {"IF EXISTS" if if_exists else ""}
            {quote_ident(view_name)}
        """

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to drop continuous aggregate {view_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def refresh(
        view_name: str,
        start_time: str | None = None,
        end_time: str | None = None,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Refresh a continuous aggregate.

        Args:
            view_name: Name of the continuous aggregate
            start_time: Optional start of refresh window
            end_time: Optional end of refresh window
            using_db: Database connection to use (default: 'default')

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
        conn = using_db or connections.get("default")

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

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to refresh continuous aggregate {view_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def set_refresh_policy(
        view_name: str,
        start_offset: str = "1 week",
        end_offset: str = "1 day",
        schedule_interval: str = "1 hour",
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Set automatic refresh policy for a continuous aggregate.

        Args:
            view_name: Name of the continuous aggregate
            start_offset: How far back to refresh from now
            end_offset: How far back from now to stop refreshing
            schedule_interval: How often to refresh
            using_db: Database connection to use (default: 'default')

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
        conn = using_db or connections.get("default")

        sql = (
            "SELECT add_continuous_aggregate_policy("
            f"{quote_literal(view_name)}, "
            f"start_offset => INTERVAL {quote_literal(start_offset)}, "
            f"end_offset => INTERVAL {quote_literal(end_offset)}, "
            f"schedule_interval => INTERVAL {quote_literal(schedule_interval)}"
            ")"
        )

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to set refresh policy on {view_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def remove_refresh_policy(
        view_name: str,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Remove refresh policy from a continuous aggregate.

        Args:
            view_name: Name of the continuous aggregate
            using_db: Database connection to use (default: 'default')

        Example::

            await ContinuousAggregateManager.remove_refresh_policy("daily_events")
        """
        conn = using_db or connections.get("default")

        sql = f"SELECT remove_continuous_aggregate_policy({quote_literal(view_name)})"

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to remove refresh policy from {view_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def add_realtime_aggregate(
        view_name: str,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Enable realtime aggregation for a continuous aggregate.

        Args:
            view_name: Name of the continuous aggregate
            using_db: Database connection to use (default: 'default')

        Example::

            await ContinuousAggregateManager.add_realtime_aggregate("daily_events")
        """
        conn = using_db or connections.get("default")

        sql = f"""
            ALTER MATERIALIZED VIEW {quote_ident(view_name)}
            SET (timescaledb.materialized_only = false)
        """

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to enable realtime aggregation on {view_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def list(
        using_db: BaseDBAsyncClient | None = None,
    ) -> list[RowMapping]:
        """List all continuous aggregates.

        Args:
            using_db: Database connection to use (default: 'default')

        Returns:
            List of dicts with aggregate information

        Example::

            aggregates = await ContinuousAggregateManager.list()
            for agg in aggregates:
                print(f"{agg['view_name']}: {agg['query']}")
        """
        conn = using_db or connections.get("default")

        sql = """
            SELECT
                view_name,
                view_definition
            FROM timescaledb_information.continuous_aggregates
            ORDER BY view_name
        """

        try:
            result = await conn.execute_query(sql)
        except Exception as exc:
            msg = "Failed to list continuous aggregates"
            raise TimescaleError(msg) from exc
        rows = cast(
            Sequence[RowMapping | tuple[RowValue, ...]],
            result[1] if isinstance(result, tuple) else result,
        )

        return [dict(cast(RowMapping, row)) for row in rows]
