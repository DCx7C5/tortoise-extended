"""Tests for the Tier-1 base-model family.

Covers ``BaseModel`` (BigInt pk), ``TimestampMixin``, and
``BaseSoftDeleteModel``/``SoftDeleteQuerySet`` (auto-filtered soft delete).
Runs against SQLite — soft delete is backend-agnostic.
"""

from collections.abc import AsyncGenerator
from datetime import datetime

import pytest
from tortoise import Tortoise
from tortoise import fields as tf
from tortoise.exceptions import DoesNotExist

from tortoise_extended.models import BaseModel, BaseSoftDeleteModel, TimestampMixin

# --- Test models ---------------------------------------------------------


class BaseThing(BaseModel):
    name = tf.CharField(max_length=64)

    class Meta:
        table = "base_things"
        verbose_name = "Base Thing"
        verbose_name_plural = "Base Things"


class TimestampedThing(TimestampMixin, BaseModel):
    name = tf.CharField(max_length=64)

    class Meta:
        table = "timestamped_things"
        verbose_name = "Timestamped Thing"
        verbose_name_plural = "Timestamped Things"


class SoftThing(BaseSoftDeleteModel, BaseModel):  # pyright: ignore[reportIncompatibleMethodOverride]
    name = tf.CharField(max_length=64)

    class Meta:
        table = "soft_things"
        verbose_name = "Soft Thing"
        verbose_name_plural = "Soft Things"


# --- Fixtures -------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _db() -> AsyncGenerator[None, None]: # pyright: ignore[reportUnusedFunction]
    _ = await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["tests.test_base_models"]},
    )
    await Tortoise.generate_schemas()
    yield
    await Tortoise.close_connections()


# --- Tests -----------------------------------------------------------------


class TestBaseModel:
    """BigInt primary key behavior."""

    async def test_pk_is_bigint(self) -> None:
        pk_field = BaseThing._meta.fields_map["id"]
        assert isinstance(pk_field, tf.BigIntField)
        assert pk_field.pk  # Field.pk is the primary-key marker

    async def test_auto_increment(self) -> None:
        first = await BaseThing.create(name="a")
        second = await BaseThing.create(name="b")
        assert isinstance(first.pk, int)
        assert second.pk == first.pk + 1

    async def test_exports(self) -> None:
        from tortoise_extended import BaseModel as TopBase

        assert TopBase is BaseModel


class TestTimestampMixin:
    """auto_now_add / auto_now timestamps."""

    async def test_created_and_updated_set_on_create(self) -> None:
        thing = await TimestampedThing.create(name="a")
        assert isinstance(thing.created_at, datetime)
        assert isinstance(thing.updated_at, datetime)
        assert thing.created_at is not None
        assert thing.updated_at is not None

    async def test_updated_at_changes_on_update(self) -> None:
        thing = await TimestampedThing.create(name="a")
        original_updated = thing.updated_at
        thing.name = "b"
        await thing.save(update_fields=["name"])
        await thing.refresh_from_db()
        assert thing.updated_at >= original_updated
        assert thing.created_at <= thing.updated_at


async def _seed() -> SoftThing:
    live = await SoftThing.create(name="live")
    dead = await SoftThing.create(name="dead")
    await dead.delete()
    assert dead.deleted_at is not None
    return live


class TestSoftDeleteModel:
    """Soft delete auto-filtering on every QuerySet entry point."""

    async def test_all_excludes_deleted(self) -> None:
        _ = await _seed()
        rows = await SoftThing.all()
        assert [r.name for r in rows] == ["live"]

    async def test_filter_excludes_deleted(self) -> None:
        _ = await _seed()
        assert await SoftThing.filter(name="dead").count() == 0
        assert await SoftThing.filter(name="live").count() == 1

    async def test_get_raises_for_deleted(self) -> None:
        dead = await SoftThing.create(name="dead")
        await dead.delete()
        with pytest.raises(DoesNotExist):
            _ = await SoftThing.get(name="dead")

    async def test_count_excludes_deleted(self) -> None:
        _ = await _seed()
        assert await SoftThing.all().count() == 1
        assert await SoftThing.filter().count() == 1

    async def test_exists_excludes_deleted(self) -> None:
        _ = await _seed()
        assert not await SoftThing.filter(name="dead").exists()
        assert await SoftThing.with_deleted().filter(name="dead").exists()

    async def test_with_deleted(self) -> None:
        _ = await _seed()
        rows = await SoftThing.with_deleted().order_by("name")
        assert [r.name for r in rows] == ["dead", "live"]

    async def test_only_deleted(self) -> None:
        _ = await _seed()
        rows = await SoftThing.only_deleted()
        assert [r.name for r in rows] == ["dead"]

    async def test_update_excludes_deleted(self) -> None:
        _ = await _seed()
        _ = await SoftThing.filter(name="dead").update(name="dead2")
        assert await SoftThing.with_deleted().filter(name="dead2").count() == 0
        assert await SoftThing.with_deleted().filter(name="dead").count() == 1

    async def test_instance_restore(self) -> None:
        dead = await SoftThing.create(name="dead")
        await dead.delete()
        await dead.restore()
        assert dead.deleted_at is None
        assert await SoftThing.filter(name="dead").count() == 1

    async def test_queryset_restore(self) -> None:
        _ = await SoftThing.create(name="a")
        _ = await SoftThing.create(name="b")
        for t in await SoftThing.with_deleted():
            await t.delete()
        restored = await SoftThing.only_deleted().restore()
        assert restored == 2
        assert await SoftThing.all().count() == 2

    async def test_hard_delete(self) -> None:
        _ = await _seed()
        deleted = await SoftThing.with_deleted().hard_delete()
        assert deleted == 2
        assert await SoftThing.with_deleted().count() == 0

    async def test_queryset_clone_keeps_mode(self) -> None:
        _ = await _seed()
        # Filtering after with_deleted() must not re-inject the live filter.
        rows = await SoftThing.with_deleted().filter(name="dead")
        assert [r.name for r in rows] == ["dead"]
