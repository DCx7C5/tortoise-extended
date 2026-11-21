"""Unit tests for the sample primitives in ``tortoise_extended.models.sample``.

Covers the BigInt :class:`BaseSample` identity base, the pk-free
:class:`SampleMixin`, the six ClassVar defaults reaching subclasses through
both ``BaseSample`` and the ``BaseEventStreamModel`` alias, and ``__str__``.
No database is used.
"""

import pytest
from tortoise import fields

from tortoise_extended.models import BaseEventStreamModel, BaseSample, SampleMixin

DEFAULT_CLASS_VARS = {
    "time_field": "created_at",
    "stream_field": "stream_id",
    "chunk_time_interval": "1 day",
    "number_partitions": 4,
    "compress_after": None,
    "drop_after": None,
}


class UnitSample(BaseSample):
    """Concrete stream table built on BaseSample."""

    created_at = fields.DatetimeField(auto_now_add=True, use_tz=True)
    stream_id = fields.IntField()
    value = fields.FloatField()

    class Meta:
        table = "sample_mixin_unit_events"


class UnitAliasEvent(BaseEventStreamModel):
    """Same shape through the deprecated BaseEventStreamModel alias."""

    created_at = fields.DatetimeField(auto_now_add=True, use_tz=True)
    stream_id = fields.IntField()
    value = fields.FloatField()

    class Meta:
        table = "sample_mixin_unit_alias_events"


class CustomUnitSample(BaseSample):
    """Overrides every ClassVar with a matching column."""

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
        table = "sample_mixin_unit_custom"


class TestIdentityBases:
    """BaseSample owns the BigInt pk; SampleMixin owns none."""

    def test_base_sample_pk_is_a_single_bigint(self) -> None:
        fields_map = BaseSample._meta.fields_map
        assert BaseSample._meta.pk_attr == "id"
        assert sorted(fields_map) == ["id"]
        assert isinstance(fields_map["id"], fields.BigIntField)
        assert [name for name, field in fields_map.items() if field.pk] == ["id"]

    def test_sample_mixin_has_no_primary_key(self) -> None:
        assert SampleMixin._meta.fields_map == {}
        assert [
            field for field in SampleMixin._meta.fields_map.values() if field.pk
        ] == []
        assert "id" not in SampleMixin._meta.fields_map

    def test_alias_extends_base_sample(self) -> None:
        assert issubclass(BaseEventStreamModel, BaseSample)
        assert BaseEventStreamModel._meta.pk_attr == "id"
        assert isinstance(
            BaseEventStreamModel._meta.fields_map["id"], fields.BigIntField
        )


class TestClassVarDefaults:
    """The six ClassVars reach subclasses of both bases with their defaults."""

    @pytest.mark.parametrize(("attr", "expected"), sorted(DEFAULT_CLASS_VARS.items()))
    def test_defaults_reach_subclasses(self, attr: str, expected: object) -> None:
        for model in (UnitSample, UnitAliasEvent):
            assert getattr(model, attr) == expected

    def test_overrides_are_honoured(self) -> None:
        assert CustomUnitSample.time_field == "ts"
        assert CustomUnitSample.stream_field == "device_id"
        assert CustomUnitSample.chunk_time_interval == "6 hours"
        assert CustomUnitSample.number_partitions == 8
        assert CustomUnitSample.compress_after == "7 days"
        assert CustomUnitSample.drop_after == "90 days"


class TestStr:
    """__str__ carries the class name and the concrete table name."""

    def test_str_carries_the_table_name(self) -> None:
        assert str(UnitSample()) == "<UnitSample sample_mixin_unit_events>"
        assert (
            str(UnitAliasEvent()) == "<UnitAliasEvent sample_mixin_unit_alias_events>"
        )
        assert str(CustomUnitSample()) == "<CustomUnitSample sample_mixin_unit_custom>"
