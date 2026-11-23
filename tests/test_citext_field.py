"""Tests for CitextField against live PostgreSQL.

``citext`` moves case-insensitive behaviour into the database: equality,
uniqueness and ``ORDER BY`` compare case-insensitively while the text keeps its
original spelling. Every assertion that depends on PostgreSQL's behaviour here
was measured against PostgreSQL 18 with the ``citext`` extension (the
measurement is named in the test), not inferred from the documentation — one
measured result is that Tortoise's ``__contains`` casts the column to
``VARCHAR`` and is *not* case-insensitive, unlike citext's own ``LIKE``.

The extension is created in the module fixture: the live Docker volume predates
the init-script change, and recreating the container is out of scope.
"""

import os
import socket
from collections.abc import AsyncGenerator

import pytest
import tortoise_extended  # noqa: F401 — apply patches
from tortoise import Tortoise, fields, models
from tortoise.exceptions import IntegrityError
from tortoise.utils import get_schema_sql

from tortoise_extended.fields import CitextField

# ---------------------------------------------------------------------------
# Configuration — a database-backed module, skipped without the Docker PG
# ---------------------------------------------------------------------------

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test",
)

TABLE = "citext_probe_rows"
UNIQUE_TABLE = "citext_probe_unique"
_TABLES = (TABLE, UNIQUE_TABLE)

MAX_LENGTH = 32
AT_LIMIT = "aB" * 16  # exactly 32 characters, mixed case
OVER_LIMIT = AT_LIMIT + "c"  # 33 characters


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


# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------


class CitextProbe(models.Model):
    """Free-text citext column with an explicit ``max_length``."""

    id = fields.IntField(primary_key=True)
    label = fields.CharField(max_length=64)
    v = CitextField(max_length=MAX_LENGTH)

    class Meta:
        table = TABLE


class CitextUniqueProbe(models.Model):
    """A UNIQUE citext column, to pin case-insensitive uniqueness."""

    id = fields.IntField(primary_key=True)
    slug = CitextField(max_length=MAX_LENGTH, unique=True)

    class Meta:
        table = UNIQUE_TABLE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def _db() -> AsyncGenerator[None]:
    """Initialize Tortoise, ensure the extension, and own our tables."""
    await Tortoise.init(db_url=DB_URL, modules={"models": [__name__]})
    conn = Tortoise.get_connection("default")
    # The extension must exist before any citext column is created. Idempotent,
    # and it never touches the container.
    await conn.execute_query("CREATE EXTENSION IF NOT EXISTS citext")
    # DROP before CREATE: generate_schemas() is IF NOT EXISTS, so a stale table
    # left behind by an aborted run would be silently reused.
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.generate_schemas()
    yield
    for table in _TABLES:
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.close_connections()


@pytest.fixture
async def _clean(_db: None) -> AsyncGenerator[None]:
    """Start every test from empty tables with fresh integer ids."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY")
    yield


# ---------------------------------------------------------------------------
# Column type
# ---------------------------------------------------------------------------


class TestCitextColumnType:
    """The column really is ``citext``, and it carries no length modifier."""

    pytestmark = pytest.mark.usefixtures("_db")

    def test_generated_ddl_uses_citext(self) -> None:
        ddl = get_schema_sql(Tortoise.get_connection("default"), safe=True)
        assert f'"{TABLE}"' in ddl
        assert f'"{UNIQUE_TABLE}"' in ddl
        assert '"v" citext' in ddl
        assert '"slug" citext' in ddl

    async def test_information_schema_reports_citext(self) -> None:
        conn = Tortoise.get_connection("default")
        result = await conn.execute_query(
            "SELECT table_name, column_name, udt_name, character_maximum_length "
            "FROM information_schema.columns "
            f"WHERE table_name IN ('{TABLE}', '{UNIQUE_TABLE}')"
        )
        actual = {
            (record["table_name"], record["column_name"]): record
            for record in result[1]
        }

        assert actual[(TABLE, "v")]["udt_name"] == "citext"
        assert actual[(UNIQUE_TABLE, "slug")]["udt_name"] == "citext"
        # PostgreSQL's citext type has no length modifier — the max_length guard
        # lives in Python (CitextField.to_db_value), not in the schema.
        assert actual[(TABLE, "v")]["character_maximum_length"] is None
        assert actual[(UNIQUE_TABLE, "slug")]["character_maximum_length"] is None


# ---------------------------------------------------------------------------
# Case-insensitive equality, original spelling preserved
# ---------------------------------------------------------------------------


class TestCitextEquality:
    """``=`` ignores case; the stored text does not."""

    pytestmark = pytest.mark.usefixtures("_clean")

    async def test_filter_is_case_insensitive(self) -> None:
        await CitextProbe.create(label="probe", v="MiXeD")

        assert await CitextProbe.filter(v="mixed").count() == 1
        assert await CitextProbe.filter(v="MIXED").count() == 1
        assert await CitextProbe.filter(v="MiXeD").count() == 1
        # A near miss is still a miss: the comparison is case-insensitive, not
        # permissive.
        assert await CitextProbe.filter(v="mixed2").count() == 0

    async def test_get_by_case_variant_returns_the_row(self) -> None:
        created = await CitextProbe.create(label="probe", v="MiXeD")

        fetched = await CitextProbe.get(v="MIXED")
        assert fetched.id == created.id
        assert fetched.label == "probe"

    async def test_round_trip_preserves_original_spelling(self) -> None:
        created = await CitextProbe.create(label="probe", v="MiXeD")

        fetched = await CitextProbe.get(id=created.id)
        assert fetched.v == "MiXeD"

        uppercase = await CitextProbe.create(label="upper", v="UPPER")
        assert (await CitextProbe.get(id=uppercase.id)).v == "UPPER"


# ---------------------------------------------------------------------------
# Case-insensitive ORDER BY
# ---------------------------------------------------------------------------


class TestCitextOrdering:
    """``ORDER BY`` sorts by the lowercased text, measured here."""

    pytestmark = pytest.mark.usefixtures("_clean")

    async def test_order_by_is_case_insensitive_measured(self) -> None:
        """Measured 2026-11-23, PostgreSQL 18 + citext.

        ``ORDER BY`` compares via the lowercased form, so ``"apple"`` sorts
        first and ``"Zebra"`` last. ``"MiXeD"`` and ``"mixed"`` compare equal,
        so they land as adjacent neighbours between the two; which of the pair
        comes first is physical order, not a case rule, and is deliberately
        not asserted.

        The cluster's locale-aware collation already sorts ``text`` roughly
        this way, so the ordering alone does not prove the column is citext.
        ``COUNT(DISTINCT v)`` does, independently of the collation: equality
        is the operator's, and citext's is case-insensitive, so the two case
        variants are one distinct value (three distinct values here, not four).
        """
        for value in ("Zebra", "apple", "MiXeD", "mixed"):
            await CitextProbe.create(label="sort", v=value)

        ordered = [row.v for row in await CitextProbe.all().order_by("v")]

        assert ordered[0] == "apple"
        assert ordered[-1] == "Zebra"
        assert len(ordered) == 4
        assert set(ordered[1:3]) == {"MiXeD", "mixed"}

        conn = Tortoise.get_connection("default")
        distinct = await conn.execute_query(
            "SELECT COUNT(DISTINCT v) AS n FROM citext_probe_rows"
        )
        assert distinct[1][0]["n"] == 3


# ---------------------------------------------------------------------------
# Case-insensitive uniqueness
# ---------------------------------------------------------------------------


class TestCitextUnique:
    """A UNIQUE citext column rejects a case variant of an existing value."""

    pytestmark = pytest.mark.usefixtures("_clean")

    async def test_unique_constraint_rejects_case_variant(self) -> None:
        """Measured 2026-11-23: Tortoise raises ``IntegrityError``.

        Tortoise wraps the driver's ``asyncpg.UniqueViolationError`` (a
        ``IntegrityConstraintViolationError``) in ``tortoise.exceptions
        .IntegrityError``; the original is available as ``__cause__``.
        """
        await CitextUniqueProbe.create(slug="MiXeD")

        with pytest.raises(IntegrityError) as excinfo:
            await CitextUniqueProbe.create(slug="mixed")

        assert excinfo.value.__cause__ is not None
        assert "duplicate key" in str(excinfo.value).lower()
        assert await CitextUniqueProbe.all().count() == 1

    async def test_unique_constraint_allows_distinct_values(self) -> None:
        await CitextUniqueProbe.create(slug="alpha")
        await CitextUniqueProbe.create(slug="beta")
        assert await CitextUniqueProbe.all().count() == 2


# ---------------------------------------------------------------------------
# LIKE / ILIKE behaviour
# ---------------------------------------------------------------------------


class TestCitextContains:
    """``__contains`` / ``__icontains``, recorded exactly as measured."""

    pytestmark = pytest.mark.usefixtures("_clean")

    async def test_contains_measured_orm_casts_column_to_varchar(self) -> None:
        """Measured 2026-11-23, PostgreSQL 18 + citext.

        The column type behaves as documented: raw ``v LIKE '%IX%'`` on the
        citext column is case-insensitive and matches ``"MiXeD"``.

        Through Tortoise the two operators disagree, and that is what this test
        pins: ``__contains`` compiles to a ``CAST("v" AS VARCHAR) LIKE $1`` with
        an explicit ``ESCAPE`` clause — the cast to ``VARCHAR`` selects the
        case-sensitive ``~~(text, text)`` operator, so ``__contains="IX"`` does
        **not** match ``"MiXeD"``. ``__icontains`` compiles to
        ``UPPER(CAST("v" AS VARCHAR)) LIKE UPPER($1)`` and does match.
        ``tortoise.filters.contains`` and friends apply that cast to every
        field type; ``CitextField`` cannot change it.
        """
        await CitextProbe.create(label="probe", v="MiXeD")

        # The database's own LIKE on the citext column: case-insensitive.
        conn = Tortoise.get_connection("default")
        raw = await conn.execute_query(
            "SELECT v LIKE '%IX%' AS matched FROM citext_probe_rows"
        )
        assert raw[1][0]["matched"] is True

        # Tortoise's __contains: case-SENSITIVE because of the VARCHAR cast.
        assert await CitextProbe.filter(v__contains="IX").count() == 0
        # ... and it still finds an exactly-cased substring.
        assert await CitextProbe.filter(v__contains="iX").count() == 1

        # Tortoise's __icontains: case-insensitive (UPPER on both sides).
        assert await CitextProbe.filter(v__icontains="IX").count() == 1
        assert await CitextProbe.filter(v__icontains="ixed").count() == 1

        # Still a substring match: absent text misses under both operators.
        assert await CitextProbe.filter(v__contains="zz").count() == 0
        assert await CitextProbe.filter(v__icontains="zz").count() == 0


# ---------------------------------------------------------------------------
# max_length guard
# ---------------------------------------------------------------------------


class TestCitextMaxLengthField:
    """The Python-side guard on the field itself."""

    def test_field_guard_accepts_value_at_limit(self) -> None:
        field = CitextField(max_length=5)
        assert field.to_db_value("AbCdE", None) == "AbCdE"

    def test_field_guard_rejects_one_over_and_names_the_length(self) -> None:
        field = CitextField(max_length=5)
        with pytest.raises(ValueError) as excinfo:
            field.to_db_value("AbCdEf", None)
        message = str(excinfo.value)
        assert "max_length=5" in message, message
        assert "(6 chars)" in message, message


class TestCitextMaxLengthModel:
    """The same guard reached through a real save against the column."""

    pytestmark = pytest.mark.usefixtures("_clean")

    async def test_model_guard_accepts_value_at_limit(self) -> None:
        row = await CitextProbe.create(label="max", v=AT_LIMIT)
        fetched = await CitextProbe.get(id=row.id)
        assert fetched.v == AT_LIMIT
        assert len(fetched.v) == MAX_LENGTH

    async def test_model_guard_rejects_one_over_limit(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            await CitextProbe.create(label="over", v=OVER_LIMIT)
        message = str(excinfo.value)
        assert f"max_length={MAX_LENGTH}" in message, message
        assert f"({len(OVER_LIMIT)} chars)" in message, message
        assert await CitextProbe.all().count() == 0
