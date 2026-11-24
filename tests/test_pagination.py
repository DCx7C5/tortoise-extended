"""Tests for offset pagination — ``PageInfo`` arithmetic, the
``QuerySet.paginate`` patch contract, and live PostgreSQL paging.

Two sections:

* **No database** — :class:`PageInfo` page arithmetic, argument validation and
  the patch-ownership contract. These run on a machine without Docker.
* **Live PostgreSQL** — paging against the docker stack (see
  ``docker-compose.dev.yml``): database ``tortoise_test_pag`` on
  ``127.0.0.1:5434``, override with ``TORTOISE_TEST_DB``. The database-backed
  classes skip when the server is unreachable.

Run with: uv run pytest tests/test_pagination.py -v
"""

import math
from collections.abc import AsyncGenerator
from dataclasses import FrozenInstanceError
from typing import cast

import pytest
from tortoise import Tortoise, fields
from tortoise.models import Model
from tortoise.queryset import QuerySet

import tortoise_extended  # importing applies the monkey-patches
from tortoise_extended import (
    PageInfo,
    PatchConflictError,
    _apply_patches,
    paginate,
)
from tests._pg_env import database_url, pg_skip_reason

# ---------------------------------------------------------------------------
# Test model — used by the no-DB validation tests and the live section
# ---------------------------------------------------------------------------


class PaginationRow(Model):
    """Row for pagination tests: a filter column and an explicit sort key."""

    id = fields.IntField(primary_key=True)
    bucket = fields.CharField(max_length=16)
    sort_key = fields.IntField()

    class Meta:
        table = "test_pagination_rows"


def _info(total: int, *, page: int = 1, page_size: int = 2) -> PageInfo[PaginationRow]:
    """Build a ``PageInfo`` with no items and the given metadata."""
    return PageInfo(items=[], page=page, page_size=page_size, total=total)


# =========================================================================
# Section 1 — Page arithmetic (no database)
# =========================================================================


class TestPageInfoMath:
    """``PageInfo`` page arithmetic — pure computation, no connection needed."""

    @pytest.mark.parametrize(
        ("total", "page_size", "expected"),
        [
            (0, 1, 0),
            (0, 10, 0),
            (1, 10, 1),
            (4, 2, 2),
            (5, 2, 3),
            (6, 2, 3),
            (7, 3, 3),
            (10, 10, 1),
            (11, 10, 2),
        ],
    )
    def test_total_pages(self, total: int, page_size: int, expected: int) -> None:
        """``ceil(total / page_size)``, and ``0`` for an empty result set.

        The partial-last-page rows (5/2, 7/3, 11/10) are the cases a floor
        division gets wrong.
        """
        assert _info(total, page_size=page_size).total_pages == expected

    def test_exact_multiple_has_no_spare_page(self) -> None:
        """4 rows of 2 are exactly 2 pages — not 3."""
        assert _info(4, page_size=2).total_pages == 2

    def test_partial_last_page_counts_as_a_page(self) -> None:
        """5 rows of 2 are 3 pages; the last one holds a single row."""
        assert _info(5, page_size=2).total_pages == 3

    def test_empty_result_set_has_no_pages_and_no_flags(self) -> None:
        """An empty result set has zero pages, and neither flag is set."""
        info = _info(0, page=1, page_size=5)

        assert info.items == []
        assert info.total_pages == 0
        assert info.has_next is False
        assert info.has_prev is False

    def test_first_page_flags(self) -> None:
        """Page 1 of 3: nothing before it, something after it."""
        info = _info(5, page=1, page_size=2)

        assert info.has_prev is False
        assert info.has_next is True

    @pytest.mark.parametrize("page", [3, 4, 99])
    def test_page_past_the_end_keeps_the_true_total(self, page: int) -> None:
        """A page past the end is empty, not an error, and keeps ``total``."""
        info = _info(5, page=page, page_size=2)

        assert info.items == []
        assert info.total == 5
        assert info.total_pages == 3
        assert info.has_prev is True
        assert info.has_next is False

    def test_page_info_is_frozen(self) -> None:
        """``PageInfo`` is immutable — metadata cannot be edited after the fact."""
        info = _info(4, page=1, page_size=2)

        with pytest.raises(FrozenInstanceError):
            info.page = 2


# =========================================================================
# Section 2 — Argument validation (no database)
# =========================================================================


class TestArgumentValidation:
    """Invalid arguments are rejected before any query runs.

    The queryset is a real :class:`~tortoise.queryset.QuerySet` with no
    connection attached: these tests fail with a connection error if a future
    implementation moves the checks below the ``count()`` call, so they prove
    the rejection happens first.
    """

    @pytest.mark.parametrize(
        ("page", "page_size", "fragment"),
        [
            (0, 10, r"^page must be >= 1, got 0$"),
            (-1, 10, r"^page must be >= 1, got -1$"),
            (1, 0, r"^page_size must be >= 1, got 0$"),
            (1, -1, r"^page_size must be >= 1, got -1$"),
        ],
    )
    async def test_rejects_non_positive_arguments(
        self, page: int, page_size: int, fragment: str
    ) -> None:
        """``ValueError`` names the offending argument and the received value."""
        with pytest.raises(ValueError, match=fragment):
            await paginate(QuerySet(PaginationRow), page, page_size)

    @pytest.mark.parametrize(("page", "page_size"), [("2", 10), (2, "10")])
    async def test_string_arguments_are_not_coerced(
        self, page: object, page_size: object
    ) -> None:
        """A query-string value is the caller's to convert — never coerced here.

        Silently accepting ``"2"`` would hide a tampered or malformed request
        parameter behind an implicit ``int()``.
        """
        with pytest.raises(TypeError):
            # ``cast`` is a no-op at runtime: the string still reaches the call,
            # so this proves the function compares rather than coerces.
            await paginate(
                QuerySet(PaginationRow), cast(int, page), cast(int, page_size)
            )


# =========================================================================
# Section 3 — Patch contract (no database)
# =========================================================================


class TestPatchContract:
    """``QuerySet.paginate`` follows the package's identity-ownership contract."""

    def test_paginate_is_installed_on_queryset(self) -> None:
        """The import-time patch installs this package's function on QuerySet."""
        assert QuerySet.paginate is paginate

    def test_paginate_is_exported(self) -> None:
        """Both names are public API — a missing ``__all__`` entry breaks ``*``."""
        assert "PageInfo" in tortoise_extended.__all__
        assert "paginate" in tortoise_extended.__all__
        assert tortoise_extended.paginate is paginate
        assert tortoise_extended.PageInfo is PageInfo

    def test_reapplying_patches_keeps_the_same_object(self) -> None:
        """Re-running the patches on our own function is an identity no-op."""
        before = QuerySet.paginate

        _apply_patches()
        _apply_patches()

        assert QuerySet.paginate is before
        assert QuerySet.paginate is paginate

    def test_foreign_paginate_raises_and_is_restored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A foreign object under the name raises instead of silently skipping.

        ``monkeypatch`` restores the real function at teardown even if the
        assertions below fail, and ``undo()`` restores it inside the test so
        the restored identity is asserted here rather than assumed.
        """

        async def _foreign_paginate(
            queryset: QuerySet[PaginationRow], page: int, page_size: int
        ) -> PageInfo[PaginationRow]:
            raise AssertionError("foreign paginate must never be called")

        monkeypatch.setattr(QuerySet, "paginate", _foreign_paginate)
        with pytest.raises(PatchConflictError, match=r"QuerySet\.paginate"):
            _apply_patches()

        monkeypatch.undo()
        assert QuerySet.paginate is paginate


# =========================================================================
# Section 4 — Live PostgreSQL integration
# =========================================================================

DB_URL = database_url()

TABLE = "test_pagination_rows"


_PG_SKIP_REASON = pg_skip_reason()

#: Skip the database-backed classes (not the arithmetic ones) without Docker.
_requires_pg = [
    pytest.mark.skipif(
        _PG_SKIP_REASON is not None,
        reason=_PG_SKIP_REASON or "",
    ),
    pytest.mark.usefixtures("_clean_rows"),
]


@pytest.fixture(scope="module")
async def _db() -> AsyncGenerator[None]:
    """Initialize Tortoise ORM for the test module and own its schema."""
    await Tortoise.init(
        db_url=DB_URL,
        modules={"models": ["tests.test_pagination"]},
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


async def _seed(count: int, *, bucket: str = "a") -> list[PaginationRow]:
    """Create ``count`` rows in ``bucket`` with ascending ``sort_key``."""
    return [
        await PaginationRow.create(bucket=bucket, sort_key=index)
        for index in range(count)
    ]


class TestPaginateAgainstPostgres:
    """``paginate`` against a live table: real offsets, real ``COUNT(*)``."""

    pytestmark = _requires_pg

    async def test_walks_every_page_disjointly(self) -> None:
        """The pages are disjoint and their union is every row.

        This is the off-by-one detector: an offset of ``page * page_size``
        (instead of ``(page - 1) * page_size``) skips the first page and
        repeats later rows, so the union misses ids.
        """
        rows = await _seed(7)
        all_ids = {row.id for row in rows}

        page_ids: list[list[int]] = []
        for page_number in (1, 2, 3):
            info = await paginate(PaginationRow.all().order_by("id"), page_number, 3)

            assert info.page == page_number
            assert info.page_size == 3
            assert info.total == 7
            assert info.total_pages == 3
            assert info.has_prev is (page_number > 1)
            assert info.has_next is (page_number < 3)
            page_ids.append([row.id for row in info.items])

        assert [len(ids) for ids in page_ids] == [3, 3, 1]

        collected = [row_id for ids in page_ids for row_id in ids]
        assert len(collected) == len(set(collected)), "pages overlap"
        assert set(collected) == all_ids, "pages do not cover every row"

    async def test_total_respects_the_filter_not_the_table(self) -> None:
        """``total`` counts the filtered set — not every row in the table."""
        bucket_a = await _seed(5, bucket="a")
        bucket_b = await _seed(3, bucket="b")
        a_ids = {row.id for row in bucket_a}
        b_ids = {row.id for row in bucket_b}

        info = await paginate(
            PaginationRow.filter(bucket="a").order_by("id"), page=1, page_size=2
        )

        assert info.total == 5
        assert info.total_pages == 3
        assert [row.id for row in info.items] == sorted(a_ids)[:2]

        collected: list[int] = []
        for page_number in (1, 2, 3):
            page = await paginate(
                PaginationRow.filter(bucket="a").order_by("id"),
                page_number,
                2,
            )
            collected.extend(row.id for row in page.items)

        assert set(collected) == a_ids
        assert not set(collected) & b_ids

    async def test_empty_result_set(self) -> None:
        """No rows: zero pages, empty page, neither flag set."""
        info = await paginate(PaginationRow.all().order_by("id"), page=1, page_size=5)

        assert info.items == []
        assert info.total == 0
        assert info.total_pages == 0
        assert info.has_next is False
        assert info.has_prev is False

    @pytest.mark.parametrize("page", [4, 99])
    async def test_page_past_the_end_returns_no_items(self, page: int) -> None:
        """A page past the end is empty with the true ``total``, not an error."""
        await _seed(5)

        info = await paginate(PaginationRow.all().order_by("id"), page, 2)

        assert info.items == []
        assert info.total == 5
        assert info.total_pages == 3
        assert info.has_prev is True
        assert info.has_next is False

    async def test_ordering_is_honest_across_pages(self) -> None:
        """Concatenated pages equal the same queryset's ``limit(total)`` order."""
        await _seed(7)
        base = PaginationRow.all().order_by("-sort_key")
        total = await base.count()
        expected = [row.id for row in await base.limit(total)]

        collected: list[int] = []
        for page_number in range(1, math.ceil(total / 3) + 1):
            info = await paginate(base, page_number, 3)
            collected.extend(row.id for row in info.items)

        assert collected == expected
        assert [row.sort_key for row in await base.limit(total)] == [
            6,
            5,
            4,
            3,
            2,
            1,
            0,
        ]

    async def test_oversized_page_size_returns_one_page(self) -> None:
        """A page size larger than the table yields one page holding every row."""
        await _seed(3)

        info = await paginate(
            PaginationRow.all().order_by("id"), page=1, page_size=1_000_000
        )

        assert info.total == 3
        assert info.total_pages == 1
        assert len(info.items) == 3
        assert info.has_next is False
        assert info.has_prev is False

    async def test_installed_method_form(self) -> None:
        """The patched ``QuerySet.paginate`` binds the queryset as its first arg."""
        await _seed(4)
        queryset = PaginationRow.all().order_by("id")

        via_method = await queryset.paginate(page=2, page_size=2)
        via_function = await paginate(queryset, page=2, page_size=2)

        assert [row.id for row in via_method.items] == [
            row.id for row in via_function.items
        ]
        assert via_method.page == 2
        assert via_method.page_size == 2
        assert via_method.total == 4
        assert via_method.total_pages == 2
        assert via_method.has_prev is True
        assert via_method.has_next is False
