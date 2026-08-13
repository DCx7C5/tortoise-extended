"""Integration tests for the TimescaleDB runtime managers.

Requires: the docker ``postgres-ext`` container (PostgreSQL 18 + TimescaleDB)
exposed on ``127.0.0.1:5433``.

Run with: uv run pytest tests/test_timescale_integration.py -v
"""

import datetime
import os
import socket
from collections.abc import AsyncGenerator

import pytest
from tortoise import Tortoise, fields
from tortoise.exceptions import OperationalError
from tortoise.models import Model

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.models import BaseEventStreamModel
from tortoise_extended.timescale import (
    CompressionManager,
    ContinuousAggregateManager,
    HypertableManager,
    RetentionPolicy,
    TimeBucketRow,
)

# ---------------------------------------------------------------------------
# Config — skip entire module if TimescaleDB PG is not available
# ---------------------------------------------------------------------------

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5433/tortoise_test",
)

EVENTS_DDL = """
    CREATE TABLE test_events (
        id BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        value DOUBLE PRECISION NOT NULL,
        tenant_id INT NOT NULL,
        PRIMARY KEY (id, created_at, tenant_id)
    )
"""

AGG_QUERY = (
    "SELECT time_bucket('1 day', created_at) AS bucket, "
    "COUNT(*) AS count FROM test_events GROUP BY bucket"
)

AGG_FULL = """
    CREATE MATERIALIZED VIEW test_daily_events2
    WITH (timescaledb.continuous)
    AS (SELECT time_bucket('1 day', created_at) AS bucket, COUNT(*) AS c
        FROM test_events GROUP BY bucket)
    WITH NO DATA
"""

# TimescaleDB requires the hypertable partition column to be part of the
# primary key / every unique index. Tortoise models only support single-column
# pks, so stream tables follow the raw-DDL composite-pk pattern (same as
# test_events above).
STREAM_DDL = """
    CREATE TABLE test_stream_events (
        id BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        stream_id INT NOT NULL,
        value DOUBLE PRECISION NOT NULL,
        token_count INT NOT NULL DEFAULT 0,
        kind VARCHAR(20) NOT NULL DEFAULT 'event',
        PRIMARY KEY (id, created_at, stream_id)
    )
"""


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5433), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not available on localhost:5433",
)


class TimescaleProbe(Model):
    """Minimal model so Tortoise.init has a non-empty module."""

    id = fields.IntField(primary_key=True)
    label = fields.CharField(max_length=32)

    class Meta:
        table = "test_timescale_probe"


class StreamEvent(BaseEventStreamModel):
    """Multi-stream event model exercising the BaseEventStreamModel."""

    created_at = fields.DatetimeField(use_tz=True)
    stream_id = fields.IntField()
    value = fields.FloatField()
    token_count = fields.IntField(default=0)
    kind = fields.CharField(max_length=20, default="event")

    class Meta:
        table = "test_stream_events"


class AutoStreamEvent(BaseEventStreamModel):
    """G9 — stream model with ``auto_now_add`` and a ``db_default``-only field."""

    created_at = fields.DatetimeField(auto_now_add=True, use_tz=True)
    stream_id = fields.IntField()
    value = fields.FloatField()
    kind = fields.CharField(max_length=20, db_default="event")

    class Meta:
        table = "test_auto_stream_events"


AUTO_STREAM_DDL = """
    CREATE TABLE test_auto_stream_events (
        id BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        stream_id INT NOT NULL,
        value DOUBLE PRECISION NOT NULL,
        kind VARCHAR(20) NOT NULL DEFAULT 'event',
        PRIMARY KEY (id, created_at, stream_id)
    )
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
async def _init_db():
    """Initialize Tortoise ORM for the test module."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_timescale_integration"]},
    )
    await Tortoise.generate_schemas()
    yield
    conn = Tortoise.get_connection("default")
    await conn.execute_query("DROP TABLE IF EXISTS test_events CASCADE")
    await conn.execute_query("DROP TABLE IF EXISTS test_timescale_probe CASCADE")
    await conn.execute_query("DROP TABLE IF EXISTS test_stream_events CASCADE")
    await conn.execute_query("DROP TABLE IF EXISTS test_auto_stream_events CASCADE")
    await Tortoise.close_connections()


async def _make_hypertable() -> None:
    """Create ``test_events`` as a hypertable with 35 days of data."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query("DROP TABLE IF EXISTS test_events CASCADE")
    await conn.execute_query(EVENTS_DDL)
    await HypertableManager.create_hypertable(
        "test_events",
        time_column="created_at",
        chunk_time_interval="1 day",
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    await conn.execute_query(
        "INSERT INTO test_events (id, created_at, value, tenant_id) "
        "SELECT i, $1::timestamptz - make_interval(days => i), i::float8, i % 3 "
        "FROM generate_series(0, 34) i",
        [now],
    )


async def _cleanup_aggregates() -> None:
    """Drop any continuous aggregates left over from previous tests."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(
        "DROP MATERIALIZED VIEW IF EXISTS test_daily_events CASCADE"
    )
    await conn.execute_query(
        "DROP MATERIALIZED VIEW IF EXISTS test_daily_events2 CASCADE"
    )


# ---------------------------------------------------------------------------
# 1. HypertableManager
# ---------------------------------------------------------------------------


class TestHypertableManager:
    """Verify hypertable lifecycle operations against live TimescaleDB."""

    @pytest.mark.asyncio
    async def test_create_and_is_hypertable(self) -> None:
        await _make_hypertable()
        assert await HypertableManager.is_hypertable("test_events") is True
        assert await HypertableManager.is_hypertable("test_events_missing") is False

    @pytest.mark.asyncio
    async def test_list_hypertables(self) -> None:
        await _make_hypertable()
        hypertables = await HypertableManager.list_hypertables()
        names = {ht["table_name"] for ht in hypertables}
        assert "public.test_events" in names
        event = next(ht for ht in hypertables if ht["table_name"] == "public.test_events")
        assert event["num_chunks"] == 35
        assert event["compression_enabled"] is False

    @pytest.mark.asyncio
    async def test_show_chunks_filters(self) -> None:
        await _make_hypertable()
        conn = Tortoise.get_connection("default")
        await conn.execute_query(
            "INSERT INTO test_events (id, created_at, value, tenant_id) "
            "VALUES (100, '2026-07-01T00:00:00+00'::timestamptz, 1.0, 1)"
        )
        all_chunks = await HypertableManager.show_chunks("test_events")
        assert len(all_chunks) >= 35
        ranged = await HypertableManager.show_chunks(
            "test_events",
            start_time="2026-07-01",
            end_time="2026-07-15",
        )
        assert 0 < len(ranged) < len(all_chunks)
        start_only = await HypertableManager.show_chunks(
            "test_events",
            start_time="2026-07-15",
        )
        assert len(start_only) < len(all_chunks)

    @pytest.mark.asyncio
    async def test_add_dimension_number_partitions(self) -> None:
        await _make_hypertable()
        await HypertableManager.add_dimension(
            "test_events",
            "tenant_id",
            number_partitions=4,
        )
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query(
            """
            SELECT column_name, num_partitions
            FROM timescaledb_information.dimensions
            WHERE hypertable_name = 'test_events'
              AND column_name = 'tenant_id'
            """
        )
        assert len(result[1]) == 1
        assert result[1][0]["num_partitions"] == 4

    @pytest.mark.asyncio
    async def test_add_dimension_time_interval(self) -> None:
        """A second time-typed dimension with an explicit interval."""
        conn = Tortoise.get_connection("default")
        await conn.execute_query("DROP TABLE IF EXISTS test_events2 CASCADE")
        await conn.execute_query(
            """
            CREATE TABLE test_events2 (
                id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                period TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (id, created_at, period)
            )
            """
        )
        await HypertableManager.create_hypertable(
            "test_events2",
            time_column="created_at",
            chunk_time_interval="1 day",
        )
        await HypertableManager.add_dimension(
            "test_events2",
            "period",
            chunk_time_interval="1 day",
        )
        result = await conn.execute_query(
            """
            SELECT column_name, time_interval
            FROM timescaledb_information.dimensions
            WHERE hypertable_name = 'test_events2'
              AND column_name = 'period'
            """
        )
        assert len(result[1]) == 1
        assert result[1][0]["column_name"] == "period"
        await conn.execute_query("DROP TABLE IF EXISTS test_events2 CASCADE")

    @pytest.mark.asyncio
    async def test_add_dimension_requires_config(self) -> None:
        await _make_hypertable()
        with pytest.raises(ValueError, match="chunk_time_interval or number_partitions"):
            await HypertableManager.add_dimension("test_events", "tenant_id")

    @pytest.mark.asyncio
    async def test_drop_hypertable(self) -> None:
        await _make_hypertable()
        await HypertableManager.drop_hypertable("test_events")
        assert await HypertableManager.is_hypertable("test_events") is False


# ---------------------------------------------------------------------------
# 2. CompressionManager
# ---------------------------------------------------------------------------


class TestCompressionManager:
    """Verify compression lifecycle operations against live TimescaleDB."""

    @pytest.mark.asyncio
    async def test_enable_and_stats(self) -> None:
        await _make_hypertable()
        await CompressionManager.enable_compression("test_events")
        stats = await CompressionManager.get_stats("test_events")
        assert stats["uncompressed_chunks"] >= 35
        assert stats["compressed_chunks"] == 0

    @pytest.mark.asyncio
    async def test_compress_and_decompress_chunk(self) -> None:
        await _make_hypertable()
        await CompressionManager.enable_compression("test_events")
        chunk = (await HypertableManager.show_chunks("test_events"))[0]
        await CompressionManager.compress_chunk(chunk)
        stats = await CompressionManager.get_stats("test_events")
        assert stats["compressed_chunks"] == 1
        await CompressionManager.decompress_chunk(chunk)
        stats = await CompressionManager.get_stats("test_events")
        assert stats["compressed_chunks"] == 0

    @pytest.mark.asyncio
    async def test_compression_policy_roundtrip(self) -> None:
        await _make_hypertable()
        await CompressionManager.enable_compression("test_events")
        await CompressionManager.add_compression_policy(
            "test_events",
            compress_after="1 day",
        )
        await CompressionManager.remove_compression_policy("test_events")

    @pytest.mark.asyncio
    async def test_disable_compression(self) -> None:
        await _make_hypertable()
        await CompressionManager.enable_compression("test_events")
        await CompressionManager.disable_compression("test_events")


# ---------------------------------------------------------------------------
# 3. RetentionPolicy
# ---------------------------------------------------------------------------


class TestRetentionPolicy:
    """Verify retention policy operations against live TimescaleDB."""

    @pytest.mark.asyncio
    async def test_set_and_list_policies(self) -> None:
        await _make_hypertable()
        await RetentionPolicy.set_retention("test_events", drop_after="90 days")
        policies = await RetentionPolicy.list_policies()
        tables = {p["table_name"] for p in policies}
        assert "test_events" in tables
        event = next(p for p in policies if p["table_name"] == "test_events")
        assert "90 days" in str(event["drop_after"])

    @pytest.mark.asyncio
    async def test_get_chunks_to_drop(self) -> None:
        await _make_hypertable()
        chunks = await RetentionPolicy.get_chunks_to_drop(
            "test_events",
            older_than="90 days",
        )
        assert isinstance(chunks, list)
        assert all(isinstance(c, str) for c in chunks)

    @pytest.mark.asyncio
    async def test_remove_retention(self) -> None:
        await _make_hypertable()
        await RetentionPolicy.set_retention("test_events", drop_after="90 days")
        await RetentionPolicy.remove_retention("test_events")
        policies = await RetentionPolicy.list_policies()
        assert all(p["table_name"] != "test_events" for p in policies)


# ---------------------------------------------------------------------------
# 4. ContinuousAggregateManager
# ---------------------------------------------------------------------------


class TestContinuousAggregateManager:
    """Verify continuous aggregate operations against live TimescaleDB."""

    @pytest.fixture(autouse=True)
    async def _fresh(self) -> AsyncGenerator[None, None]:
        await _make_hypertable()
        yield
        await _cleanup_aggregates()

    @pytest.mark.asyncio
    async def test_create_refresh_list_drop(self) -> None:
        await ContinuousAggregateManager.create("test_daily_events", "test_events", AGG_QUERY)
        await ContinuousAggregateManager.refresh("test_daily_events")
        aggregates = await ContinuousAggregateManager.list()
        names = {agg["view_name"] for agg in aggregates}
        assert "test_daily_events" in names

    @pytest.mark.asyncio
    async def test_refresh_with_window(self) -> None:
        await ContinuousAggregateManager.create("test_daily_events", "test_events", AGG_QUERY)
        await ContinuousAggregateManager.refresh(
            "test_daily_events",
            start_time="2026-07-01",
            end_time="2026-08-01",
        )

    @pytest.mark.asyncio
    async def test_refresh_policy_roundtrip(self) -> None:
        await ContinuousAggregateManager.create("test_daily_events", "test_events", AGG_QUERY)
        await ContinuousAggregateManager.set_refresh_policy("test_daily_events")
        await ContinuousAggregateManager.remove_refresh_policy("test_daily_events")

    @pytest.mark.asyncio
    async def test_add_realtime_aggregate(self) -> None:
        await ContinuousAggregateManager.create("test_daily_events", "test_events", AGG_QUERY)
        await ContinuousAggregateManager.add_realtime_aggregate("test_daily_events")
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query(
            """
            SELECT materialized_only
            FROM _timescaledb_catalog.continuous_agg
            WHERE user_view_name = 'test_daily_events'
            """
        )
        assert len(result[1]) == 1
        assert result[1][0]["materialized_only"] is False

    @pytest.mark.asyncio
    async def test_drop(self) -> None:
        await ContinuousAggregateManager.create("test_daily_events", "test_events", AGG_QUERY)
        await ContinuousAggregateManager.drop("test_daily_events")
        aggregates = await ContinuousAggregateManager.list()
        assert all(a["view_name"] != "test_daily_events" for a in aggregates)

    @pytest.mark.asyncio
    async def test_create_with_full_statement(self) -> None:
        await ContinuousAggregateManager.create("test_daily_events2", "test_events", AGG_FULL)
        aggregates = await ContinuousAggregateManager.list()
        names = {agg["view_name"] for agg in aggregates}
        assert "test_daily_events2" in names
        await ContinuousAggregateManager.drop("test_daily_events2", if_exists=False)
        aggregates = await ContinuousAggregateManager.list()
        assert all(a["view_name"] != "test_daily_events2" for a in aggregates)


# ---------------------------------------------------------------------------
# 5. BaseEventStreamModel
# ---------------------------------------------------------------------------


class TestEventStream:
    """Verify the multi-stream mixin end-to-end against live TimescaleDB."""

    @pytest.fixture(autouse=True)
    async def _fresh(self) -> AsyncGenerator[None, None]:
        conn = Tortoise.get_connection("default")
        await conn.execute_query("DROP TABLE IF EXISTS test_stream_events CASCADE")
        # Composite PK (id, created_at) — see STREAM_DDL comment.
        await conn.execute_query(STREAM_DDL)
        yield

    @pytest.mark.asyncio
    async def test_setup_creates_hypertable_and_dimension(self) -> None:
        await StreamEvent.setup()

        assert await HypertableManager.is_hypertable("test_stream_events")
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query(
            "SELECT column_name FROM timescaledb_information.dimensions "
            "WHERE hypertable_name = 'test_stream_events'"
        )
        columns = {row["column_name"] for row in result[1]}
        assert "stream_id" in columns
        assert "created_at" in columns

    @pytest.mark.asyncio
    async def test_setup_is_idempotent(self) -> None:
        await StreamEvent.setup()
        await StreamEvent.setup()  # second call must not raise

    @pytest.mark.asyncio
    async def test_bulk_insert_via_copy(self) -> None:
        await StreamEvent.setup()
        now = datetime.datetime.now(datetime.timezone.utc)
        instances = [
            StreamEvent(
                id=i,
                created_at=now + datetime.timedelta(seconds=i),
                stream_id=(i % 3) + 1,
                value=float(i),
            )
            for i in range(1, 6)
        ]
        inserted = await StreamEvent.bulk_insert(instances)
        assert inserted == 5
        assert await StreamEvent.all().count() == 5

    @pytest.mark.asyncio
    async def test_bulk_insert_requires_explicit_pk(self) -> None:
        await StreamEvent.setup()
        with pytest.raises(ValueError):
            await StreamEvent.bulk_insert(
                [StreamEvent(created_at=datetime.datetime.now(datetime.timezone.utc), stream_id=1, value=1.0)]
            )

    @pytest.mark.asyncio
    async def test_latest_per_stream(self) -> None:
        await StreamEvent.setup()
        base = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.timezone.utc)
        # stream 1: two events (newest at +10s), stream 2: one event, stream 3: one
        await StreamEvent.bulk_insert(
            [
                StreamEvent(id=1, created_at=base, stream_id=1, value=1.0),
                StreamEvent(id=2, created_at=base + datetime.timedelta(seconds=10), stream_id=1, value=2.0),
                StreamEvent(id=3, created_at=base, stream_id=2, value=3.0),
                StreamEvent(id=4, created_at=base, stream_id=3, value=4.0),
            ]
        )
        latest = await StreamEvent.latest_per_stream(stream_ids=[1, 2, 3])
        by_stream = {e.stream_id: e for e in latest}
        assert len(latest) == 3
        assert by_stream[1].id == 2
        assert by_stream[2].id == 3
        assert by_stream[3].id == 4

    @pytest.mark.asyncio
    async def test_latest_per_stream_after_and_limit(self) -> None:
        await StreamEvent.setup()
        base = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.timezone.utc)
        await StreamEvent.bulk_insert(
            [
                StreamEvent(id=1, created_at=base, stream_id=1, value=1.0),
                StreamEvent(id=2, created_at=base + datetime.timedelta(minutes=5), stream_id=1, value=2.0),
                StreamEvent(id=3, created_at=base + datetime.timedelta(minutes=10), stream_id=2, value=3.0),
            ]
        )
        latest = await StreamEvent.latest_per_stream(
            after=base + datetime.timedelta(minutes=4), limit=2
        )
        ids = {e.id for e in latest}
        assert ids == {2, 3}

    @pytest.mark.asyncio
    async def test_time_series_count_and_avg(self) -> None:
        await StreamEvent.setup()
        base = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.timezone.utc)
        # stream 1: two events in hour bucket 12:00, one at 13:30
        # stream 2: one event in hour bucket 12:00
        await StreamEvent.bulk_insert(
            [
                StreamEvent(id=1, created_at=base, stream_id=1, value=2.0),
                StreamEvent(id=2, created_at=base + datetime.timedelta(minutes=30), stream_id=1, value=4.0),
                StreamEvent(id=3, created_at=base + datetime.timedelta(minutes=90), stream_id=1, value=8.0),
                StreamEvent(id=4, created_at=base, stream_id=2, value=10.0),
            ]
        )
        counts = await StreamEvent.time_series(
            "1 hour",
            start=base,
            end=base + datetime.timedelta(hours=3),
            stream_ids=[1, 2],
        )
        assert isinstance(counts[0], TimeBucketRow)
        by_key = {(r.stream_id, r.bucket) : r for r in counts}
        assert by_key[(1, base)].count == 2
        assert by_key[(1, base + datetime.timedelta(hours=1))].count == 1
        assert by_key[(2, base)].count == 1
        assert by_key[(1, base)].value == 2.0  # count aggregate fills value

        avgs = await StreamEvent.time_series(
            "1 hour",
            aggregate="avg",
            field="value",
            start=base,
            end=base + datetime.timedelta(hours=3),
            stream_ids=[1],
        )
        avg_by_bucket = {r.bucket: r for r in avgs}
        assert avg_by_bucket[base].value == 3.0
        assert avg_by_bucket[base + datetime.timedelta(hours=1)].value == 8.0

    @pytest.mark.asyncio
    async def test_time_series_first_and_last(self) -> None:
        await StreamEvent.setup()
        base = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.timezone.utc)
        await StreamEvent.bulk_insert(
            [
                StreamEvent(id=1, created_at=base, stream_id=1, value=1.0),
                StreamEvent(id=2, created_at=base + datetime.timedelta(minutes=10), stream_id=1, value=2.0),
                StreamEvent(id=3, created_at=base + datetime.timedelta(minutes=20), stream_id=1, value=3.0),
            ]
        )
        firsts = await StreamEvent.time_series(
            "1 hour",
            aggregate="first",
            field="value",
            start=base,
            end=base + datetime.timedelta(hours=1),
            stream_ids=[1],
        )
        lasts = await StreamEvent.time_series(
            "1 hour",
            aggregate="last",
            field="value",
            start=base,
            end=base + datetime.timedelta(hours=1),
            stream_ids=[1],
        )
        assert firsts[0].value == 1.0
        assert lasts[0].value == 3.0

    @pytest.mark.asyncio
    async def test_time_series_requires_field_for_value_aggregates(self) -> None:
        await StreamEvent.setup()
        with pytest.raises(ValueError):
            await StreamEvent.time_series(
                "1 hour",
                aggregate="avg",
                start=datetime.datetime.now(datetime.timezone.utc),
                end=datetime.datetime.now(datetime.timezone.utc),
            )

    @pytest.mark.asyncio
    async def test_in_range_pure_orm(self) -> None:
        await StreamEvent.setup()
        base = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.timezone.utc)
        await StreamEvent.bulk_insert(
            [
                StreamEvent(id=1, created_at=base, stream_id=1, value=1.0),
                StreamEvent(id=2, created_at=base + datetime.timedelta(hours=2), stream_id=2, value=2.0),
                StreamEvent(id=3, created_at=base + datetime.timedelta(hours=4), stream_id=1, value=3.0),
            ]
        )
        rows = await StreamEvent.in_range(
            base,
            base + datetime.timedelta(hours=3),
            stream_ids=[1],
        ).all()
        assert [e.id for e in rows] == [1]


# ---------------------------------------------------------------------------
# 5b. G9 — bulk_insert default population (auto_now_add / db_default)
# ---------------------------------------------------------------------------


class TestBulkInsertDefaults:
    """G9 — COPY must populate auto_now_add and omit unset db_default columns."""

    @pytest.fixture(autouse=True)
    async def _fresh(self) -> AsyncGenerator[None, None]:
        conn = Tortoise.get_connection("default")
        await conn.execute_query("DROP TABLE IF EXISTS test_auto_stream_events CASCADE")
        await conn.execute_query(AUTO_STREAM_DDL)
        yield

    @pytest.mark.asyncio
    async def test_bulk_insert_populates_auto_now_add_and_db_default(self) -> None:
        inserted = await AutoStreamEvent.bulk_insert(
            [
                AutoStreamEvent(id=1, stream_id=1, value=1.0),
                AutoStreamEvent(id=2, stream_id=1, value=2.0),
            ]
        )
        assert inserted == 2
        rows = await AutoStreamEvent.all().order_by("id")
        assert len(rows) == 2
        for row in rows:
            assert row.created_at is not None  # auto_now_add populated by COPY
            assert row.kind == "event"  # db_default applied by the server

    @pytest.mark.asyncio
    async def test_bulk_insert_explicit_db_default_value(self) -> None:
        inserted = await AutoStreamEvent.bulk_insert(
            [
                AutoStreamEvent(id=1, stream_id=1, value=1.0, kind="custom"),
                AutoStreamEvent(id=2, stream_id=1, value=2.0, kind="custom"),
            ]
        )
        assert inserted == 2
        rows = await AutoStreamEvent.all().order_by("id")
        assert {r.kind for r in rows} == {"custom"}

    @pytest.mark.asyncio
    async def test_bulk_insert_mixed_db_default_raises(self) -> None:
        with pytest.raises(OperationalError, match="db_default"):
            await AutoStreamEvent.bulk_insert(
                [
                    AutoStreamEvent(id=1, stream_id=1, value=1.0),
                    AutoStreamEvent(id=2, stream_id=1, value=2.0, kind="custom"),
                ]
            )
