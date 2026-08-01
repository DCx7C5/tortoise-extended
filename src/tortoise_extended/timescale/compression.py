"""TimescaleDB compression manager.

Provides compression for TimescaleDB hypertables to reduce storage.
Requires: TimescaleDB extension

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

from tortoise import connections

from tortoise_extended._types import LibraryAny


class CompressionManager:
    """Manager for TimescaleDB compression.

    Compression reduces storage requirements and can improve query performance
    for time-series data by compressing older chunks.
    """

    @staticmethod
    async def enable_compression(table_name: str) -> None:
        """Enable compression on a hypertable.

        Args:
            table_name: Name of the hypertable

        Example::

            await CompressionManager.enable_compression("events")

        To compress chunks after a delay, add a compression policy with
        :meth:`add_compression_policy` (``compress_after`` is not a valid
        TimescaleDB reloption — it only exists on policies).
        """
        conn = connections.get("default")

        sql = f"""
            ALTER TABLE {table_name}
            SET (timescaledb.compress)
        """

        await conn.execute_query(sql)

    @staticmethod
    async def disable_compression(table_name: str) -> None:
        """Disable compression on a hypertable.

        Args:
            table_name: Name of the hypertable

        Example::

            await CompressionManager.disable_compression("events")
        """
        conn = connections.get("default")

        sql = f"""
            ALTER TABLE {table_name}
            SET (timescaledb.compress = false)
        """

        await conn.execute_query(sql)

    @staticmethod
    async def add_compression_policy(
        table_name: str,
        compress_after: str = "7 days",
        if_not_exists: bool = True,
    ) -> None:
        """Add a compression policy to automatically compress chunks.

        Args:
            table_name: Name of the hypertable
            compress_after: When to compress chunks
            if_not_exists: Don't error if policy exists

        Example::

            await CompressionManager.add_compression_policy(
                "events",
                compress_after="7 days",
            )
        """
        conn = connections.get("default")

        sql = f"""
            SELECT add_compression_policy(
                '{table_name}',
                INTERVAL '{compress_after}',
                if_not_exists => {str(if_not_exists).lower()}
            )
        """

        await conn.execute_query(sql)

    @staticmethod
    async def remove_compression_policy(table_name: str) -> None:
        """Remove compression policy from a hypertable.

        Args:
            table_name: Name of the hypertable

        Example::

            await CompressionManager.remove_compression_policy("events")
        """
        conn = connections.get("default")

        sql = f"""
            SELECT remove_compression_policy('{table_name}')
        """

        await conn.execute_query(sql)

    @staticmethod
    async def compress_chunk(chunk_name: str) -> None:
        """Manually compress a specific chunk.

        Args:
            chunk_name: Full chunk name (e.g., '_timescaledb_internal._hyper_1_1_chunk')

        Example::

            await CompressionManager.compress_chunk(
                "_timescaledb_internal._hyper_1_1_chunk",
            )
        """
        conn = connections.get("default")

        sql = f"""
            SELECT compress_chunk('{chunk_name}')
        """

        await conn.execute_query(sql)

    @staticmethod
    async def decompress_chunk(chunk_name: str) -> None:
        """Decompress a compressed chunk.

        Args:
            chunk_name: Full chunk name

        Example::

            await CompressionManager.decompress_chunk(
                "_timescaledb_internal._hyper_1_1_chunk",
            )
        """
        conn = connections.get("default")

        sql = f"""
            SELECT decompress_chunk('{chunk_name}')
        """

        await conn.execute_query(sql)

    @staticmethod
    async def get_stats(table_name: str) -> dict[str, LibraryAny]:  # pyright: ignore[reportExplicitAny]
        """Get compression statistics for a hypertable.

        Args:
            table_name: Name of the hypertable

        Returns:
            Dict with compression stats

        Example::

            stats = await CompressionManager.get_stats("events")
            print(f"Compression ratio: {stats['compression_ratio']}")
            print(f"Uncompressed chunks: {stats['uncompressed_chunks']}")
            print(f"Compressed chunks: {stats['compressed_chunks']}")
        """
        conn = connections.get("default")

        sql = f"""
            SELECT
                pg_size_pretty(before_compression_total_bytes) AS uncompressed_size,
                pg_size_pretty(after_compression_total_bytes) AS compressed_size,
                CASE
                    WHEN before_compression_total_bytes > 0
                    THEN ROUND(
                        before_compression_total_bytes::numeric /
                        after_compression_total_bytes,
                        2
                    )
                    ELSE 1
                END AS compression_ratio,
                (SELECT COUNT(*) FROM _timescaledb_catalog.chunk c
                 JOIN _timescaledb_catalog.hypertable h
                 ON c.hypertable_id = h.id
                 WHERE h.table_name = '{table_name}'
                 AND c.compressed_chunk_id IS NULL) AS uncompressed_chunks,
                (SELECT COUNT(*) FROM _timescaledb_catalog.chunk c
                 JOIN _timescaledb_catalog.hypertable h
                 ON c.hypertable_id = h.id
                 WHERE h.table_name = '{table_name}'
                 AND c.compressed_chunk_id IS NOT NULL) AS compressed_chunks
            FROM hypertable_compression_stats('{table_name}')
            LIMIT 1
        """

        result = await conn.execute_query(sql)
        rows: list[LibraryAny] = result[1] if isinstance(result, tuple) else result  # pyright: ignore[reportExplicitAny, reportUnknownVariableType]

        if rows:
            row: LibraryAny = rows[0]  # pyright: ignore[reportExplicitAny, reportUnknownVariableType]
            if isinstance(row, dict):
                return row  # pyright: ignore[reportUnknownVariableType]
            return {
                "uncompressed_size": row[0],
                "compressed_size": row[1],
                "compression_ratio": row[2],
                "uncompressed_chunks": row[3],
                "compressed_chunks": row[4],
            }
        return {}
