"""Tests for random queryset ordering — ``random_ordering``, the
``QuerySet.random`` patch contract, and live PostgreSQL shuffling.

Two sections:

* **No database** — the import-time patch identity, idempotent re-application
  and the foreign-object conflict guard. These run on a machine without Docker.
* **Live PostgreSQL** — SQL shape, alias collision and every documented
  composition against the docker stack (see ``docker-compose.dev.yml``):
  database ``tortoise_test_rand`` on ``127.0.0.1:5434``, override with
  ``TORTOISE_TEST_DB``. The database-backed class skips when the server is
  unreachable.

No assertion here depends on one particular shuffle: orderings are compared as
sets, and the single order-sensitive assertion only requires *a* difference
between two independent draws (a false failure needs the two draws to coincide,
probability ``1/50!``).

Run with: uv run pytest tests/test_queryset_random.py -v
"""

import os
import socket
from collections.abc import AsyncGenerator

import pytest
from tortoise import Tortoise, fields
from tortoise.contrib.postgres.functions import Random
from tortoise.models import Model
from tortoise.queryset import QuerySet

import tortoise_extended  # importing applies the monkey-patches
from tortoise_extended import (
    PatchConflictError,
    _apply_patches,
    paginate,
    random_ordering,
)

#: The fixed annotation name this module pins as public contract (docs/api/queryset.md).
RANDOM_ALIAS = "_tortoise_extended_random"

# ---------------------------------------------------------------------------
# Test model — used by the no-DB conflict test and the live section
# ---------------------------------------------------------------------------


class Item(Model):
    """Row for random-ordering tests: a filter column and a unique-ish name."""

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=32)
    bucket = fields.CharField(max_length=8)

    class Meta:
        table = "test_queryset_random_items"


# =========================================================================
# Section 1 — Patch contract (no database)
# =========================================================================


class TestPatchContract:
    """``QuerySet.random`` follows the package's identity-ownership contract."""

    def test_random_is_installed_on_queryset(self) -> None:
        """The import-time patch installs this package's function on QuerySet."""
        assert QuerySet.random is random_ordering

    def test_random_ordering_is_exported(self) -> None:
        """The name is public API — a missing ``__all__`` entry breaks ``*``."""
        assert "random_ordering" in tortoise_extended.__all__
        assert tortoise_extended.random_ordering is random_ordering

    def test_patch_reapplies_as_an_identity_no_op(self) -> None:
        """``patch()`` on our own function leaves the same object in place."""
        before = QuerySet.random

        tortoise_extended.patch()

        assert QuerySet.random is before
        assert QuerySet.random is random_ordering

    def test_foreign_random_raises_and_is_restored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A foreign object under the name raises instead of silently skipping.

        ``monkeypatch`` restores the real function at teardown even if the
        assertions below fail, and ``undo()`` restores it inside the test so
        the restored identity is asserted here rather than assumed.
        """

        def _foreign_random(queryset: QuerySet[Item]) -> QuerySet[Item]:
            raise AssertionError("foreign random must never be called")

        monkeypatch.setattr(QuerySet, "random", _foreign_random)
        with pytest.raises(PatchConflictError, match=r"QuerySet\.random"):
            _apply_patches()

        monkeypatch.undo()
        assert QuerySet.random is random_ordering


# =========================================================================
# Section 2 — Live PostgreSQL integration
# =========================================================================

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test_rand",
)

TABLE = "test_queryset_random_items"
SEED_COUNT = 50


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5434), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


#: Skip the database-backed class (not the patch contract) without Docker.
_requires_pg = [
    pytest.mark.skipif(
        not _pg_available(),
        reason="PostgreSQL not available on localhost:5434",
    ),
    pytest.mark.usefixtures("_clean_rows"),
]


@pytest.fixture(scope="module")
async def _db() -> AsyncGenerator[None]:
    """Initialize Tortoise ORM for the test module and own its schema."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_queryset_random"]},
    )
    conn = Tortoise.get_connection("default")
    # DROP before CREATE: generate_schemas() is IF NOT EXISTS, so a stale table
    # left behind by an aborted run would be silently reused.
    await conn.execute_query(f"DROP TABLE IF EXISTS {TABLE} CASCADE")
    await Tortoise.generate_schemas()
    yield
    await conn.execute_query(f"DROP TABLE IF EXISTS {TABLE} CASCADE")
    await Tortoise.close_connections()


@pytest.fixture
async def _clean_rows(_db: None) -> AsyncGenerator[None]:
    """Start every test from an empty table with fresh integer ids."""
    conn = Tortoise.get_connection("default")
    await conn.execute_query(f"TRUNCATE {TABLE} RESTART IDENTITY")
    yield


async def _seed(count: int = SEED_COUNT) -> None:
    """Create ``count`` rows, alternating between buckets ``a`` and ``b``."""
    await Item.bulk_create(
        [
            Item(name=f"row-{index:02d}", bucket="a" if index % 2 == 0 else "b")
            for index in range(count)
        ]
    )


def _names(count: int = SEED_COUNT) -> set[str]:
    """The names :func:`_seed` creates."""
    return {f"row-{index:02d}" for index in range(count)}


class TestRandomAgainstPostgres:
    """``.random()`` against a live table: real ``RANDOM()``, real shuffling."""

    pytestmark = _requires_pg

    async def test_sql_shape(self) -> None:
        """The select annotates ``RANDOM()`` and orders by that annotation."""
        sql = Item.all().random().sql()

        assert "RANDOM()" in sql
        assert f'ORDER BY "{RANDOM_ALIAS}"' in sql

    async def test_two_draws_each_return_every_row(self) -> None:
        """Both draws are full permutations of the table — and they differ.

        Set comparison, not list comparison: a correct implementation may
        return any permutation. ``first != second`` only requires that the
        ordering is not fixed; a false failure needs two independent draws to
        coincide, which is what the ``1/50!`` note in the module docstring is
        about.
        """
        await _seed()
        expected = set(await Item.all().values_list("id", flat=True))

        first = [row.id for row in await Item.all().random()]
        second = [row.id for row in await Item.all().random()]

        assert len(first) == SEED_COUNT
        assert len(second) == SEED_COUNT
        assert set(first) == expected
        assert set(second) == expected
        assert first != second

    async def test_count_is_unaffected_by_the_annotation(self) -> None:
        """``count()`` still counts rows, not the extra annotation column."""
        await _seed()

        assert await Item.all().random().count() == SEED_COUNT

    async def test_filter_composes_with_random(self) -> None:
        """The filter is kept; only the filtered rows come back."""
        await _seed()
        expected = set(await Item.filter(bucket="a").values_list("id", flat=True))

        rows = await Item.filter(bucket="a").random()

        assert len(rows) == len(expected) == SEED_COUNT // 2
        assert {row.id for row in rows} == expected
        assert {row.bucket for row in rows} == {"a"}

    async def test_limit_composes_with_random(self) -> None:
        """``limit`` applies after the shuffle and never duplicates a row."""
        await _seed()

        rows = await Item.all().random().limit(3)

        assert len(rows) == 3
        assert len({row.id for row in rows}) == 3

    async def test_values_list_composes_with_random(self) -> None:
        """``values_list`` keeps the shuffle and returns every projected value."""
        await _seed()

        names = await Item.all().random().values_list("name", flat=True)

        assert len(names) == SEED_COUNT
        assert set(names) == _names()

    async def test_paginate_composes_with_random(self) -> None:
        """``paginate`` pages a random-ordered queryset with the true total."""
        await _seed()
        expected = set(await Item.all().values_list("id", flat=True))

        info = await paginate(Item.all().random(), page=1, page_size=10)

        assert len(info.items) == 10
        assert {row.id for row in info.items} <= expected
        assert info.total == SEED_COUNT
        assert info.total_pages == SEED_COUNT // 10
        assert info.page == 1
        assert info.page_size == 10

    async def test_reserved_alias_collision_is_refused(self) -> None:
        """A caller annotation on the reserved alias raises, naming the alias."""
        await _seed()
        queryset = Item.all().annotate(**{RANDOM_ALIAS: Random()})

        with pytest.raises(ValueError, match=RANDOM_ALIAS) as excinfo:
            queryset.random()

        assert "already annotates" in str(excinfo.value)
        assert RANDOM_ALIAS in str(excinfo.value)

    async def test_random_does_not_mutate_the_source_queryset(self) -> None:
        """``.random()`` annotates a clone; the source keeps its own ordering."""
        await _seed()
        ordered_ids = [row.id for row in await Item.all().order_by("id")]
        base = Item.all().order_by("id")

        shuffled = base.random()

        assert [row.id for row in await base] == ordered_ids
        assert len([row.id for row in await shuffled]) == SEED_COUNT
