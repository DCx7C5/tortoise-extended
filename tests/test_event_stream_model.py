"""Tests for tortoise_extended.models.event_stream.

Covers ``BaseEventStreamModel`` ClassVar wiring, ``_assert_pk_present``,
``_copy_db_fields``, ``_omit_db_default_fields``, and ``_copy_records`` —
all pure/static tests that do not need a live DB connection.
"""

from unittest.mock import MagicMock, patch

import pytest
from tortoise import fields
from tortoise.exceptions import OperationalError
from tortoise.fields.base import DatabaseDefault

from tortoise_extended.models import BaseEventStreamModel


# --- Test models -----------------------------------------------------------


class SensorEvent(BaseEventStreamModel):
    """Standard event stream model for testing."""

    created_at = fields.DatetimeField(auto_now_add=True, use_tz=True)
    stream_id = fields.IntField()
    temperature = fields.FloatField()
    humidity = fields.FloatField(default=0.0)

    class Meta:
        table = "sensor_events"


class CustomEvent(BaseEventStreamModel):
    """Override ClassVars for testing."""

    time_field = "ts"
    stream_field = "device_id"
    chunk_time_interval = "6 hours"
    number_partitions = 8
    compress_after = "7 days"
    drop_after = "90 days"

    ts = fields.DatetimeField(auto_now_add=True, use_tz=True)
    device_id = fields.CharField(max_length=64)
    payload = fields.JSONField(default=dict)

    class Meta:
        table = "custom_events"


class MinimalEvent(BaseEventStreamModel):
    """Minimal — no space dimension (number_partitions=None)."""

    number_partitions = None
    created_at = fields.DatetimeField(auto_now_add=True, use_tz=True)
    stream_id = fields.IntField()
    value = fields.FloatField()

    class Meta:
        table = "minimal_events"


class EventWithFK(BaseEventStreamModel):
    """Model with a foreign key to test _copy_db_fields exclusion."""

    created_at = fields.DatetimeField(auto_now_add=True, use_tz=True)
    stream_id = fields.IntField()
    sensor = fields.ForeignKeyField(
        "models.Sensor", related_name="events", null=True
    )

    class Meta:
        table = "events_with_fk"


class EventWithDBDefault(BaseEventStreamModel):
    """Model with a db_default field for testing _omit_db_default_fields."""

    created_at = fields.DatetimeField(auto_now_add=True, use_tz=True)
    stream_id = fields.IntField()
    token_count = fields.IntField(db_default=0)

    class Meta:
        table = "events_with_db_default"


# --- Tests: ClassVar defaults ----------------------------------------------


class TestClassVarDefaults:
    """Default ClassVar values inherited from BaseEventStreamModel."""

    def test_default_time_field(self) -> None:
        assert SensorEvent.time_field == "created_at"

    def test_default_stream_field(self) -> None:
        assert SensorEvent.stream_field == "stream_id"

    def test_default_chunk_time_interval(self) -> None:
        assert SensorEvent.chunk_time_interval == "1 day"

    def test_default_number_partitions(self) -> None:
        assert SensorEvent.number_partitions == 4

    def test_default_compress_after(self) -> None:
        assert SensorEvent.compress_after is None

    def test_default_drop_after(self) -> None:
        assert SensorEvent.drop_after is None


# --- Tests: ClassVar overrides ---------------------------------------------


class TestClassVarOverrides:
    """Subclass overrides of BaseEventStreamModel ClassVar defaults."""

    def test_custom_time_field(self) -> None:
        assert CustomEvent.time_field == "ts"

    def test_custom_stream_field(self) -> None:
        assert CustomEvent.stream_field == "device_id"

    def test_custom_chunk_interval(self) -> None:
        assert CustomEvent.chunk_time_interval == "6 hours"

    def test_custom_partitions(self) -> None:
        assert CustomEvent.number_partitions == 8

    def test_custom_compress_after(self) -> None:
        assert CustomEvent.compress_after == "7 days"

    def test_custom_drop_after(self) -> None:
        assert CustomEvent.drop_after == "90 days"

    def test_minimal_no_partitions(self) -> None:
        assert MinimalEvent.number_partitions is None


# --- Tests: _assert_pk_present --------------------------------------------


class TestAssertPkPresent:
    """_assert_pk_present validates that all instances carry explicit PKs."""

    def test_assert_pk_present_all_set(self) -> None:
        instances = [
            SensorEvent.construct(id=1, stream_id=10, temperature=20.0),
            SensorEvent.construct(id=2, stream_id=20, temperature=21.0),
            SensorEvent.construct(id=3, stream_id=30, temperature=22.0),
        ]
        SensorEvent._assert_pk_present(instances)

    def test_assert_pk_present_none_raises(self) -> None:
        instances = [
            SensorEvent.construct(id=1, stream_id=10, temperature=20.0),
            SensorEvent.construct(id=None, stream_id=20, temperature=21.0),
        ]
        with pytest.raises(
            ValueError, match="bulk_insert requires explicit primary keys"
        ):
            SensorEvent._assert_pk_present(instances)

    def test_assert_pk_present_empty_list(self) -> None:
        SensorEvent._assert_pk_present([])


# --- Tests: _copy_db_fields ------------------------------------------------


class TestCopyDbFields:
    """_copy_db_fields returns concrete columns, excluding relation fields."""

    def test_copy_db_fields_excludes_fk(self) -> None:
        result = EventWithFK._copy_db_fields()
        assert "sensor" not in result

    def test_copy_db_fields_includes_concrete(self) -> None:
        result = SensorEvent._copy_db_fields()
        assert "created_at" in result
        assert "stream_id" in result
        assert "temperature" in result
        assert "humidity" in result


# --- Tests: _omit_db_default_fields ----------------------------------------


class TestOmitDbDefaultFields:
    """_omit_db_default_fields detects db_default column usage patterns."""

    def test_omit_db_default_fields_all_default(self) -> None:
        db_default_field = EventWithDBDefault._meta.fields_map["token_count"]
        instances = [
            EventWithDBDefault.construct(id=1, stream_id=10),
            EventWithDBDefault.construct(id=2, stream_id=20),
        ]
        for inst in instances:
            setattr(inst, "token_count", DatabaseDefault(db_default_field))

        db_fields = ["created_at", "stream_id", "token_count"]
        omit = EventWithDBDefault._omit_db_default_fields(instances, db_fields)
        assert "token_count" in omit
        assert "created_at" not in omit
        assert "stream_id" not in omit

    def test_omit_db_default_fields_mixed_raises(self) -> None:
        db_default_field = EventWithDBDefault._meta.fields_map["token_count"]
        instances = [
            EventWithDBDefault.construct(id=1, stream_id=10),
            EventWithDBDefault.construct(id=2, stream_id=20),
        ]
        setattr(instances[0], "token_count", DatabaseDefault(db_default_field))
        instances[1].token_count = 42

        db_fields = ["created_at", "stream_id", "token_count"]
        with pytest.raises(OperationalError, match="Cannot use bulk_insert"):
            _ = EventWithDBDefault._omit_db_default_fields(instances, db_fields)

    def test_omit_db_default_fields_all_explicit(self) -> None:
        instances = [
            EventWithDBDefault.construct(id=1, stream_id=10, token_count=5),
            EventWithDBDefault.construct(id=2, stream_id=20, token_count=10),
        ]
        db_fields = ["created_at", "stream_id", "token_count"]
        omit = EventWithDBDefault._omit_db_default_fields(instances, db_fields)
        assert omit == set()


# --- Tests: _copy_records ---------------------------------------------------


class TestCopyRecords:
    """_copy_records coerces field values via to_db_value."""

    def test_copy_records_calls_to_db_value(self) -> None:
        instances = [
            SensorEvent.construct(
                id=1, stream_id=10, temperature=20.0, humidity=50.0
            ),
            SensorEvent.construct(
                id=2, stream_id=20, temperature=21.0, humidity=51.0
            ),
        ]
        db_fields = ["stream_id", "temperature", "humidity"]
        fields_map = SensorEvent._meta.fields_map

        mocks: dict[str, MagicMock] = {}
        patchers = []
        for name in db_fields:
            mock = MagicMock(return_value=f"coerced_{name}")
            mocks[name] = mock
            p = patch.object(fields_map[name], "to_db_value", mock)
            _ = p.start()
            patchers.append(p)

        try:
            result = SensorEvent._copy_records(instances, db_fields)
            assert len(result) == 2
            assert result[0] == [
                "coerced_stream_id",
                "coerced_temperature",
                "coerced_humidity",
            ]
            assert result[1] == [
                "coerced_stream_id",
                "coerced_temperature",
                "coerced_humidity",
            ]
            for name in db_fields:
                assert mocks[name].call_count == 2
        finally:
            for p in patchers:
                p.stop()
