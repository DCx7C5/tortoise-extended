"""Tests for ``UnifiedIdModel`` — BigInt pk + UUID7 unified identifier.

Runs against the live PostgreSQL test database (see ``docker-compose.dev.yml``).
``UnifiedIdModel`` is a pure model-layer abstract base; the assertions below
pin its contract, not a backend quirk.
"""

from collections.abc import AsyncGenerator
from uuid import UUID

import pytest
from tortoise import Tortoise
from tortoise import fields as tf
from tortoise.exceptions import IntegrityError

from tortoise_extended.fields import CharField
from tortoise_extended.models.unified import UnifiedIdModel
from tests._pg_env import database_url, pg_skip_reason

# --- Test models -----------------------------------------------------------


class UnifiedThing(UnifiedIdModel):
    name = CharField(max_length=64)

    class Meta:
        table = "unified_things"
        verbose_name = "Unified Thing"
        verbose_name_plural = "Unified Things"


# --- Config — skip entire module if PG is not available --------------------

DB_URL = database_url()


_PG_SKIP_REASON = pg_skip_reason()
pytestmark = pytest.mark.skipif(
    _PG_SKIP_REASON is not None,
    reason=_PG_SKIP_REASON or "",
)


# --- Fixtures --------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> AsyncGenerator[None, None]:
    """Initialize Tortoise ORM against the live PostgreSQL test database."""
    _ = await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_unified_id_model"]},
    )
    conn = Tortoise.get_connection("default")
    await conn.execute_query("DROP TABLE IF EXISTS unified_things CASCADE")
    await Tortoise.generate_schemas()
    yield
    await conn.execute_query("DROP TABLE IF EXISTS unified_things CASCADE")
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
async def _clean_tables() -> AsyncGenerator[None, None]:
    """Truncate the table before each test."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query("TRUNCATE unified_things CASCADE")
    yield


# --- Tests -----------------------------------------------------------------


class TestUnifiedIdModel:
    """UnifiedIdModel: BigInt pk + UUID7 uid for cross-table references."""

    async def test_inherits_bigint_pk(self) -> None:
        """``id`` is inherited from BaseModel as a BigIntField primary key."""
        pk_field = UnifiedThing._meta.fields_map["id"]
        assert isinstance(pk_field, tf.BigIntField)
        assert pk_field.pk

    async def test_uid_field_exists_and_is_uuid7(self) -> None:
        """``uid`` is a UUID7Field declared on the model."""
        uid_field = UnifiedThing._meta.fields_map["uid"]
        from tortoise_extended.fields.uuid import UUID7Field

        assert isinstance(uid_field, UUID7Field)

    async def test_uid_auto_generates_on_create(self) -> None:
        """``uid`` is auto-generated — no need to supply it on create()."""
        thing = await UnifiedThing.create(name="a")
        assert thing.uid is not None
        assert isinstance(thing.uid, UUID)

    async def test_uid_is_unique(self) -> None:
        """Two rows with the same forced ``uid`` raise IntegrityError."""
        thing_a = await UnifiedThing.create(name="a")
        duplicate_uid = thing_a.uid
        with pytest.raises(IntegrityError):
            await UnifiedThing.create(name="b", uid=duplicate_uid)

    async def test_id_auto_increments(self) -> None:
        """BigInt ``id`` auto-increments across creates."""
        first = await UnifiedThing.create(name="a")
        second = await UnifiedThing.create(name="b")
        assert isinstance(first.id, int)
        assert second.id == first.id + 1

    async def test_uid_values_are_valid_uuids(self) -> None:
        """Created ``uid`` values are valid UUID instances."""
        a = await UnifiedThing.create(name="a")
        b = await UnifiedThing.create(name="b")
        assert isinstance(a.uid, UUID)
        assert isinstance(b.uid, UUID)
        # Two created uids must differ (UUID7 is time-ordered + unique)
        assert a.uid != b.uid

    async def test_uid_is_time_ordered(self) -> None:
        """UUID7 integer values are monotonically non-decreasing."""
        a = await UnifiedThing.create(name="a")
        b = await UnifiedThing.create(name="b")
        assert int(a.uid) < int(b.uid)

    async def test_exports(self) -> None:
        """UnifiedIdModel is importable from the models subpackage."""
        from tortoise_extended.models import UnifiedIdModel as UIM

        assert UIM is UnifiedIdModel
