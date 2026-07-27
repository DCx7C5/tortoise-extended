"""CTE-based graph traversal for edge table patterns.

Provides recursive CTE traversal for graphs stored with separate node
and edge tables. Supports ancestor/descendant queries, neighbor discovery,
and cycle detection.

Usage::

    from tortoise_extended.expressions.graph_traversal import GraphTraversal

    traversal = GraphTraversal(NodeModel, EdgeModel)

    # Find all ancestors
    ancestors = await traversal.ancestors(node_id, max_depth=5)

    # Find all descendants
    descendants = await traversal.descendants(node_id, max_depth=5)

    # Get neighbors
    neighbors = await traversal.neighbors(node_id, direction="both")
"""

from typing import Any

from tortoise import connections


class GraphTraversal:
    """CTE-based graph traversal with depth limits and cycle detection.

    Traverses graphs stored in separate node and edge tables using
    PostgreSQL recursive CTEs. Handles bidirectional edges and
    edge type filtering.

    :param node_model: The Tortoise ORM model for graph nodes.
    :param edge_model: The Tortoise ORM model for graph edges.
    :param source_field: FK field name on edge for source node (default: ``source_id``).
    :param target_field: FK field name on edge for target node (default: ``target_id``).

    Usage::

        from myapp.models import Entity, Relationship

        traversal = GraphTraversal(Entity, Relationship)

        # Ancestors of entity 42
        ancestors = await traversal.ancestors(
            node_id=42,
            max_depth=5,
            edge_type="parent_of",
        )

        # Neighbors of entity 42
        neighbors = await traversal.neighbors(
            node_id=42,
            direction="outgoing",
            edge_type="knows",
        )
    """

    def __init__(
        self,
        node_model: type,
        edge_model: type,
        source_field: str = "source_id",
        target_field: str = "target_id",
    ) -> None:
        self.node_model = node_model
        self.edge_model = edge_model
        self._node_table = node_model._meta.db_table
        self._edge_table = edge_model._meta.db_table
        self._source_field = source_field
        self._target_field = target_field

    async def ancestors(
        self,
        node_id: Any,
        max_depth: int = 10,
        edge_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find all ancestors of a node using recursive CTE.

        Traverses edges in reverse (target → source) to find all nodes
        that can reach the given node. Supports bidirectional edges.

        :param node_id: Starting node ID.
        :param max_depth: Maximum traversal depth.
        :param edge_type: Filter by edge type (None = all types).
        :returns: List of node dicts ordered by depth ascending (root first).
        """
        et_filter = f"AND e.edge_type = '{edge_type}'" if edge_type else ""

        sql = f"""
            WITH RECURSIVE ancestors AS (
                SELECT n.id, n.name, n.depth, 0 AS path_depth
                FROM {self._node_table} n
                WHERE n.id = $1

                UNION

                SELECT n.id, n.name, n.depth, a.path_depth + 1
                FROM {self._node_table} n
                JOIN {self._edge_table} e ON (
                    e.{self._target_field} = a.id
                    OR (e.is_bidirectional AND e.{self._source_field} = a.id)
                )
                JOIN ancestors a ON (
                    e.{self._source_field} = a.id
                    OR (e.is_bidirectional AND e.{self._target_field} = a.id)
                )
                WHERE a.path_depth < $2 {et_filter}
                AND n.id != $1
            )
            SELECT DISTINCT id, name, depth, path_depth
            FROM ancestors
            WHERE id != $1
            ORDER BY path_depth, name
        """

        conn = connections.get("default")
        results, _ = await conn.execute_query(sql, [node_id, max_depth])
        return [dict(r) for r in results]

    async def descendants(
        self,
        node_id: Any,
        max_depth: int = 10,
        edge_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find all descendants of a node using recursive CTE.

        Traverses edges forward (source → target) to find all nodes
        reachable from the given node. Supports bidirectional edges.

        :param node_id: Starting node ID.
        :param max_depth: Maximum traversal depth.
        :param edge_type: Filter by edge type (None = all types).
        :returns: List of node dicts ordered by depth ascending (children first).
        """
        et_filter = f"AND e.edge_type = '{edge_type}'" if edge_type else ""

        sql = f"""
            WITH RECURSIVE descendants AS (
                SELECT n.id, n.name, n.depth, 0 AS path_depth
                FROM {self._node_table} n
                WHERE n.id = $1

                UNION

                SELECT n.id, n.name, n.depth, d.path_depth + 1
                FROM {self._node_table} n
                JOIN {self._edge_table} e ON (
                    e.{self._source_field} = d.id
                    OR (e.is_bidirectional AND e.{self._target_field} = d.id)
                )
                JOIN descendants d ON (
                    e.{self._target_field} = d.id
                    OR (e.is_bidirectional AND e.{self._source_field} = d.id)
                )
                WHERE d.path_depth < $2 {et_filter}
                AND n.id != $1
            )
            SELECT DISTINCT id, name, depth, path_depth
            FROM descendants
            WHERE id != $1
            ORDER BY path_depth, name
        """

        conn = connections.get("default")
        results, _ = await conn.execute_query(sql, [node_id, max_depth])
        return [dict(r) for r in results]

    async def neighbors(
        self,
        node_id: Any,
        direction: str = "both",
        edge_type: str | None = None,
        max_depth: int = 1,
    ) -> list[dict[str, Any]]:
        """Get neighbors within max_depth hops.

        :param node_id: Starting node ID.
        :param direction: ``"outgoing"``, ``"incoming"``, or ``"both"``.
        :param edge_type: Filter by edge type (None = all types).
        :param max_depth: Maximum traversal depth (1 = direct neighbors only).
        :returns: List of neighbor node dicts with edge metadata.
        """
        et_filter = f"AND e.edge_type = '{edge_type}'" if edge_type else ""

        # Build direction-specific CTE SQL
        if direction == "outgoing":
            sql = f"""
                WITH RECURSIVE bfs AS (
                    SELECT n.id, n.name, n.depth, 0 AS hops
                    FROM {self._node_table} n
                    WHERE n.id = $1

                    UNION

                    SELECT n.id, n.name, n.depth, b.hops + 1
                    FROM {self._node_table} n
                    JOIN {self._edge_table} e ON e.{self._source_field} = b.id
                    JOIN bfs b ON e.{self._target_field} = b.id
                    WHERE b.hops < $2 {et_filter}
                    AND n.id != $1
                )
                SELECT DISTINCT id, name, depth, hops
                FROM bfs WHERE id != $1
                ORDER BY hops, name
            """
        elif direction == "incoming":
            sql = f"""
                WITH RECURSIVE bfs AS (
                    SELECT n.id, n.name, n.depth, 0 AS hops
                    FROM {self._node_table} n
                    WHERE n.id = $1

                    UNION

                    SELECT n.id, n.name, n.depth, b.hops + 1
                    FROM {self._node_table} n
                    JOIN {self._edge_table} e ON e.{self._target_field} = b.id
                    JOIN bfs b ON e.{self._source_field} = b.id
                    WHERE b.hops < $2 {et_filter}
                    AND n.id != $1
                )
                SELECT DISTINCT id, name, depth, hops
                FROM bfs WHERE id != $1
                ORDER BY hops, name
            """
        else:
            # "both" — outgoing + bidirectional
            sql = f"""
                WITH RECURSIVE bfs AS (
                    SELECT n.id, n.name, n.depth, 0 AS hops
                    FROM {self._node_table} n
                    WHERE n.id = $1

                    UNION

                    SELECT n.id, n.name, n.depth, b.hops + 1
                    FROM {self._node_table} n
                    JOIN {self._edge_table} e ON (
                        e.{self._source_field} = b.id
                        OR (e.is_bidirectional AND e.{self._target_field} = b.id)
                    )
                    JOIN bfs b ON (
                        e.{self._target_field} = b.id
                        OR (e.is_bidirectional AND e.{self._source_field} = b.id)
                    )
                    WHERE b.hops < $2 {et_filter}
                    AND n.id != $1
                )
                SELECT DISTINCT id, name, depth, hops
                FROM bfs WHERE id != $1
                ORDER BY hops, name
            """

        conn = connections.get("default")
        results, _ = await conn.execute_query(sql, [node_id, max_depth])
        return [dict(r) for r in results]

    async def has_cycle(
        self,
        edge_type: str | None = None,
        max_depth: int = 20,
    ) -> bool:
        """Check if the graph contains any cycles.

        Traverses from every node and checks if any node can reach itself.

        :param edge_type: Filter by edge type (None = all types).
        :param max_depth: Maximum traversal depth to check.
        :returns: True if a cycle is detected.
        """
        et_filter = f"AND e.edge_type = '{edge_type}'" if edge_type else ""

        sql = f"""
            SELECT EXISTS (
                WITH RECURSIVE walk AS (
                    SELECT n.id, 0 AS depth
                    FROM {self._node_table} n

                    UNION

                    SELECT n.id, w.depth + 1
                    FROM {self._node_table} n
                    JOIN {self._edge_table} e ON (
                        e.{self._source_field} = w.id
                        OR (e.is_bidirectional AND e.{self._target_field} = w.id)
                    )
                    JOIN walk w ON (
                        e.{self._target_field} = w.id
                        OR (e.is_bidirectional AND e.{self._source_field} = w.id)
                    )
                    WHERE w.depth < $1 {et_filter}
                    AND n.id = w.id
                )
                SELECT 1 FROM walk WHERE depth > 0 LIMIT 1
            ) AS has_cycle
        """

        conn = connections.get("default")
        results, _ = await conn.execute_query(sql, [max_depth])
        return bool(results[0]["has_cycle"])
