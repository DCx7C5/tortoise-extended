"""Graph pathfinding algorithms via recursive CTE.

Provides shortest path, all paths, and cycle detection for graphs
stored in separate node and edge tables.

Usage::

    from tortoise_extended.expressions.pathfinding import shortest_path, all_paths

    path = await shortest_path(NodeModel, EdgeModel, from_id=1, to_id=42)
    paths = await all_paths(NodeModel, EdgeModel, from_id=1, to_id=42, max_hops=5)
"""

from typing import Any

from tortoise import connections


async def shortest_path(
    node_model: type,
    edge_model: type,
    from_id: Any,
    to_id: Any,
    max_hops: int = 6,
    edge_type: str | None = None,
) -> list[dict[str, Any]] | None:
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
    et_filter = f"AND e.edge_type = '{edge_type}'" if edge_type else ""

    sql = f"""
        WITH RECURSIVE paths AS (
            SELECT
                n.id, n.name, n.depth,
                ARRAY[n.id] AS path_ids,
                0 AS hops
            FROM {node_table} n
            WHERE n.id = $1

            UNION

            SELECT
                n.id, n.name, n.depth,
                p.path_ids || n.id,
                p.hops + 1
            FROM {node_table} n
            JOIN {edge_table} e ON (
                e.source_id = p.id
                OR (e.is_bidirectional AND e.target_id = p.id)
            )
            JOIN paths p ON (
                e.target_id = p.id
                OR (e.is_bidirectional AND e.source_id = p.id)
            )
            WHERE p.hops < $2
            AND NOT (n.id = ANY(p.path_ids))
            {et_filter}
        )
        SELECT id, name, depth, hops
        FROM paths
        WHERE id = $3
        ORDER BY hops
        LIMIT 1
    """

    conn = connections.get("default")
    results, _ = await conn.execute_query(sql, [from_id, max_hops, to_id])
    if not results:
        return None
    return [dict(r) for r in results]


async def all_paths(
    node_model: type,
    edge_model: type,
    from_id: Any,
    to_id: Any,
    max_hops: int = 6,
    max_paths: int = 10,
    edge_type: str | None = None,
) -> list[list[dict[str, Any]]]:
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
    et_filter = f"AND e.edge_type = '{edge_type}'" if edge_type else ""

    sql = f"""
        WITH RECURSIVE paths AS (
            SELECT
                n.id, n.name, n.depth,
                ARRAY[n.id] AS path_ids,
                ARRAY[n.name] AS path_names,
                0 AS hops
            FROM {node_table} n
            WHERE n.id = $1

            UNION

            SELECT
                n.id, n.name, n.depth,
                p.path_ids || n.id,
                p.path_names || n.name,
                p.hops + 1
            FROM {node_table} n
            JOIN {edge_table} e ON (
                e.source_id = p.id
                OR (e.is_bidirectional AND e.target_id = p.id)
            )
            JOIN paths p ON (
                e.target_id = p.id
                OR (e.is_bidirectional AND e.source_id = p.id)
            )
            WHERE p.hops < $2
            AND NOT (n.id = ANY(p.path_ids))
            {et_filter}
        )
        SELECT path_ids, path_names, hops
        FROM paths
        WHERE id = $3
        ORDER BY hops
        LIMIT $4
    """

    conn = connections.get("default")
    results, _ = await conn.execute_query(sql, [from_id, max_hops, to_id, max_paths])

    paths = []
    for row in results:
        path = [
            {"id": pid, "name": pname}
            for pid, pname in zip(row["path_ids"], row["path_names"], strict=False)
        ]
        paths.append(path)
    return paths


async def find_cycles(
    node_model: type,
    edge_model: type,
    max_depth: int = 10,
    edge_type: str | None = None,
) -> list[list[dict[str, Any]]]:
    """Detect cycles in the graph.

    Returns a list of cycles found. Each cycle is a list of node
    dicts forming a closed loop. Returns empty list if no cycles.

    :param node_model: Tortoise ORM model for nodes.
    :param edge_model: Tortoise ORM model for edges.
    :param max_depth: Maximum cycle length to detect.
    :param edge_type: Filter by edge type (None = all types).
    :returns: List of cycles, each cycle is a list of node dicts.

    Usage::

        cycles = await find_cycles(Entity, Relationship, max_depth=5)
        for cycle in cycles:
            print(" → ".join(n["name"] for n in cycle) + " → " + cycle[0]["name"])
    """
    node_table = node_model._meta.db_table
    edge_table = edge_model._meta.db_table
    et_filter = f"AND e.edge_type = '{edge_type}'" if edge_type else ""

    sql = f"""
        WITH RECURSIVE walk AS (
            SELECT
                n.id, n.name,
                ARRAY[n.id] AS path_ids,
                ARRAY[n.name] AS path_names,
                0 AS depth
            FROM {node_table} n

            UNION

            SELECT
                n.id, n.name,
                w.path_ids || n.id,
                w.path_names || n.name,
                w.depth + 1
            FROM {node_table} n
            JOIN {edge_table} e ON (
                e.source_id = w.id
                OR (e.is_bidirectional AND e.target_id = w.id)
            )
            JOIN walk w ON (
                e.target_id = w.id
                OR (e.is_bidirectional AND e.source_id = w.id)
            )
            WHERE w.depth < $1 {et_filter}
            AND NOT (n.id = ANY(w.path_ids))
        )
        SELECT DISTINCT path_ids, path_names, depth
        FROM walk
        WHERE depth > 0
        AND id = (
            SELECT walk.path_ids[1] FROM walk
            WHERE walk.path_ids[walk.depth + 1] = walk.id
            AND walk.depth > 0
            LIMIT 1
        )
        ORDER BY depth
        LIMIT 100
    """

    # Simpler approach: find nodes that can reach themselves
    sql = f"""
        WITH RECURSIVE walk AS (
            SELECT
                n.id, n.name,
                ARRAY[n.id] AS path_ids,
                ARRAY[n.name] AS path_names,
                0 AS depth
            FROM {node_table} n

            UNION

            SELECT
                n.id, n.name,
                w.path_ids || n.id,
                w.path_names || n.name,
                w.depth + 1
            FROM {node_table} n
            JOIN {edge_table} e ON (
                e.source_id = w.id
                OR (e.is_bidirectional AND e.target_id = w.id)
            )
            JOIN walk w ON (
                e.target_id = w.id
                OR (e.is_bidirectional AND e.source_id = w.id)
            )
            WHERE w.depth < $1 {et_filter}
            AND NOT (n.id = ANY(w.path_ids))
        )
        SELECT path_ids, path_names, depth
        FROM walk
        WHERE depth > 0
        AND id = path_ids[1]
        GROUP BY path_ids, path_names, depth
        ORDER BY depth
        LIMIT 100
    """

    conn = connections.get("default")
    results, _ = await conn.execute_query(sql, [max_depth])

    cycles = []
    for row in results:
        cycle = [
            {"id": cid, "name": cname}
            for cid, cname in zip(row["path_ids"], row["path_names"], strict=False)
        ]
        cycles.append(cycle)
    return cycles
