"""TimescaleDB retention policy manager.

Provides automatic data retention for TimescaleDB hypertables.
Requires: TimescaleDB extension

Usage (illustrative; needs async context + configured DB)::

    from tortoise_extended.timescale import RetentionPolicy

    # Set retention policy (drop data older than 90 days)
    await RetentionPolicy.set_retention(
        "events",
        drop_after="90 days",
    )

    # Remove retention policy
    await RetentionPolicy.remove_retention("events")

    # List all retention policies
    policies = await RetentionPolicy.list_policies()
"""

from collections.abc import Sequence
from typing import cast

import msgspec
from tortoise import connections
from tortoise.backends.base.client import BaseDBAsyncClient

from tortoise_extended._quote import quote_literal
from tortoise_extended._types import JsonScalar, RowMapping, RowValue
from tortoise_extended.exceptions import TimescaleError


class RetentionPolicyInfo(msgspec.Struct):
    """One retention policy returned by :meth:`RetentionPolicy.list_policies`.

    Attributes:
        table_name: Name of the hypertable the retention policy applies to.
        drop_after: The retention job's ``config`` jsonb column decoded to a
            dict — typically ``{"drop_after": "90 days"}`` giving how long the
            policy retains data.
        schedule_interval: How often the retention job runs (e.g.
            ``"1 day"``), or ``None`` when not set.
        initial_start: When the retention job can first run, or ``None`` when
            not set.
        scheduled: Whether the retention job is currently enabled/scheduled.
    """

    table_name: str
    drop_after: dict[str, JsonScalar]
    schedule_interval: str | None
    initial_start: str | None
    scheduled: bool


class RetentionPolicy:
    """Manager for TimescaleDB retention policies.

    Retention policies automatically drop data older than a specified interval.
    This is essential for managing storage and complying with data retention
    requirements.
    """

    @staticmethod
    async def set_retention(
        table_name: str,
        drop_after: str = "90 days",
        if_not_exists: bool = True,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Set a retention policy on a hypertable.

        Args:
            table_name: Name of the hypertable
            drop_after: When to drop chunks (e.g., '90 days')
            if_not_exists: Don't error if policy exists
            using_db: Database connection to use (default: 'default')

        Example (illustrative; needs async context + configured DB)::

            await RetentionPolicy.set_retention(
                "events",
                drop_after="90 days",
            )
        """
        conn = using_db or connections.get("default")

        sql = (
            "SELECT add_retention_policy("
            f"{quote_literal(table_name)}, "
            f"INTERVAL {quote_literal(drop_after)}, "
            f"if_not_exists => {str(if_not_exists).lower()}"
            ")"
        )

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to set retention policy on {table_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def remove_retention(
        table_name: str,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Remove retention policy from a hypertable.

        Args:
            table_name: Name of the hypertable
            using_db: Database connection to use (default: 'default')

        Example (illustrative; needs async context + configured DB)::

            await RetentionPolicy.remove_retention("events")
        """
        conn = using_db or connections.get("default")

        sql = f"SELECT remove_retention_policy({quote_literal(table_name)})"

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to remove retention policy from {table_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def list_policies(
        using_db: BaseDBAsyncClient | None = None,
    ) -> list[RetentionPolicyInfo]:
        """List all retention policies.

        Args:
            using_db: Database connection to use (default: 'default')

        Returns:
            List of :class:`RetentionPolicyInfo` structs describing each
            retention policy.

        Example (illustrative; needs async context + configured DB)::

            policies = await RetentionPolicy.list_policies()
            for policy in policies:
                print(f"{policy.table_name}: keep {policy.drop_after}")
        """
        conn = using_db or connections.get("default")

        sql = """
            SELECT
                hypertable_name AS table_name,
                config AS drop_after,
                schedule_interval,
                initial_start,
                scheduled
            FROM timescaledb_information.jobs
            WHERE proc_name = 'policy_retention'
            ORDER BY hypertable_name
        """

        try:
            result = await conn.execute_query(sql)
        except Exception as exc:
            msg = "Failed to list retention policies"
            raise TimescaleError(msg) from exc
        rows = cast(
            Sequence[RowMapping | tuple[RowValue, ...]],
            result[1] if isinstance(result, tuple) else result,
        )

        policies: list[RetentionPolicyInfo] = []
        for row in rows:
            if isinstance(row, dict):
                drop_after = cast(
                    "RowValue | dict[str, JsonScalar]", row.get("drop_after")
                )
                if not isinstance(drop_after, dict):
                    # ``config`` is a jsonb column: drivers decode it to a
                    # dict even though the row mapping types it as a scalar
                    # RowValue. Bridge through ``object`` to keep the narrow
                    # dict at the scalar value position (G20).
                    drop_after = cast("dict[str, JsonScalar]", cast(object, drop_after))
                policies.append(
                    RetentionPolicyInfo(
                        table_name=cast(str, row["table_name"]),
                        drop_after=drop_after,
                        schedule_interval=cast(str | None, row["schedule_interval"]),
                        initial_start=cast(str | None, row["initial_start"]),
                        scheduled=cast(bool, row["scheduled"]),
                    )
                )
            else:
                drop_after = cast("RowValue | dict[str, JsonScalar]", row[1])
                if not isinstance(drop_after, dict):
                    # Same jsonb handling as the dict-row branch (G20).
                    drop_after = cast("dict[str, JsonScalar]", cast(object, drop_after))
                policies.append(
                    RetentionPolicyInfo(
                        cast(str, row[0]),
                        drop_after,
                        cast(str | None, row[2]),
                        cast(str | None, row[3]),
                        cast(bool, row[4]),
                    )
                )
        return policies

    @staticmethod
    async def get_chunks_to_drop(
        table_name: str,
        older_than: str = "90 days",
        using_db: BaseDBAsyncClient | None = None,
    ) -> list[str]:
        """Preview chunks that would be dropped by retention policy.

        Args:
            table_name: Name of the hypertable
            older_than: Age threshold (e.g., '90 days')
            using_db: Database connection to use (default: 'default')

        Returns:
            List of chunk names that would be dropped

        Example (illustrative; needs async context + configured DB)::

            chunks = await RetentionPolicy.get_chunks_to_drop(
                "events",
                older_than="90 days",
            )
            print(f"Would drop {len(chunks)} chunks")
        """
        conn = using_db or connections.get("default")

        sql = f"""
            SELECT
                show_chunks AS chunk_name
            FROM show_chunks(
                {quote_literal(table_name)},
                older_than => INTERVAL {quote_literal(older_than)}
            )
            ORDER BY chunk_name
        """

        try:
            result = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to list chunks to drop for {table_name!r}: {exc}"
            raise TimescaleError(msg) from exc
        rows = cast(
            Sequence[tuple[RowValue, ...]],
            cast(
                object,
                result[1] if isinstance(result, tuple) else result,
            ),
        )

        return [cast(str, row[0]) for row in rows]
