"""Single-query graph + vector compositor with typed results.

Combines a recursive-CTE graph traversal (seed node, bounded hops) with a
pgvector distance predicate in ONE SQL statement, then hydrates the raw
result rows back into typed Tortoise model instances.

Typing layer:
    Raw rows from ``conn.execute_query`` are :data:`~tortoise_extended._types.RowMapping`
    dicts. The typing layer maps each row's DB column names back to model
    field names (``field.source_field or field_name``), then hands the keyword
    arguments to ``Model._init_from_db`` — Tortoise's own row-hydration entry
    point — so every result node is a real typed model instance wrapped in a
    :class:`GraphVectorHit`. The private upstream method is invoked through
    ``getattr`` to keep ``reportPrivateUsage`` at zero warnings (see
    ``_types.py`` for the sanctioned pattern).

Requires: PostgreSQL + the ``vector`` extension, and an edge model that
declares ``source_id`` / ``target_id`` (customizable) plus — when
bidirectional edges are used — an ``is_bidirectional`` boolean column.

Usage::

    from tortoise_extended import GraphVectorSearch

    results = await GraphVectorSearch(
        node_model=Entity,
        edge_model=Relationship,
        query_vector=[0.1, 0.2, 0.3],
        seed_id="uuid-of-seed",
        max_hops=2,
    ).search()

    for hit in results:
        entity: Entity = hit.node  # typed model instance
        print(entity.name, hit.distance, hit.hops)
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Generic, TypeVar, cast
from uuid import UUID

import msgspec
from tortoise import connections

from tortoise_extended._types import RowMapping
from tortoise_extended.exceptions import HybridSearchError
from tortoise_extended.expressions._edge_filter import et_clause as _et_clause
from tortoise_extended.expressions.graph_filters import vector_encoder

if TYPE_CHECKING:
    from tortoise.models import Model

ModelT = TypeVar("ModelT", bound="Model")

__all__ = ["GraphVectorHit", "GraphVectorSearch"]


class GraphVectorHit(msgspec.Struct, Generic[ModelT]):
    """A typed vector+graph hit: a hydrated node model plus search metadata.

    :param node: The hydrated Tortoise model instance (typed via ``ModelT``).
    :param distance: Distance for ``l2``/``cosine`` metrics, or the positive
        inner product for the ``inner_product`` metric.
    :param hops: Number of graph hops from the seed node (0 = the seed).
    """

    node: ModelT
    distance: float
    hops: int


class GraphVectorSearch:
    """Find nodes that are both vector-similar and reachable from a seed.

    Executes a single parameterized SQL statement: a recursive CTE walks the
    graph from *seed_id* up to *max_hops*, then the reachable nodes are joined
    against their pgvector distance from *query_vector*, filtered by
    *min_distance* when given, ordered by similarity, and limited to
    *max_results*.

    :param node_model: The Tortoise ORM model for graph nodes.
    :param edge_model: The Tortoise ORM model for graph edges.
    :param query_vector: Query embedding (list of floats or a pgvector string
        such as ``"[0.1,0.2]"``).
    :param seed_id: Node ID to start the graph traversal from.
    :param vector_field: Name of the VectorField column on *node_model*
        (default: ``"embedding"``).
    :param max_hops: Maximum traversal depth from the seed (0 = seed only).
    :param direction: ``"outgoing"``, ``"incoming"``, or ``"both"``.
    :param edge_type: Optional edge type filter (``None`` = all types). The
        edge model must declare an ``edge_type`` column when set.
    :param distance_metric: ``"l2"``, ``"cosine"``, or ``"inner_product"``.
    :param max_results: Maximum number of hits to return.
    :param min_distance: Optional threshold — distance ``<=`` threshold for
        ``l2``/``cosine``, inner product ``>=`` threshold for
        ``inner_product`` (``None`` = no filter).
    :param source_field: Edge field holding the source node id (default ``"source_id"``).
    :param target_field: Edge field holding the target node id (default ``"target_id"``).
    :raises HybridSearchError: If *distance_metric* or *direction* is unsupported.
    """

    _METRICS: tuple[str, ...] = ("l2", "cosine", "inner_product")
    _DIRECTIONS: tuple[str, ...] = ("outgoing", "incoming", "both")

    def __init__(
        self,
        node_model: type[ModelT],
        edge_model: type[Model],
        *,
        query_vector: list[float] | str,
        seed_id: int | str | UUID,
        vector_field: str = "embedding",
        max_hops: int = 2,
        direction: str = "both",
        edge_type: str | None = None,
        distance_metric: str = "l2",
        max_results: int = 20,
        min_distance: float | None = None,
        source_field: str = "source_id",
        target_field: str = "target_id",
    ) -> None:
        if distance_metric not in self._METRICS:
            raise HybridSearchError(
                f"Unsupported distance_metric {distance_metric!r}; "
                f"expected one of {', '.join(self._METRICS)}"
            )
        if direction not in self._DIRECTIONS:
            raise HybridSearchError(
                f"Unsupported direction {direction!r}; "
                f"expected one of {', '.join(self._DIRECTIONS)}"
            )
        if vector_field not in node_model._meta.fields_map:
            raise HybridSearchError(
                f"Unknown vector field {vector_field!r} on {node_model.__name__}; "
                f"expected one of {', '.join(sorted(node_model._meta.fields_map))}"
            )
        edge_fields = edge_model._meta.fields_map
        for name in (source_field, target_field):
            if name not in edge_fields:
                raise HybridSearchError(
                    f"Unknown edge field {name!r} on {edge_model.__name__}; "
                    f"expected one of {', '.join(sorted(edge_fields))}"
                )
        if edge_type is not None and "edge_type" not in edge_fields:
            raise HybridSearchError(
                f"edge_type filtering requires an 'edge_type' field on "
                f"{edge_model.__name__}; found {', '.join(sorted(edge_fields))}"
            )
        self._node_model = node_model
        self._edge_model = edge_model
        self._node_table = node_model._meta.db_table
        self._edge_table = edge_model._meta.db_table
        self._pk_col = node_model._meta.db_pk_column
        self._vector_field = vector_field
        self._source_field = source_field
        self._target_field = target_field
        self._has_bidirectional = "is_bidirectional" in edge_model._meta.fields_map
        self._seed_id: int | str | UUID = seed_id
        self._max_hops = max_hops
        self._direction = direction
        self._edge_type = edge_type
        self._metric = distance_metric
        self._max_results = max_results
        self._min_distance = min_distance
        if isinstance(query_vector, str):
            self._vector_literal = query_vector.strip().strip("'\"")
        else:
            self._vector_literal = vector_encoder(query_vector)

    # ------------------------------------------------------------------
    # SQL assembly
    # ------------------------------------------------------------------

    def _distance_sql(self, column: str, param_index: int) -> tuple[str, str, str]:
        """Return ``(distance_expr, threshold_op, order_dir)`` for the metric.

        ``l2``/``cosine`` use pgvector distance operators directly (smaller is
        better, threshold is a maximum distance). ``inner_product`` uses the
        negated ``<#>`` operator so the value is the positive inner product
        (larger is better, threshold is a minimum) — matching ``HybridSearch``.
        """
        if self._metric == "l2":
            return f"(n.{column} <-> ${param_index}::vector)", "<=", "ASC"
        if self._metric == "cosine":
            return f"(n.{column} <=> ${param_index}::vector)", "<=", "ASC"
        return f"((-1) * (n.{column} <#> ${param_index}::vector))", ">=", "DESC"

    def _recursion_sql(self) -> tuple[str, str]:
        """Return ``(edge_join, next_node_expr)`` for the recursive step."""
        src = self._source_field
        tgt = self._target_field
        if self._has_bidirectional:
            if self._direction == "outgoing":
                join = (
                    f"e.{src} = b.node_id OR (e.is_bidirectional AND e.{tgt} = b.node_id)"
                )
                nxt = f"CASE WHEN e.{src} = b.node_id THEN e.{tgt} ELSE e.{src} END"
            elif self._direction == "incoming":
                join = (
                    f"e.{tgt} = b.node_id OR (e.is_bidirectional AND e.{src} = b.node_id)"
                )
                nxt = f"CASE WHEN e.{tgt} = b.node_id THEN e.{src} ELSE e.{tgt} END"
            else:
                join = f"e.{src} = b.node_id OR e.{tgt} = b.node_id"
                nxt = f"CASE WHEN e.{src} = b.node_id THEN e.{tgt} ELSE e.{src} END"
        else:
            if self._direction == "outgoing":
                join = f"e.{src} = b.node_id"
                nxt = f"e.{tgt}"
            elif self._direction == "incoming":
                join = f"e.{tgt} = b.node_id"
                nxt = f"e.{src}"
            else:
                join = f"e.{src} = b.node_id OR e.{tgt} = b.node_id"
                nxt = f"CASE WHEN e.{src} = b.node_id THEN e.{tgt} ELSE e.{src} END"
        return join, nxt

    # ------------------------------------------------------------------
    # Typing layer — raw rows -> typed model instances
    # ------------------------------------------------------------------

    def _node_kwargs(self, row: RowMapping) -> dict[str, object]:
        """Map DB column names in *row* back to model field names.

        ``n.*`` returns columns using their DB names; ``_init_from_db`` expects
        model attribute names, which differ only when ``source_field`` /
        ``db_column`` is set on the field.
        """
        kwargs: dict[str, object] = {}
        for field_name, field_obj in self._node_model._meta.fields_map.items():
            column = field_obj.source_field or field_name
            if column in row:
                kwargs[field_name] = row[column]
        return kwargs

    def _hydrate(self, row: RowMapping) -> GraphVectorHit[ModelT]:
        """Hydrate one raw result row into a typed hit.

        ``Model._init_from_db`` is Tortoise's row-hydration entry point. It is
        invoked via ``getattr`` (string lookup) so pyright's
        ``reportPrivateUsage`` stays quiet — the sanctioned pattern from
        ``_types.py``.
        """
        init_from_db = cast(
            Callable[..., ModelT],
            getattr(self._node_model, "_init_from_db"),
        )
        node = init_from_db(**self._node_kwargs(row))
        # Metadata aliases are prefixed (_gvs_*) so they can never collide with
        # a node column named "distance" / "hops" in the raw row.
        return GraphVectorHit(
            node=node,
            distance=float(row["_gvs_distance"]),
            hops=int(row["_gvs_hops"]),
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def search(self) -> list[GraphVectorHit[ModelT]]:
        """Execute the single-query graph + vector search.

        :returns: Typed hits ordered by similarity (best first), each wrapping
            a hydrated node model instance plus ``distance`` / ``hops``.
        """
        pk = self._pk_col
        vf = self._vector_field
        join, nxt = self._recursion_sql()
        dist, threshold_op, order_dir = self._distance_sql(vf, 4)

        # Parameter layout: $1 seed, $2 max_hops, $3 max_results, $4 vector,
        # then optionally $5 edge_type and/or $6 min_distance (sequential).
        et_clause, et_params = _et_clause(self._edge_type, 5)
        threshold_index = 5 + len(et_params)
        if self._min_distance is not None:
            threshold_clause = f"AND {dist} {threshold_op} ${threshold_index}"
        else:
            threshold_clause = ""

        sql = f"""
            WITH RECURSIVE reach AS (
                SELECT n.{pk} AS node_id, 0 AS hops
                FROM {self._node_table} n
                WHERE n.{pk} = $1

                UNION

                SELECT {nxt} AS node_id, b.hops + 1
                FROM reach b
                JOIN {self._edge_table} e ON ({join})
                WHERE b.hops < $2
                  AND {nxt} != $1
                  {et_clause}
            )
            SELECT
                n.*,
                {dist} AS _gvs_distance,
                MIN(r.hops) AS _gvs_hops
            FROM reach r
            JOIN {self._node_table} n ON n.{pk} = r.node_id
            WHERE n.{vf} IS NOT NULL
              {threshold_clause}
            -- GROUP BY the node PK: PostgreSQL's functional-dependency
            -- inference makes n.* and {dist} valid per group (PG-only query).
            GROUP BY n.{pk}
            ORDER BY {dist} {order_dir}, n.{pk}
            LIMIT $3
        """

        params: list[object] = [
            self._seed_id,
            self._max_hops,
            self._max_results,
            self._vector_literal,
            *et_params,
        ]
        if self._min_distance is not None:
            params.append(self._min_distance)

        conn = connections.get("default")
        _, results = await conn.execute_query(sql, params)
        return [self._hydrate(row) for row in results]
