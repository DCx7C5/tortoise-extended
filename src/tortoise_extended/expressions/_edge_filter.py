"""Shared raw-SQL helpers for graph expression modules.

The recursive-CTE based modules (``graph_traversal``, ``pathfinding``,
``graph_vector_search``) each build parameterized ``edge_type`` filters for
their recursive step. The helper lives here so the SQL shape and the
``$N`` placeholder numbering stay consistent across all three callers.
"""


def et_clause(edge_type: str | None, param_index: int) -> tuple[str, list[str]]:
    """Build a parameterized ``edge_type`` filter for the recursive step.

    Args:
        edge_type: Optional edge type to filter on.
        param_index: Positional ``$N`` parameter number to use.

    Returns:
        Tuple of ``(sql_clause, params)`` where the clause is empty and
        params is an empty list when *edge_type* is ``None``.
    """
    if edge_type is None:
        return "", []
    return f"AND e.edge_type = ${param_index}", [edge_type]
