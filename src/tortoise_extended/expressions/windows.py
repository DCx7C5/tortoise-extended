"""Window-function builders for tortoise-orm querysets.

Tortoise ``annotate()`` already accepts pypika analytic terms, and those terms
render the right SQL when they are configured with pypika ``Field`` objects::

    RowNumber().over(Field("bucket")).orderby(Field("n"))
    # ROW_NUMBER() OVER(PARTITION BY "bucket" ORDER BY "n")

    Sum(Field("n")).over(Field("bucket"))
    # SUM("n") OVER(PARTITION BY "bucket")

The trap is Tortoise's own :class:`~tortoise.expressions.F`: ``.over()`` and
``.orderby()`` call ``str()`` on anything that is not a pypika term, so an
``F("bucket")`` is spliced into the SQL as
``PARTITION BY <tortoise.expressions.F object at 0x...>`` and PostgreSQL
rejects the statement. The builders here take **field names** and construct the
``Field`` terms themselves, so that broken shape is never offered.

Every builder returns a configured pypika analytic term for
``QuerySet.annotate()``::

    from tortoise_extended import row_number, sum_over

    rows = await Sale.annotate(
        position=row_number(partition_by="region", order_by="-amount"),
        region_total=sum_over("amount", partition_by="region"),
    ).order_by("region", "position")

``partition_by`` and ``order_by`` each accept one field name or a sequence of
them; a leading ``-`` on an ``order_by`` name means ``DESC``. Names are plain
column names of the queryset's table — not expressions, not ``F()``, not
``table.column`` strings — and anything else raises ``ValueError``. With
neither argument the term renders an empty ``OVER()``: one window covering the
whole result set.

Window frames (``ROWS`` / ``RANGE``) are **out of scope**: a frame changes what
a windowed aggregate means (whole-partition total vs. running total), and the
builders do not guess one. A caller who needs a frame configures the pypika
term directly with its ``.rows()`` / ``.range()`` methods — the terms these
builders return are ordinary pypika analytics, not wrappers.

No raw SQL: every builder emits SQL through pypika, so the query stays inside
Tortoise's composition rules (filters, annotations, ordering, pagination).
"""

import re
from collections.abc import Sequence

from pypika_tortoise.analytics import (
    Avg,
    Count,
    DenseRank,
    Max,
    Min,
    NTile,
    Rank,
    RowNumber,
    Sum,
)
from pypika_tortoise.enums import Order
from pypika_tortoise.terms import AnalyticFunction, Field, Star

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
"""A plain SQL identifier: no quoting, dots, operators or whitespace."""


def _terms(names: str | Sequence[str]) -> list[Field]:
    """Map one field name, or a sequence of them, onto pypika ``Field`` terms.

    :param names: A plain identifier, or a sequence of plain identifiers.
    :returns: One :class:`pypika_tortoise.terms.Field` per name, in order.
    :raises ValueError: When a name is not a plain identifier — the message
        names the offending value. Quoted names, qualified names
        (``table.column``) and expressions are refused because they would need
        escaping or table resolution this helper does not perform.
    """
    if isinstance(names, str):
        candidates: tuple[str, ...] = (names,)
    else:
        try:
            candidates = tuple(names)
        except TypeError as exc:
            # Tortoise's ``F()`` is not a pypika term and is not iterable; the
            # builders refuse it here so the caller never sees the
            # ``PARTITION BY <F object …>`` SQL that ``.over()`` would emit.
            raise ValueError(
                f"{names!r} is not a plain field name: window builders take "
                f"column names (letters, digits and underscores, not starting "
                f"with a digit), not expressions or qualified names"
            ) from exc
    fields: list[Field] = []
    for name in candidates:
        if _IDENTIFIER.fullmatch(name) is None:
            raise ValueError(
                f"{name!r} is not a plain field name: window builders take "
                f"column names (letters, digits and underscores, not starting "
                f"with a digit), not expressions or qualified names"
            )
        fields.append(Field(name))
    return fields


def _ordered(names: str | Sequence[str]) -> list[tuple[Field, Order]]:
    """Map order-by names onto ``(Field, Order)`` pairs.

    A leading ``-`` selects ``DESC``; every other name is ``ASC``.
    """
    if isinstance(names, str):
        names = (names,)
    pairs: list[tuple[Field, Order]] = []
    for name in names:
        descending = name.startswith("-")
        stripped = name[1:] if descending else name
        # A bare "-" has an empty remainder: validate the original token so the
        # error names what the caller wrote, not "".
        field = _terms(stripped or name)[0]
        pairs.append((field, Order.desc if descending else Order.asc))
    return pairs


def _windowed[T: AnalyticFunction](
    term: T,
    partition_by: str | Sequence[str],
    order_by: str | Sequence[str],
) -> T:
    """Configure ``term`` with the given partition and order terms.

    Each ``.over()`` / ``.orderby()`` call returns a new pypika term (the
    builder decorator copies), so ``term`` is reassigned rather than mutated in
    place; the returned object is the fully configured one. ``.over()`` is
    always applied — even with no partition names — so the term renders
    ``OVER()`` (one window over the whole result set) instead of the invalid
    ``ROW_NUMBER()`` a pypika analytic without ``OVER`` would produce.
    """
    term = term.over(*_terms(partition_by))
    for field, direction in _ordered(order_by):
        term = term.orderby(field, order=direction)
    return term


def row_number(
    partition_by: str | Sequence[str] = (),
    order_by: str | Sequence[str] = (),
) -> RowNumber:
    """``ROW_NUMBER() OVER(...)`` — 1-based position of each row in its window.

    Row numbers restart at 1 per partition, and peer rows (equal order keys)
    still receive distinct numbers; use :func:`rank` for gap-preserving
    ordering of ties.

    :param partition_by: Window partition column name(s).
    :param order_by: Row-order column name(s); a leading ``-`` means ``DESC``.
    :returns: A configured :class:`pypika_tortoise.analytics.RowNumber`.
    """
    return _windowed(RowNumber(), partition_by, order_by)


def rank(
    partition_by: str | Sequence[str] = (),
    order_by: str | Sequence[str] = (),
) -> Rank:
    """``RANK() OVER(...)`` — rank with gaps after ties (1, 1, 3, ...).

    :param partition_by: Window partition column name(s).
    :param order_by: Row-order column name(s); a leading ``-`` means ``DESC``.
    :returns: A configured :class:`pypika_tortoise.analytics.Rank`.
    """
    return _windowed(Rank(), partition_by, order_by)


def dense_rank(
    partition_by: str | Sequence[str] = (),
    order_by: str | Sequence[str] = (),
) -> DenseRank:
    """``DENSE_RANK() OVER(...)`` — rank without gaps after ties (1, 1, 2, ...).

    :param partition_by: Window partition column name(s).
    :param order_by: Row-order column name(s); a leading ``-`` means ``DESC``.
    :returns: A configured :class:`pypika_tortoise.analytics.DenseRank`.
    """
    return _windowed(DenseRank(), partition_by, order_by)


def ntile(
    buckets: int,
    partition_by: str | Sequence[str] = (),
    order_by: str | Sequence[str] = (),
) -> NTile:
    """``NTILE(buckets) OVER(...)`` — split each window into equal buckets.

    :param buckets: Number of buckets; must be at least 1.
    :param partition_by: Window partition column name(s).
    :param order_by: Row-order column name(s); a leading ``-`` means ``DESC``.
    :returns: A configured :class:`pypika_tortoise.analytics.NTile`.
    :raises ValueError: When ``buckets`` is below 1.
    """
    if buckets < 1:
        raise ValueError(f"ntile bucket count must be >= 1, got {buckets}")
    return _windowed(NTile(buckets), partition_by, order_by)


def sum_over(
    field: str,
    partition_by: str | Sequence[str] = (),
    order_by: str | Sequence[str] = (),
) -> Sum:
    """``SUM(field) OVER(...)`` — windowed sum of a column.

    Without an ``order_by`` the sum covers the whole partition; with one, the
    default frame (``RANGE UNBOUNDED PRECEDING``) makes it a running total.

    :param field: Column to sum.
    :param partition_by: Window partition column name(s).
    :param order_by: Row-order column name(s); a leading ``-`` means ``DESC``.
    :returns: A configured :class:`pypika_tortoise.analytics.Sum`.
    """
    return _windowed(Sum(_terms(field)[0]), partition_by, order_by)


def avg_over(
    field: str,
    partition_by: str | Sequence[str] = (),
    order_by: str | Sequence[str] = (),
) -> Avg:
    """``AVG(field) OVER(...)`` — windowed average of a column.

    :param field: Column to average.
    :param partition_by: Window partition column name(s).
    :param order_by: Row-order column name(s); a leading ``-`` means ``DESC``.
    :returns: A configured :class:`pypika_tortoise.analytics.Avg`.
    """
    return _windowed(Avg(_terms(field)[0]), partition_by, order_by)


def count_over(
    field: str | None = None,
    partition_by: str | Sequence[str] = (),
    order_by: str | Sequence[str] = (),
) -> Count:
    """``COUNT(field) OVER(...)`` — windowed row count.

    With no ``field`` it counts every row of the partition (``COUNT(*)``);
    with a column name it counts the non-null values of that column, which is
    the distinction between "rows in each partition" and "rows with a value".

    :param field: Column to count, or ``None`` for ``COUNT(*)``.
    :param partition_by: Window partition column name(s).
    :param order_by: Row-order column name(s); a leading ``-`` means ``DESC``.
    :returns: A configured :class:`pypika_tortoise.analytics.Count`.
    """
    term = Count(Star()) if field is None else Count(_terms(field)[0])
    return _windowed(term, partition_by, order_by)


def min_over(
    field: str,
    partition_by: str | Sequence[str] = (),
    order_by: str | Sequence[str] = (),
) -> Min:
    """``MIN(field) OVER(...)`` — windowed minimum of a column.

    :param field: Column to take the minimum of.
    :param partition_by: Window partition column name(s).
    :param order_by: Row-order column name(s); a leading ``-`` means ``DESC``.
    :returns: A configured :class:`pypika_tortoise.analytics.Min`.
    """
    return _windowed(Min(_terms(field)[0]), partition_by, order_by)


def max_over(
    field: str,
    partition_by: str | Sequence[str] = (),
    order_by: str | Sequence[str] = (),
) -> Max:
    """``MAX(field) OVER(...)`` — windowed maximum of a column.

    :param field: Column to take the maximum of.
    :param partition_by: Window partition column name(s).
    :param order_by: Row-order column name(s); a leading ``-`` means ``DESC``.
    :returns: A configured :class:`pypika_tortoise.analytics.Max`.
    """
    return _windowed(Max(_terms(field)[0]), partition_by, order_by)
