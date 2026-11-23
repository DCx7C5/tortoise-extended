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

``where`` is **trusted SQL text**, not a value: it is concatenated into the
statement exactly as written, so it must never receive user input — bind every
caller value through ``params`` and ``$1``-style placeholders instead. Only
``params`` are bound. Placeholders are recognised quote-aware: a ``$1`` inside
a single-quoted literal, a double-quoted identifier, a dollar-quoted body
(``$$…$$`` / ``$tag$…$tag$``) or a SQL comment is text, not a parameter.

Hydration note: the rows come back from
``BaseDBAsyncClient.execute_query_dict`` and are turned into instances with
``Model._init_from_db`` — the same path the executor uses. That method is
private to tortoise-orm but is the only public-ish way to hydrate a model from
a mapping of database column names; the helper documents the dependency here
rather than re-implementing field conversion. Instances come back with
``_saved_in_db = True``, so they can be saved again.

Backend note: this helper needs the **asyncpg** backend. ``where`` placeholders
are ``$n`` tokens, which only asyncpg binds — tortoise can also reach PostgreSQL
through psycopg, where ``$1`` is literal text — and hydration goes through the
asyncpg-shaped ``execute_query_dict``. Any other connection is refused with
``ValueError`` before a statement is built.

Soft-delete note: the ``deleted_at IS NULL`` filter selects on the **column**,
not on a field name. A model declares the column either as a field named
``deleted_at`` (the usual ``BaseSoftDeleteModel`` shape) or as another field
projected onto it (``removed_at = fields.DatetimeField(source_field="deleted_at")``);
both get the filter unless ``include_deleted=True``.
"""

import re
from collections.abc import Sequence

from tortoise.backends.asyncpg.client import AsyncpgDBClient
from tortoise.backends.base.client import BaseDBAsyncClient
from tortoise.expressions import Q
from tortoise.models import Model
from tortoise.queryset import QuerySet

from tortoise_extended._quote import quote_ident
from tortoise_extended._types import CoercedValue
from tortoise_extended.expressions._soft_delete import soft_delete_column

_PARAMETER = re.compile(r"\$(\d+)")
"""Bound-parameter placeholder, e.g. ``$1``; applied only outside quoted text."""

_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
"""Dollar-quote delimiter, e.g. ``$$`` or ``$tag$`` (tag: identifier without ``$``)."""

_QUERYSET_CARRIED = (
    "_q_objects",
    "_annotations",
    "_orderings",
    "_limit",
    "_offset",
    "_group_bys",
    "_distinct",
    "_select_for_update",
    "_prefetch_map",
    "_prefetch_queries",
    "_fields_for_select",
)
"""Queryset attributes this helper cannot translate into raw SQL.

Verified against the installed tortoise-orm 1.1.9 ``QuerySet.__slots__``
(after tortoise-extended's supported range ``>=1.1.7,<1.2``): ``_distinct``
and ``_select_for_update`` are real slots; the prefetch state lives in
``_prefetch_map`` (string relations) **and** ``_prefetch_queries``
(``Prefetch`` objects); field narrowing via ``.only()`` / ``.values()`` lives
in ``_fields_for_select`` (there is no ``_fields_for_fetch``).
"""


def _is_live_row_filter(condition: Q) -> bool:
    """True for the exact ``Q(deleted_at__isnull=True)`` injected by base models.

    ``Q.__eq__`` ignores negation, so the structural check reads
    ``_is_negated`` itself instead of comparing to a fresh ``Q``.
    """
    return (
        not condition._is_negated  # pyright: ignore[reportPrivateUsage]
        and not condition.children
        and condition.filters == {"deleted_at__isnull": True}
    )


def _q_objects_without_soft_delete_default[M: Model](
    target: QuerySet[M],
) -> list[Q]:
    """Return ``target``'s q-objects minus the soft-delete default condition.

    ``SoftDeleteQuerySet.__init__`` injects ``Q(deleted_at__isnull=True)`` at
    ``_sd_index``, so ``Model.all()`` on a ``BaseSoftDeleteModel`` is never an
    empty-q-objects queryset. ``distinct_on`` re-applies that exact condition
    itself (unless ``include_deleted=True``), so the injected entry is not
    caller state and is dropped here. Everything else survives: a caller
    filter, or ``only_deleted()``'s ``IS NOT NULL`` replacement, still trips
    the carried check.
    """
    q_objects = list(target._q_objects)  # pyright: ignore[reportPrivateUsage]
    injected = getattr(target, "_sd_index", None)
    if (
        isinstance(injected, int)
        and 0 <= injected < len(q_objects)
        and _is_live_row_filter(q_objects[injected])
    ):
        del q_objects[injected]
    return q_objects


def _carried_state[M: Model](target: QuerySet[M]) -> list[str]:
    """Return the carried queryset attributes this helper cannot honour.

    An attribute is empty when it holds no caller-set state: ``None``, a
    ``False`` flag, or an empty container. ``0`` is *not* empty here —
    ``.limit(0)`` / ``.offset(0)`` are still state this helper would drop.
    """
    carried: list[str] = []
    for name in _QUERYSET_CARRIED:
        value = (
            _q_objects_without_soft_delete_default(target)
            if name == "_q_objects"
            else getattr(target, name, None)
        )
        if value is None or value is False:
            continue
        if isinstance(value, (list, dict, tuple, set)) and not value:
            continue
        carried.append(name)
    return carried


def _target_model[M: Model](target: type[M] | QuerySet[M]) -> type[M]:
    """Return the model ``target`` stands for, refusing a stateful queryset.

    Argument validation runs on the model alone, so a caller gets
    ``ValueError`` for bad arguments even before a connection exists.

    :param target: A model class, or an unfiltered queryset (``Model.all()``,
        optionally ``.using_db(...)``). On a ``BaseSoftDeleteModel`` the
        ``deleted_at IS NULL`` condition that ``SoftDeleteQuerySet`` injects
        into every default queryset is accepted, because the helper re-applies
        that same condition itself unless ``include_deleted=True``.
    :returns: The model class.
    :raises TypeError: When ``target`` is neither a model class nor a
        ``QuerySet`` — a model **instance** is row state this helper cannot
        translate to a statement. (A ``Model.get(...)`` result *is* a
        ``QuerySet``, so it is refused as carried state instead, below.)
    :raises ValueError: When ``target`` is a queryset that carries filters,
        annotations, ordering, grouping, ``DISTINCT``, ``SELECT … FOR
        UPDATE``, prefetching, only/values field narrowing, limit or offset —
        none of which can be represented in this helper's single statement.
        Predicates belong in ``where`` / ``params``.
    """
    if isinstance(target, QuerySet):
        carried = _carried_state(target)
        if carried:
            raise ValueError(
                f"distinct_on() cannot carry a queryset with {', '.join(carried)}: "
                f"this helper builds one raw statement, so a queryset may only "
                f"supply the model and connection — put predicates in "
                f"`where=` / `params=` and ordering in `order_by=`"
            )
        return target.model
    _refuse_row_target(target)
    return target


def _refuse_row_target[M: Model](
    target: type[M] | QuerySet[M] | Model,
) -> None:
    """Raise ``TypeError`` when ``target`` is a row rather than a class or queryset.

    The signature says only a class or a queryset can arrive, so this runs for
    the caller a type checker never saw — a model **instance**, which carries
    row state this helper cannot turn into a statement. ``QuerySet`` is allowed
    through: ``Model.get(...)`` returns one carrying a filter and a limit, and
    that is refused as carried state by the caller, not as a type error.

    :param target: The value the caller passed as ``target``.
    :raises TypeError: When ``target`` is a model instance.
    """
    if isinstance(target, Model):
        raise TypeError(
            f"distinct_on() target must be a model class or a QuerySet (e.g. "
            f"`Reading` / `Reading.all()`), got a {type(target).__name__} "
            f"instance: pass the class, not a row"
        )


def _assert_asyncpg_backend(client: BaseDBAsyncClient) -> None:
    """Raise unless ``client`` is the asyncpg backend.

    ``where`` placeholders are ``$n`` tokens and hydration reads the mappings
    ``execute_query_dict`` returns; both are asyncpg-shaped. Tortoise also
    reaches PostgreSQL through psycopg, where ``$1`` is literal text inside a
    statement, so that connection is refused here instead of letting the
    fragment reach the server with its placeholders unbound.

    :param client: The connection the statement would run on.
    :raises ValueError: When the client is not
        :class:`tortoise.backends.asyncpg.client.AsyncpgDBClient`.
    """
    if not isinstance(client, AsyncpgDBClient):
        raise ValueError(
            f"distinct_on() requires the asyncpg backend: `where` placeholders "
            f"are `$n` tokens, which asyncpg binds (psycopg treats `$1` as "
            f"literal text), and this connection uses "
            f"{type(client).__name__}. Configure the connection with the "
            f"'asyncpg' engine."
        )


def _connection_for[M: Model](
    target: type[M] | QuerySet[M],
    model: type[M],
) -> BaseDBAsyncClient:
    """Resolve the connection to run the statement on.

    :param target: The original argument, so a queryset's ``.using_db(...)``
        choice is honoured.
    :param model: The model resolved from ``target``.
    :returns: The connection.
    :raises ValueError: When the resolved connection is not the asyncpg
        backend (see :func:`_assert_asyncpg_backend`).
    """
    if isinstance(target, QuerySet):
        client = target._db or model._choose_db()  # pyright: ignore[reportPrivateUsage]
    else:
        client = model._choose_db()
    _assert_asyncpg_backend(client)
    return client


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


def _placeholder_indices(fragment: str) -> list[int]:
    """Collect the ``$n`` parameter indices used outside literals and comments.

    PostgreSQL's lexer treats ``$1`` as a parameter only in SQL code: inside a
    single-quoted literal (``''`` escapes a quote), a double-quoted identifier
    (``""`` escapes), a dollar-quoted body (``$$…$$`` / ``$tag$…$tag$``), a
    ``--`` line comment or a ``/* */`` block comment (nested, as PostgreSQL
    allows) it is plain text. Scanning the fragment the same way keeps a legal
    ``where="title <> '$1'"`` legal and stops a ``$1`` hidden in a comment
    from being counted as bound.

    :param fragment: The ``where`` fragment.
    :returns: The indices used, in source order, repeats preserved.
    :raises ValueError: When a placeholder is ``$0`` or written with a leading
        zero — PostgreSQL numbers parameters from ``$1`` and rejects both.
    """
    indices: list[int] = []
    position = 0
    length = len(fragment)
    while position < length:
        char = fragment[position]
        if char == "'":
            position += 1
            while position < length:
                if fragment[position] != "'":
                    position += 1
                elif position + 1 < length and fragment[position + 1] == "'":
                    position += 2
                else:
                    position += 1
                    break
            continue
        if char == '"':
            position += 1
            while position < length:
                if fragment[position] != '"':
                    position += 1
                elif position + 1 < length and fragment[position + 1] == '"':
                    position += 2
                else:
                    position += 1
                    break
            continue
        if fragment.startswith("--", position):
            newline = fragment.find("\n", position + 2)
            position = length if newline < 0 else newline + 1
            continue
        if fragment.startswith("/*", position):
            depth = 1
            position += 2
            while position < length and depth:
                if fragment.startswith("/*", position):
                    depth += 1
                    position += 2
                elif fragment.startswith("*/", position):
                    depth -= 1
                    position += 2
                else:
                    position += 1
            continue
        if char == "$":
            delimiter = _DOLLAR_QUOTE.match(fragment, position)
            if delimiter is not None:
                closing = fragment.find(delimiter.group(0), delimiter.end())
                position = length if closing < 0 else closing + len(delimiter.group(0))
                continue
            parameter = _PARAMETER.match(fragment, position)
            if parameter is not None:
                digits = parameter.group(1)
                if digits.startswith("0"):
                    rewritten = int(digits)
                    hint = f" — write ${rewritten}" if rewritten else ""
                    raise ValueError(
                        f"distinct_on() `where` uses the placeholder ${digits}: "
                        f"PostgreSQL parameters start at $1 and take no leading "
                        f"zeros{hint}"
                    )
                indices.append(int(digits))
                position = parameter.end()
                continue
        position += 1
    return indices


def _validate_placeholders(where: str | None, params: Sequence[CoercedValue]) -> None:
    """Check that ``where``'s ``$n`` placeholders match ``params`` exactly.

    :param where: The optional ``WHERE`` fragment (trusted SQL text).
    :param params: The values bound to it, in ``$1`` … order.
    :raises ValueError: When a placeholder is ``$0`` / zero-padded, when the
        fragment uses placeholders without values, supplies values without
        placeholders, or skips an index — all of which would surface as an
        opaque driver error instead.
    """
    indices = _placeholder_indices(where or "")
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

    :param target: Model class, or a queryset carrying nothing but its model
        and connection (``Model.all()``, optionally ``.using_db(...)``; on a
        ``BaseSoftDeleteModel`` the injected ``deleted_at IS NULL`` filter is
        accepted because the helper re-applies it). Any other state raises
        ``ValueError``; an instance or a ``Model.get(...)`` result raises
        ``TypeError``.
    :param on: Model field name(s) that identify a group; one row per group.
    :param order_by: Model field name(s) deciding which row wins; a leading
        ``-`` means ``DESC``. Must **start** with the ``on`` columns — that is
        PostgreSQL's rule for ``DISTINCT ON`` and is enforced here.
    :param limit: Optional maximum number of groups returned; must be an
        integer of at least 1. A ``bool`` is refused rather than passed on as
        PostgreSQL's ``LIMIT true``.
    :param where: Optional ``WHERE`` fragment — **trusted SQL text**, concatenated
        into the statement verbatim, so never pass user input. Use ``$1``-style
        placeholders and bind the values through ``params``.
    :param params: Values for those placeholders, in order; bound, never inlined.
    :param include_deleted: When the model projects a ``deleted_at`` **column**
        — a field of that name, or another field mapped onto it (e.g.
        ``removed_at = fields.DatetimeField(source_field="deleted_at")``) —
        soft-deleted rows are excluded unless this is ``True``.
    :returns: The winning rows, in ``ORDER BY`` order, as model instances.
    :raises TypeError: When ``target`` is neither a model class nor a
        ``QuerySet`` (for example a model instance).
    :raises ValueError: For an empty ``on``, an unknown field, an ``order_by``
        that does not lead with the ``on`` columns, mismatched placeholders, a
        ``$0`` / zero-padded placeholder, a ``limit`` below 1 or a boolean
        ``limit``, a queryset carrying state (filters, annotations, ordering,
        grouping, ``DISTINCT``, ``SELECT … FOR UPDATE``, prefetching,
        only/values fields, limit, offset), or a connection that is not the
        asyncpg backend.

    ``on`` groups are ordered by the ``on`` columns first (ascending) so the
    output is stable when several groups share an order key; append a
    tiebreaker to ``order_by`` for a fully deterministic winner per group.
    """
    model = _target_model(target)
    if not on:
        raise ValueError("distinct_on() needs at least one field name in `on`")
    if isinstance(limit, bool):
        raise ValueError(
            f"distinct_on() limit must be an integer, got {limit}: a bool is not "
            f"a row count"
        )
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
    if not include_deleted:
        deleted_column = soft_delete_column(model)
        if deleted_column is not None:
            conditions.append(f"{quote_ident(deleted_column)} IS NULL")

    sql = f"SELECT DISTINCT ON ({on_sql}) {select_list} FROM {model._meta.basetable}"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += f" ORDER BY {order_sql}"
    if limit is not None:
        sql += f" LIMIT {limit}"

    rows = await _connection_for(target, model).execute_query_dict(sql, list(params))
    return [model._init_from_db(**row) for row in rows]
