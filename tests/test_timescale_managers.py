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

import msgspec
import pytest

from tortoise_extended._types import RowMapping, RowValue
from tortoise_extended.exceptions import TimescaleError
from tortoise_extended.timescale.compression import CompressionManager
from tortoise_extended.timescale.continuous_aggregate import (
    ContinuousAggregateManager,
    validate_cagg_query,
)
from tortoise_extended.timescale.hypertable import HypertableManager
from tortoise_extended.timescale.retention import RetentionPolicy, RetentionPolicyInfo
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

    def __init__(self, conn: FakeConn | ErrorConn | RecordingConn) -> None:
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


class TestListHypertables:
    """HypertableManager.list_hypertables() result-shape branches."""

    @pytest.mark.asyncio
    async def test_mapping_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(
            monkeypatch,
            (
                0,
                [
                    {
                        "table_name": "public.events",
                        "num_chunks": 3,
                        "compression_enabled": False,
                        "table_size": "8192 kB",
                    }
                ],
            ),
        )
        hypertables = await HypertableManager.list_hypertables()
        assert hypertables[0].table_name == "public.events"
        assert hypertables[0].num_chunks == 3
        assert hypertables[0].compression_enabled is False
        assert hypertables[0].table_size == "8192 kB"

    @pytest.mark.asyncio
    async def test_tuple_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(
            monkeypatch,
            (0, [("public.events", 3, False, "8192 kB")]),
        )
        hypertables = await HypertableManager.list_hypertables()
        assert hypertables[0].table_name == "public.events"
        assert hypertables[0].num_chunks == 3
        assert hypertables[0].compression_enabled is False
        assert hypertables[0].table_size == "8192 kB"


class TestListPolicies:
    """RetentionPolicy.list_policies() result-shape branches."""

    @pytest.mark.asyncio
    async def test_mapping_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(
            monkeypatch,
            (
                0,
                [
                    {
                        "table_name": "events",
                        "drop_after": {"drop_after": "90 days"},
                        "schedule_interval": "1 day",
                        "initial_start": "2026-01-01 00:00:00+00",
                        "scheduled": True,
                    }
                ],
            ),
        )
        policies = await RetentionPolicy.list_policies()
        assert policies[0].table_name == "events"
        assert policies[0].drop_after == {"drop_after": "90 days"}
        assert policies[0].schedule_interval == "1 day"
        assert policies[0].initial_start == "2026-01-01 00:00:00+00"
        assert policies[0].scheduled is True

    @pytest.mark.asyncio
    async def test_tuple_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(
            monkeypatch,
            (0, [("events", {"drop_after": "90 days"}, "1 day", None, False)]),
        )
        policies = await RetentionPolicy.list_policies()
        assert policies[0].table_name == "events"
        assert policies[0].drop_after == {"drop_after": "90 days"}
        assert policies[0].schedule_interval == "1 day"
        assert policies[0].initial_start is None
        assert policies[0].scheduled is False


class TestRetentionPolicyInfoDecode:
    """``RetentionPolicyInfo`` stays a decodable ``msgspec.Struct``.

    Regression guard: ``drop_after`` must not widen back to ``RowValue`` —
    ``bytes`` in that union makes msgspec reject the struct outright
    ("Type unions may not contain more than one str-like type").
    """

    def test_json_decode(self) -> None:
        payload = (
            b'{"table_name": "events",'
            b' "drop_after": {"drop_after": "90 days"},'
            b' "schedule_interval": null,'
            b' "initial_start": null,'
            b' "scheduled": true}'
        )
        info = msgspec.json.decode(payload, type=RetentionPolicyInfo)
        assert info.table_name == "events"
        assert info.drop_after == {"drop_after": "90 days"}
        assert info.schedule_interval is None
        assert info.initial_start is None
        assert info.scheduled is True

    def test_encode_decode_round_trip(self) -> None:
        info = RetentionPolicyInfo(
            table_name="events",
            drop_after={"drop_after": "90 days"},
            schedule_interval="1 day",
            initial_start=None,
            scheduled=False,
        )
        decoded = msgspec.json.decode(
            msgspec.json.encode(info), type=RetentionPolicyInfo
        )
        assert decoded == info
        assert decoded.drop_after == {"drop_after": "90 days"}


class TestListContinuousAggregates:
    """ContinuousAggregateManager.list() result-shape branches."""

    @pytest.mark.asyncio
    async def test_mapping_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(
            monkeypatch,
            (
                0,
                [
                    {
                        "view_name": "daily_events",
                        "view_definition": "SELECT time_bucket(...) AS bucket ...",
                    }
                ],
            ),
        )
        aggs = await ContinuousAggregateManager.list()
        assert aggs[0].view_name == "daily_events"
        assert aggs[0].view_definition == "SELECT time_bucket(...) AS bucket ..."

    @pytest.mark.asyncio
    async def test_tuple_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(
            monkeypatch,
            (0, [("daily_events", "SELECT 1 AS bucket")]),
        )
        aggs = await ContinuousAggregateManager.list()
        assert aggs[0].view_name == "daily_events"
        assert aggs[0].view_definition == "SELECT 1 AS bucket"


class TestHypertableTimeColumnValidation:
    """Q3-HARDEN — create_hypertable() pre-validates the time column is in a
    PK/unique index (TimescaleDB requirement) before attempting creation."""

    @pytest.mark.asyncio
    async def test_covered_mapping_row_proceeds(self, monkeypatch) -> None:
        """A covered time column (mapping row) does not raise and creates."""
        conn = CannedRecordingConn((0, [{"is_covered": True}]))
        holder = FakeConnections(conn)
        monkeypatch.setattr(
            "tortoise_extended.timescale.hypertable.connections", holder
        )
        await HypertableManager.create_hypertable("events", time_column="created_at")
        # introspection (covered=True) then the create
        assert len(conn.sqls) == 2
        assert "pg_index" in conn.sqls[0]
        assert "create_hypertable(" in conn.sqls[1]

    @pytest.mark.asyncio
    async def test_covered_tuple_row_proceeds(self, monkeypatch) -> None:
        """A covered time column (tuple row) does not raise and creates."""
        conn = CannedRecordingConn((0, [(True,)]))
        holder = FakeConnections(conn)
        monkeypatch.setattr(
            "tortoise_extended.timescale.hypertable.connections", holder
        )
        await HypertableManager.create_hypertable("events", time_column="created_at")
        # introspection (covered=True) then the create
        assert len(conn.sqls) == 2
        assert "pg_index" in conn.sqls[0]
        assert "create_hypertable(" in conn.sqls[1]

    @pytest.mark.asyncio
    async def test_uncovered_raises_before_create(self, monkeypatch) -> None:
        """An uncovered time column raises TimescaleError before the create."""
        conn = CannedRecordingConn((0, [{"is_covered": False}]))
        holder = FakeConnections(conn)
        monkeypatch.setattr(
            "tortoise_extended.timescale.hypertable.connections", holder
        )
        with pytest.raises(TimescaleError, match="PRIMARY KEY or a UNIQUE index"):
            await HypertableManager.create_hypertable(
                "events", time_column="created_at"
            )
        # No CREATE was issued — the error surfaced before the create call.
        assert all("pg_index" in s for s in conn.sqls)
        assert not any("create_hypertable(" in s for s in conn.sqls)

    @pytest.mark.asyncio
    async def test_introspection_failure_swallowed(self, monkeypatch) -> None:
        """If introspection itself errors, the create is still attempted."""
        conn = ErrorConn(RuntimeError("introspection boom"))
        holder = FakeConnections(conn)
        monkeypatch.setattr(
            "tortoise_extended.timescale.hypertable.connections", holder
        )
        # ErrorConn raises on the introspection; the create then also raises and
        # is wrapped as TimescaleError — proving the real error surfaced.
        with pytest.raises(TimescaleError, match="create hypertable"):
            await HypertableManager.create_hypertable("events")

    @pytest.mark.asyncio
    async def test_opt_out_skips_introspection(self, monkeypatch) -> None:
        """validate_time_column=False records only the create statement."""
        conn = RecordingConn()
        holder = FakeConnections(conn)
        monkeypatch.setattr(
            "tortoise_extended.timescale.hypertable.connections", holder
        )
        await HypertableManager.create_hypertable("events", validate_time_column=False)
        assert len(conn.sqls) == 1
        assert "create_hypertable(" in conn.sqls[0]
        assert "pg_index" not in conn.sqls[0]

    @pytest.mark.asyncio
    async def test_introspection_quotes_identifiers(self, monkeypatch) -> None:
        """The introspection query quotes the table/column literals safely."""
        conn = CannedRecordingConn((0, [{"is_covered": True}]))
        holder = FakeConnections(conn)
        monkeypatch.setattr(
            "tortoise_extended.timescale.hypertable.connections", holder
        )
        await HypertableManager.create_hypertable("metrics.events", time_column="when")
        intro = conn.sqls[0]
        assert "pg_index" in intro
        assert "'events'" in intro
        assert "'metrics'" in intro or "current_schemas" in intro
        assert "'when'" in intro
        assert "i.indisunique" in intro


class CannedRecordingConn(RecordingConn):
    """RecordingConn whose ``execute_query`` returns a fixed canned result.

    Combines SQL recording (for introspection inspection) with a deterministic
    ``execute_query`` return value (for the covered/uncovered branches).
    """

    def __init__(self, result: QueryResult) -> None:
        super().__init__()
        self.result = result

    async def execute_query(self, sql: str, *_args: RowValue) -> QueryResult:
        self.sqls.append(sql)
        return self.result


class TestGetCompressionStats:
    """CompressionManager.get_stats() result-shape branches."""

    @pytest.mark.asyncio
    async def test_mapping_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(
            monkeypatch,
            (
                0,
                [
                    {
                        "uncompressed_size": 10,
                        "compressed_size": 5,
                        "compression_ratio": 2.0,
                        "uncompressed_chunks": 4,
                        "compressed_chunks": 2,
                    }
                ],
            ),
        )
        stats = await CompressionManager.get_stats("events")
        assert stats.uncompressed_size == 10
        assert stats.compressed_size == 5
        assert stats.compression_ratio == 2.0
        assert stats.uncompressed_chunks == 4
        assert stats.compressed_chunks == 2

    @pytest.mark.asyncio
    async def test_tuple_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(
            monkeypatch,
            (0, [(10, 5, 2.0, 4, 2)]),
        )
        stats = await CompressionManager.get_stats("events")
        assert stats.uncompressed_size == 10
        assert stats.compressed_size == 5
        assert stats.compression_ratio == 2.0
        assert stats.uncompressed_chunks == 4
        assert stats.compressed_chunks == 2

    @pytest.mark.asyncio
    async def test_empty_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connections(monkeypatch, ([], 0))
        stats = await CompressionManager.get_stats("events")
        assert stats.compression_ratio == 1.0
        assert stats.uncompressed_chunks == 0
        assert stats.compressed_chunks == 0

    @pytest.mark.asyncio
    async def test_tuple_row_none_ratio(self, monkeypatch) -> None:
        """A NULL compression ratio falls back to 1.0 (float() on None)."""
        _patch_connections(monkeypatch, (0, [(10, 5, None, 4, 2)]))
        stats = await CompressionManager.get_stats("events")
        assert stats.compression_ratio == 1.0

    @pytest.mark.asyncio
    async def test_mapping_row_decimal_ratio_cast(self, monkeypatch) -> None:
        """numeric ROUND() values from a mapping row are exposed as float."""
        from decimal import Decimal

        _patch_connections(
            monkeypatch,
            (
                0,
                [
                    {
                        "uncompressed_size": 10,
                        "compressed_size": 5,
                        "compression_ratio": Decimal("2.00"),
                        "uncompressed_chunks": 4,
                        "compressed_chunks": 2,
                    }
                ],
            ),
        )
        stats = await CompressionManager.get_stats("events")
        assert stats.compression_ratio == 2.0
        assert isinstance(stats.compression_ratio, float)


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
            validate_time_column=False,
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
        # The policy-job cleanup runs first; the DROP is the last statement.
        assert "delete_job(job_id)" in conn.sqls[0]
        assert '"weird name"' in conn.sqls[-1]

    @pytest.mark.asyncio
    async def test_drop_hypertable_clears_policy_jobs_first(self, monkeypatch) -> None:
        """Policy jobs are scheduler rows, not constraints — delete them first."""
        conn = self._recording_conn(monkeypatch)
        await HypertableManager.drop_hypertable("metrics.events")
        cleanup = conn.sqls[0]
        assert "delete_job(job_id)" in cleanup
        assert "hypertable_name = 'events'" in cleanup
        assert "hypertable_schema = 'metrics'" in cleanup
        # Bare name matches every schema instead of guessing one.
        conn2 = self._recording_conn(monkeypatch)
        await HypertableManager.drop_hypertable("events")
        assert "hypertable_schema" not in conn2.sqls[0]
        assert "hypertable_name = 'events'" in conn2.sqls[0]

    @pytest.mark.asyncio
    async def test_error_message_is_single_line(self, monkeypatch) -> None:
        """A newline echoed from PostgreSQL cannot forge extra log lines."""
        _patch_error_connections(
            monkeypatch,
            Exception('relation "a\nb" does not exist\r\nDETAIL: forged line'),
        )
        with pytest.raises(TimescaleError) as excinfo:
            await ContinuousAggregateManager.refresh("a\nb")
        message = str(excinfo.value)
        assert "\n" not in message
        assert "\r" not in message
        # The text itself is preserved — only the line breaks are flattened.
        assert "DETAIL: forged line" in message

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
    async def test_add_dimension_coerces_partition_count(self, monkeypatch) -> None:
        """An untyped caller's string is coerced to an int, not interpolated."""
        conn = self._recording_conn(monkeypatch)
        await HypertableManager.add_dimension(
            "events",
            "tenant_id",
            number_partitions="4",
        )
        assert "number_partitions => 4" in conn.sqls[0]

    @pytest.mark.asyncio
    async def test_add_dimension_rejects_non_numeric_partition_count(
        self, monkeypatch
    ) -> None:
        """A non-numeric count raises before any SQL reaches the server."""
        conn = self._recording_conn(monkeypatch)
        with pytest.raises(ValueError):
            await HypertableManager.add_dimension(
                "events",
                "tenant_id",
                number_partitions="4); DROP TABLE x",
            )
        assert conn.sqls == []

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
    async def test_cagg_comment_injection_never_reaches_connection(
        self, monkeypatch
    ) -> None:
        """The comment-quote payload is rejected before any SQL is sent."""
        conn = self._recording_conn(monkeypatch)
        payload = (
            "SELECT time_bucket('1 day', ts) AS b, count(*) FROM events "
            "GROUP BY b /* ' */ ) ; DROP TABLE x; "
            "CREATE MATERIALIZED VIEW x AS (SELECT 1 /* ' */"
        )
        with pytest.raises(ValueError):
            await ContinuousAggregateManager.create("v", "events", payload)
        assert conn.sqls == []

    def test_cagg_validate_semicolon_inside_literal_is_allowed(self) -> None:
        """A ``;`` inside a string literal is NOT a multi-statement separator.

        Regression for a validation false-positive: ``SELECT 'a;b' ...`` was
        rejected because the ``;`` scan ran on the raw query before string
        literals were stripped. It is a valid bare SELECT and must pass.
        """
        validate_cagg_query(
            "SELECT 'a;b' AS label, COUNT(*) FROM events GROUP BY label",
            "events",
        )

    def test_cagg_validate_escaped_quote_inside_literal(self) -> None:
        """Doubled single-quotes (``''``) inside a literal don't hide a ``;``.

        ``'a;''b'`` (an escaped quote inside the literal) must not leave a
        stray ``;`` in the literal-stripped text.
        """
        validate_cagg_query("SELECT 'a;''b' AS x FROM events", "events")

    def test_cagg_validate_multi_statement_still_rejected(self) -> None:
        """A genuine multi-statement query still raises."""
        with pytest.raises(ValueError, match="single bare SELECT"):
            validate_cagg_query("SELECT 1; SELECT 2")

    def test_cagg_validate_mismatched_source_rejected(self) -> None:
        """The source-table cross-check still rejects a mismatched source."""
        with pytest.raises(ValueError, match="must reference the source table"):
            validate_cagg_query(
                "SELECT 'a;b' AS label, COUNT(*) FROM other_table GROUP BY label",
                "events",
            )

    def test_cagg_validate_comment_quote_hides_no_separator(self) -> None:
        """2026-11-22 HIGH: quotes inside ``/* */`` no longer hide a ``;``.

        The pre-fix literal-stripping regex paired the two quotes that sit
        inside the comments, deleting the span between them — including the
        ``;`` — so this two-statement payload passed validation and was
        interpolated into ``CREATE MATERIALIZED VIEW ... AS ({query})``.
        """
        with pytest.raises(ValueError, match="single bare SELECT"):
            validate_cagg_query(
                "SELECT 1 FROM events /* ' */ ; SELECT 2 /* ' */",
                "events",
            )

    def test_cagg_validate_reproduced_payload_rejected(self) -> None:
        """The reproduced payload never parses as a single statement."""
        payload = (
            "SELECT time_bucket('1 day', ts) AS b, count(*) FROM events "
            "GROUP BY b /* ' */ ) ; DROP TABLE x; "
            "CREATE MATERIALIZED VIEW x AS (SELECT 1 /* ' */"
        )
        with pytest.raises(ValueError, match="not valid SQL"):
            validate_cagg_query(payload, "events")

    def test_cagg_validate_source_only_inside_comment_rejected(self) -> None:
        """A declared source that appears only in a comment is not a source.

        Same root cause as the injection: the old cross-check ran on
        comment-stripped text, so any table could be aggregated as long as the
        declared name was mentioned in a comment.
        """
        with pytest.raises(ValueError, match="must reference the source table"):
            validate_cagg_query(
                "SELECT time_bucket('1 day', ts) AS b, count(*) "
                "FROM secret /* events */ GROUP BY b",
                "events",
            )

    def test_cagg_validate_cte_named_after_source_rejected(self) -> None:
        """A CTE reference is not a table reference."""
        with pytest.raises(ValueError, match="must reference the source table"):
            validate_cagg_query(
                "WITH events AS (SELECT 1 AS ts) "
                "SELECT time_bucket('1 day', ts) AS b FROM events",
                "events",
            )

    def test_cagg_validate_trailing_semicolon_stripped(self) -> None:
        """A single trailing ``;`` is one statement — accepted and normalized."""
        assert (
            validate_cagg_query("SELECT 1 FROM events;", "events")
            == "SELECT 1 FROM events"
        )
        assert (
            validate_cagg_query("  SELECT 1 FROM events ;  ", "events")
            == "SELECT 1 FROM events"
        )

    def test_cagg_validate_invalid_sql_raises_value_error(self) -> None:
        """A parse failure surfaces as ``ValueError``, not ``pglast.Error``."""
        with pytest.raises(ValueError, match="not valid SQL"):
            validate_cagg_query("SELECT FROM WHERE", "events")

    def test_cagg_validate_keyword_forms_rejected(self) -> None:
        """``TABLE``/``VALUES`` parse as ``SelectStmt``; the keyword gate holds."""
        for query in ("TABLE events", "VALUES (1)"):
            with pytest.raises(ValueError, match="single bare SELECT"):
                validate_cagg_query(query, "events")

    def test_cagg_validate_qualified_source_requires_matching_schema(self) -> None:
        """A schema-qualified source must match schema and relation."""
        with pytest.raises(ValueError, match="must reference the source table"):
            validate_cagg_query("SELECT 1 FROM events", "metrics.events")
        assert (
            validate_cagg_query("SELECT 1 FROM metrics.events", "metrics.events")
            == "SELECT 1 FROM metrics.events"
        )

    def test_cagg_validate_unqualified_source_matches_qualified_reference(
        self,
    ) -> None:
        """An unqualified source matches the relation name alone."""
        assert validate_cagg_query("SELECT 1 FROM metrics.events", "events")

    def test_cagg_validate_leading_comment_does_not_mask_keyword(self) -> None:
        """The leading keyword comes from the lexer, so a comment cannot hide it."""
        assert (
            validate_cagg_query("/* note */ SELECT 1 FROM events", "events")
            == "SELECT 1 FROM events"
        )

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
        assert 'CREATE MATERIALIZED VIEW "v"' not in conn.sqls[0]

    @pytest.mark.asyncio
    async def test_cagg_allow_full_statement_rejects_empty_query(
        self, monkeypatch
    ) -> None:
        """SEC-M04: empty query must be rejected even when allow_full_statement=True."""
        with pytest.raises(ValueError, match="must not be empty"):
            await ContinuousAggregateManager.create(
                "v",
                "events",
                "   ",
                allow_full_statement=True,
            )

    @pytest.mark.asyncio
    async def test_drop_hypertable_schema_qualified(self, monkeypatch) -> None:
        """Schema-qualified names quote both parts separately."""
        conn = self._recording_conn(monkeypatch)
        await HypertableManager.drop_hypertable("metrics.events")
        drop_sql = conn.sqls[-1]
        assert '"metrics"."events"' in drop_sql
        # Not quoted as a single broken identifier
        assert '"metrics.events"' not in drop_sql

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
        await HypertableManager.create_hypertable(
            "events", using_db=conn, validate_time_column=False
        )
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


# ---------------------------------------------------------------------------
# Source-table case folding (row 3008)
# ---------------------------------------------------------------------------


class TestCaggSourceCaseFolding:
    """A declared mixed-case ``source_table`` must still match its query.

    ``referenced_relations`` folds unquoted identifiers to lowercase, while a
    declared ``source_table`` is a plain string.  Both readings must be tried,
    or a declaration like ``"Events"`` regresses against ``FROM Events``.
    """

    def test_declared_mixed_case_matches_unquoted_reference(self) -> None:
        assert (
            validate_cagg_query("SELECT 1 FROM Events", "Events")
            == "SELECT 1 FROM Events"
        )

    def test_declared_mixed_case_matches_quoted_reference(self) -> None:
        assert (
            validate_cagg_query('SELECT 1 FROM "Events"', "Events")
            == 'SELECT 1 FROM "Events"'
        )

    def test_declared_lowercase_does_not_match_quoted_mixed_case(self) -> None:
        with pytest.raises(ValueError, match="must reference the source table"):
            validate_cagg_query('SELECT 1 FROM "Events"', "events")

    def test_schema_qualified_mixed_case_folds(self) -> None:
        assert (
            validate_cagg_query("SELECT 1 FROM Metrics.Events", "Metrics.Events")
            == "SELECT 1 FROM Metrics.Events"
        )
