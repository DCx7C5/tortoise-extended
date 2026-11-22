"""TimescaleDB continuous aggregate manager.

Provides continuous aggregates for materialized views that auto-refresh.
Requires: TimescaleDB extension

Usage (illustrative; needs async context + configured DB)::

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
from typing import cast

import msgspec
import pglast
from pglast.ast import SelectStmt
from pglast.stream import maybe_double_quote_name
from pglast.visitors import referenced_relations
from tortoise import connections
from tortoise.backends.base.client import BaseDBAsyncClient

from tortoise_extended._quote import quote_ident, quote_literal
from tortoise_extended._types import RowMapping, RowValue
from tortoise_extended.exceptions import TimescaleError


_INVALID_SHAPE = (
    "Continuous aggregate query must be a single bare "
    "SELECT (or WITH ... SELECT) statement; got "
)
"""Shared prefix for the two shape rejections (statement count, statement kind)."""

_COMMENT_TOKENS = frozenset({"BOM", "C_COMMENT", "SQL_COMMENT"})
"""``pglast.scan`` token names that carry no SQL token of their own."""

_ALLOWED_LEADING_KEYWORDS = frozenset({"SELECT", "WITH"})


def _first_keyword(statement: str) -> str:
    """Return the first non-comment keyword of *statement*, upper-cased.

    PostgreSQL's own lexer (:func:`pglast.scan`) is used instead of
    ``str.split`` so a leading ``--``/``/* */`` comment does not mask the
    keyword it precedes.

    Args:
        statement: A single SQL statement.

    Returns:
        The leading keyword (for example ``"SELECT"``), or ``""`` when the
        text holds no token other than comments.
    """
    for token in pglast.scan(statement):
        if token.name not in _COMMENT_TOKENS:
            return token.name.upper()
    return ""


def _quoted_name(name: str) -> str:
    """Render *name* the way PostgreSQL prints a (possibly qualified) relation.

    Each dotted component is quoted only when PostgreSQL would have to quote
    it, matching the strings returned by
    :func:`pglast.visitors.referenced_relations`.
    """
    return ".".join(maybe_double_quote_name(part) for part in name.split("."))


def _references_source(relations: set[str], source_table: str) -> bool:
    """Whether a parsed query reads from *source_table*.

    A schema-qualified ``source_table`` (``metrics.events``) requires both the
    schema and the relation to match.  An unqualified one matches the relation
    name alone, whether or not the query qualifies it (``events`` matches both
    ``events`` and ``metrics.events``).
    """
    wanted = _quoted_name(source_table)
    if "." in source_table:
        return wanted in relations
    return any(
        relation == wanted or relation.endswith(f".{wanted}") for relation in relations
    )


class ContinuousAggregateInfo(msgspec.Struct):
    """One continuous aggregate returned by
    :meth:`ContinuousAggregateManager.list`.

    Attributes:
        view_name: Name of the continuous aggregate materialized view.
        view_definition: The ``SELECT`` statement the aggregate is
            materialized from (as stored by TimescaleDB).
    """

    view_name: str
    view_definition: str


def validate_cagg_query(query: str, source_table: str | None = None) -> str:
    """Validate a continuous-aggregate ``SELECT`` and return it normalized.

    The query is parsed with PostgreSQL's own grammar (``pglast``).  It must be
    exactly one statement, that statement must be a ``SELECT`` — a ``WITH``
    query is a ``SELECT`` carrying a ``withClause``, so it passes the same
    check — and it must begin with the ``SELECT`` or ``WITH`` keyword.  Because
    statement boundaries come from the real parser, a ``;`` inside a string
    literal, a comment, or a dollar-quoted string is never mistaken for a
    separator, and a payload that only *looks* like one statement is rejected.

    One trailing ``;`` is accepted and stripped from the returned text
    (``"SELECT 1;"`` validates as ``"SELECT 1"``).  That is safe — a single
    statement cannot smuggle in a second one — and it keeps the result usable
    inside the ``AS (...)`` wrapper, where a trailing ``;`` would be a syntax
    error.

    When ``source_table`` is given, the parsed query must reference it through
    a ``RangeVar``: ``events`` matches ``events`` and ``metrics.events``, while
    ``metrics.events`` requires both the schema and the relation to match.  A
    name that only appears inside a comment or a string literal does not count,
    and a CTE reference never does.  Shared by
    :meth:`ContinuousAggregateManager.create` and the
    :class:`~tortoise_extended.migrations.operations.CreateContinuousAggregate`
    migration operation so both enforce the same shape contract.

    Args:
        query: The aggregate SELECT (or ``WITH ... SELECT``) statement.
        source_table: Optional source hypertable the aggregate reads from.  When
            ``None`` the source-reference cross-check is skipped.

    Returns:
        The exact text of that statement, without trailing semicolons or
        leading comments.

    Raises:
        ValueError: If the query is not valid SQL, is not exactly one statement,
            is not a bare SELECT/WITH statement, or (when ``source_table`` is
            given) does not reference it.
    """
    inner = query.strip()
    # ``parse_sql`` returns one entry per statement and ``split`` returns the
    # exact text of each: the count is the injection gate, the slice is the
    # text that gets interpolated into the DDL (so a trailing ``;`` and any
    # leading comments never reach it).  Both use PostgreSQL's grammar, so a
    # ``;`` hidden inside a comment cannot masquerade as a statement boundary.
    try:
        statements = pglast.parse_sql(inner)
        slices = pglast.split(inner, only_slices=True)
    except pglast.Error as exc:
        msg = f"Continuous aggregate query is not valid SQL: {exc}"
        raise ValueError(msg) from exc
    if len(statements) != 1 or len(slices) != 1:
        raise ValueError(f"{_INVALID_SHAPE}{len(statements)} statements")
    statement = inner[slices[0]]
    stmt = statements[0].stmt
    first_keyword = _first_keyword(statement)
    # The keyword check is kept (rather than relying on the AST alone) because
    # ``TABLE x`` and ``VALUES (1)`` parse as ``SelectStmt`` too, and the
    # documented contract is a bare ``SELECT``/``WITH ... SELECT``.
    if first_keyword not in _ALLOWED_LEADING_KEYWORDS or not isinstance(
        stmt, SelectStmt
    ):
        raise ValueError(f"{_INVALID_SHAPE}{first_keyword!r}...")
    if source_table is None:
        return statement
    # The query must actually read from the declared source hypertable —
    # validating it keeps a mismatched source_table from silently creating an
    # aggregate over the wrong table.  ``referenced_relations`` walks the
    # parsed tree's ``RangeVar`` nodes (skipping CTE references), so a table
    # name that only appears inside a comment, a string literal, or a longer
    # identifier (``my_events``) is not falsely accepted.
    if not _references_source(referenced_relations(stmt), source_table):
        raise ValueError(
            f"Continuous aggregate query must reference the source "
            f"table {source_table!r}"
        )
    return statement


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

                .. danger::

                    When ``True``, the ``query`` string is interpolated directly
                    into the DDL statement without any sanitization.  Only pass
                    values you fully control — never pass user-supplied strings
                    without first validating them through ``validate_cagg_query``.
            using_db: Database connection to use (default: 'default')

        Raises:
            ValueError: If *query* is not valid SQL, is not a single bare
                ``SELECT``/``WITH`` statement (when ``allow_full_statement``
                is False), or if it does not reference ``_source_table``.
            TimescaleError: If the aggregate cannot be created.

        .. warning::

            Unless ``allow_full_statement=True``, ``query`` is parsed with
            PostgreSQL's grammar and must be exactly one ``SELECT`` (or
            ``WITH ... SELECT``) statement; it is then interpolated into
            ``CREATE MATERIALIZED VIEW ... AS ({query})``. More than one
            statement, a non-``SELECT`` statement, or a query that does not
            parse raises :class:`ValueError`. A ``;`` inside a string literal
            (e.g. ``'a;b'``), a comment, or a dollar-quoted string belongs to
            that statement and is accepted; a single trailing ``;`` is
            accepted and stripped. The pre-fix auto-detection of
            ``"CREATE MATERIALIZED VIEW"`` prefixes was removed because
            a bare SELECT may legitimately contain that literal text,
            silently bypassing validation (G23).

        Example (illustrative; needs async context + configured DB)::

            await ContinuousAggregateManager.create(
                "daily_events",
                "events",
                "SELECT time_bucket('1 day', created_at) AS bucket, "
                "COUNT(*) AS count FROM events GROUP BY bucket",
            )
        """
        # Validate before resolving the connection: an empty (or otherwise
        # invalid) query must be rejected without requiring a configured
        # database, and a rejected query must never reach a connection.
        if allow_full_statement:
            # Trusted verbatim passthrough (caller-supplied complete
            # CREATE MATERIALIZED VIEW statement).
            if not query.strip():
                raise ValueError("query must not be empty")
            query = query.strip()
        else:
            inner = validate_cagg_query(query, _source_table)
            data_clause = "DATA" if with_data else "NO DATA"
            query = f"""
                CREATE MATERIALIZED VIEW {quote_ident(view_name)}
                WITH (timescaledb.continuous)
                AS ({inner})
                WITH {data_clause}
            """

        conn = using_db or connections.get("default")

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

        Example (illustrative; needs async context + configured DB)::

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

        Example (illustrative; needs async context + configured DB)::

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

        Example (illustrative; needs async context + configured DB)::

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

        Example (illustrative; needs async context + configured DB)::

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

        Example (illustrative; needs async context + configured DB)::

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
    ) -> list[ContinuousAggregateInfo]:
        """List all continuous aggregates.

        Args:
            using_db: Database connection to use (default: 'default')

        Returns:
            List of :class:`ContinuousAggregateInfo` structs describing each
            continuous aggregate.

        Example (illustrative; needs async context + configured DB)::

            aggregates = await ContinuousAggregateManager.list()
            for agg in aggregates:
                print(f"{agg.view_name}: {agg.view_definition}")
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

        infos: list[ContinuousAggregateInfo] = []
        for row in rows:
            if isinstance(row, dict):
                infos.append(
                    ContinuousAggregateInfo(
                        view_name=cast(str, row["view_name"]),
                        view_definition=cast(str, row["view_definition"]),
                    )
                )
            else:
                infos.append(
                    ContinuousAggregateInfo(
                        cast(str, row[0]),
                        cast(str, row[1]),
                    )
                )
        return infos
