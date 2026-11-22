"""Live-error branch coverage for TimescaleDB managers.

Exercises every ``except Exception → raise TimescaleError`` error branch
in the timescale manager modules by calling public methods against a LIVE
Postgres connection with bogus / non-existent object names so the server
raises, and the manager wraps it.

Requires: the docker ``postgres-ext`` container (PostgreSQL 18 + TimescaleDB)
exposed on ``127.0.0.1:5434``.

Run with: uv run pytest tests/test_timescale_live_errors.py -v
"""

from __future__ import annotations

import datetime
import os
import socket
from collections.abc import AsyncGenerator

import pytest
from tortoise import Tortoise, fields
from tortoise.models import Model

import tortoise_extended  # noqa: F401 — apply patches
from tortoise_extended.exceptions import TimescaleError
from tortoise_extended.fields import CharField
from tortoise_extended.models import BaseEventStreamModel
from tortoise_extended.timescale import (
    CompressionManager,
    ContinuousAggregateManager,
    HypertableManager,
    RetentionPolicy,
)
from tortoise_extended.timescale.continuous_aggregate import validate_cagg_query
from tortoise_extended.timescale.stream import (
    bucket_datetime,
    fetch_rows,
    table_field,
)

# ---------------------------------------------------------------------------
# Config — skip entire module if TimescaleDB PG is not available
# ---------------------------------------------------------------------------

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test",
)

BOGUS_TABLE = "definitely_not_a_table_xyz"
BOGUS_VIEW = "does_not_exist_agg_xyz"
BOGUS_CHUNK = "_timescaledb_internal._hyper_99999_99999_chunk"


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5434), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not available on localhost:5434",
)


class _Probe(Model):
    """Minimal model so Tortoise.init has a non-empty module."""

    id = fields.IntField(primary_key=True)
    label = CharField(max_length=32)

    class Meta:
        table = "test_error_probe"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
async def _init_db():
    """Initialize Tortoise ORM for the test module."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_timescale_live_errors"]},
    )
    await Tortoise.generate_schemas()
    yield
    conn = Tortoise.get_connection("default")
    await conn.execute_query("DROP TABLE IF EXISTS test_error_probe CASCADE")
    await Tortoise.close_connections()


# ===========================================================================
# 1. CompressionManager error branches
# ===========================================================================


class TestCompressionManagerErrors:
    """Hit every uncovered error branch in compression.py."""

    @pytest.mark.asyncio
    async def test_disable_compression_nonexistent_table(self) -> None:
        """Lines 138-140: disable_compression → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to disable compression"):
            await CompressionManager.disable_compression(BOGUS_TABLE)

    @pytest.mark.asyncio
    async def test_add_compression_policy_nonexistent_table(self) -> None:
        """Lines 179-181: add_compression_policy → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to add compression policy"):
            await CompressionManager.add_compression_policy(BOGUS_TABLE)

    @pytest.mark.asyncio
    async def test_remove_compression_policy_nonexistent_table(self) -> None:
        """Lines 207-209: remove_compression_policy → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to remove compression policy"):
            await CompressionManager.remove_compression_policy(BOGUS_TABLE)

    @pytest.mark.asyncio
    async def test_compress_chunk_nonexistent(self) -> None:
        """Lines 237-239: compress_chunk → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to compress chunk"):
            await CompressionManager.compress_chunk(BOGUS_CHUNK)

    @pytest.mark.asyncio
    async def test_decompress_chunk_nonexistent(self) -> None:
        """Lines 267-269: decompress_chunk → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to decompress chunk"):
            await CompressionManager.decompress_chunk(BOGUS_CHUNK)


# ===========================================================================
# 2. ContinuousAggregateManager error branches
# ===========================================================================


class TestContinuousAggregateManagerErrors:
    """Hit every uncovered error branch in continuous_aggregate.py."""

    @pytest.mark.asyncio
    async def test_validate_cagg_query_schema_qualified_source(self) -> None:
        """Line 105: validate_cagg_query with dot-qualified source_table.

        Exercises the ``if "." in source_table:`` branch which builds
        ``source_pattern = re.escape(source_table)``.
        """
        result = validate_cagg_query(
            "SELECT time_bucket('1 day', t) AS b FROM public.events GROUP BY b",
            source_table="public.events",
        )
        assert "public.events" in result

    @pytest.mark.asyncio
    async def test_drop_nonexistent_view(self) -> None:
        """Lines 222-224: drop → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to drop continuous aggregate"):
            await ContinuousAggregateManager.drop(BOGUS_VIEW, if_exists=False)

    @pytest.mark.asyncio
    async def test_refresh_nonexistent_view(self) -> None:
        """Lines 272-274: refresh → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to refresh continuous aggregate"):
            await ContinuousAggregateManager.refresh(BOGUS_VIEW)

    @pytest.mark.asyncio
    async def test_set_refresh_policy_nonexistent_view(self) -> None:
        """Lines 320-322: set_refresh_policy → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to set refresh policy"):
            await ContinuousAggregateManager.set_refresh_policy(BOGUS_VIEW)

    @pytest.mark.asyncio
    async def test_remove_refresh_policy_nonexistent_view(self) -> None:
        """Lines 345-347: remove_refresh_policy → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to remove refresh policy"):
            await ContinuousAggregateManager.remove_refresh_policy(BOGUS_VIEW)

    @pytest.mark.asyncio
    async def test_add_realtime_aggregate_nonexistent_view(self) -> None:
        """Lines 373-375: add_realtime_aggregate → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to enable realtime aggregation"):
            await ContinuousAggregateManager.add_realtime_aggregate(BOGUS_VIEW)


# ===========================================================================
# 3. RetentionPolicy error branches
# ===========================================================================


class TestRetentionPolicyErrors:
    """Hit every uncovered error branch in retention.py."""

    @pytest.mark.asyncio
    async def test_remove_retention_nonexistent_table(self) -> None:
        """Lines 124-126: remove_retention → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to remove retention policy"):
            await RetentionPolicy.remove_retention(BOGUS_TABLE)

    @pytest.mark.asyncio
    async def test_get_chunks_to_drop_nonexistent_table(self) -> None:
        """Lines 252-254: get_chunks_to_drop → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to list chunks to drop"):
            await RetentionPolicy.get_chunks_to_drop(BOGUS_TABLE)


# ===========================================================================
# 4. HypertableManager error branches
# ===========================================================================


class TestHypertableManagerErrors:
    """Hit every uncovered error branch in hypertable.py."""

    @pytest.mark.asyncio
    async def test_split_schema_name_with_quoted_identifier(self) -> None:
        """Line 62: _split_schema_name hits the ``in_quote`` toggle branch.

        Pass a schema-qualified name containing double-quoted identifiers
        so the char-by-char parser encounters ``"`` characters and toggles
        ``in_quote`` (line 62).
        """
        result = await HypertableManager.is_hypertable('"public".nonexistent')
        assert result is False

    @pytest.mark.asyncio
    async def test_drop_hypertable_nonexistent(self) -> None:
        """Lines 261-263: drop_hypertable → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to drop hypertable"):
            await HypertableManager.drop_hypertable(BOGUS_TABLE, if_exists=False)

    @pytest.mark.asyncio
    async def test_add_dimension_nonexistent_table(self) -> None:
        """Lines 458-463: add_dimension → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to add dimension"):
            await HypertableManager.add_dimension(
                BOGUS_TABLE, "col", number_partitions=4,
            )

    @pytest.mark.asyncio
    async def test_show_chunks_nonexistent_table(self) -> None:
        """Lines 514-516: show_chunks → TimescaleError."""
        with pytest.raises(TimescaleError, match="Failed to show chunks"):
            await HypertableManager.show_chunks(BOGUS_TABLE)


# ===========================================================================
# 5. stream.py error branches (public aliases)
# ===========================================================================


class TestStreamErrors:
    """Hit every uncovered error branch in stream.py via public aliases."""

    def test_table_field_nonexistent_field(self) -> None:
        """Line 110: table_field → ValueError for an undeclared field."""
        with pytest.raises(ValueError, match="is not declared on"):
            table_field(_Probe, "nonexistent_field_xyz", what="aggregate")

    def test_bucket_datetime_with_string(self) -> None:
        """Lines 144-145: bucket_datetime parses an ISO-8601 string."""
        result = bucket_datetime("2024-01-01T00:00:00+00:00")
        assert result.year == 2024
        assert result.month == 1

    def test_bucket_datetime_with_non_timestamp(self) -> None:
        """Line 146: bucket_datetime → TypeError for non-timestamp value."""
        with pytest.raises(TypeError, match="expected a timestamp"):
            bucket_datetime(42)

    @pytest.mark.asyncio
    async def test_fetch_rows_bad_sql(self) -> None:
        """Lines 164-166: fetch_rows → TimescaleError on bad SQL."""
        with pytest.raises(TimescaleError, match="Failed to fetch stream rows"):
            await fetch_rows("INVALID SQL 12345 BROKEN")


# ===========================================================================
# 6. BaseEventStreamModel branch coverage
# ===========================================================================

STREAM_COV_DDL = """
    CREATE TABLE test_stream_cov (
        id BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        stream_id INT NOT NULL,
        value DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (id, created_at, stream_id)
    )
"""


class _CovStreamEvent(BaseEventStreamModel):
    """Stream model with compression and retention policies configured."""

    created_at = fields.DatetimeField(use_tz=True)
    stream_id = fields.IntField()
    value = fields.FloatField()
    compress_after = "1 hour"
    drop_after = "7 days"

    class Meta:
        table = "test_stream_cov"


class TestEventStreamBranchCoverage:
    """Close uncovered branches in BaseEventStreamModel (live TimescaleDB)."""

    @pytest.fixture(autouse=True)
    async def _fresh(self) -> AsyncGenerator[None, None]:
        conn = Tortoise.get_connection("default")
        await conn.execute_query("DROP TABLE IF EXISTS test_stream_cov CASCADE")
        await conn.execute_query(STREAM_COV_DDL)
        yield
        await conn.execute_query("DROP TABLE IF EXISTS test_stream_cov CASCADE")

    def test_str_repr(self) -> None:
        """Line 138: __str__ renders the class name + table."""
        inst = _CovStreamEvent(
            id=1,
            created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            stream_id=1,
            value=1.0,
        )
        assert str(inst) == "<_CovStreamEvent test_stream_cov>"

    @pytest.mark.asyncio
    async def test_setup_applies_compression_and_retention(self) -> None:
        """Lines 181-189: setup() enables compression + retention policies."""
        await _CovStreamEvent.setup()

        assert await HypertableManager.is_hypertable("test_stream_cov")
        # Compression + retention policies were installed.
        assert await _CovStreamEvent._is_compression_enabled()

    @pytest.mark.asyncio
    async def test_setup_retention_when_drop_after_none(self) -> None:
        """Line 189 skipped cleanly when drop_after is None — already
        exercised (no drop_after on the plain model); here we assert the
        model configured with drop_after installs it (line 189 hit)."""
        await _CovStreamEvent.setup()
        # RetentionPolicy.list_policies lists jobs referencing the table.
        policies = await RetentionPolicy.list_policies()
        matched = [
            p for p in policies if "test_stream_cov" in str(p).lower()
        ]
        assert matched

    @pytest.mark.asyncio
    async def test_bulk_insert_empty_returns_zero(self) -> None:
        """Line 355: bulk_insert([]) short-circuits to 0."""
        assert await _CovStreamEvent.bulk_insert([]) == 0

    @pytest.mark.asyncio
    async def test_latest_per_stream_empty_stream_ids(self) -> None:
        """Line 439: latest_per_stream(stream_ids=[]) → []."""
        await _CovStreamEvent.setup()
        assert await _CovStreamEvent.latest_per_stream(stream_ids=[]) == []

    @pytest.mark.asyncio
    async def test_time_series_empty_stream_ids(self) -> None:
        """Line 516: time_series with empty stream_ids → []."""
        await _CovStreamEvent.setup()
        rows = await _CovStreamEvent.time_series(
            "1 hour",
            aggregate="avg",
            field="value",
            start=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc),
            stream_ids=[],
        )
        assert rows == []
