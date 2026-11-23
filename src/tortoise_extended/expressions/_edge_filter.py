"""Shared raw-SQL helpers for graph expression modules.

The recursive-CTE based modules (``graph_traversal``, ``pathfinding``,
``graph_vector_search``) each build parameterized ``edge_type`` filters for
their recursive step, plus the soft-delete predicate that keeps tombstoned
rows out of every traversal. The helpers live here so the SQL shape, the
predicate text and the ``$N`` placeholder numbering stay consistent across
all three callers.

The soft-delete predicate keys on the **column** a model projects — the rule
:func:`tortoise_extended.expressions._soft_delete.soft_delete_column`
implements — so a renamed soft-delete field (``removed_at`` mapped onto
``deleted_at``) is filtered like the ordinary ``deleted_at`` field. Callers
pass ``None`` for a model with no such column and generate the SQL they always
did.
"""


def live_predicate(alias: str, column: str = "deleted_at") -> str:
    """Return the soft-delete predicate for *alias* (no leading ``AND``).

    Args:
        alias: SQL alias of the node or edge table inside the CTE.
        column: The soft-delete column the model projects, as
            :func:`tortoise_extended.expressions._soft_delete.soft_delete_column`
            resolves it. The default is the ordinary ``deleted_at`` shape, so
            the emitted text is unchanged for it.

    Returns:
        ``"<alias>.<column> IS NULL"`` — the same condition
        ``SoftDeleteQuerySet`` injects eagerly at queryset construction.
    """
    return f"{alias}.{column} IS NULL"


def et_clause(
    edge_type: str | None,
    param_index: int,
    soft_delete_column: str | None = None,
) -> tuple[str, list[str]]:
    """Build a parameterized ``edge_type`` filter for the recursive step.

    Args:
        edge_type: Optional edge type to filter on.
        param_index: Positional ``$N`` parameter number to use.
        soft_delete_column: The edge model's soft-delete column, as
            :func:`tortoise_extended.expressions._soft_delete.soft_delete_column`
            resolves it, or ``None`` when the edge model projects none. When
            set, the edge live-row predicate (``e.<column> IS NULL``) is
            appended.

    Returns:
        Tuple of ``(sql_clause, params)`` where the clause is empty and
        params is an empty list when neither filter applies.
    """
    clauses: list[str] = []
    params: list[str] = []
    if edge_type is not None:
        clauses.append(f"e.edge_type = ${param_index}")
        params.append(edge_type)
    if soft_delete_column is not None:
        clauses.append(live_predicate("e", soft_delete_column))
    if not clauses:
        return "", []
    return "AND " + " AND ".join(clauses), params
