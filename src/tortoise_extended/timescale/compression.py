"""TimescaleDB compression manager.

Provides compression for TimescaleDB hypertables to reduce storage.
Requires: TimescaleDB extension (>= 2.18 — uses the columnstore API:
``add_columnstore_policy`` / ``convert_to_columnstore`` /
``convert_to_rowstore``; the legacy ``add_compression_policy`` /
``compress_chunk`` / ``decompress_chunk`` are deprecated since 2.18).

Usage::

    from tortoise_extended.timescale import CompressionManager

    # Enable compression on a hypertable
    await CompressionManager.enable_compression("events")

    # Compress chunks older than 7 days (automatic policy)
    await CompressionManager.add_compression_policy(
        "events",
        compress_after="7 days",
    )

    # Compress a chunk manually
    await CompressionManager.compress_chunk(
        "_timescaledb_internal._hyper_1_1_chunk",
    )

    # Get compression stats
    stats = await CompressionManager.get_stats("events")
    print(f"Compression ratio: {stats['compression_ratio']}")
"""

from collections.abc import Sequence
from typing import cast

from tortoise import connections
from tortoise.backends.base.client import BaseDBAsyncClient

from tortoise_extended._quote import quote_ident, quote_literal
from tortoise_extended._types import RowMapping, RowValue
from tortoise_extended.exceptions import TimescaleError


class CompressionManager:
    """Manager for TimescaleDB compression.

    Compression reduces storage requirements and can improve query performance
    for time-series data by compressing older chunks.

    Since TimescaleDB 2.18 the underlying API is the *columnstore*:
    this manager's method names are kept stable, but they call
    ``add_columnstore_policy``, ``convert_to_columnstore`` and
    ``convert_to_rowstore`` internally.
    """

    @staticmethod
    async def enable_compression(
        table_name: str,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Enable compression on a hypertable.

        Args:
            table_name: Name of the hypertable
            using_db: Database connection to use (default: 'default')

        Example::

            await CompressionManager.enable_compression("events")

        To compress chunks after a delay, add a compression policy with
        :meth:`add_compression_policy` (``compress_after`` is not a valid
        TimescaleDB reloption — it only exists on policies).

        Uses the 2.18+ ``timescaledb.enable_columnstore`` reloption
        (``timescaledb.compress`` is deprecated since 2.18).
        """
        conn = using_db or connections.get("default")

        sql = f"""
            ALTER TABLE {quote_ident(table_name)}
            SET (timescaledb.enable_columnstore)
        """

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to enable compression on {table_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def disable_compression(
        table_name: str,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Disable compression on a hypertable.

        Args:
            table_name: Name of the hypertable
            using_db: Database connection to use (default: 'default')

        Example::

            await CompressionManager.disable_compression("events")
        """
        conn = using_db or connections.get("default")

        sql = f"""
            ALTER TABLE {quote_ident(table_name)}
            SET (timescaledb.enable_columnstore = false)
        """

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to disable compression on {table_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def add_compression_policy(
        table_name: str,
        compress_after: str = "7 days",
        if_not_exists: bool = True,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Add a compression policy to automatically compress chunks.

        Args:
            table_name: Name of the hypertable
            compress_after: When to compress chunks
            if_not_exists: Don't error if policy exists
            using_db: Database connection to use (default: 'default')

        Example::

            await CompressionManager.add_compression_policy(
                "events",
                compress_after="7 days",
            )

        Calls ``add_columnstore_policy`` (2.18+; the legacy
        ``add_compression_policy`` is deprecated since 2.18).
        """
        conn = using_db or connections.get("default")

        sql = (
            "CALL add_columnstore_policy("
            f"hypertable => {quote_literal(table_name)}, "
            f"after => INTERVAL {quote_literal(compress_after)}, "
            f"if_not_exists => {str(if_not_exists).lower()}"
            ")"
        )

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to add compression policy to {table_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def remove_compression_policy(
        table_name: str,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Remove compression policy from a hypertable.

        Args:
            table_name: Name of the hypertable
            using_db: Database connection to use (default: 'default')

        Example::

            await CompressionManager.remove_compression_policy("events")

        Calls ``remove_columnstore_policy`` (2.18+; the legacy
        ``remove_compression_policy`` is deprecated since 2.18).
        """
        conn = using_db or connections.get("default")

        sql = f"CALL remove_columnstore_policy({quote_literal(table_name)})"

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to remove compression policy from {table_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def compress_chunk(
        chunk_name: str,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Manually compress a specific chunk.

        Args:
            chunk_name: Full chunk name (e.g., '_timescaledb_internal._hyper_1_1_chunk')
            using_db: Database connection to use (default: 'default')

        Example::

            await CompressionManager.compress_chunk(
                "_timescaledb_internal._hyper_1_1_chunk",
            )

        Calls ``convert_to_columnstore`` (2.18+; the legacy
        ``compress_chunk`` is deprecated since 2.18).
        """
        conn = using_db or connections.get("default")

        sql = f"CALL convert_to_columnstore({quote_literal(chunk_name)})"

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to compress chunk {chunk_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def decompress_chunk(
        chunk_name: str,
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """Decompress a compressed chunk.

        Args:
            chunk_name: Full chunk name
            using_db: Database connection to use (default: 'default')

        Example::

            await CompressionManager.decompress_chunk(
                "_timescaledb_internal._hyper_1_1_chunk",
            )

        Calls ``convert_to_rowstore`` (2.18+; the legacy
        ``decompress_chunk`` is deprecated since 2.18).
        """
        conn = using_db or connections.get("default")

        sql = f"CALL convert_to_rowstore({quote_literal(chunk_name)})"

        try:
            _ = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to decompress chunk {chunk_name!r}: {exc}"
            raise TimescaleError(msg) from exc

    @staticmethod
    async def get_stats(
        table_name: str,
        using_db: BaseDBAsyncClient | None = None,
    ) -> RowMapping:
        """Get compression statistics for a hypertable.

        Args:
            table_name: Name of the hypertable
            using_db: Database connection to use (default: 'default')

        Returns:
            Dict with compression stats

        Example::

            stats = await CompressionManager.get_stats("events")
            print(f"Compression ratio: {stats['compression_ratio']}")
            print(f"Uncompressed chunks: {stats['uncompressed_chunks']}")
            print(f"Compressed chunks: {stats['compressed_chunks']}")

        Uses ``hypertable_columnstore_stats`` (2.18+; the legacy
        ``hypertable_compression_stats`` is deprecated since 2.18).
        """
        conn = using_db or connections.get("default")

        sql = f"""
            SELECT
                pg_size_pretty(before_compression_total_bytes) AS uncompressed_size,
                pg_size_pretty(after_compression_total_bytes) AS compressed_size,
                CASE
                    WHEN before_compression_total_bytes > 0
                     AND after_compression_total_bytes > 0
                    THEN ROUND(
                        before_compression_total_bytes::numeric /
                        after_compression_total_bytes,
                        2
                    )
                    ELSE 1
                END AS compression_ratio,
                (SELECT COUNT(*) FROM timescaledb_information.chunks c
                 WHERE c.hypertable_name = {quote_literal(table_name)}
                 AND c.is_compressed = FALSE) AS uncompressed_chunks,
                (SELECT COUNT(*) FROM timescaledb_information.chunks c
                 WHERE c.hypertable_name = {quote_literal(table_name)}
                 AND c.is_compressed = TRUE) AS compressed_chunks
            FROM hypertable_columnstore_stats({quote_literal(table_name)})
            LIMIT 1
        """

        try:
            result = await conn.execute_query(sql)
        except Exception as exc:
            msg = f"Failed to get compression stats for {table_name!r}: {exc}"
            raise TimescaleError(msg) from exc
        rows = cast(
            Sequence[RowMapping | tuple[RowValue, ...]],
            result[1] if isinstance(result, tuple) else result,
        )

        if rows:
            row = rows[0]
            if isinstance(row, dict):
                ratio = row.get("compression_ratio")
                if ratio is not None:
                    # The ROUND() expression returns a numeric; cast at the
                    # boundary so the exposed type is stable across drivers.
                    return {**row, "compression_ratio": float(ratio)}
                return row
            return {
                "uncompressed_size": row[0],
                "compressed_size": row[1],
                "compression_ratio": (
                    float(row[2]) if row[2] is not None else None
                ),
                "uncompressed_chunks": row[3],
                "compressed_chunks": row[4],
            }
        return {}
