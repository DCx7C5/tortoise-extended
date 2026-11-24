"""Tests for window-function builders — SQL rendering, argument validation,
public exports and live PostgreSQL window evaluation.

Two sections:

* **No database** — exact SQL for all nine builders, two-column partitions,
  ``DESC``/``ASC`` ordering, the empty ``OVER()`` form, frame configuration,
  name/bucket validation, the ``F()`` trap and the public exports. These run
  on a machine without Docker.
* **Live PostgreSQL** — window evaluation against the docker stack (see
  ``docker-compose.dev.yml``): database ``tortoise_test_win`` on
  ``127.0.0.1:5434``, override with ``TORTOISE_TEST_DB``. The database-backed
  class skips when the server is unreachable.

Every live read carries a deterministic tiebreaker (``region``, ``day``) so no
assertion depends on PostgreSQL's arbitrary order among window peers.

Run with: uv run pytest tests/test_windows.py -v
"""

from collections.abc import AsyncGenerator, Callable

import pytest
from pypika_tortoise.analytics import CURRENT_ROW, Preceding
from pypika_tortoise.terms import AnalyticFunction
from tortoise import Tortoise, fields
from tortoise.expressions import F
from tortoise.models import Model

import tortoise_extended  # importing applies the monkey-patches
from tortoise_extended import (
    avg_over,
    count_over,
    dense_rank,
    expressions,
    max_over,
    min_over,
    ntile,
    rank,
    row_number,
    sum_over,
)
from tests._pg_env import database_url, pg_skip_reason

#: Every public builder name, used by the export contract.
BUILDER_NAMES = (
    "avg_over",
    "count_over",
    "dense_rank",
    "max_over",
    "min_over",
    "ntile",
    "rank",
    "row_number",
    "sum_over",
)

# ---------------------------------------------------------------------------
# Test model — the live section; the no-DB section only needs the builders
# ---------------------------------------------------------------------------


class Sale(Model):
    """Row for window tests: two regions, one row per day, a summed amount."""

    id = fields.IntField(primary_key=True)
    region = fields.CharField(max_length=8)
    day = fields.IntField()
    amount = fields.IntField()

    class Meta:
        table = "test_window_sales"


# =========================================================================
# Section 1 — Rendering (no database)
# =========================================================================


class TestRendering:
    """The builders emit exactly the SQL the docs promise."""

    @pytest.mark.parametrize(
        ("term", "expected"),
        [
            pytest.param(row_number(), "ROW_NUMBER() OVER()", id="row_number"),
            pytest.param(rank(), "RANK() OVER()", id="rank"),
            pytest.param(dense_rank(), "DENSE_RANK() OVER()", id="dense_rank"),
            pytest.param(ntile(1), "NTILE(1) OVER()", id="ntile-one"),
            pytest.param(ntile(4), "NTILE(4) OVER()", id="ntile-four"),
            pytest.param(sum_over("amount"), 'SUM("amount") OVER()', id="sum"),
            pytest.param(avg_over("amount"), 'AVG("amount") OVER()', id="avg"),
            pytest.param(count_over(), "COUNT(*) OVER()", id="count-star"),
            pytest.param(
                count_over("amount"), 'COUNT("amount") OVER()', id="count-field"
            ),
            pytest.param(min_over("amount"), 'MIN("amount") OVER()', id="min"),
            pytest.param(max_over("amount"), 'MAX("amount") OVER()', id="max"),
        ],
    )
    def test_builders_render_exact_sql(
        self, term: AnalyticFunction, expected: str
    ) -> None:
        """Each builder renders its analytic function with an explicit ``OVER()``.

        The empty ``OVER()`` is the no-argument case: omitting ``.over()``
        would render a bare ``ROW_NUMBER()``, which PostgreSQL rejects as a
        set-returning function at the wrong place — so the exact string is the
        contract, not a substring check.
        """
        assert str(term) == expected

    def test_two_column_partition_and_order_directions(self) -> None:
        """A sequence partitions by every name; ``-`` means ``DESC``, else ``ASC``.

        Catches a builder that keeps only the first partition column, ignores
        the ``DESC`` marker, or drops the explicit ``ASC``.
        """
        assert str(
            row_number(partition_by=("region", "day"), order_by=("-amount", "day"))
        ) == (
            'ROW_NUMBER() OVER(PARTITION BY "region","day" '
            'ORDER BY "amount" DESC,"day" ASC)'
        )
        assert (
            str(sum_over("amount", partition_by="region", order_by="day"))
            == 'SUM("amount") OVER(PARTITION BY "region" ORDER BY "day" ASC)'
        )

    def test_frame_is_configured_on_the_returned_term(self) -> None:
        """A caller-supplied frame renders inside ``OVER(...)`` (documented).

        Frames are deliberately not builder arguments; the terms are ordinary
        pypika analytics, so ``.rows()`` on the returned term must work.
        """
        framed = sum_over("amount", partition_by="region", order_by="day").rows(
            Preceding(2), CURRENT_ROW
        )

        assert str(framed) == (
            'SUM("amount") OVER(PARTITION BY "region" ORDER BY "day" ASC '
            "ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)"
        )


# =========================================================================
# Section 2 — Validation (no database)
# =========================================================================


class TestValidation:
    """Bad names and bucket counts are rejected before any SQL is built."""

    @pytest.mark.parametrize(
        ("build", "bad"),
        [
            pytest.param(
                lambda name: row_number(partition_by=name),
                "table.column",
                id="partition-qualified",
            ),
            pytest.param(
                lambda name: rank(partition_by=name),
                "1bad",
                id="partition-leading-digit",
            ),
            pytest.param(
                lambda name: dense_rank(partition_by=name),
                "-",
                id="partition-lone-marker",
            ),
            pytest.param(
                lambda name: row_number(order_by=name),
                "table.column",
                id="order-qualified",
            ),
            pytest.param(
                lambda name: ntile(2, order_by=name),
                "1bad",
                id="order-leading-digit",
            ),
            pytest.param(
                lambda name: sum_over(name), "table.column", id="field-qualified"
            ),
            pytest.param(lambda name: min_over(name), "1bad", id="field-leading-digit"),
            pytest.param(lambda name: max_over(name), "-", id="field-lone-marker"),
        ],
    )
    def test_bad_names_raise_value_error_naming_the_value(
        self, build: Callable[[str], AnalyticFunction], bad: str
    ) -> None:
        """``ValueError`` names the offending value — not a generic message.

        A qualified name would need table resolution, a leading digit is not an
        identifier, and a lone ``-`` (or anything else outside
        ``[A-Za-z_][A-Za-z0-9_]*``) is not a column this builder may quote.
        """
        with pytest.raises(ValueError) as excinfo:
            build(bad)

        message = str(excinfo.value)
        assert repr(bad) in message
        assert "not a plain field name" in message

    def test_lone_desc_marker_is_rejected_as_an_order_name(self) -> None:
        """``order_by="-"`` is refused instead of becoming ``ORDER BY ""``.

        The leading ``-`` is stripped as the ``DESC`` marker, so the empty name
        that remains is what the message names; the exception type is the
        contract asserted here.
        """
        with pytest.raises(ValueError):
            row_number(order_by="-")

    @pytest.mark.parametrize("buckets", [0, -1])
    def test_ntile_rejects_fewer_than_one_bucket(self, buckets: int) -> None:
        """``ntile(0)`` and negatives are refused; the message names the count.

        The boundary is inclusive at 1: ``NTILE(1)`` is legal and is covered by
        the rendering table.
        """
        with pytest.raises(ValueError) as excinfo:
            ntile(buckets)

        assert str(buckets) in str(excinfo.value)
        assert ">= 1" in str(excinfo.value)

    def test_f_expression_is_refused(self) -> None:
        """Tortoise ``F()`` is not a pypika term — the documented trap.

        Passing ``F("bucket")`` must raise ``ValueError``, never splice
        ``<tortoise.expressions.F object at 0x...>`` into ``PARTITION BY``.
        """
        with pytest.raises(ValueError, match="not a plain field name"):
            row_number(partition_by=F("bucket"))


# =========================================================================
# Section 3 — Public exports (no database)
# =========================================================================


class TestExports:
    """The builders are reachable from both packages and listed in ``__all__``."""

    def test_every_builder_is_exported_from_both_packages(self) -> None:
        """Top-level and ``expressions`` expose the same object under each name.

        A missing ``__all__`` entry breaks ``from ... import *``; a different
        object under the same name means a re-export was forked.
        """
        for name in BUILDER_NAMES:
            top_level = getattr(tortoise_extended, name)
            nested = getattr(expressions, name)

            assert top_level is nested, name
            assert name in tortoise_extended.__all__, name
            assert name in expressions.__all__, name

    def test_expressions_dunder_all_is_ascii_sorted(self) -> None:
        """``tortoise_extended.expressions.__all__`` is sorted, not append-ordered.

        ``sorted()`` is plain code-point order (uppercase before lowercase),
        which is the ordering the package documents.
        """
        assert expressions.__all__ == sorted(expressions.__all__)


# =========================================================================
# Section 4 — Live PostgreSQL integration
# =========================================================================

DB_URL = database_url()

TABLE = "test_window_sales"

#: ``(region, day, amount)`` — two partitions of three rows each.
#: East running sums: 10 / 30 / 60. West: 5 / 12 / 23.
SEED = (
    ("east", 1, 10),
    ("east", 2, 20),
    ("east", 3, 30),
    ("west", 1, 5),
    ("west", 2, 7),
    ("west", 3, 11),
)


_PG_SKIP_REASON = pg_skip_reason()

#: Skip the database-backed class (not the rendering ones) without Docker.
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
        modules={"models": ["tests.test_windows"]},
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


async def _seed() -> None:
    """Insert the fixed two-partition fixture."""
    await Sale.bulk_create(
        [Sale(region=region, day=day, amount=amount) for region, day, amount in SEED]
    )


class TestWindowFunctionsAgainstPostgres:
    """The builders evaluate as SQL windows — not as ``GROUP BY`` aggregates."""

    pytestmark = _requires_pg

    async def test_row_numbers_restart_per_partition_and_cover_the_whole_set(
        self,
    ) -> None:
        """Partitioned numbers restart at 1; unpartitioned numbers are 1..N.

        Peers in the unpartitioned window (equal ``day``) are ordered
        arbitrarily by SQL, so only the *set* of positions is asserted there;
        the partitioned read is fully ordered by ``region``, ``day``.
        """
        await _seed()

        partitioned = await Sale.annotate(
            position=row_number(partition_by="region", order_by="day")
        ).order_by("region", "day")

        assert [(row.region, row.position) for row in partitioned] == [
            ("east", 1),
            ("east", 2),
            ("east", 3),
            ("west", 1),
            ("west", 2),
            ("west", 3),
        ]

        whole = await Sale.annotate(position=row_number(order_by="day")).order_by(
            "day", "region"
        )

        assert sorted(row.position for row in whole) == [1, 2, 3, 4, 5, 6]
        assert len({row.position for row in whole}) == len(SEED)

    async def test_running_sum_vs_partition_total(self) -> None:
        """With ``order_by`` the default frame accumulates; without it, the total.

        Catches a dropped partition (east would start from west's rows), a
        dropped ``order_by`` (every row would carry the partition total), and a
        builder that invents a ``ROWS`` frame.
        """
        await _seed()

        rows = await Sale.annotate(
            running=sum_over("amount", partition_by="region", order_by="day"),
            total=sum_over("amount", partition_by="region"),
        ).order_by("region", "day")

        assert [(row.region, row.running, row.total) for row in rows] == [
            ("east", 10, 60),
            ("east", 30, 60),
            ("east", 60, 60),
            ("west", 5, 23),
            ("west", 12, 23),
            ("west", 23, 23),
        ]

    async def test_count_over_is_the_partition_size(self) -> None:
        """``count_over(partition_by=...)`` emits ``COUNT(*)`` per partition."""
        await _seed()

        rows = await Sale.annotate(
            region_rows=count_over(partition_by="region")
        ).order_by("region", "day")

        assert [(row.region, row.region_rows) for row in rows] == [
            ("east", 3),
            ("east", 3),
            ("east", 3),
            ("west", 3),
            ("west", 3),
            ("west", 3),
        ]

    async def test_window_query_returns_every_seeded_row(self) -> None:
        """A window annotation is not an aggregate — no row is collapsed."""
        await _seed()
        seeded_ids = set(await Sale.all().values_list("id", flat=True))

        rows = await Sale.annotate(
            position=row_number(partition_by="region", order_by="day"),
            total=sum_over("amount", partition_by="region"),
        ).order_by("region", "day")

        assert len(rows) == len(SEED)
        assert {row.id for row in rows} == seeded_ids

    async def test_order_by_annotation_alias_and_desc_window(self) -> None:
        """``order_by("position")`` orders by the alias; ``-amount`` is ``DESC``.

        Amounts are unique per region, so the row order this asserts is fully
        determined by the window ordering — no tiebreaker is needed beyond the
        partition column.
        """
        await _seed()

        rows = await Sale.annotate(
            position=row_number(partition_by="region", order_by="-amount")
        ).order_by("region", "position")

        assert [(row.region, row.amount, row.position) for row in rows] == [
            ("east", 30, 1),
            ("east", 20, 2),
            ("east", 10, 3),
            ("west", 11, 1),
            ("west", 7, 2),
            ("west", 5, 3),
        ]
