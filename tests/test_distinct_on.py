"""Tests for ``tortoise_extended.distinct_on`` — input validation, public
exports and live PostgreSQL ``SELECT DISTINCT ON`` evaluation.

Three sections:

* **Validation** — every documented refusal raises ``ValueError`` (never a
  database error) before any SQL is sent: an empty ``on``, an unknown field,
  an ``order_by`` that does not lead with ``on``, ``limit < 1``, a
  placeholder/parameter mismatch, and a queryset carrying filters,
  annotations, ordering, grouping, limit or offset.
* **Exports** — ``distinct_on`` is reachable from ``tortoise_extended`` and
  listed in ``__all__``.
* **Live PostgreSQL** — real ``DISTINCT ON`` rows against the docker stack
  (``docker-compose.dev.yml``): database ``tortoise_test_don`` on
  ``127.0.0.1:5434``, override with ``TORTOISE_TEST_DB``. The database-backed
  class skips when the server is unreachable.

The fixture is five readings per sensor over three sensors, inserted oldest
first into a freshly truncated table. Timestamps differ per sensor so the
chosen row is unambiguous whenever ``order_by`` names ``observed_at``.

Run with: uv run pytest tests/test_distinct_on.py -v
"""

import os
import socket
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime

import pytest
from tortoise import Tortoise, fields
from tortoise.functions import Count
from tortoise.models import Model

import tortoise_extended
from tortoise_extended import distinct_on
from tortoise_extended.expressions.distinct_on import _assert_asyncpg_backend

# =========================================================================
# Models
# =========================================================================


class Reading(Model):
    """One sensor reading; no ``deleted_at`` so soft-delete must not apply."""

    id = fields.IntField(primary_key=True)
    sensor_id = fields.CharField(max_length=8)
    observed_at = fields.DatetimeField()
    value = fields.IntField()

    class Meta:
        table = "test_distinct_reading"


class SoftReading(Model):
    """A reading with the soft-delete column ``distinct_on`` must honour."""

    id = fields.IntField(primary_key=True)
    sensor_id = fields.CharField(max_length=8)
    observed_at = fields.DatetimeField()
    value = fields.IntField()
    deleted_at = fields.DatetimeField(null=True)

    class Meta:
        table = "test_distinct_soft_reading"


class RenamedSoftReading(Model):
    """The soft-delete column under a different attribute name.

    ``removed_at`` projects onto the ``deleted_at`` column, so detection must
    key on the *column*, not the field name.
    """

    id = fields.IntField(primary_key=True)
    sensor_id = fields.CharField(max_length=8)
    observed_at = fields.DatetimeField()
    value = fields.IntField()
    removed_at = fields.DatetimeField(null=True, source_field="deleted_at")

    class Meta:
        table = "test_distinct_renamed_soft_reading"


def _at(hour: int, minute: int = 0) -> datetime:
    """A timestamp on the fixed fixture day, timezone-aware UTC."""
    return datetime(2026, 1, 1, hour, minute, tzinfo=UTC)


#: ``(sensor_id, observed_at, value)`` — five rows per sensor. Sensor ``a``
#: spans 00:00–04:00, ``b`` 06:00–10:00, ``c`` 12:00–16:00. The latest value
#: per sensor (51/52/53) differs from the oldest (10/11/12) so a wrong
#: ordering direction cannot pass.
SEED: tuple[tuple[str, datetime, int], ...] = tuple(
    (sensor, _at(start + offset), value)
    for sensor, start, values in (
        ("a", 0, (10, 20, 10, 20, 51)),
        ("b", 6, (11, 21, 11, 21, 52)),
        ("c", 12, (12, 22, 12, 22, 53)),
    )
    for offset, value in enumerate(values)
)

#: ``(sensor_id, observed_at, value, deleted_at)`` — sensor ``s`` has a
#: soft-deleted newest row; sensor ``t`` is entirely alive.
SOFT_SEED: tuple[tuple[str, datetime, int, datetime | None], ...] = (
    ("s", _at(0), 10, None),
    ("s", _at(1), 20, _at(1)),
    ("t", _at(0, 30), 5, None),
    ("t", _at(1, 30), 6, None),
)


# =========================================================================
# Section 1 — Validation (no database)
# =========================================================================


class TestValidation:
    """Bad arguments fail fast with ``ValueError``, never a DB error."""

    async def test_empty_on_is_refused(self) -> None:
        """``on`` is mandatory — there is no grouping without it.

        Catches an implementation that omits the guard and emits invalid
        ``SELECT DISTINCT ON ()`` SQL, which PostgreSQL would only reject once
        a connection is involved.
        """
        with pytest.raises(ValueError):
            await distinct_on(Reading)

    async def test_unknown_field_is_refused_naming_the_value(self) -> None:
        """A name outside the model's fields cannot be quoted into SQL.

        Catches blindly quoting caller text (``"no_such_column"`` would reach
        PostgreSQL as a column error, not ``ValueError``).
        """
        with pytest.raises(ValueError) as excinfo:
            await distinct_on(Reading, "no_such_column")

        assert "no_such_column" in str(excinfo.value)

    async def test_order_by_must_lead_with_the_on_columns(self) -> None:
        """A different leading column violates PostgreSQL's ``DISTINCT ON``.

        ``ORDER BY observed_at, sensor_id`` with ``DISTINCT ON (sensor_id)``
        is rejected by PostgreSQL itself, so the builder must refuse it.
        """
        with pytest.raises(ValueError) as excinfo:
            await distinct_on(
                Reading, "sensor_id", order_by=("observed_at", "sensor_id")
            )

        assert "order" in str(excinfo.value).lower()

    async def test_order_by_shorter_than_on_is_refused(self) -> None:
        """Every ``on`` column must appear at the front of ``order_by``.

        Catches a prefix check that only compares the first column and lets
        ``ORDER BY sensor_id`` through for ``DISTINCT ON (sensor_id, value)``.
        """
        with pytest.raises(ValueError) as excinfo:
            await distinct_on(Reading, "sensor_id", "value", order_by=("sensor_id",))

        assert "order" in str(excinfo.value).lower()

    @pytest.mark.parametrize("limit", [0, -3])
    async def test_limit_below_one_is_refused(self, limit: int) -> None:
        """``LIMIT 0`` / negative is a caller mistake; the message names it.

        Catches silent coercion to ``None`` or passing the value to
        PostgreSQL, which rejects a negative ``LIMIT``.
        """
        with pytest.raises(ValueError) as excinfo:
            await distinct_on(Reading, "sensor_id", limit=limit)

        assert str(limit) in str(excinfo.value)

    @pytest.mark.parametrize(
        ("where", "params"),
        [
            pytest.param("value > $1", [], id="missing-value"),
            pytest.param("value > $1", [1, 2], id="extra-value"),
            pytest.param("value > $2", [1], id="skipped-index"),
        ],
    )
    async def test_placeholder_param_mismatch_is_refused(
        self, where: str, params: list[int]
    ) -> None:
        """Ambiguous binding is refused rather than silently mis-bound.

        A missing value or a skipped index would make Postgres raise
        ``there is no parameter $2`` after a round trip; an extra value is
        dead input. All three must be ``ValueError`` before connecting.
        """
        with pytest.raises(ValueError):
            await distinct_on(Reading, "sensor_id", where=where, params=params)


# =========================================================================
# Section 2 — Public exports (no database)
# =========================================================================


class TestExports:
    """``distinct_on`` is part of the package's public surface."""

    def test_distinct_on_is_exported_and_listed_in_all(self) -> None:
        """Top-level import and ``__all__`` entry point at the same object.

        Catches a re-export that is importable but omitted from ``__all__``
        (breaks ``from tortoise_extended import *``).
        """
        assert tortoise_extended.distinct_on is distinct_on
        assert "distinct_on" in tortoise_extended.__all__


# =========================================================================
# Section 3 — Live PostgreSQL integration
# =========================================================================

DB_URL = os.environ.get(
    "TORTOISE_TEST_DB",
    "postgres://postgres:postgres@localhost:5434/tortoise_test_don",
)

READING_TABLE = "test_distinct_reading"
SOFT_TABLE = "test_distinct_soft_reading"
RENAMED_SOFT_TABLE = "test_distinct_renamed_soft_reading"


def _pg_available() -> bool:
    """Quick check — can we connect to the test PG?"""
    try:
        sock = socket.create_connection(("localhost", 5434), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


#: Skip the database-backed class (not the validation ones) without Docker.
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
        modules={"models": ["tests.test_distinct_on"]},
    )
    conn = Tortoise.get_connection("default")
    # DROP before CREATE: generate_schemas() is IF NOT EXISTS, so a stale table
    # left behind by an aborted run would be silently reused.
    for table in (READING_TABLE, SOFT_TABLE, RENAMED_SOFT_TABLE):
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.generate_schemas()
    yield
    for table in (READING_TABLE, SOFT_TABLE, RENAMED_SOFT_TABLE):
        await conn.execute_query(f"DROP TABLE IF EXISTS {table} CASCADE")
    await Tortoise.close_connections()


@pytest.fixture
async def _clean_rows(_db: None) -> AsyncGenerator[None]:
    """Start every test from empty tables with fresh integer ids."""
    conn = Tortoise.get_connection("default")
    for table in (READING_TABLE, SOFT_TABLE, RENAMED_SOFT_TABLE):
        await conn.execute_query(f"TRUNCATE {table} RESTART IDENTITY")
    yield


async def _seed() -> None:
    """Insert the fixed five-per-sensor fixture, oldest row first."""
    await Reading.bulk_create(
        [
            Reading(sensor_id=sensor, observed_at=observed_at, value=value)
            for sensor, observed_at, value in SEED
        ]
    )


async def _seed_soft() -> None:
    """Insert the soft-delete fixture."""
    await SoftReading.bulk_create(
        [
            SoftReading(
                sensor_id=sensor,
                observed_at=observed_at,
                value=value,
                deleted_at=deleted_at,
            )
            for sensor, observed_at, value, deleted_at in SOFT_SEED
        ]
    )


class TestDistinctOnAgainstPostgres:
    """``distinct_on`` selects real, saveable rows from PostgreSQL."""

    pytestmark = _requires_pg

    @pytest.mark.parametrize(
        "build",
        [
            pytest.param(lambda: Reading.filter(value__gt=0), id="filtered"),
            pytest.param(lambda: Reading.annotate(total=Count("id")), id="annotated"),
            pytest.param(lambda: Reading.all().order_by("value"), id="ordered"),
            pytest.param(lambda: Reading.all().group_by("sensor_id"), id="grouped"),
            pytest.param(lambda: Reading.all().limit(1), id="limited"),
            pytest.param(lambda: Reading.all().offset(1), id="offset"),
        ],
    )
    async def test_queryset_with_state_is_refused(
        self, build: Callable[[], object]
    ) -> None:
        """Only an unfiltered queryset may supply model + connection.

        ``distinct_on`` writes its own ``WHERE``/``ORDER BY``/``LIMIT``; a
        queryset that already carries any of them would be silently ignored
        or duplicated, so it is refused instead.
        """
        with pytest.raises(ValueError):
            await distinct_on(build(), "sensor_id")

    async def test_latest_row_per_group(self) -> None:
        """``-observed_at`` picks each sensor's newest reading.

        Asserts the chosen values and timestamps, not just the row count: a
        builder that keeps the first row, ignores ``DESC``, or groups by the
        wrong column fails on ``value`` 51/52/53.
        """
        await _seed()

        rows = await distinct_on(
            Reading, "sensor_id", order_by=("sensor_id", "-observed_at")
        )

        assert [(row.sensor_id, row.value, row.observed_at) for row in rows] == [
            ("a", 51, _at(4)),
            ("b", 52, _at(10)),
            ("c", 53, _at(16)),
        ]

    async def test_default_ordering_keeps_the_first_row_per_group(self) -> None:
        """With no ``order_by`` the ``on`` columns ascend; the first wins.

        ``DISTINCT ON`` leaves ties inside a group unspecified by SQL; the
        fixture inserts oldest-first into a freshly truncated heap, so the
        first scanned row is the oldest. Catches a default of
        ``-observed_at`` (which would return 51/52/53 instead of 10/11/12).
        """
        await _seed()

        rows = await distinct_on(Reading, "sensor_id")

        assert [(row.sensor_id, row.value) for row in rows] == [
            ("a", 10),
            ("b", 11),
            ("c", 12),
        ]

    async def test_limit_keeps_the_first_groups_in_order(self) -> None:
        """``limit=2`` truncates after grouping, in ``order_by`` order.

        Catches applying ``LIMIT`` to the base scan before ``DISTINCT ON``
        (which would yield two rows both from sensor ``a``).
        """
        await _seed()

        rows = await distinct_on(
            Reading,
            "sensor_id",
            order_by=("sensor_id", "-observed_at"),
            limit=2,
        )

        assert [(row.sensor_id, row.value) for row in rows] == [
            ("a", 51),
            ("b", 52),
        ]

    async def test_where_filters_before_grouping(self) -> None:
        """The predicate removes rows before the latest-per-sensor choice.

        ``value < 50`` drops every sensor's newest row (51/52/53), so the
        previous row wins: ``a`` 20 @ 03:00, ``b`` 21 @ 09:00, ``c`` 22 @
        15:00. A builder that grouped first and filtered afterwards would pick
        51/52/53 and then discard all three groups — an empty result — so this
        cannot pass without the ``WHERE`` running first. Binding is exercised
        too: the bound is a ``$1`` parameter, never inlined.
        """
        await _seed()
        ceiling = 50

        rows = await distinct_on(
            Reading,
            "sensor_id",
            order_by=("sensor_id", "-observed_at"),
            where="value < $1",
            params=[ceiling],
        )

        assert [(row.sensor_id, row.value, row.observed_at) for row in rows] == [
            ("a", 20, _at(3)),
            ("b", 21, _at(9)),
            ("c", 22, _at(15)),
        ]

    async def test_multi_column_on_groups_by_the_pair(self) -> None:
        """``on=("sensor_id", "value")`` forms nine groups, not three.

        Values repeat within a sensor (10/20 at two timestamps each), so the
        latest row of every pair is asserted: a builder that keeps only the
        first ``on`` column would return three rows.
        """
        await _seed()

        rows = await distinct_on(
            Reading,
            "sensor_id",
            "value",
            order_by=("sensor_id", "value", "-observed_at"),
        )

        assert [(row.sensor_id, row.value, row.observed_at) for row in rows] == [
            ("a", 10, _at(2)),
            ("a", 20, _at(3)),
            ("a", 51, _at(4)),
            ("b", 11, _at(8)),
            ("b", 21, _at(9)),
            ("b", 52, _at(10)),
            ("c", 12, _at(14)),
            ("c", 22, _at(15)),
            ("c", 53, _at(16)),
        ]

    async def test_returns_saveable_model_instances(self) -> None:
        """Rows are real ``Reading`` instances whose mutation persists.

        Catches a builder that returns ``dict``s or instances with
        ``_saved_in_db`` false — ``save()`` would then INSERT a duplicate or
        raise instead of updating the selected row.
        """
        await _seed()

        rows = await distinct_on(
            Reading, "sensor_id", order_by=("sensor_id", "-observed_at")
        )

        assert all(isinstance(row, Reading) for row in rows)
        assert all(row._saved_in_db is True for row in rows)

        chosen = rows[0]
        chosen.value = 999
        await chosen.save()

        reloaded = await Reading.get(id=chosen.id)
        assert reloaded.value == 999

    async def test_soft_deleted_rows_are_excluded_by_default(self) -> None:
        """A model with ``deleted_at`` filters ``IS NULL`` unless overridden.

        Sensor ``s`` has a newer soft-deleted row; the default must return its
        older 10, and ``include_deleted=True`` must return the deleted 20.
        Sensor ``t`` (no deletions) is unchanged by the flag, proving the
        filter is per-model, not global.
        """
        await _seed_soft()

        visible = await distinct_on(
            SoftReading, "sensor_id", order_by=("sensor_id", "-observed_at")
        )
        everything = await distinct_on(
            SoftReading,
            "sensor_id",
            order_by=("sensor_id", "-observed_at"),
            include_deleted=True,
        )

        assert [(row.sensor_id, row.value) for row in visible] == [
            ("s", 10),
            ("t", 6),
        ]
        assert [(row.sensor_id, row.value) for row in everything] == [
            ("s", 20),
            ("t", 6),
        ]

    async def test_unfiltered_queryset_argument_supplies_model_and_connection(
        self,
    ) -> None:
        """An unfiltered ``.using_db(...)`` queryset target works like a model.

        Catches an implementation that only accepts ``type[Model]`` and fails
        to read the connection from a queryset.
        """
        await _seed()
        queryset = Reading.all().using_db(Tortoise.get_connection("default"))

        rows = await distinct_on(
            queryset, "sensor_id", order_by=("sensor_id", "-observed_at")
        )

        assert [(row.sensor_id, row.value) for row in rows] == [
            ("a", 51),
            ("b", 52),
            ("c", 53),
        ]

    async def test_filtered_queryset_is_refused_at_the_database_boundary(
        self,
    ) -> None:
        """A queryset carrying a ``WHERE`` raises ``ValueError``, not SQL.

        Catches silently combining the caller's filter with the generated one,
        which would make the result depend on hidden state.
        """
        with pytest.raises(ValueError):
            await distinct_on(
                Reading.filter(value__gt=0),
                "sensor_id",
                order_by=("sensor_id", "-observed_at"),
            )

    async def test_model_all_target_on_soft_delete_model(self) -> None:
        """``SoftReading.all()`` is accepted: the injected condition is stripped.

        ``.all()`` on a soft-delete model carries an implicit ``deleted_at IS
        NULL`` clause. Refusing it would make the documented queryset target
        unusable on exactly the models the filter exists for; returning the
        deleted row with ``include_deleted=True`` proves the injected clause is
        dropped rather than merely tolerated.
        """
        await _seed_soft()

        visible = await distinct_on(
            SoftReading.all(), "sensor_id", order_by=("sensor_id", "-observed_at")
        )
        everything = await distinct_on(
            SoftReading.all(),
            "sensor_id",
            order_by=("sensor_id", "-observed_at"),
            include_deleted=True,
        )

        assert [(row.sensor_id, row.value) for row in visible] == [
            ("s", 10),
            ("t", 6),
        ]
        assert [(row.sensor_id, row.value) for row in everything] == [
            ("s", 20),
            ("t", 6),
        ]

    async def test_model_all_still_refuses_a_real_filter(self) -> None:
        """Only the injected soft-delete condition is stripped.

        Catches a blanket "ignore every filter because ``.all()`` was passed"
        rule, which would silently drop a caller's own ``WHERE``.
        """
        with pytest.raises(ValueError):
            await distinct_on(SoftReading.all().filter(value__gt=0), "sensor_id")

    @pytest.mark.parametrize(
        ("build", "needle"),
        [
            pytest.param(
                lambda: SoftReading.all().distinct(), "distinct", id="distinct"
            ),
            pytest.param(
                lambda: SoftReading.all().select_for_update(),
                "select_for_update",
                id="select-for-update",
            ),
            pytest.param(lambda: SoftReading.all().only("id"), "only", id="only"),
        ],
    )
    async def test_carried_state_is_refused(
        self, build: Callable[[], object], needle: str
    ) -> None:
        """State that changes the emitted SQL is refused *and* named.

        ``distinct`` would duplicate ``DISTINCT ON``, ``select_for_update``
        cannot combine with the generated query, and ``only`` leaves rows
        partial — each must produce a ``ValueError`` whose message names the
        attribute so the caller can find the call site.
        """
        with pytest.raises(ValueError) as excinfo:
            await distinct_on(build(), "sensor_id")

        assert needle in str(excinfo.value).lower()

    @pytest.mark.parametrize(
        "where",
        [
            pytest.param("sensor_id <> '$1'", id="single-quoted-literal"),
            pytest.param("sensor_id <> $$ $1 $$", id="dollar-quoted-body"),
        ],
    )
    async def test_placeholder_scanning_ignores_quoted_literals(
        self, where: str
    ) -> None:
        """A ``$1`` inside a string or dollar-quoted body is not a placeholder.

        Both fragments bind no parameter, so they must reach PostgreSQL intact
        and return every row. Catches a lexical scan that counts ``$n`` without
        honouring quoting: it would report a param mismatch for a query the
        driver accepts, which is the false positive that makes the guard
        unusable.
        """
        await _seed_soft()

        rows = await distinct_on(
            SoftReading.all(),
            "sensor_id",
            order_by=("sensor_id", "-observed_at"),
            where=where,
            params=[],
        )

        assert [(row.sensor_id, row.value) for row in rows] == [("s", 10), ("t", 6)]

    @pytest.mark.parametrize(
        ("where", "needle"),
        [
            pytest.param("value = $0", "$0", id="zero-index"),
            pytest.param("value = $01", "$01", id="leading-zero"),
        ],
    )
    async def test_placeholder_scanning(self, where: str, needle: str) -> None:
        """``$0`` and ``$01`` are refused, naming the placeholder as written.

        PostgreSQL placeholders are 1-based and un-padded; accepting either
        form would bind ``param[0]`` to a parameter the server never numbers
        that way (or normalise ``$01`` to ``$1`` and hide a typo). The message
        must quote the fragment so the caller can find it.
        """
        with pytest.raises(ValueError) as excinfo:
            await distinct_on(SoftReading.all(), "sensor_id", where=where, params=[1])

        assert needle in str(excinfo.value)

    async def test_where_placeholder_still_binds(self) -> None:
        """``$1`` binds the caller's value and filters *before* grouping.

        With ``params=[20]`` every row of ``s`` (10, 20) and ``t`` (5, 6) fails
        the predicate, so only ``u`` survives. Sensor ``u`` is seeded here as
        newer-but-smaller (40 @ 02:00, then 15 @ 03:00): filtering before
        grouping keeps 40, while picking the newest row per group first and
        filtering afterwards would keep 15, fail ``> 20`` and return nothing at
        all. Ignoring the ``WHERE`` would return ``s`` 20, ``t`` 6 and ``u`` 15.
        A dropped or mis-bound ``$1`` makes PostgreSQL complain about a missing
        parameter or compares against ``NULL`` and returns nothing.
        """
        await _seed_soft()
        await SoftReading.create(sensor_id="u", observed_at=_at(2), value=40)
        await SoftReading.create(sensor_id="u", observed_at=_at(3), value=15)

        rows = await distinct_on(
            SoftReading.all(),
            "sensor_id",
            order_by=("sensor_id", "-observed_at"),
            where="value > $1",
            params=[20],
            include_deleted=True,
        )

        assert [(row.sensor_id, row.value, row.observed_at) for row in rows] == [
            ("u", 40, _at(2)),
        ]

    @pytest.mark.parametrize("limit", [True, False])
    async def test_bool_limit_is_refused(self, limit: bool) -> None:
        """``bool`` is an ``int``, but ``limit=True`` is not ``limit=1``.

        ``False < 1`` would already be caught as a below-one limit, so the
        contrast below is what proves the refusal is about the type. Catches a
        guard that lets ``True`` through as one row and a message that does not
        quote the offending value.
        """
        with pytest.raises(ValueError) as excinfo:
            await distinct_on(Reading, "sensor_id", limit=limit)

        assert str(limit).lower() in str(excinfo.value).lower()

    async def test_integer_limit_one_is_accepted(self) -> None:
        """The contrast for the ``bool`` refusal: ``limit=1`` still works.

        Catches a guard that refuses every limit it does not freshly validate,
        or one that conflates ``1`` with ``True``.
        """
        await _seed()

        rows = await distinct_on(
            Reading, "sensor_id", order_by=("sensor_id", "-observed_at"), limit=1
        )

        assert [(row.sensor_id, row.value) for row in rows] == [("a", 51)]

    async def test_model_instance_target_is_type_error(self) -> None:
        """A fetched row is not a target; the model class is.

        Catches an implementation that accepts any object with ``_meta`` and
        would then read carried state off a loaded instance (silently ignoring
        the row's pk). The message must name ``instance`` so the caller can find
        the call site, and passing the class afterwards must still work.
        """
        await _seed()
        row = await Reading.get(sensor_id="a", value=51)

        with pytest.raises(TypeError) as excinfo:
            await distinct_on(row, "sensor_id")

        assert "instance" in str(excinfo.value).lower()

        rows = await distinct_on(
            Reading, "sensor_id", order_by=("sensor_id", "-observed_at")
        )
        assert [(r.sensor_id, r.value) for r in rows] == [
            ("a", 51),
            ("b", 52),
            ("c", 53),
        ]

    async def test_model_get_target_is_carried_state(self) -> None:
        """An un-awaited ``.get()`` is a ``QuerySet`` with state, not a target.

        ``Reading.get(...)`` returns a queryset carrying ``_limit = 2`` and
        ``_q_objects``; ``distinct_on`` would silently rewrite that query. The
        documented outcome is ``ValueError`` naming the carried state — a
        ``TypeError`` here means the implementation and its docs disagree.
        """
        await _seed()
        pending = Reading.get(sensor_id="a", value=51)

        with pytest.raises(ValueError) as excinfo:
            await distinct_on(pending, "sensor_id")

        message = str(excinfo.value)
        assert "_limit" in message or "_q_objects" in message

    async def test_soft_delete_detected_by_column(self) -> None:
        """Soft delete is found through the projection, not the field name.

        ``removed_at`` maps to the ``deleted_at`` column. Catches detection
        that looks for a field *called* ``deleted_at``: it would miss this
        model and return the soft-deleted newest row by default.
        """
        assert (
            RenamedSoftReading._meta.fields_db_projection["removed_at"] == "deleted_at"
        )
        await RenamedSoftReading.create(sensor_id="r", observed_at=_at(0), value=10)
        newer = await RenamedSoftReading.create(
            sensor_id="r", observed_at=_at(1), value=20
        )
        await RenamedSoftReading.filter(id=newer.pk).update(removed_at=_at(1))

        stored = await RenamedSoftReading.get(id=newer.pk)
        assert stored.removed_at == _at(1)

        visible = await distinct_on(
            RenamedSoftReading, "sensor_id", order_by=("sensor_id", "-observed_at")
        )
        everything = await distinct_on(
            RenamedSoftReading,
            "sensor_id",
            order_by=("sensor_id", "-observed_at"),
            include_deleted=True,
        )

        assert [(row.sensor_id, row.value) for row in visible] == [("r", 10)]
        assert [(row.sensor_id, row.value) for row in everything] == [("r", 20)]

    async def test_non_asyncpg_client_is_refused(self) -> None:
        """The backend guard refuses a non-asyncpg client and accepts the real one.

        Catches a guard that only checks for a connection-shaped attribute:
        the codec and the ``$n`` placeholder scan are asyncpg-specific, so a
        duck-typed client must be refused rather than receive SQL it cannot
        bind.
        """

        class _Fake:
            """A connection-shaped object that is not an asyncpg client."""

        with pytest.raises(ValueError) as excinfo:
            _assert_asyncpg_backend(_Fake())

        assert "asyncpg" in str(excinfo.value).lower()
        assert _assert_asyncpg_backend(Tortoise.get_connection("default")) is None
