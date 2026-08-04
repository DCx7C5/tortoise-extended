"""Unit tests for timescale manager helpers with a fake connection.

Exercises the tuple-row and mapping-row result branches of
``HypertableManager.is_hypertable`` and ``CompressionManager.get_compression_stats``
without a live TimescaleDB. Uses monkeypatch to swap the module-level
``connections`` lookup.
"""

import pytest

from tortoise_extended.timescale.compression import CompressionManager
from tortoise_extended.timescale.continuous_aggregate import ContinuousAggregateManager
from tortoise_extended.timescale.hypertable import HypertableManager
from tortoise_extended.timescale.retention import RetentionPolicy


class FakeConn:
    """Returns a canned ``execute_query`` result."""

    def __init__(self, result: object) -> None:
        self.result = result

    async def execute_query(self, sql: str, *args: object) -> object:
        return self.result


class FakeConnections:
    """Stand-in for the module-level ``connections`` singleton."""

    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def get(self, name: str) -> FakeConn:
        return self.conn


def _patch_connections(
    monkeypatch: pytest.MonkeyPatch, result: object
) -> FakeConn:
    conn = FakeConn(result)
    holder = FakeConnections(conn)
    # Replace the whole module-level `connections` binding — the real proxy
    # delegates through the active TortoiseContext, which we don't have here.
    monkeypatch.setattr("tortoise_extended.timescale.hypertable.connections", holder)
    monkeypatch.setattr("tortoise_extended.timescale.compression.connections", holder)
    monkeypatch.setattr("tortoise_extended.timescale.retention.connections", holder)
    monkeypatch.setattr(
        "tortoise_extended.timescale.continuous_aggregate.connections", holder
    )
    return conn


class RecordingConn:
    """Captures SQL passed to ``execute_query``."""

    def __init__(self) -> None:
        self.sqls: list[str] = []

    async def execute_query(self, sql: str, *args: object) -> object:
        self.sqls.append(sql)
        return (0, [])


class TestIsHypertable:
    """HypertableManager.is_hypertable() result-shape branches."""

    @pytest.mark.asyncio
    async def test_tuple_row_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(monkeypatch, (0, [(True,)]))
        assert await HypertableManager.is_hypertable("events") is True

    @pytest.mark.asyncio
    async def test_mapping_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(monkeypatch, (0, [{"is_hypertable": False}]))
        assert await HypertableManager.is_hypertable("events") is False

    @pytest.mark.asyncio
    async def test_empty_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(monkeypatch, ([], 0))
        assert await HypertableManager.is_hypertable("events") is False


class TestGetCompressionStats:
    """CompressionManager.get_stats() result-shape branches."""

    @pytest.mark.asyncio
    async def test_mapping_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(
            monkeypatch,
            (0, [{"uncompressed_size": 10, "compressed_size": 5}]),
        )
        stats = await CompressionManager.get_stats("events")
        assert stats["uncompressed_size"] == 10

    @pytest.mark.asyncio
    async def test_tuple_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(
            monkeypatch,
            (0, [(10, 5, 2.0, 4, 2)]),
        )
        stats = await CompressionManager.get_stats("events")
        assert stats == {
            "uncompressed_size": 10,
            "compressed_size": 5,
            "compression_ratio": 2.0,
            "uncompressed_chunks": 4,
            "compressed_chunks": 2,
        }

    @pytest.mark.asyncio
    async def test_empty_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(monkeypatch, ([], 0))
        assert await CompressionManager.get_stats("events") == {}


class TestSqlHardening:
    """G7 — identifiers are quoted, literals escaped, private catalogs avoided."""

    def _recording_conn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> RecordingConn:
        conn = RecordingConn()
        holder = FakeConnections(conn)  # type: ignore[arg-type]
        monkeypatch.setattr("tortoise_extended.timescale.hypertable.connections", holder)
        monkeypatch.setattr("tortoise_extended.timescale.compression.connections", holder)
        monkeypatch.setattr("tortoise_extended.timescale.retention.connections", holder)
        monkeypatch.setattr(
            "tortoise_extended.timescale.continuous_aggregate.connections", holder
        )
        return conn

    @pytest.mark.asyncio
    async def test_hypertable_create_quotes_identifier_and_literal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = self._recording_conn(monkeypatch)
        await HypertableManager.create_hypertable(
            'events; DROP TABLE x',
            time_column="when",
            chunk_time_interval="7 days",
        )
        sql = conn.sqls[0]
        # create_hypertable takes the table name as a function-argument literal
        assert "'events; DROP TABLE x'" in sql
        assert "'7 days'" in sql
        # No raw unquoted interpolation of the crafty name
        assert "events; DROP TABLE x" not in sql.replace("'events; DROP TABLE x'", "")

    @pytest.mark.asyncio
    async def test_drop_hypertable_quotes_identifier(self, monkeypatch) -> None:
        conn = self._recording_conn(monkeypatch)
        await HypertableManager.drop_hypertable("weird name")
        assert '"weird name"' in conn.sqls[0]

    @pytest.mark.asyncio
    async def test_is_hypertable_uses_public_view(self, monkeypatch) -> None:
        conn = self._recording_conn(monkeypatch)
        await HypertableManager.is_hypertable("events")
        sql = conn.sqls[0]
        assert "timescaledb_information.hypertables" in sql
        assert "_timescaledb_catalog" not in sql
        assert "'events'" in sql

    @pytest.mark.asyncio
    async def test_add_dimension_quotes(self, monkeypatch) -> None:
        conn = self._recording_conn(monkeypatch)
        await HypertableManager.add_dimension(
            "events", "tenant_id", number_partitions=4
        )
        sql = conn.sqls[0]
        assert "'events'" in sql and "'tenant_id'" in sql

    @pytest.mark.asyncio
    async def test_show_chunks_quotes(self, monkeypatch) -> None:
        conn = self._recording_conn(monkeypatch)
        await HypertableManager.show_chunks(
            "events", start_time="2024-01-01", end_time="2024-02-01"
        )
        sql = conn.sqls[0]
        assert "'events'" in sql and "'2024-01-01'" in sql

    @pytest.mark.asyncio
    async def test_get_stats_uses_public_views_and_guards_division(
        self, monkeypatch
    ) -> None:
        conn = self._recording_conn(monkeypatch)
        await CompressionManager.get_stats("events")
        sql = conn.sqls[0]
        assert "timescaledb_information.chunks" in sql
        assert "_timescaledb_catalog" not in sql
        assert "after_compression_total_bytes > 0" in sql
        assert "hypertable_columnstore_stats" in sql
        assert "hypertable_compression_stats" not in sql

    @pytest.mark.asyncio
    async def test_compression_uses_2_18_columnstore_api(self, monkeypatch) -> None:
        """G13: all deprecated pre-2.18 compression functions are gone."""
        conn = self._recording_conn(monkeypatch)
        await CompressionManager.enable_compression("events")
        await CompressionManager.disable_compression("events")
        await CompressionManager.add_compression_policy("events", "1 day")
        await CompressionManager.remove_compression_policy("events")
        await CompressionManager.compress_chunk("_timescaledb_internal._c")
        await CompressionManager.decompress_chunk("_timescaledb_internal._c")
        joined = "\n".join(conn.sqls)
        assert "timescaledb.enable_columnstore" in joined
        assert "CALL add_columnstore_policy(" in joined
        assert "hypertable => 'events'" in joined
        assert "after => INTERVAL '1 day'" in joined
        assert "CALL remove_columnstore_policy('events')" in joined
        assert "CALL convert_to_columnstore(" in joined
        assert "CALL convert_to_rowstore(" in joined
        for legacy in (
            "timescaledb.compress",
            "add_compression_policy",
            "remove_compression_policy",
            "compress_chunk(",
            "decompress_chunk(",
        ):
            assert legacy not in joined, f"legacy API still present: {legacy}"

    @pytest.mark.asyncio
    async def test_retention_list_uses_public_view(self, monkeypatch) -> None:
        conn = self._recording_conn(monkeypatch)
        await RetentionPolicy.list_policies()
        sql = conn.sqls[0]
        assert "timescaledb_information.jobs" in sql
        assert "_timescaledb_catalog" not in sql
        assert "_timescaledb_config" not in sql

    @pytest.mark.asyncio
    async def test_retention_set_quotes(self, monkeypatch) -> None:
        conn = self._recording_conn(monkeypatch)
        await RetentionPolicy.set_retention("events", drop_after="90 days")
        sql = conn.sqls[0]
        assert "'events'" in sql and "'90 days'" in sql

    @pytest.mark.asyncio
    async def test_cagg_quotes_identifiers(self, monkeypatch) -> None:
        conn = self._recording_conn(monkeypatch)
        await ContinuousAggregateManager.create(
            "daily_events",
            "events",
            "SELECT 1 FROM events",
        )
        assert '"daily_events"' in conn.sqls[0]
        await ContinuousAggregateManager.drop("daily_events")
        assert '"daily_events"' in conn.sqls[1]
        await ContinuousAggregateManager.set_refresh_policy(
            "daily_events", start_offset="1 week"
        )
        sql = conn.sqls[2]
        assert "'daily_events'" in sql and "'1 week'" in sql
