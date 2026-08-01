"""TimescaleDB retention policy manager.

Provides automatic data retention for TimescaleDB hypertables.
Requires: TimescaleDB extension

Usage::

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

from typing import cast

from tortoise import connections

from tortoise_extended._types import LibraryAny, RowMapping


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
    ) -> None:
        """Set a retention policy on a hypertable.

        Args:
            table_name: Name of the hypertable
            drop_after: When to drop chunks (e.g., '90 days')
            if_not_exists: Don't error if policy exists

        Example::

            await RetentionPolicy.set_retention(
                "events",
                drop_after="90 days",
            )
        """
        conn = connections.get("default")

        sql = f"""
            SELECT add_retention_policy(
                '{table_name}',
                INTERVAL '{drop_after}',
                if_not_exists => {str(if_not_exists).lower()}
            )
        """

        await conn.execute_query(sql)

    @staticmethod
    async def remove_retention(table_name: str) -> None:
        """Remove retention policy from a hypertable.

        Args:
            table_name: Name of the hypertable

        Example::

            await RetentionPolicy.remove_retention("events")
        """
        conn = connections.get("default")

        sql = f"""
            SELECT remove_retention_policy('{table_name}')
        """

        await conn.execute_query(sql)

    @staticmethod
    async def list_policies() -> list[dict[str, LibraryAny]]:  # pyright: ignore[reportExplicitAny]
        """List all retention policies.

        Returns:
            List of dicts with policy information

        Example::

            policies = await RetentionPolicy.list_policies()
            for policy in policies:
                print(f"{policy['table_name']}: keep {policy['drop_after']}")
        """
        conn = connections.get("default")

        sql = """
            SELECT
                h.table_name,
                p.config AS drop_after,
                p.schedule_interval,
                p.next_start
            FROM _timescaledb_config.bgw_job p
            JOIN _timescaledb_catalog.hypertable h
            ON p.hypertable_id = h.id
            WHERE p.proc_name = 'policy_retention'
            ORDER BY h.table_name
        """

        result = await conn.execute_query(sql)
        rows: list[LibraryAny] = result[1] if isinstance(result, tuple) else result  # pyright: ignore[reportExplicitAny, reportUnknownVariableType]

        return [cast(RowMapping, dict(row)) for row in rows]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]

    @staticmethod
    async def get_chunks_to_drop(
        table_name: str,
        older_than: str = "90 days",
    ) -> list[str]:
        """Preview chunks that would be dropped by retention policy.

        Args:
            table_name: Name of the hypertable
            older_than: Age threshold (e.g., '90 days')

        Returns:
            List of chunk names that would be dropped

        Example::

            chunks = await RetentionPolicy.get_chunks_to_drop(
                "events",
                older_than="90 days",
            )
            print(f"Would drop {len(chunks)} chunks")
        """
        conn = connections.get("default")

        sql = f"""
            SELECT
                chunk_name
            FROM show_chunks(
                '{table_name}',
                older_than => INTERVAL '{older_than}'
            )
            ORDER BY chunk_name
        """

        result = await conn.execute_query(sql)
        rows: list[LibraryAny] = result[1] if isinstance(result, tuple) else result  # pyright: ignore[reportExplicitAny, reportUnknownVariableType]

        return [row[0] for row in rows]  # pyright: ignore[reportUnknownVariableType]
