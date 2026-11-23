"""Model-class facade over the graph traversal, pathfinding and vector builders.

:class:`Graph` binds a node model and an edge model once and exposes the
graph operations against model **instances** instead of raw table/column
names: it composes :class:`~tortoise_extended.expressions.graph_traversal.GraphTraversal`,
the pathfinding helpers (:func:`~tortoise_extended.expressions.pathfinding.shortest_path`,
:func:`~tortoise_extended.expressions.pathfinding.find_cycles`) and
:class:`~tortoise_extended.expressions.graph_vector_search.GraphVectorSearch`.
The facade itself adds no SQL — every query is one of those builders, or an
ordinary Tortoise queryset used to hydrate returned rows into model instances.

Node models must satisfy the builders' shared schema contract: an ``id``
primary-key column, a ``name`` text column and an integer ``depth`` column.
Edge models declare ``source_id`` / ``target_id`` (customizable), and
``is_bidirectional`` when directed traversal is used; ``edge_type`` /
``weight`` / ``namespace`` are optional and written only when declared.

Usage::

    from tortoise_extended import Graph

    graph = Graph(Entity, Relationship, namespace="shop")

    edge = await graph.link(a, b, edge_type="cites", weight=0.5)
    peers = await graph.neighbours(a, direction="outgoing")
    path = await graph.shortest_path(a, b, max_depth=4)
    hits = await graph.search([0.1, 0.2, 0.3], seed=a, k=5, metric="cosine")
"""

from collections.abc import Sequence
from typing import Literal, cast
from uuid import UUID

from tortoise.models import Model

from tortoise_extended._types import CacheValue, RowValue
from tortoise_extended.exceptions import GraphError
from tortoise_extended.expressions.graph_traversal import GraphTraversal
from tortoise_extended.expressions.graph_vector_search import (
    GraphVectorHit,
    GraphVectorSearch,
)
from tortoise_extended.expressions.pathfinding import (
    find_cycles as _find_cycles,
)
from tortoise_extended.expressions.pathfinding import (
    shortest_path as _shortest_path,
)

__all__ = ["Graph"]


class Graph[ModelT: Model]:
    """Graph operations bound to one node model and one edge model.

    The constructor validates the edge field names once, so every method can
    assume the configured columns exist. The node model must declare ``id``,
    ``name`` and ``depth`` (the recursive-CTE contract of
    :class:`GraphTraversal` and the pathfinding helpers); the edge model must
    declare the configured source/target fields.

    Namespace scoping: :meth:`link` and :meth:`unlink` write and filter the
    edge ``namespace`` column when the edge model declares one, and every
    node passed to the facade must belong to the facade's namespace when the
    node model declares a ``namespace`` column. The recursive-CTE builders are
    table-wide (they have no namespace predicate), so :meth:`neighbours`
    scopes its hydrated result to the facade namespace while
    :meth:`shortest_path` / :meth:`find_cycles` remain namespace-agnostic —
    filtering a path's nodes would break its contiguity.

    Args:
        node_model: Tortoise ORM model for graph nodes.
        edge_model: Tortoise ORM model for graph edges.
        namespace: Namespace written to edges and required of every node.
        source_field: Edge field holding the source node id.
        target_field: Edge field holding the target node id.
        edge_type_field: Edge field holding the relationship type.

    Raises:
        GraphError: If the edge model does not declare
            ``source_field`` / ``target_field`` / ``edge_type_field``.

    Usage (illustrative; needs async context + configured DB)::

        graph = Graph(Entity, Relationship)

        await graph.link(a, b, edge_type="cites")
        neighbours = await graph.neighbours(a, direction="both")
        path = await graph.shortest_path(a, b, max_depth=5)
        cycles = await graph.find_cycles()
        hits = await graph.search([0.1, 0.2, 0.3], seed=a, k=10)
    """

    def __init__(
        self,
        node_model: type[ModelT],
        edge_model: type[Model],
        *,
        namespace: str = "default",
        source_field: str = "source_id",
        target_field: str = "target_id",
        edge_type_field: str = "edge_type",
    ) -> None:
        edge_fields = edge_model._meta.fields_map
        for name in (source_field, target_field, edge_type_field):
            if name not in edge_fields:
                raise GraphError(
                    f"{edge_model.__name__} is not usable as an edge model: "
                    f"missing the {name!r} field. "
                    f"Declared fields: {', '.join(sorted(edge_fields))}."
                )
        self._node_model = node_model
        self._edge_model = edge_model
        self._namespace = namespace
        self._source_field = source_field
        self._target_field = target_field
        self._edge_type_field = edge_type_field
        # Built once: GraphTraversal validates the fields again and quotes the
        # table/column identifiers for its recursive CTEs.
        self._traversal = GraphTraversal(
            node_model,
            edge_model,
            source_field=source_field,
            target_field=target_field,
        )

    # ------------------------------------------------------------------
    # Node validation
    # ------------------------------------------------------------------

    def _require_node(self, node: Model, *, role: str) -> None:
        """Validate a node against the facade's model and namespace.

        Args:
            node: The node instance supplied by the caller.
            role: Human-readable role used in error messages
                (``"source"``, ``"target"``, ``"seed"``, ``"node"``).

        Raises:
            GraphError: If the node's class is not the facade node model, the
                node belongs to another namespace, or the node is unpersisted.
        """
        if not isinstance(node, self._node_model):
            raise GraphError(
                f"{role} is a {type(node).__name__}, not a "
                f"{self._node_model.__name__}: this Graph is bound to "
                f"{self._node_model.__name__} nodes."
            )
        if "namespace" in self._node_model._meta.fields_map:
            node_namespace = getattr(node, "namespace", None)
            if node_namespace != self._namespace:
                raise GraphError(
                    f"{role} is in namespace {node_namespace!r}; this Graph is "
                    f"scoped to {self._namespace!r}."
                )
        if node.pk is None:
            raise GraphError(f"{role} is unpersisted: save it before using it.")

    # ------------------------------------------------------------------
    # Hydration
    # ------------------------------------------------------------------

    async def _hydrate(
        self,
        ids: Sequence[int | str | UUID],
        *,
        scoped: bool = False,
    ) -> list[ModelT]:
        """Load model instances for *ids*, preserving the input order.

        Args:
            ids: Primary keys in result order; duplicates are collapsed.
            scoped: Filter the loaded rows to the facade namespace when the
                node model declares a ``namespace`` column.

        Returns:
            Hydrated node instances in *ids* order; ids that no longer exist
            (a concurrent delete) are skipped.
        """
        if not ids:
            return []
        filters: dict[str, RowValue | list[RowValue]] = {
            "pk__in": cast(list[RowValue], list(ids)),
        }
        if scoped and "namespace" in self._node_model._meta.fields_map:
            filters["namespace"] = self._namespace
        nodes = await self._node_model.filter(**filters)
        by_pk: dict[int | str | UUID, ModelT] = {
            cast(int | str | UUID, node.pk): node for node in nodes
        }
        return [by_pk[key] for key in ids if key in by_pk]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def link(
        self,
        source: ModelT,
        target: ModelT,
        *,
        edge_type: str | None = None,
        weight: float | None = None,
        **attrs: CacheValue,
    ) -> Model:
        """Create an edge from *source* to *target* through the edge model.

        The row is written with ``edge_model.create(...)`` unconditionally —
        the facade already owns the edge model, so there is no fallback path.
        ``namespace`` is written when the edge model declares the column;
        ``edge_type`` / ``weight`` are written only when requested and only
        when the corresponding field exists. Extra *attrs* must name real
        edge-model fields.

        Args:
            source: Source node (facade node model, facade namespace,
                persisted).
            target: Target node with the same requirements.
            edge_type: Optional relationship type, written to
                ``edge_type_field``.
            weight: Optional edge weight, written to a ``weight`` field.
            **attrs: Extra edge-model fields, written verbatim.

        Returns:
            The created edge instance.

        Raises:
            GraphError: If a node has the wrong class, belongs to another
                namespace or is unpersisted; or if a requested field is absent
                from the edge model.
        """
        self._require_node(source, role="source")
        self._require_node(target, role="target")
        edge_fields = self._edge_model._meta.fields_map
        unknown = set(attrs) - set(edge_fields)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise GraphError(
                f"{self._edge_model.__name__} has no field(s) {names}; "
                "pass only fields the edge model declares."
            )
        payload: dict[str, CacheValue] = dict(attrs)
        if "namespace" in edge_fields:
            payload["namespace"] = self._namespace
        if edge_type is not None:
            if self._edge_type_field not in edge_fields:
                raise GraphError(
                    f"{self._edge_model.__name__} has no "
                    f"{self._edge_type_field!r} field; omit edge_type or use "
                    "an edge model that declares it."
                )
            payload[self._edge_type_field] = edge_type
        if weight is not None:
            if "weight" not in edge_fields:
                raise GraphError(
                    f"{self._edge_model.__name__} has no 'weight' field; "
                    "omit weight or use an edge model that declares it."
                )
            payload["weight"] = weight
        payload[self._source_field] = cast(CacheValue, source.pk)
        payload[self._target_field] = cast(CacheValue, target.pk)
        return await self._edge_model.create(using_db=None, **payload)

    async def unlink(
        self,
        source: ModelT,
        target: ModelT,
        *,
        edge_type: str | None = None,
    ) -> int:
        """Delete edges from *source* to *target* and return the row count.

        Deletion is directed: only rows whose source field is *source* and
        whose target field is *target* are removed. ``namespace`` and
        ``edge_type`` narrow the filter when the edge model declares them.

        Args:
            source: Source node (facade node model, facade namespace,
                persisted).
            target: Target node with the same requirements.
            edge_type: Only delete edges of this type.

        Returns:
            Number of deleted edge rows.

        Raises:
            GraphError: If a node has the wrong class, belongs to another
                namespace or is unpersisted; or if ``edge_type`` is requested
                on an edge model without the configured field.
        """
        self._require_node(source, role="source")
        self._require_node(target, role="target")
        edge_fields = self._edge_model._meta.fields_map
        filters: dict[str, RowValue | list[RowValue]] = {
            self._source_field: cast(RowValue, source.pk),
            self._target_field: cast(RowValue, target.pk),
        }
        if "namespace" in edge_fields:
            filters["namespace"] = self._namespace
        if edge_type is not None:
            if self._edge_type_field not in edge_fields:
                raise GraphError(
                    f"{self._edge_model.__name__} has no "
                    f"{self._edge_type_field!r} field; omit edge_type or use "
                    "an edge model that declares it."
                )
            filters[self._edge_type_field] = edge_type
        deleted = await self._edge_model.filter(**filters).delete()
        return int(deleted)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def neighbours(
        self,
        node: ModelT,
        *,
        edge_type: str | None = None,
        direction: Literal["outgoing", "incoming", "both"] = "both",
    ) -> list[ModelT]:
        """Return the direct neighbours of *node* as model instances.

        Delegates to :meth:`GraphTraversal.neighbors` (one hop) and hydrates
        the returned rows through the node model. When the node model
        declares a ``namespace`` column the hydrated result is scoped to the
        facade namespace; the CTE walk itself has no namespace predicate.

        Args:
            node: Starting node (facade node model, facade namespace,
                persisted).
            edge_type: Only follow edges of this type.
            direction: ``"outgoing"``, ``"incoming"`` or ``"both"``.

        Returns:
            Hydrated neighbour nodes, closest first (ties by name).

        Raises:
            GraphError: If *node* fails validation, ``edge_type`` is requested
                while a non-standard ``edge_type_field`` is configured (the
                traversal builders filter the ``edge_type`` column), or
                *direction* is unsupported.
        """
        self._require_node(node, role="node")
        if edge_type is not None and self._edge_type_field != "edge_type":
            raise GraphError(
                "edge_type filtering through the traversal builders requires "
                f"the standard 'edge_type' column; this Graph is configured "
                f"with edge_type_field={self._edge_type_field!r}."
            )
        rows = await self._traversal.neighbors(
            node.pk,
            direction=direction,
            edge_type=edge_type,
            max_depth=1,
        )
        return await self._hydrate([row.id for row in rows], scoped=True)

    async def shortest_path(
        self,
        source: ModelT,
        target: ModelT,
        *,
        max_depth: int | None = None,
    ) -> list[ModelT]:
        """Return the shortest path from *source* to *target*, hydrated.

        Delegates to :func:`~tortoise_extended.expressions.pathfinding.shortest_path`
        and loads the path rows into model instances in path order. The
        builder has no namespace predicate, so the path may cross nodes of
        other namespaces; filtering would break contiguity.

        Args:
            source: Starting node (facade node model, facade namespace,
                persisted).
            target: Destination node with the same requirements.
            max_depth: Maximum path length in hops; ``None`` uses the
                builder default (6).

        Returns:
            Nodes from *source* to *target* inclusive, or an empty list when
            no path exists.

        Raises:
            GraphError: If a node has the wrong class, belongs to another
                namespace or is unpersisted.
        """
        self._require_node(source, role="source")
        self._require_node(target, role="target")
        max_hops = 6 if max_depth is None else max_depth
        rows = await _shortest_path(
            self._node_model,
            self._edge_model,
            from_id=source.pk,
            to_id=target.pk,
            max_hops=max_hops,
            source_field=self._source_field,
            target_field=self._target_field,
        )
        if not rows:
            return []
        ids = [cast(int | str | UUID, row["id"]) for row in rows]
        return await self._hydrate(ids)

    async def find_cycles(self) -> list[list[ModelT]]:
        """Return every simple cycle in the graph, hydrated.

        Delegates to :func:`~tortoise_extended.expressions.pathfinding.find_cycles`
        with the builder defaults (maximum depth 10, at most 100 cycles) and
        loads each cycle's nodes into model instances. Cycles do not repeat
        their closing node.

        Returns:
            One node list per cycle; empty when the graph is acyclic.

        Raises:
            GraphError: If the edge model no longer declares the configured
                source/target fields (the builder validates on every call).
        """
        cycles = await _find_cycles(
            self._node_model,
            self._edge_model,
            source_field=self._source_field,
            target_field=self._target_field,
        )
        return [
            await self._hydrate([cast(int | str | UUID, row["id"]) for row in cycle])
            for cycle in cycles
        ]

    async def search(
        self,
        vector: Sequence[float],
        *,
        seed: ModelT,
        k: int = 10,
        max_hops: int = 2,
        metric: Literal["cosine", "l2", "inner_product"] = "cosine",
        min_distance: float | None = None,
    ) -> list[GraphVectorHit[ModelT]]:
        """Find vector-similar nodes reachable from *seed* within *max_hops*.

        Delegates to :class:`GraphVectorSearch`: one SQL statement walks the
        graph from the seed and ranks the reached nodes by their distance to
        *vector*. ``seed`` is required — a graph + vector search has no
        meaning without a start node, and the facade does not invent a
        vector-only mode.

        Args:
            vector: Query embedding (a float sequence or a pgvector literal
                string such as ``"[0.1,0.2]"``).
            seed: Node the traversal starts from (facade node model, facade
                namespace, persisted).
            k: Maximum number of hits to return.
            max_hops: Maximum traversal depth from the seed (0 = seed only).
            metric: ``"cosine"``, ``"l2"`` or ``"inner_product"``.
            min_distance: Optional distance threshold — ``<=`` for
                ``cosine``/``l2``, ``>=`` for ``inner_product``.

        Returns:
            Typed hits ordered by similarity (best first); each hit wraps a
            hydrated node instance plus ``distance`` and ``hops``.

        Raises:
            GraphError: If *seed* has the wrong class, belongs to another
                namespace or is unpersisted; or if the node model lacks the
                vector field (``embedding``) or *metric* is unsupported.
        """
        self._require_node(seed, role="seed")
        return await GraphVectorSearch(
            node_model=self._node_model,
            edge_model=self._edge_model,
            query_vector=list(vector),
            seed_id=seed.pk,
            max_hops=max_hops,
            distance_metric=metric,
            max_results=k,
            min_distance=min_distance,
            source_field=self._source_field,
            target_field=self._target_field,
        ).search()
