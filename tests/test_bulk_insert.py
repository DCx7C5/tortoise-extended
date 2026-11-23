"""``Model.bulk_insert`` — COPY ingestion for any model (patch site 7).

Sections:
  1. ``TestBulkInsertHelpers`` — pure helpers and the refusal rules (no DB).
  2. ``TestBulkInsertPatch`` — the install is idempotent and conflict-safe.
  3. ``TestBulkInsertPG`` — real ``COPY`` against PostgreSQL.
"""

import os
import socket

import pytest
from tortoise import Tortoise, fields
from tortoise.exceptions import OperationalError
from tortoise.fields.base import DatabaseDefault
from tortoise.models import Model

import tortoise_extended
from tortoise_extended.bulk import (
    assert_no_relations,
    assert_pk_present,
    copy_db_fields,
    field_db_column,
)
from tortoise_extended.exceptions import PatchConflictError

_DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test",
)


def _pg_available() -> bool:
    """Quick check - can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5434), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


# =========================================================================
# Test models
# =========================================================================


class RefTarget(Model):
    """FK target for the relation-refusal tests."""

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=50)

    class Meta:
        table = "test_bulk_ref_target"


class RefSource(Model):
    """Model with an FK — a COPY record cannot carry it."""

    id = fields.IntField(primary_key=True)
    target = fields.ForeignKeyField("models.RefTarget", related_name="sources")

    class Meta:
        table = "test_bulk_ref_source"


class RefM2M(Model):
    """Model with a many-to-many field — also ineligible."""

    id = fields.IntField(primary_key=True)
    targets = fields.ManyToManyField("models.RefTarget", related_name="m2m_models")

    class Meta:
        table = "test_bulk_ref_m2m"


class RefBackward(Model):
    """Model targeted by an FK — its backward relation is ineligible too."""

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=50)

    class Meta:
        table = "test_bulk_ref_backward"


class RefChild(Model):
    """Forward side of the backward-FK pair."""

    id = fields.IntField(primary_key=True)
    parent = fields.ForeignKeyField("models.RefBackward", related_name="children")

    class Meta:
        table = "test_bulk_ref_child"


class PlainRecord(Model):
    """Insertable model: mapped column, db_default, auto_now_add."""

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=64)
    note = fields.CharField(max_length=64, source_field="note_text", null=True)
    count = fields.IntField(db_default=0)
    created_at = fields.DatetimeField(auto_now_add=True, use_tz=True)

    class Meta:
        table = "test_bulk_plain"


# =========================================================================
# Section 1 — helpers and refusals (no DB)
# =========================================================================


class TestBulkInsertHelpers:
    """The rules are decided before a connection is used."""

    def test_field_db_column_prefers_source_field(self) -> None:
        mapped = PlainRecord._meta.fields_map["note"]
        assert field_db_column(mapped, "note") == "note_text"
        assert field_db_column(PlainRecord._meta.fields_map["name"], "name") == "name"

    def test_relation_fields_are_refused_by_name(self) -> None:
        with pytest.raises(ValueError, match=r"relation fields \(target\)"):
            assert_no_relations(RefSource)

    def test_many_to_many_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"relation fields \(targets\)"):
            assert_no_relations(RefM2M)

    def test_relation_refusal_points_at_bulk_create(self) -> None:
        with pytest.raises(ValueError, match="Use bulk_create"):
            assert_no_relations(RefSource)

    def test_plain_model_passes_the_relation_check(self) -> None:
        assert_no_relations(PlainRecord)  # no raise

    def test_missing_primary_key_is_refused(self) -> None:
        with pytest.raises(ValueError, match="explicit primary keys"):
            assert_pk_present(
                PlainRecord, [PlainRecord(id=1, name="a"), PlainRecord(name="b")]
            )

    def test_present_primary_keys_pass(self) -> None:
        assert_pk_present(PlainRecord, [PlainRecord(id=1, name="a")])  # no raise

    async def test_empty_batch_returns_zero(self) -> None:
        assert await PlainRecord.bulk_insert([]) == 0

    async def test_relation_model_is_refused_before_connecting(self) -> None:
        """No database is configured here — the refusal must come first."""
        with pytest.raises(ValueError, match="relation fields"):
            await RefSource.bulk_insert([RefSource(id=1, target_id=1)])


# =========================================================================
# Section 2 — install contract
# =========================================================================


class TestBulkInsertPatch:
    """``Model.bulk_insert`` is installed once and refuses foreign owners."""

    def test_every_model_exposes_bulk_insert(self) -> None:
        assert Model.bulk_insert.__func__ is tortoise_extended.bulk_insert
        assert PlainRecord.bulk_insert.__func__ is tortoise_extended.bulk_insert

    def test_reapplying_is_idempotent(self) -> None:
        before = Model.bulk_insert.__func__
        tortoise_extended.patch()
        assert Model.bulk_insert.__func__ is before

    def test_foreign_owner_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def foreign(cls: type[Model], instances: list[Model]) -> int:
            return -1

        monkeypatch.setattr(Model, "bulk_insert", classmethod(foreign))
        with pytest.raises(PatchConflictError, match="bulk_insert"):
            tortoise_extended.patch()


# =========================================================================
# Section 3 — PostgreSQL
# =========================================================================


_TABLES = (
    "test_bulk_child",
    "test_bulk_backward",
    "test_bulk_m2m_targets",
    "test_bulk_ref_m2m",
    "test_bulk_ref_source",
    "test_bulk_ref_target",
    "test_bulk_plain",
)


class TestBulkInsertPG:
    """A real ``COPY`` writes the rows and leaves the instances persisted."""

    pytestmark = pytest.mark.skipif(
        not _pg_available(),
        reason="PostgreSQL not available on localhost:5434",
    )

    @pytest.fixture(autouse=True)
    async def _init_pg_db(self):
        await Tortoise.init(
            db_url=_DB_URL,
            modules={"models": ["tests.test_bulk_insert"]},
        )
        conn = Tortoise.get_connection("default")
        for table in _TABLES:
            await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
        await Tortoise.generate_schemas()
        yield
        for table in _TABLES:
            await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
        await Tortoise.close_connections()

    async def test_copy_db_fields_lists_projected_fields(self) -> None:
        """Every non-relation field is a projected column, mapped or not."""
        columns = copy_db_fields(PlainRecord)
        assert columns == ["id", "name", "note", "count", "created_at"]

    async def test_backward_relation_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"relation fields \(children\)"):
            assert_no_relations(RefBackward)

    async def test_inserts_rows_and_returns_count(self) -> None:
        inserted = await PlainRecord.bulk_insert(
            [PlainRecord(id=1, name="a"), PlainRecord(id=2, name="b")]
        )
        assert inserted == 2
        assert await PlainRecord.all().count() == 2

    async def test_source_field_maps_to_the_real_column(self) -> None:
        await PlainRecord.bulk_insert([PlainRecord(id=1, name="a", note="mapped")])
        conn = Tortoise.get_connection("default")
        _, rows = await conn.execute_query(
            "SELECT note_text FROM test_bulk_plain WHERE id = 1"
        )
        assert rows[0]["note_text"] == "mapped"

    async def test_db_default_applies_when_every_instance_defers(self) -> None:
        await PlainRecord.bulk_insert(
            [PlainRecord(id=1, name="a"), PlainRecord(id=2, name="b")]
        )
        rows = await PlainRecord.all().order_by("id")
        assert [row.count for row in rows] == [0, 0]

    async def test_explicit_db_default_value_is_written(self) -> None:
        await PlainRecord.bulk_insert([PlainRecord(id=1, name="a", count=7)])
        assert (await PlainRecord.get(id=1)).count == 7

    async def test_mixed_db_default_usage_raises(self) -> None:
        with pytest.raises(OperationalError, match="db_default"):
            await PlainRecord.bulk_insert(
                [PlainRecord(id=1, name="a", count=5), PlainRecord(id=2, name="b")]
            )

    async def test_auto_now_add_is_stamped(self) -> None:
        await PlainRecord.bulk_insert([PlainRecord(id=1, name="a")])
        row = await PlainRecord.get(id=1)
        assert row.created_at is not None

    async def test_instances_are_marked_persisted(self) -> None:
        record = PlainRecord(id=1, name="before")
        await PlainRecord.bulk_insert([record])
        assert record._saved_in_db is True

        record.name = "after"
        await record.save()
        assert await PlainRecord.all().count() == 1
        assert (await PlainRecord.get(id=1)).name == "after"

    async def test_missing_primary_key_raises_before_inserting(self) -> None:
        with pytest.raises(ValueError, match="explicit primary keys"):
            await PlainRecord.bulk_insert([PlainRecord(name="no-id")])
        assert await PlainRecord.all().count() == 0

    async def test_backward_relation_is_refused_once_relations_resolve(self) -> None:
        with pytest.raises(ValueError, match="relation fields"):
            await RefBackward.bulk_insert([RefBackward(id=1, name="root")])

    async def test_nullable_column_round_trips(self) -> None:
        await PlainRecord.bulk_insert([PlainRecord(id=1, name="a")])
        assert (await PlainRecord.get(id=1)).note is None

    async def test_db_default_instance_still_casts_cleanly(self) -> None:
        """The instance attribute is a DatabaseDefault until COPY omits it."""
        record = PlainRecord(id=1, name="a")
        assert isinstance(record.count, DatabaseDefault)
