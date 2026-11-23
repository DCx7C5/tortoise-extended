"""Tests for :class:`tortoise_extended.VersionMixin` and the optimistic-lock
contract it promises.

Every case runs against a live PostgreSQL (see ``docker-compose.dev.yml``):
database ``tortoise_test_ver`` on ``127.0.0.1:5434``, override with
``TORTOISE_TEST_DB``. The class skips when the server is unreachable.

The guarded save is the whole point of the mixin, so the tests assert *both*
sides of it: what lands in the row and what stays in memory when the guard
refuses. The bypass surfaces (``bulk_create``, ``bulk_update``,
``QuerySet.update()``) are covered because they silently defeat a lock that
only exists inside ``save()``.

Run with: uv run pytest tests/test_versioned.py -q
"""

import asyncio
import os
import socket
from collections.abc import AsyncGenerator
from urllib.parse import urlsplit

import asyncpg
import pytest
from asyncpg import exceptions as pg_errors
from tortoise import Tortoise, fields
from tortoise.exceptions import (
    IncompleteInstanceError,
    IntegrityError,
    OperationalError,
)

import tortoise_extended
from tortoise_extended import (
    BaseModel,
    TimestampMixin,
    VersionMixin,
    VersionMismatchError,
)

# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------

TABLE = "test_version_doc"
PLAIN_TABLE = "test_version_plain"


class Doc(BaseModel, TimestampMixin, VersionMixin):
    """Concrete model stacking the version guard on the identity + timestamp bases."""

    title = fields.CharField(max_length=64)
    body = fields.CharField(max_length=128, default="")

    class Meta:
        table = TABLE


class Plain(VersionMixin):
    """The mixin directly on ``Model``: tortoise supplies the auto ``id`` pk."""

    label = fields.CharField(max_length=32)

    class Meta:
        table = PLAIN_TABLE


# ---------------------------------------------------------------------------
# Live PostgreSQL harness
# ---------------------------------------------------------------------------

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test_ver",
)


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5434), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


#: Skip the database-backed class (not the export checks) without Docker.
_requires_pg = [
    pytest.mark.skipif(
        not _pg_available(),
        reason="PostgreSQL not available on localhost:5434",
    ),
    pytest.mark.usefixtures("_clean_rows"),
]


async def _ensure_database() -> None:
    """Create the module's database when the docker stack does not have it.

    ``CREATE DATABASE`` cannot run inside a transaction, so it goes through a
    throwaway asyncpg connection to the ``postgres`` maintenance database
    before Tortoise initialises. The available shell grant here is
    pytest/ruff-only, so the module provisions its own database instead of
    relying on an out-of-band ``createdb``.
    """
    parts = urlsplit(DB_URL)
    db_name = parts.path.lstrip("/")
    if not db_name or parts.scheme not in {"postgres", "postgresql"}:
        return
    if not db_name.replace("_", "").isalnum():
        raise ValueError(f"refusing to CREATE DATABASE from {db_name!r}")
    try:
        probe = await asyncpg.connect(DB_URL)
    except pg_errors.InvalidCatalogNameError:
        admin = await asyncpg.connect(parts._replace(path="/postgres").geturl())
        try:
            await admin.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin.close()
        return
    await probe.close()


@pytest.fixture(scope="module")
async def _db() -> AsyncGenerator[None]:
    """Initialize Tortoise ORM for the test module and own its schema."""
    await _ensure_database()
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_versioned"]},
    )
    conn = Tortoise.get_connection("default")
    # DROP before CREATE: generate_schemas() is IF NOT EXISTS, so a stale table
    # left behind by an aborted run would be silently reused.
    for table in (TABLE, PLAIN_TABLE):
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.generate_schemas()
    yield
    for table in (TABLE, PLAIN_TABLE):
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.close_connections()


@pytest.fixture
async def _clean_rows(_db: None) -> AsyncGenerator[None]:
    """Start every test from empty tables with fresh identities."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(f"TRUNCATE {TABLE}, {PLAIN_TABLE} RESTART IDENTITY")
    yield


class TestVersionGuardAgainstPostgres:
    """``save()`` is an optimistic lock: bump on success, refuse on a stale read."""

    pytestmark = _requires_pg

    async def test_insert_starts_at_version_zero(self) -> None:
        """A fresh insert writes ``version = 0`` and the column exists.

        Catches a default that starts at 1 (every first guarded save would then
        report version 1 instead of 2) and a column missing from the DDL.
        """
        doc = Doc(title="alpha", body="first")
        await doc.save()

        assert doc.pk is not None
        assert doc.version == 0
        assert doc._saved_in_db is True

        row = await Doc.get(id=doc.pk)
        assert row.version == 0

        columns = await Tortoise.get_connection("default").execute_query_dict(
            "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
            [TABLE],
        )
        assert "version" in {column["column_name"] for column in columns}

    async def test_guarded_update_bumps_version_and_persists_field(self) -> None:
        """One guarded save → version 1 in memory *and* in the row."""
        doc = await Doc.create(title="alpha", body="first")
        assert doc.version == 0

        doc.title = "beta"
        await doc.save()

        assert doc.version == 1
        assert doc._saved_in_db is True
        row = await Doc.get(id=doc.pk)
        assert row.version == 1
        assert row.title == "beta"
        assert row.body == "first"

    async def test_timestamp_mixin_restamps_updated_at(self) -> None:
        """A guarded save re-stamps ``updated_at`` alongside the version bump.

        The guard writes through a plain ``QuerySet``, which never sees the
        instance, so this catches an ``auto_now`` column silently frozen at its
        insert value.
        """
        doc = await Doc.create(title="alpha")
        before = doc.updated_at

        await asyncio.sleep(0.01)
        doc.title = "beta"
        await doc.save()

        assert doc.version == 1
        assert doc.updated_at > before
        row = await Doc.get(id=doc.pk)
        assert row.updated_at > before

    async def test_stale_save_raises_with_mismatch_attributes(self) -> None:
        """A lost race reports both versions and writes nothing, anywhere.

        The stale instance keeps its in-memory edits (so the caller can merge
        rather than silently lose them) and the row keeps the winner's write.
        """
        doc = await Doc.create(title="original", body="keep")
        winner = await Doc.get(id=doc.pk)
        stale = await Doc.get(id=doc.pk)

        winner.title = "first"
        await winner.save()

        stale.title = "stale"
        with pytest.raises(VersionMismatchError) as excinfo:
            await stale.save()

        exc = excinfo.value
        assert isinstance(exc, tortoise_extended.VersionMismatchError)
        assert exc.model == "Doc"
        assert exc.pk == doc.pk
        assert exc.expected == 0
        assert exc.current == 1
        assert stale.version == 0
        assert stale.title == "stale"

        row = await Doc.get(id=doc.pk)
        assert row.version == 1
        assert row.title == "first"
        assert row.body == "keep"

    async def test_refresh_then_save_recovers_from_mismatch(self) -> None:
        """Reloading adopts the row's version and the retry succeeds."""
        doc = await Doc.create(title="original")
        winner = await Doc.get(id=doc.pk)
        stale = await Doc.get(id=doc.pk)

        winner.title = "first"
        await winner.save()

        stale.title = "retry"
        with pytest.raises(VersionMismatchError):
            await stale.save()

        await stale.refresh_from_db()
        assert stale.version == 1
        # refresh_from_db() adopts the row wholesale, including the winner's
        # title: the retry must re-apply its own edit after reloading.
        assert stale.title == "first"

        stale.title = "retry"
        await stale.save()
        row = await Doc.get(id=doc.pk)
        assert row.version == 2
        assert row.title == "retry"

    async def test_force_update_on_deleted_row_is_integrity_error(self) -> None:
        """A forced update with no matching row reports a deleted object.

        Asserts the message, because the same zero-row outcome must not be
        reported as a version mismatch — there is no ``current`` version to
        name.
        """
        doc = await Doc.create(title="gone")
        pk = doc.pk
        await doc.delete()

        with pytest.raises(
            IntegrityError, match="Can't update object that doesn't exist"
        ):
            await doc.save(force_update=True)

        assert pk is not None
        assert await Doc.filter(id=pk).first() is None

    async def test_update_fields_narrows_the_write_and_bumps_version(self) -> None:
        """``update_fields`` writes only the named field (plus ``version``)."""
        doc = await Doc.create(title="alpha", body="keep")

        doc.title = "renamed"
        doc.body = "must-not-write"
        await doc.save(update_fields=["title"])

        assert doc.version == 1
        row = await Doc.get(id=doc.pk)
        assert row.version == 1
        assert row.title == "renamed"
        assert row.body == "keep"

    async def test_update_fields_naming_the_pk_raises(self) -> None:
        """Naming the primary key is an ``OperationalError`` and writes nothing."""
        doc = await Doc.create(title="alpha")

        doc.title = "beta"
        with pytest.raises(OperationalError):
            await doc.save(update_fields=["id"])

        row = await Doc.get(id=doc.pk)
        assert row.version == 0
        assert row.title == "alpha"

    async def test_force_create_inserts_without_bump(self) -> None:
        """``force_create`` takes the insert path even with an explicit pk."""
        doc = Doc(id=7777, title="forced")
        await doc.save(force_create=True)

        assert doc.version == 0
        row = await Doc.get(id=7777)
        assert row.version == 0
        assert row.title == "forced"

    async def test_bulk_paths_bypass_the_guard(self) -> None:
        """``bulk_create``/``QuerySet.update()``/``bulk_update`` never bump.

        This is the documented hole: the guard lives in ``save()`` only, so a
        lock that must hold for batch writers has to be enforced elsewhere.
        The assertions also prove the writes actually happened.
        """
        await Doc.bulk_create([Doc(title="b1"), Doc(title="b2")])
        rows = await Doc.all().order_by("id")
        assert [row.version for row in rows] == [0, 0]

        await Doc.all().update(title="bulk")
        rows = await Doc.all().order_by("id")
        assert [row.title for row in rows] == ["bulk", "bulk"]
        assert [row.version for row in rows] == [0, 0]

        for row in rows:
            row.title = f"bulk-{row.id}"
        await Doc.bulk_update(rows, ["title"])
        rows = await Doc.all().order_by("id")
        assert [row.title for row in rows] == ["bulk-1", "bulk-2"]
        assert [row.version for row in rows] == [0, 0]

    async def test_partial_save_without_update_fields_raises(self) -> None:
        """``.only("id")`` cannot be saved: neither the row nor memory moves."""
        doc = await Doc.create(title="partial")
        partial = await Doc.all().only("id").get(id=doc.pk)

        with pytest.raises(IncompleteInstanceError):
            await partial.save()

        row = await Doc.get(id=doc.pk)
        assert row.version == 0
        assert row.title == "partial"

    async def test_partial_with_version_and_update_fields_bumps(self) -> None:
        """A partial load carrying ``version`` may guarded-save a named field."""
        doc = await Doc.create(title="partial", body="kept")
        partial = await Doc.all().only("id", "version").get(id=doc.pk)

        partial.title = "patched"
        await partial.save(update_fields=["title"])

        assert partial.version == 1
        row = await Doc.get(id=doc.pk)
        assert row.version == 1
        assert row.title == "patched"
        assert row.body == "kept"

    async def test_partial_without_version_and_update_fields_raises(self) -> None:
        """Naming fields is not enough — the expected version must be loaded."""
        doc = await Doc.create(title="partial")
        partial = await Doc.all().only("id", "title").get(id=doc.pk)

        partial.title = "patched"
        with pytest.raises(IncompleteInstanceError):
            await partial.save(update_fields=["title"])

        row = await Doc.get(id=doc.pk)
        assert row.version == 0
        assert row.title == "partial"

    async def test_version_mixin_works_directly_on_model(self) -> None:
        """``class Plain(VersionMixin)`` gets an auto pk and the same guard."""
        plain = Plain(label="one")
        await plain.save()
        assert plain.pk is not None
        assert plain.version == 0

        plain.label = "two"
        await plain.save()

        assert plain.version == 1
        row = await Plain.get(id=plain.pk)
        assert row.version == 1
        assert row.label == "two"

    def test_public_exports_and_abstract_base(self) -> None:
        """Both names are exported, listed in ``__all__``, and the base is abstract.

        A concrete ``VersionMixin`` would create its own default table; the
        ``abstract`` flag is what makes stacking it on an identity base work.
        """
        assert tortoise_extended.VersionMixin is VersionMixin
        assert "VersionMixin" in tortoise_extended.__all__
        assert tortoise_extended.VersionMismatchError is VersionMismatchError
        assert "VersionMismatchError" in tortoise_extended.__all__
        assert issubclass(VersionMismatchError, Exception)
        assert VersionMixin._meta.abstract is True
