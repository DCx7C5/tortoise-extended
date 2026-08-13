"""Graph pathfinding algorithms via recursive CTE.

Provides shortest path, all paths, and cycle detection for graphs
stored in separate node and edge tables.

Usage::

    from tortoise_extended.expressions.pathfinding import shortest_path, all_paths

    path = await shortest_path(NodeModel, EdgeModel, from_id=1, to_id=42)
    paths = await all_paths(NodeModel, EdgeModel, from_id=1, to_id=42, max_hops=5)
"""

from typing import TYPE_CHECKING, TypedDict, cast
from uuid import UUID

from tortoise import connections

from tortoise_extended._types import RowMapping
from tortoise_extended.expressions._edge_filter import et_clause as _et_clause

if TYPE_CHECKING:
    from tortoise.models import Model


class PathRow(TypedDict):
    """One result row from a recursive-CTE path query.

    ``path_ids`` / ``path_names`` are parallel PostgreSQL arrays; ``hops``
    is the path length.
    """

    path_ids: list[int | str]
    path_names: list[str]
    hops: int


def _as_path_row(row: RowMapping | PathRow) -> PathRow:
    """Narrow a raw result row to :class:`PathRow`.

    asyncpg returns PostgreSQL arrays as Python lists, so the concrete
    ``list[int | str]`` / ``list[str]`` shapes are guaranteed by the query.

    :param row: Raw row from ``execute_query``.
    :returns: The same row, statically narrowed.
    """
    return cast(PathRow, row)


def _walk_cte(node_table: str, edge_table: str, et_clause: str) -> str:
    """Build the shared recursive-CTE walk used by pathfinding queries.

    Walks the edge table from the anchor node (``$1``) up to ``$2`` hops,
    tracking ``path_ids`` / ``path_names`` arrays and rejecting revisits.
    The optional ``et_clause`` (already parameterized) restricts the edge
    type; its placeholder offset is baked in by the caller.

    :param node_table: Node table name.
    :param edge_table: Edge table name.
    :param et_clause: Optional edge-type filter fragment (empty when unused).
    :returns: The ``WITH RECURSIVE paths AS (...)`` statement body.
    """
    return f"""
        WITH RECURSIVE paths AS (
            SELECT
                n.id, n.name, n.depth,
                ARRAY[n.id] AS path_ids,
                ARRAY[n.name::text] AS path_names,
                0 AS hops
            FROM {node_table} n
            WHERE n.id = $1

            UNION

            SELECT
                n.id, n.name, n.depth,
                p.path_ids || n.id,
                p.path_names || n.name,
                p.hops + 1
            FROM paths p
            JOIN {edge_table} e ON (
                e.source_id = p.id
                OR (e.is_bidirectional AND e.target_id = p.id)
            )
            JOIN {node_table} n ON (
                (e.source_id = p.id AND n.id = e.target_id)
                OR (e.is_bidirectional AND e.target_id = p.id AND n.id = e.source_id)
            )
            WHERE p.hops < $2
            AND NOT (n.id = ANY(p.path_ids))
            {et_clause}
        )
    """


def _path_from_row(row: PathRow, *, strip_closing: bool = False) -> list[RowMapping]:
    """Convert a pathfinding result row into a list of node dicts.

    :param row: Result row with parallel ``path_ids`` / ``path_names`` arrays.
    :param strip_closing: Drop the trailing entry — used for cycles, whose
        walk repeats the start node to close the loop.
    :returns: ``[{"id": ..., "name": ...}, ...]`` node list.
    """
    ids = row["path_ids"]
    names = row["path_names"]
    if strip_closing:
        ids = ids[:-1]
        names = names[:-1]
    return [{"id": pid, "name": pname} for pid, pname in zip(ids, names, strict=False)]


async def shortest_path(
    node_model: type[Model],
    edge_model: type[Model],
    from_id: int | str | UUID,
    to_id: int | str | UUID,
    max_hops: int = 6,
    edge_type: str | None = None,
) -> list[RowMapping] | None:
    """Find shortest path between two nodes using BFS in SQL.

    Uses a recursive CTE with level-order traversal and path tracking
    to find the shortest path. Detects cycles via path array.

    :param node_model: Tortoise ORM model for nodes.
    :param edge_model: Tortoise ORM model for edges.
    :param from_id: Source node ID.
    :param to_id: Target node ID.
    :param max_hops: Maximum path length.
    :param edge_type: Filter by edge type (None = all types).
    :returns: List of node dicts forming the path, or None if no path exists.

    Usage::

        path = await shortest_path(
            Entity, Relationship,
            from_id=entity_a.id,
            to_id=entity_b.id,
            max_hops=5,
        )
        if path:
            for node in path:
                print(node["name"])
    """
    node_table = node_model._meta.db_table
    edge_table = edge_model._meta.db_table
    et_clause, et_params = _et_clause(edge_type, 4)

    sql = f"""
        {_walk_cte(node_table, edge_table, et_clause)}
        SELECT path_ids, path_names, hops
        FROM paths
        WHERE id = $3
        ORDER BY hops
        LIMIT 1
    """

    conn = connections.get("default")
    params: list[int | str | UUID] = [from_id, max_hops, to_id, *et_params]
    _, results = await conn.execute_query(sql, params)
    if not results:
        return None
    return _path_from_row(_as_path_row(results[0]))


async def all_paths(
    node_model: type[Model],
    edge_model: type[Model],
    from_id: int | str | UUID,
    to_id: int | str | UUID,
    max_hops: int = 6,
    max_paths: int = 10,
    edge_type: str | None = None,
) -> list[list[RowMapping]]:
    """Find all paths between two nodes.

    Returns up to ``max_paths`` distinct paths, each as a list of
    node dicts. Paths are ordered by length (shortest first).

    :param node_model: Tortoise ORM model for nodes.
    :param edge_model: Tortoise ORM model for edges.
    :param from_id: Source node ID.
    :param to_id: Target node ID.
    :param max_hops: Maximum path length.
    :param max_paths: Maximum number of paths to return.
    :param edge_type: Filter by edge type (None = all types).
    :returns: List of paths, each path is a list of node dicts.

    Usage::

        paths = await all_paths(
            Entity, Relationship,
            from_id=entity_a.id,
            to_id=entity_b.id,
            max_hops=5,
            max_paths=10,
        )
        for path in paths:
            print(" → ".join(n["name"] for n in path))
    """
    node_table = node_model._meta.db_table
    edge_table = edge_model._meta.db_table
    et_clause, et_params = _et_clause(edge_type, 5)

    sql = f"""
        {_walk_cte(node_table, edge_table, et_clause)}
        SELECT DISTINCT path_ids, path_names, hops
        FROM paths
        WHERE id = $3
        ORDER BY hops
        LIMIT $4
    """

    conn = connections.get("default")
    params: list[int | str | UUID] = [from_id, max_hops, to_id, max_paths, *et_params]
    _, results = await conn.execute_query(sql, params)

    return [_path_from_row(_as_path_row(row)) for row in results]


async def find_cycles(
    node_model: type[Model],
    edge_model: type[Model],
    max_depth: int = 10,
    edge_type: str | None = None,
) -> list[list[RowMapping]]:
    """Detect cycles in the graph.

    Walks every edge (plus both directions of bidirectional edges) and
    returns each simple cycle exactly once — the cycle is canonicalized
    so the walk always starts at its minimum node ID, rotations are not
    duplicated, and walks never circle a cycle more than once.

    :param node_model: Tortoise ORM model for nodes.
    :param edge_model: Tortoise ORM model for edges.
    :param max_depth: Maximum cycle length to detect.
    :param edge_type: Filter by edge type (None = all types).
    :returns: List of cycles, each cycle is a list of node dicts
        (without the repeated closing node).

    Usage::

        cycles = await find_cycles(Entity, Relationship, max_depth=5)
        for cycle in cycles:
            print(" → ".join(n["name"] for n in cycle) + " → " + cycle[0]["name"])
    """
    node_table = node_model._meta.db_table
    edge_table = edge_model._meta.db_table
    et_clause, et_params = _et_clause(edge_type, 2)

    sql = f"""
        WITH RECURSIVE walk AS (
            SELECT
                n.id AS start_id,
                n.id AS curr_id,
                ARRAY[n.id] AS path_ids,
                ARRAY[n.name::text] AS path_names,
                0 AS depth
            FROM {node_table} n

            UNION

            SELECT
                w.start_id,
                n.id AS curr_id,
                w.path_ids || n.id,
                w.path_names || n.name,
                w.depth + 1
            FROM walk w
            JOIN {edge_table} e ON (
                e.source_id = w.curr_id
                OR (e.is_bidirectional AND e.target_id = w.curr_id)
            )
            JOIN {node_table} n ON (
                (e.source_id = w.curr_id AND n.id = e.target_id)
                OR (e.is_bidirectional AND e.target_id = w.curr_id AND n.id = e.source_id)
            )
            WHERE w.depth < $1 {et_clause}
            -- Allow the anchor (depth 0) to take its first hop, but never
            -- extend a walk that has already closed (curr_id = start_id).
            AND (w.depth = 0 OR w.curr_id <> w.start_id)
            -- Close the walk when returning to the start; otherwise only
            -- visit unvisited nodes that sort after the start so each
            -- cycle is reported once, starting at its minimum node.
            AND (n.id = w.start_id OR (NOT (n.id = ANY(w.path_ids)) AND n.id > w.start_id))
        )
        SELECT DISTINCT path_ids, path_names, depth
        FROM walk
        WHERE depth > 0
        AND curr_id = path_ids[1]
        ORDER BY depth
        LIMIT 100
    """

    conn = connections.get("default")
    params: list[int | str] = [max_depth, *et_params]
    _, results = await conn.execute_query(sql, params)

    return [
        # The closing start node is repeated at the end of the walk —
        # strip it so the printed cycle reads "a → b → a".
        _path_from_row(_as_path_row(row), strip_closing=True)
        for row in results
    ]
