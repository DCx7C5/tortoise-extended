"""Tests for the Tier-1 base-model family.

Covers ``BaseModel`` (BigInt pk), ``TimestampMixin``,
``TimestampEndMixin``, and ``BaseSoftDeleteModel``/``SoftDeleteQuerySet``
(auto-filtered soft delete).  Runs against the live PostgreSQL test database
(see ``docker-compose.dev.yml``) — this package is PostgreSQL-only.
"""

import os
import socket
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from tortoise import Tortoise
from tortoise import fields as tf
from tortoise.exceptions import DoesNotExist

from tortoise_extended.fields import CharField
from tortoise_extended.models import (
    BaseModel,
    BaseSoftDeleteModel,
    TimestampEndMixin,
    TimestampMixin,
)

# --- Test models ---------------------------------------------------------


class BaseThing(BaseModel):
    name = CharField(max_length=64)

    class Meta:
        table = "base_things"
        verbose_name = "Base Thing"
        verbose_name_plural = "Base Things"


class TimestampedThing(BaseModel, TimestampMixin):
    name = CharField(max_length=64)

    class Meta:
        table = "timestamped_things"
        verbose_name = "Timestamped Thing"
        verbose_name_plural = "Timestamped Things"


class SoftThing(BaseSoftDeleteModel, BaseModel):  # pyright: ignore[reportIncompatibleMethodOverride]
    """BaseSoftDeleteModel narrows Model.all/filter/delete return types — intentional MRO stacking."""

    name = CharField(max_length=64)

    class Meta:
        table = "soft_things"
        verbose_name = "Soft Thing"
        verbose_name_plural = "Soft Things"


class EndedThing(BaseModel, TimestampEndMixin):
    """Model using TimestampEndMixin (created_at + ended_at)."""

    name = CharField(max_length=64)

    class Meta:
        table = "ended_things"
        verbose_name = "Ended Thing"
        verbose_name_plural = "Ended Things"


class FullyStampedThing(BaseModel, BaseSoftDeleteModel, TimestampMixin):  # pyright: ignore[reportIncompatibleMethodOverride]
    """Model stacking BaseModel + SoftDelete + Timestamp (mixin-last).

    BaseSoftDeleteModel narrows Model.all/filter/delete return types —
    intentional MRO stacking.
    """

    name = CharField(max_length=64)

    class Meta:
        table = "fully_stamped_things"
        verbose_name = "Fully Stamped Thing"
        verbose_name_plural = "Fully Stamped Things"


# --- Config — skip entire module if PG is not available --------------------

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test",
)


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


# --- Fixtures -------------------------------------------------------------

_TABLES = (
    "base_things",
    "timestamped_things",
    "soft_things",
    "ended_things",
    "fully_stamped_things",
)


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> AsyncGenerator[None, None]:  # pyright: ignore[reportUnusedFunction]
    """Initialize Tortoise ORM against the live PostgreSQL test database."""
    _ = await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_base_models"]},
    )
    conn = Tortoise.get_connection("default")
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.generate_schemas()
    yield
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
async def _clean_tables() -> AsyncGenerator[None, None]:
    """Truncate every table before each test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(f"TRUNCATE {', '.join(_TABLES)} CASCADE")
    yield


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
        assert thing.updated_at is not None
        # use_tz=True — timestamps are timezone-aware
        assert thing.created_at.tzinfo is not None
        assert thing.updated_at.tzinfo is not None

    async def test_updated_at_fires_on_full_save(self) -> None:
        """auto_now must fire on a full save (no update_fields)."""
        thing = await TimestampedThing.create(name="a")
        original_created = thing.created_at
        original_updated = thing.updated_at
        thing.name = "b"
        await thing.save()  # full save — auto_now fires
        await thing.refresh_from_db()
        assert thing.updated_at > original_updated
        assert thing.created_at == original_created

    async def test_updated_at_not_fired_on_partial_save(self) -> None:
        """Tortoise auto_now only fires when the field is in update_fields.

        A partial save with ``update_fields=[\"name\"]`` excludes
        ``updated_at``, so it must NOT be rewritten — documents the
        intentional Tortoise 1.1.7 behavior (differs from Django).
        """
        thing = await TimestampedThing.create(name="a")
        original_updated = thing.updated_at
        thing.name = "b"
        await thing.save(update_fields=["name"])
        await thing.refresh_from_db()
        assert thing.updated_at == original_updated


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


# --- TimestampEndMixin tests -----------------------------------------------


class TestTimestampEndMixin:
    """created_at (auto) + ended_at (caller-managed) timestamps."""

    async def test_created_set_on_create(self) -> None:
        thing = await EndedThing.create(name="a")
        assert isinstance(thing.created_at, datetime)
        assert thing.created_at is not None

    async def test_ended_at_defaults_to_none(self) -> None:
        thing = await EndedThing.create(name="a")
        assert thing.ended_at is None

    async def test_ended_at_set_explicitly(self) -> None:
        thing = await EndedThing.create(name="a")
        now = datetime.now(UTC)
        thing.ended_at = now
        await thing.save(update_fields=["ended_at"])
        await thing.refresh_from_db()
        assert thing.ended_at is not None
        assert thing.ended_at == now

    async def test_ended_at_not_clobbered_by_later_save(self) -> None:
        """ended_at is caller-managed (NOT auto_now); it must survive saves."""
        thing = await EndedThing.create(name="a")
        now = datetime.now(UTC)
        thing.ended_at = now
        await thing.save(update_fields=["ended_at"])
        saved_end = thing.ended_at
        thing.name = "b"
        await thing.save()  # full save — must NOT clobber ended_at
        await thing.refresh_from_db()
        assert thing.ended_at == saved_end

    async def test_created_at_does_not_change_on_update(self) -> None:
        thing = await EndedThing.create(name="a")
        original_created = thing.created_at
        thing.name = "b"
        await thing.save(update_fields=["name"])
        await thing.refresh_from_db()
        assert thing.created_at == original_created

    async def test_created_at_survives_full_save(self) -> None:
        thing = await EndedThing.create(name="a")
        original_created = thing.created_at
        thing.name = "b"
        await thing.save()  # full save
        await thing.refresh_from_db()
        assert thing.created_at == original_created


# --- Mixin stacking tests --------------------------------------------------


class TestMixinStacking:
    """Model + SoftDelete + TimestampMixin (mixin-last) all compose correctly."""

    async def test_has_timestamp_fields(self) -> None:
        thing = await FullyStampedThing.create(name="a")
        assert isinstance(thing.created_at, datetime)
        assert isinstance(thing.updated_at, datetime)
        assert thing.created_at.tzinfo is not None
        assert thing.updated_at.tzinfo is not None

    async def test_has_soft_delete_field(self) -> None:
        thing = await FullyStampedThing.create(name="a")
        assert thing.deleted_at is None

    async def test_soft_delete_does_not_bump_updated_at(self) -> None:
        """Soft-delete uses save(update_fields=[\"deleted_at\"]) — auto_now is
        NOT in the list, so updated_at is not rewritten by a soft delete."""
        thing = await FullyStampedThing.create(name="a")
        orig_updated = thing.updated_at
        await thing.delete()  # soft delete
        await thing.refresh_from_db()
        assert thing.deleted_at is not None
        assert thing.updated_at == orig_updated

    async def test_soft_delete_filters(self) -> None:
        await FullyStampedThing.create(name="live")
        dead = await FullyStampedThing.create(name="dead")
        await dead.delete()
        rows = await FullyStampedThing.all()
        assert [r.name for r in rows] == ["live"]

    async def test_with_deleted_includes_all(self) -> None:
        await FullyStampedThing.create(name="live")
        dead = await FullyStampedThing.create(name="dead")
        await dead.delete()
        rows = await FullyStampedThing.with_deleted().order_by("name")
        assert [r.name for r in rows] == ["dead", "live"]

    async def test_pk_is_bigint(self) -> None:
        _ = await FullyStampedThing.create(name="a")
        pk_field = FullyStampedThing._meta.fields_map["id"]
        assert isinstance(pk_field, tf.BigIntField)
