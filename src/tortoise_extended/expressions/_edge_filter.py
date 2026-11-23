"""Shared raw-SQL helpers for graph expression modules.

The recursive-CTE based modules (``graph_traversal``, ``pathfinding``,
``graph_vector_search``) each build parameterized ``edge_type`` filters for
their recursive step, plus the soft-delete predicate that keeps tombstoned
rows out of every traversal. The helpers live here so the SQL shape, the
predicate text and the ``$N`` placeholder numbering stay consistent across
all three callers.
"""


def live_predicate(alias: str) -> str:
    """Return the soft-delete predicate for *alias* (no leading ``AND``).

    Args:
        alias: SQL alias of the node or edge table inside the CTE.

    Returns:
        ``"<alias>.deleted_at IS NULL"`` — the same condition
        ``SoftDeleteQuerySet`` injects eagerly at queryset construction.
    """
    return f"{alias}.deleted_at IS NULL"


def et_clause(
    edge_type: str | None,
    param_index: int,
    soft_delete: bool = False,
) -> tuple[str, list[str]]:
    """Build a parameterized ``edge_type`` filter for the recursive step.

    Args:
        edge_type: Optional edge type to filter on.
        param_index: Positional ``$N`` parameter number to use.
        soft_delete: Append the edge soft-delete predicate
            (``e.deleted_at IS NULL``) when the edge model declares a
            ``deleted_at`` column.

    Returns:
        Tuple of ``(sql_clause, params)`` where the clause is empty and
        params is an empty list when neither filter applies.
    """
    clauses: list[str] = []
    params: list[str] = []
    if edge_type is not None:
        clauses.append(f"e.edge_type = ${param_index}")
        params.append(edge_type)
    if soft_delete:
        clauses.append(live_predicate("e"))
    if not clauses:
        return "", []
    return "AND " + " AND ".join(clauses), params
