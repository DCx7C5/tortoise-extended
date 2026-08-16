"""Unit tests for timescale manager helpers with a fake connection.

Exercises the tuple-row and mapping-row result branches of
``HypertableManager.is_hypertable`` and ``CompressionManager.get_stats``,
schema-qualified identifiers, explicit ``using_db`` connections, query
validation and ``TimescaleError`` wrapping — without a live TimescaleDB.
Uses monkeypatch to swap the module-level ``connections`` lookup.
"""

from collections.abc import Sequence
from datetime import timedelta
from typing import TypeAlias

import pytest

from tortoise_extended._types import RowMapping, RowValue
from tortoise_extended.exceptions import TimescaleError
from tortoise_extended.timescale.compression import CompressionManager
from tortoise_extended.timescale.continuous_aggregate import ContinuousAggregateManager
from tortoise_extended.timescale.hypertable import HypertableManager
from tortoise_extended.timescale.retention import RetentionPolicy
from tortoise_extended.timescale.stream import _bucket_to_timedelta


QueryResult: TypeAlias = (
    tuple[int, Sequence[RowMapping | tuple[RowValue, ...]]]
    | tuple[Sequence[RowMapping | tuple[RowValue, ...]], int]
    | Sequence[RowMapping | tuple[RowValue, ...]]
)
"""Canned ``execute_query`` shapes the timescale managers tolerate."""


class FakeConn:
    """Returns a canned ``execute_query`` result."""

    def __init__(self, result: QueryResult) -> None:
        self.result = result

    async def execute_query(self, _sql: str, *_args: RowValue) -> QueryResult:
        return self.result


class ErrorConn:
    """Raises on every ``execute_query`` call."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def execute_query(self, _sql: str, *_args: RowValue) -> QueryResult:
        raise self.exc


class FakeConnections:
    """Stand-in for the module-level ``connections`` singleton."""

    def __init__(
        self, conn: FakeConn | ErrorConn | RecordingConn
    ) -> None:
        self.conn = conn

    def get(self, name: str) -> FakeConn | ErrorConn | RecordingConn:
        return self.conn


def _patch_connections(
    monkeypatch: pytest.MonkeyPatch, result: QueryResult
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


def _patch_error_connections(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> ErrorConn:
    conn = ErrorConn(exc)
    holder = FakeConnections(conn)
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

    async def execute_query(
        self, sql: str, *_args: RowValue
    ) -> tuple[int, Sequence[RowMapping]]:
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

    @pytest.mark.asyncio
    async def test_schema_qualified_matches_both(self, monkeypatch) -> None:
        """A schema-qualified name must match schema AND table columns."""
        conn = RecordingConn()
        holder = FakeConnections(conn)
        monkeypatch.setattr(
            "tortoise_extended.timescale.hypertable.connections", holder
        )
        await HypertableManager.is_hypertable("metrics.events")
        sql = conn.sqls[0]
        assert "hypertable_schema = 'metrics'" in sql
        assert "hypertable_name = 'events'" in sql

    @pytest.mark.asyncio
    async def test_unqualified_ignores_schema(self, monkeypatch) -> None:
        """An unqualified name must not filter on schema at all."""
        conn = RecordingConn()
        holder = FakeConnections(conn)
        monkeypatch.setattr(
            "tortoise_extended.timescale.hypertable.connections", holder
        )
        await HypertableManager.is_hypertable("events")
        sql = conn.sqls[0]
        assert "hypertable_schema" not in sql
        assert "hypertable_name = 'events'" in sql


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

    @pytest.mark.asyncio
    async def test_tuple_row_none_ratio(self, monkeypatch) -> None:
        """A NULL compression ratio stays None (no float() on None)."""
        _patch_connections(monkeypatch, (0, [(10, 5, None, 4, 2)]))
        stats = await CompressionManager.get_stats("events")
        assert stats["compression_ratio"] is None

    @pytest.mark.asyncio
    async def test_mapping_row_decimal_ratio_cast(self, monkeypatch) -> None:
        """numeric ROUND() values from a mapping row are exposed as float."""
        from decimal import Decimal

        _patch_connections(
            monkeypatch,
            (0, [{"compression_ratio": Decimal("2.00"), "uncompressed_size": 10}]),
        )
        stats = await CompressionManager.get_stats("events")
        assert stats["compression_ratio"] == 2.0
        assert isinstance(stats["compression_ratio"], float)


class TestSqlHardening:
    """G7 — identifiers are quoted, literals escaped, private catalogs avoided."""

    def _recording_conn(self, monkeypatch: pytest.MonkeyPatch) -> RecordingConn:
        conn = RecordingConn()
        holder = FakeConnections(conn)
        monkeypatch.setattr(
            "tortoise_extended.timescale.hypertable.connections", holder
        )
        monkeypatch.setattr(
            "tortoise_extended.timescale.compression.connections", holder
        )
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
            "events; DROP TABLE x",
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
    async def test_cagg_query_validated(self, monkeypatch) -> None:
        """G23 — create() rejects non-SELECT / multi-statement queries."""
        conn = self._recording_conn(monkeypatch)
        with pytest.raises(ValueError, match="single bare SELECT"):
            await ContinuousAggregateManager.create("v", "events", "DROP TABLE events")
        with pytest.raises(ValueError, match="single bare SELECT"):
            await ContinuousAggregateManager.create(
                "v", "events", "SELECT 1; DROP TABLE x"
            )
        assert conn.sqls == []

    @pytest.mark.asyncio
    async def test_cagg_query_allows_cte_and_select(self, monkeypatch) -> None:
        """CTEs and plain SELECTs are accepted and wrapped."""
        conn = self._recording_conn(monkeypatch)
        await ContinuousAggregateManager.create(
            "v",
            "events",
            "WITH base AS (SELECT 1) SELECT time_bucket('1 day', created_at) FROM events",
        )
        await ContinuousAggregateManager.create(
            "v2",
            "events",
            "SELECT time_bucket('1 day', created_at) FROM events",
        )
        assert len(conn.sqls) == 2
        assert "CREATE MATERIALIZED VIEW" in conn.sqls[0]
        assert "CREATE MATERIALIZED VIEW" in conn.sqls[1]

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

    @pytest.mark.asyncio
    async def test_cagg_rejects_literal_create_materialized_view(
        self, monkeypatch
    ) -> None:
        """A SELECT containing the literal 'CREATE MATERIALIZED VIEW' is
        rejected — the old prefix auto-detection silently bypassed validation."""
        conn = self._recording_conn(monkeypatch)
        with pytest.raises(ValueError, match="single bare SELECT"):
            await ContinuousAggregateManager.create(
                "v",
                "events",
                "CREATE MATERIALIZED VIEW x AS SELECT 1 FROM events",
            )
        assert conn.sqls == []

    @pytest.mark.asyncio
    async def test_cagg_rejects_unreferenced_source_table(self, monkeypatch) -> None:
        conn = self._recording_conn(monkeypatch)
        with pytest.raises(ValueError, match="must reference the source table"):
            await ContinuousAggregateManager.create(
                "v",
                "events",
                "SELECT 1 FROM other_table",
            )
        assert conn.sqls == []

    @pytest.mark.asyncio
    async def test_cagg_allow_full_statement_passthrough(self, monkeypatch) -> None:
        """allow_full_statement=True sends the query verbatim, unvalidated."""
        conn = self._recording_conn(monkeypatch)
        await ContinuousAggregateManager.create(
            "v",
            "events",
            "CREATE MATERIALIZED VIEW v WITH (timescaledb.continuous) "
            "AS SELECT 1 FROM events",
            allow_full_statement=True,
        )
        assert len(conn.sqls) == 1
        assert conn.sqls[0] == (
            "CREATE MATERIALIZED VIEW v WITH (timescaledb.continuous) "
            "AS SELECT 1 FROM events"
        )
        # No wrapping, no quoting of the view name
        assert "CREATE MATERIALIZED VIEW \"v\"" not in conn.sqls[0]

    @pytest.mark.asyncio
    async def test_drop_hypertable_schema_qualified(self, monkeypatch) -> None:
        """Schema-qualified names quote both parts separately."""
        conn = self._recording_conn(monkeypatch)
        await HypertableManager.drop_hypertable("metrics.events")
        assert '"metrics"."events"' in conn.sqls[0]
        # Not quoted as a single broken identifier
        assert '"metrics.events"' not in conn.sqls[0]

    @pytest.mark.asyncio
    async def test_list_hypertables_quotes_extension_schema(self, monkeypatch) -> None:
        conn = self._recording_conn(monkeypatch)
        await HypertableManager.list_hypertables(extension_schema="metrics")
        sql = conn.sqls[0]
        assert '"metrics".hypertable_size(' in sql
        assert '"public".hypertable_size(' not in sql


class TestUsingDb:
    """Explicit ``using_db`` connections are used instead of ``default``."""

    @pytest.mark.asyncio
    async def test_hypertable_uses_explicit_connection(self) -> None:
        conn = RecordingConn()
        await HypertableManager.create_hypertable("events", using_db=conn)
        await HypertableManager.is_hypertable("events", using_db=conn)
        await HypertableManager.list_hypertables(using_db=conn)
        await HypertableManager.add_dimension(
            "events", "tenant_id", number_partitions=4, using_db=conn
        )
        await HypertableManager.show_chunks("events", using_db=conn)
        assert len(conn.sqls) == 5

    @pytest.mark.asyncio
    async def test_compression_uses_explicit_connection(self) -> None:
        conn = RecordingConn()
        await CompressionManager.get_stats("events", using_db=conn)
        await CompressionManager.enable_compression("events", using_db=conn)
        await CompressionManager.disable_compression("events", using_db=conn)
        assert len(conn.sqls) == 3

    @pytest.mark.asyncio
    async def test_retention_uses_explicit_connection(self) -> None:
        conn = RecordingConn()
        await RetentionPolicy.set_retention("events", using_db=conn)
        await RetentionPolicy.list_policies(using_db=conn)
        assert len(conn.sqls) == 2

    @pytest.mark.asyncio
    async def test_cagg_uses_explicit_connection(self) -> None:
        conn = RecordingConn()
        await ContinuousAggregateManager.create(
            "v", "events", "SELECT 1 FROM events", using_db=conn
        )
        await ContinuousAggregateManager.refresh("v", using_db=conn)
        await ContinuousAggregateManager.set_refresh_policy("v", using_db=conn)
        assert len(conn.sqls) == 3


class TestTimescaleErrorWrapping:
    """Driver failures are surfaced as TimescaleError (G8/G18/G20/G22)."""

    @pytest.mark.asyncio
    async def test_create_hypertable(self, monkeypatch) -> None:
        _patch_error_connections(monkeypatch, RuntimeError("boom"))
        with pytest.raises(TimescaleError, match="create hypertable"):
            await HypertableManager.create_hypertable("events")

    @pytest.mark.asyncio
    async def test_list_hypertables(self, monkeypatch) -> None:
        _patch_error_connections(monkeypatch, RuntimeError("boom"))
        with pytest.raises(TimescaleError, match="list hypertables"):
            await HypertableManager.list_hypertables()

    @pytest.mark.asyncio
    async def test_compression_enable(self, monkeypatch) -> None:
        _patch_error_connections(monkeypatch, RuntimeError("boom"))
        with pytest.raises(TimescaleError, match="enable compression"):
            await CompressionManager.enable_compression("events")

    @pytest.mark.asyncio
    async def test_compression_stats(self, monkeypatch) -> None:
        _patch_error_connections(monkeypatch, RuntimeError("boom"))
        with pytest.raises(TimescaleError, match="compression stats"):
            await CompressionManager.get_stats("events")

    @pytest.mark.asyncio
    async def test_retention_set(self, monkeypatch) -> None:
        _patch_error_connections(monkeypatch, RuntimeError("boom"))
        with pytest.raises(TimescaleError, match="retention"):
            await RetentionPolicy.set_retention("events", drop_after="90 days")

    @pytest.mark.asyncio
    async def test_cagg_create(self, monkeypatch) -> None:
        _patch_error_connections(monkeypatch, RuntimeError("boom"))
        with pytest.raises(TimescaleError, match="continuous aggregate"):
            await ContinuousAggregateManager.create(
                "v", "events", "SELECT 1 FROM events"
            )


class TestBucketToTimedelta:
    """_bucket_to_timedelta rejects non-positive counts (G19)."""

    def test_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            _bucket_to_timedelta("0 hour")

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            _bucket_to_timedelta("-3 day")

    def test_positive_ok(self) -> None:
        assert _bucket_to_timedelta("2 hour") == timedelta(hours=2)
