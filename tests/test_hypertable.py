"""Tests for hypertable migration operations."""

from tortoise_extended.migrations.operations import (
    CreateContinuousAggregate,
    CreateHypertable,
)


class TestCreateHypertable:
    def test_describe(self) -> None:
        op = CreateHypertable(table_name="test_table", time_column="created_at")
        desc = op.describe()
        assert "CreateHypertable" in desc
        assert "test_table" in desc

    def test_default_params(self) -> None:
        op = CreateHypertable(table_name="test_table")
        assert op.time_column == "created_at"
        assert op.chunk_time_interval == "7 days"
        assert op.migrate_data is True

    def test_deconstruct(self) -> None:
        op = CreateHypertable(table_name="events", time_column="ts", chunk_time_interval="1 day")
        class_name, args, kwargs = op.deconstruct()
        assert class_name == "CreateHypertable"
        assert args == ()
        assert kwargs["table_name"] == "events"
        assert kwargs["time_column"] == "ts"
        assert kwargs["chunk_time_interval"] == "1 day"
        assert kwargs["migrate_data"] is True

    def test_roundtrip_via_deconstruct(self) -> None:
        original = CreateHypertable(
            table_name="logs",
            time_column="recorded_at",
            chunk_time_interval="3 days",
            migrate_data=False,
        )
        _class_name, args, kwargs = original.deconstruct()
        restored = CreateHypertable(*args, **kwargs)
        assert restored.table_name == original.table_name
        assert restored.time_column == original.time_column
        assert restored.chunk_time_interval == original.chunk_time_interval
        assert restored.migrate_data == original.migrate_data


class TestCreateContinuousAggregate:
    def test_describe(self) -> None:
        op = CreateContinuousAggregate(
            view_name="my_view",
            query="SELECT id, sum(amount) as total FROM sales GROUP BY id",
        )
        desc = op.describe()
        assert "CreateContinuousAggregate" in desc
        assert "my_view" in desc

    def test_deconstruct(self) -> None:
        op = CreateContinuousAggregate(
            view_name="hourly",
            query="SELECT 1",
            time_column="bucket",
            refresh_interval="30 minutes",
        )
        class_name, args, kwargs = op.deconstruct()
        assert class_name == "CreateContinuousAggregate"
        assert args == ()
        assert kwargs["view_name"] == "hourly"
        assert kwargs["query"] == "SELECT 1"
        assert kwargs["time_column"] == "bucket"
        assert kwargs["refresh_interval"] == "30 minutes"


class TestBucketParsing:
    """Unit tests for the EventStreamMixin bucket-width parser (no DB)."""

    def test_parses_common_units(self) -> None:
        from datetime import timedelta

        from tortoise_extended.timescale.stream import _bucket_to_timedelta

        assert _bucket_to_timedelta("1 hour") == timedelta(hours=1)
        assert _bucket_to_timedelta("30 minutes") == timedelta(minutes=30)
        assert _bucket_to_timedelta("1 day") == timedelta(days=1)
        assert _bucket_to_timedelta("2 weeks") == timedelta(weeks=2)
        assert _bucket_to_timedelta("500 milliseconds") == timedelta(milliseconds=500)

    def test_rejects_variable_length_units(self) -> None:
        import pytest

        from tortoise_extended.timescale.stream import _bucket_to_timedelta

        with pytest.raises(ValueError):
            _bucket_to_timedelta("1 month")
        with pytest.raises(ValueError):
            _bucket_to_timedelta("1 year")

    def test_rejects_malformed_buckets(self) -> None:
        import pytest

        from tortoise_extended.timescale.stream import _bucket_to_timedelta

        with pytest.raises(ValueError):
            _bucket_to_timedelta("hourly")
        with pytest.raises(ValueError):
            _bucket_to_timedelta("1")
        with pytest.raises(ValueError):
            _bucket_to_timedelta("many hours")
