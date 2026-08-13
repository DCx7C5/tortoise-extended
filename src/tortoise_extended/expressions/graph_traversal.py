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

from typing import TYPE_CHECKING
from uuid import UUID

from tortoise import connections

from tortoise_extended._types import RowMapping
from tortoise_extended.expressions._edge_filter import et_clause as _et_clause

if TYPE_CHECKING:
    from tortoise.models import Model


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
        node_model: type[Model],
        edge_model: type[Model],
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
        node_id: int | str | UUID,
        max_depth: int = 10,
        edge_type: str | None = None,
    ) -> list[RowMapping]:
        """Find all ancestors of a node using recursive CTE.

        Traverses edges in reverse (target → source) to find all nodes
        that can reach the given node. Supports bidirectional edges.

        :param node_id: Starting node ID.
        :param max_depth: Maximum traversal depth.
        :param edge_type: Filter by edge type (None = all types).
        :returns: List of node dicts ordered by depth ascending (root first).
        """
        et_clause, et_params = _et_clause(edge_type, 3)

        sql = f"""
            WITH RECURSIVE ancestors AS (
                SELECT n.id, n.name, n.depth, 0 AS path_depth
                FROM {self._node_table} n
                WHERE n.id = $1

                UNION

                SELECT n.id, n.name, n.depth, a.path_depth + 1
                FROM ancestors a
                JOIN {self._edge_table} e ON (
                    e.{self._target_field} = a.id
                    OR (e.is_bidirectional AND e.{self._source_field} = a.id)
                )
                JOIN {self._node_table} n ON (
                    n.id = e.{self._source_field}
                    OR (e.is_bidirectional AND n.id = e.{self._target_field})
                )
                WHERE a.path_depth < $2 {et_clause}
                AND n.id != $1
            )
            SELECT id, MIN(name) AS name, MIN(depth) AS depth, MIN(path_depth) AS path_depth
            FROM ancestors
            WHERE id != $1
            GROUP BY id
            ORDER BY MIN(path_depth), MIN(name)
        """

        conn = connections.get("default")
        params: list[int | str | UUID] = [node_id, max_depth, *et_params]
        _, results = await conn.execute_query(sql, params)
        return [dict(r) for r in results]

    async def descendants(
        self,
        node_id: int | str | UUID,
        max_depth: int = 10,
        edge_type: str | None = None,
    ) -> list[RowMapping]:
        """Find all descendants of a node using recursive CTE.

        Traverses edges forward (source → target) to find all nodes
        reachable from the given node. Supports bidirectional edges.

        :param node_id: Starting node ID.
        :param max_depth: Maximum traversal depth.
        :param edge_type: Filter by edge type (None = all types).
        :returns: List of node dicts ordered by depth ascending (children first).
        """
        et_clause, et_params = _et_clause(edge_type, 3)

        sql = f"""
            WITH RECURSIVE descendants AS (
                SELECT n.id, n.name, n.depth, 0 AS path_depth
                FROM {self._node_table} n
                WHERE n.id = $1

                UNION

                SELECT n.id, n.name, n.depth, d.path_depth + 1
                FROM descendants d
                JOIN {self._edge_table} e ON (
                    e.{self._source_field} = d.id
                    OR (e.is_bidirectional AND e.{self._target_field} = d.id)
                )
                JOIN {self._node_table} n ON (
                    n.id = e.{self._target_field}
                    OR (e.is_bidirectional AND n.id = e.{self._source_field})
                )
                WHERE d.path_depth < $2 {et_clause}
                AND n.id != $1
            )
            SELECT id, MIN(name) AS name, MIN(depth) AS depth, MIN(path_depth) AS path_depth
            FROM descendants
            WHERE id != $1
            GROUP BY id
            ORDER BY MIN(path_depth), MIN(name)
        """

        conn = connections.get("default")
        params: list[int | str | UUID] = [node_id, max_depth, *et_params]
        _, results = await conn.execute_query(sql, params)
        return [dict(r) for r in results]

    async def neighbors(
        self,
        node_id: int | str | UUID,
        direction: str = "both",
        edge_type: str | None = None,
        max_depth: int = 1,
    ) -> list[RowMapping]:
        """Get neighbors within max_depth hops.

        :param node_id: Starting node ID.
        :param direction: ``"outgoing"``, ``"incoming"``, or ``"both"``.
        :param edge_type: Filter by edge type (None = all types).
        :param max_depth: Maximum traversal depth (1 = direct neighbors only).
        :returns: List of neighbor node dicts with edge metadata.
        """
        et_clause, et_params = _et_clause(edge_type, 3)

        if direction == "outgoing":
            edge_join = (
                f"e.{self._source_field} = b.id "
                f"OR (e.is_bidirectional AND e.{self._target_field} = b.id)"
            )
            node_join = (
                f"(e.{self._source_field} = b.id AND n.id = e.{self._target_field}) "
                f"OR (e.is_bidirectional AND e.{self._target_field} = b.id AND n.id = e.{self._source_field})"
            )
        elif direction == "incoming":
            edge_join = (
                f"e.{self._target_field} = b.id "
                f"OR (e.is_bidirectional AND e.{self._source_field} = b.id)"
            )
            node_join = (
                f"(e.{self._target_field} = b.id AND n.id = e.{self._source_field}) "
                f"OR (e.is_bidirectional AND e.{self._source_field} = b.id AND n.id = e.{self._target_field})"
            )
        else:
            # "both" — follow edges in either direction.
            edge_join = (
                f"e.{self._source_field} = b.id OR e.{self._target_field} = b.id"
            )
            node_join = (
                f"(e.{self._source_field} = b.id AND n.id = e.{self._target_field}) "
                f"OR (e.{self._target_field} = b.id AND n.id = e.{self._source_field})"
            )

        sql = f"""
            WITH RECURSIVE bfs AS (
                SELECT n.id, n.name, n.depth, 0 AS hops
                FROM {self._node_table} n
                WHERE n.id = $1

                UNION

                SELECT n.id, n.name, n.depth, b.hops + 1
                FROM bfs b
                JOIN {self._edge_table} e ON ({edge_join})
                JOIN {self._node_table} n ON ({node_join})
                WHERE b.hops < $2 {et_clause}
                AND n.id != $1
            )
            SELECT id, MIN(name) AS name, MIN(depth) AS depth, MIN(hops) AS hops
            FROM bfs WHERE id != $1
            GROUP BY id
            ORDER BY MIN(hops), MIN(name)
        """

        conn = connections.get("default")
        params: list[int | str | UUID] = [node_id, max_depth, *et_params]
        _, results = await conn.execute_query(sql, params)
        return [dict(r) for r in results]

    async def has_cycle(
        self,
        edge_type: str | None = None,
        max_depth: int = 20,
    ) -> bool:
        """Check if the graph contains any cycles.

        Follows every directed edge (plus both directions of bidirectional
        edges) and checks whether any node can reach itself via one or more
        hops. Self-loops are detected as depth-1 cycles.

        :param edge_type: Filter by edge type (None = all types).
        :param max_depth: Maximum traversal depth to check.
        :returns: True if a cycle is detected.
        """
        et_clause, et_params = _et_clause(edge_type, 2)
        anchor_filter = "WHERE e.edge_type = $2" if edge_type is not None else ""

        sql = f"""
            SELECT EXISTS (
                WITH RECURSIVE reach AS (
                    SELECT e.id AS eid, e.{self._source_field} AS from_id,
                           e.{self._target_field} AS to_id, 1 AS depth
                    FROM {self._edge_table} e
                    {anchor_filter}

                    UNION

                    SELECT r.eid, r.from_id,
                           CASE
                               WHEN e.{self._source_field} = r.to_id THEN e.{self._target_field}
                               ELSE e.{self._source_field}
                           END AS to_id,
                           r.depth + 1
                    FROM reach r
                    JOIN {self._edge_table} e ON (
                        e.{self._source_field} = r.to_id
                        OR (e.is_bidirectional AND e.{self._target_field} = r.to_id)
                    )
                    WHERE r.depth < $1 {et_clause}
                )
                SELECT 1 FROM reach WHERE from_id = to_id LIMIT 1
            ) AS has_cycle
        """

        conn = connections.get("default")
        params: list[int | str] = [max_depth, *et_params]
        _, results = await conn.execute_query(sql, params)
        return bool(results[0]["has_cycle"])
