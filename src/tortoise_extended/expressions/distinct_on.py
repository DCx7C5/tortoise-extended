"""PostgreSQL ``DISTINCT ON`` for tortoise-orm models and querysets.

Tortoise's query builder cannot express ``DISTINCT ON``: pypika-tortoise's
``Query.distinct()`` takes no arguments and emits the bare ``DISTINCT`` token,
``QuerySet.distinct()`` is a boolean flag, and ``ValuesQuery`` has neither.
:data:`DISTINCT ON` is one of the constructs AGENTS.md rule 3 names as a
legitimate raw-SQL exception — the ORM has no expression for it — so this
module builds exactly one statement and hydrates real model instances from it.

Usage::

    from tortoise_extended import distinct_on

    # Latest reading per sensor, one row per sensor.
    rows = await distinct_on(
        Reading,
        "sensor_id",
        order_by=("sensor_id", "-observed_at"),
    )

    # Same, but only over one window, with a bound predicate.
    rows = await distinct_on(
        Reading,
        "sensor_id",
        order_by=("sensor_id", "-observed_at"),
        where="observed_at > $1",
        params=[cutoff],
    )

``on`` and ``order_by`` take **model field names** (a leading ``-`` on an
``order_by`` name means ``DESC``). PostgreSQL requires the ``DISTINCT ON``
expressions to be the leftmost ``ORDER BY`` expressions, so ``order_by`` must
start with the same columns; the helper refuses anything else instead of
letting the server raise a syntax error.

Hydration note: the rows come back from
``BaseDBAsyncClient.execute_query_dict`` and are turned into instances with
``Model._init_from_db`` — the same path the executor uses. That method is
private to tortoise-orm but is the only public-ish way to hydrate a model from
a mapping of database column names; the helper documents the dependency here
rather than re-implementing field conversion. Instances come back with
``_saved_in_db = True``, so they can be saved again.
"""

import re
from collections.abc import Sequence

from tortoise.backends.base.client import BaseDBAsyncClient
from tortoise.models import Model
from tortoise.queryset import QuerySet

from tortoise_extended._quote import quote_ident
from tortoise_extended._types import CoercedValue

_PLACEHOLDER = re.compile(r"\$(\d+)")
"""Bound-parameter placeholder, e.g. ``$1``. Literal ``$`` signs are ignored."""

_QUERYSET_CARRIED = (
    "_q_objects",
    "_annotations",
    "_orderings",
    "_limit",
    "_offset",
    "_group_bys",
)
"""Queryset attributes this helper cannot translate into raw SQL."""


def _model_and_connection[M: Model](
    target: type[M] | QuerySet[M],
) -> tuple[type[M], BaseDBAsyncClient]:
    """Split ``target`` into the model class and the connection to run on.

    :param target: A model class, or an unfiltered queryset (``Model.all()``,
        optionally ``.using_db(...)``).
    :returns: The model class and its connection.
    :raises ValueError: When ``target`` is a queryset that carries filters,
        annotations, ordering, grouping, limit or offset — none of which can be
        represented in this helper's single statement. Predicates belong in
        ``where`` / ``params``.
    """
    if isinstance(target, QuerySet):
        carried = [
            name
            for name in _QUERYSET_CARRIED
            if getattr(target, name, None) not in (None, [], {}, ())
        ]
        if carried:
            raise ValueError(
                f"distinct_on() cannot carry a queryset with {', '.join(carried)}: "
                f"this helper builds one raw statement, so a queryset may only "
                f"supply the model and connection — put predicates in "
                f"`where=` / `params=` and ordering in `order_by=`"
            )
        return target.model, target._db or target.model._choose_db()  # pyright: ignore[reportPrivateUsage]
    return target, target._choose_db()


def _db_column(model: type[Model], name: str, *, argument: str) -> str:
    """Resolve a model field name (or database column name) to a column name.

    :param model: The model the statement selects from.
    :param name: A model field name, or a database column name already
        projected by the model.
    :param argument: The helper argument name, used in the error message.
    :returns: The database column name.
    :raises ValueError: When ``name`` is neither a field nor a projected column
        of ``model``.
    """
    projection = model._meta.fields_db_projection
    if name in projection:
        return projection[name]
    if name in projection.values():
        return name
    raise ValueError(
        f"{argument}={name!r} is not a field of {model.__name__}: pass one of "
        f"{', '.join(sorted(projection))} (or a column name the model projects)"
    )


def _order_terms(
    model: type[Model],
    names: Sequence[str],
) -> list[tuple[str, str]]:
    """Resolve ``order_by`` names into ``(column, ASC|DESC)`` pairs."""
    terms: list[tuple[str, str]] = []
    for name in names:
        descending = name.startswith("-")
        column = _db_column(
            model, name[1:] if descending else name, argument="order_by"
        )
        terms.append((column, "DESC" if descending else "ASC"))
    return terms


def _validate_placeholders(where: str | None, params: Sequence[CoercedValue]) -> None:
    """Check that ``where``'s ``$n`` placeholders match ``params`` exactly.

    :param where: The optional ``WHERE`` fragment.
    :param params: The values bound to it, in ``$1`` … order.
    :raises ValueError: When the fragment uses placeholders without values,
        supplies values without placeholders, or skips / repeats an index —
        all of which would surface as an opaque driver error instead.
    """
    indices = [int(match) for match in _PLACEHOLDER.findall(where or "")]
    expected = list(range(1, len(params) + 1))
    if sorted(set(indices)) == expected:
        return
    if not indices:
        raise ValueError(
            "distinct_on() received params for a `where` fragment with no $n "
            "placeholders; bind values in the fragment instead of appending "
            "them"
        )
    if not params:
        raise ValueError(
            f"distinct_on() `where` uses placeholders {sorted(set(indices))} but "
            f"no params were given: pass the values as `params=[…]` in $1… order"
        )
    raise ValueError(
        f"distinct_on() `where` placeholders {sorted(set(indices))} do not match "
        f"{len(params)} param(s): PostgreSQL expects $1…${len(params)} in order, "
        f"each used at least once"
    )


async def distinct_on[M: Model](
    target: type[M] | QuerySet[M],
    *on: str,
    order_by: Sequence[str] = (),
    limit: int | None = None,
    where: str | None = None,
    params: Sequence[CoercedValue] = (),
    include_deleted: bool = False,
) -> list[M]:
    """Return one row per ``on`` combination, picked by ``order_by``.

    Emits ``SELECT DISTINCT ON (<on>) <every column> FROM <table> [WHERE …]
    ORDER BY <on…>, <order_by…> [LIMIT n]`` and hydrates real instances.

    :param target: Model class, or an unfiltered queryset (model + connection
        only; filters raise ``ValueError``).
    :param on: Model field name(s) that identify a group; one row per group.
    :param order_by: Model field name(s) deciding which row wins; a leading
        ``-`` means ``DESC``. Must **start** with the ``on`` columns — that is
        PostgreSQL's rule for ``DISTINCT ON`` and is enforced here.
    :param limit: Optional maximum number of groups returned; must be at least 1.
    :param where: Optional ``WHERE`` fragment with ``$1``-style placeholders.
    :param params: Values for those placeholders, in order; never inlined.
    :param include_deleted: When the model declares a ``deleted_at`` column,
        soft-deleted rows are excluded unless this is ``True``.
    :returns: The winning rows, in ``ORDER BY`` order, as model instances.
    :raises ValueError: For an empty ``on``, an unknown field, an ``order_by``
        that does not lead with the ``on`` columns, mismatched placeholders, an
        empty ``on``/``limit`` below 1, or a filtered queryset.

    ``on`` groups are ordered by the ``on`` columns first (ascending) so the
    output is stable when several groups share an order key; append a
    tiebreaker to ``order_by`` for a fully deterministic winner per group.
    """
    model, connection = _model_and_connection(target)
    if not on:
        raise ValueError("distinct_on() needs at least one field name in `on`")
    if limit is not None and limit < 1:
        raise ValueError(f"distinct_on() limit must be >= 1, got {limit}")

    on_columns = [_db_column(model, name, argument="on") for name in on]
    terms = _order_terms(model, order_by) or [(column, "ASC") for column in on_columns]
    if len(terms) < len(on_columns):
        raise ValueError(
            f"distinct_on() needs `order_by` to start with the `on` columns "
            f"({', '.join(on_columns)}): PostgreSQL requires the leading ORDER BY "
            f"expressions to match DISTINCT ON, got {len(terms)} order term(s)"
        )
    leading = {column for column, _direction in terms[: len(on_columns)]}
    if leading != set(on_columns):
        raise ValueError(
            f"distinct_on() `order_by` must start with the `on` columns "
            f"({', '.join(on_columns)}), so the winning row per group is defined; "
            f"got {', '.join(column for column, _direction in terms[: len(on_columns)])}"
        )

    _validate_placeholders(where, params)

    projection = model._meta.fields_db_projection
    select_list = ", ".join(
        f"{quote_ident(column)} AS {quote_ident(column)}"
        for column in projection.values()
    )
    on_sql = ", ".join(quote_ident(column) for column in on_columns)
    order_sql = ", ".join(
        f"{quote_ident(column)} {direction}" for column, direction in terms
    )

    conditions: list[str] = []
    if where:
        conditions.append(f"({where})")
    if not include_deleted and "deleted_at" in model._meta.fields_map:
        conditions.append(
            f"{quote_ident(_db_column(model, 'deleted_at', argument='deleted_at'))} IS NULL"
        )

    sql = f"SELECT DISTINCT ON ({on_sql}) {select_list} FROM {model._meta.basetable}"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += f" ORDER BY {order_sql}"
    if limit is not None:
        sql += f" LIMIT {limit}"

    rows = await connection.execute_query_dict(sql, list(params))
    return [model._init_from_db(**row) for row in rows]
