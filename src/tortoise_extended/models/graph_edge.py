"""Graph edge base model for typed relationships.

Provides BaseGraphEdgeModel base class for edges between graph nodes with types and weights.
Requires: PostgreSQL + Tortoise ORM

Usage::

    from tortoise import models, fields
    from tortoise_extended.models.graph_edge import BaseGraphEdgeModel

    class Relationship(BaseGraphEdgeModel):
        properties = fields.JSONField(default=dict)

        class Meta:
            table = "relationships"
            # Tortoise does NOT inherit Meta.indexes from the abstract base —
            # redeclare them on every concrete subclass.
            indexes = (
                ("source_id", "edge_type"),
                ("target_id", "edge_type"),
                ("source_id", "target_id", "edge_type"),
            )
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Self, cast, override
from uuid import UUID, uuid4

from tortoise import fields
from tortoise.models import Model

if TYPE_CHECKING:
    from tortoise.queryset import QuerySet


class BaseGraphEdgeModel(Model):
    """Base class for graph edges (relationships between nodes).

    Features:
    - source_id and target_id for directed edges
    - edge_type for categorization (e.g., "parent_of", "references")
    - weight for weighted edges
    - properties for arbitrary edge metadata
    - namespace for multi-tenancy
    - bidirectional flag for undirected edges

    Orphan policy:
    ``source_id`` / ``target_id`` are bare columns with no ``ON DELETE``
    clause.  Deleting a node does not delete edges that reference it — the
    edges stay in place with a dangling endpoint.  This is deliberate for
    polymorphic graphs where node types may differ, so no automatic cascade
    is performed.  Use ``between_any`` / ``outgoing`` / ``incoming`` to find
    affected edges before deleting a node.

    Usage::

        class Relationship(BaseGraphEdgeModel):
            properties = fields.JSONField(default=dict)

            class Meta:
                table = "relationships"

        # Create relationship
        rel = await Relationship.create(
            source=node1,
            target=node2,
            edge_type="parent_of",
            weight=1.0,
        )
    """

    id = fields.UUIDField(
        primary_key=True,
        default=uuid4,
        description="Unique identifier for the edge",
    )
    source_id = fields.UUIDField(
        description="Source node ID (from)",
        db_index=True,
    )
    target_id = fields.UUIDField(
        description="Target node ID (to)",
        db_index=True,
    )
    edge_type = fields.CharField(
        max_length=50,
        description="Type of relationship (e.g., parent_of, references)",
        db_index=True,
    )
    weight = fields.FloatField(
        default=1.0,
        description="Edge weight for weighted algorithms",
    )
    properties = fields.JSONField(
        default=dict,
        description="Arbitrary metadata for the edge",
    )
    namespace = fields.CharField(
        max_length=100,
        default="default",
        description="Namespace for multi-tenancy",
        db_index=True,
    )
    is_bidirectional = fields.BooleanField(
        default=False,
        description="True if this edge is undirected (bidirectional)",
    )
    created_at = fields.DatetimeField(
        auto_now_add=True,
        description="Creation timestamp",
    )
    updated_at = fields.DatetimeField(
        auto_now=True,
        description="Last update timestamp",
    )

    class Meta:
        table = "graph_edges"
        verbose_name = "Graph Edge"
        verbose_name_plural = "Graph Edges"
        abstract = True
        indexes = (
            ("source_id", "edge_type"),
            ("target_id", "edge_type"),
            ("source_id", "target_id", "edge_type"),
        )

    def __init_subclass__(cls, **kwargs: str | int | float | bool | None) -> None:
        """Guard against silently losing the abstract base indexes.

        Tortoise does not propagate ``Meta.indexes`` from abstract bases to
        concrete subclasses — a subclass that forgets to redeclare them would
        run every edge query without its composite indexes.  Raise at
        class-creation time instead.

        Raise:
            NotImplementedError: When a concrete subclass (or one without an
                explicit ``Meta``) does not declare ``Meta.indexes``.  Opt
                out deliberately with ``Meta.indexes = ()``.
        """
        super().__init_subclass__(**kwargs)
        meta = cls.__dict__.get("Meta")
        if meta is None:
            raise NotImplementedError(
                f"{cls.__name__} must declare a Meta class with table and indexes. "
                "Tortoise does not propagate Meta.indexes from abstract bases; "
                "redeclare the edge indexes ((source_id, edge_type), "
                "(target_id, edge_type), (source_id, target_id, edge_type)) "
                "on every concrete subclass."
            )
        if getattr(meta, "abstract", False):
            return
        if "indexes" not in meta.__dict__:
            raise NotImplementedError(
                f"{cls.__name__}.Meta must redeclare indexes — Tortoise does not "
                "propagate Meta.indexes from abstract bases. Add the edge indexes "
                "((source_id, edge_type), (target_id, edge_type), "
                "(source_id, target_id, edge_type)) or opt out explicitly "
                "with indexes = ()."
            )

    @override
    def __str__(self) -> str:
        return f"{self.source_id} --[{self.edge_type}]--> {self.target_id}"

    @override
    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}("
            f"source={self.source_id}, "
            f"target={self.target_id}, "
            f"type={self.edge_type!r})>"
        )

    @classmethod
    def between(
        cls,
        source_id: UUID,
        target_id: UUID,
        edge_type: str | None = None,
        namespace: str = "default",
    ) -> QuerySet[Self]:
        """Get edges between two nodes.

        Args:
            source_id: Source node ID
            target_id: Target node ID
            edge_type: Optional edge type filter
            namespace: Namespace filter

        Returns:
            QuerySet of edges matching the criteria
        """
        filters: dict[str, str | UUID] = {
            "source_id": source_id,
            "target_id": target_id,
            "namespace": namespace,
        }
        if edge_type is not None:
            filters["edge_type"] = edge_type
        filter_method = cast(Callable[..., "QuerySet[Self]"], cls.filter)
        return filter_method(**filters)

    @classmethod
    def between_any(
        cls,
        node_id: UUID,
        edge_type: str | None = None,
        namespace: str = "default",
    ) -> QuerySet[Self]:
        """Get edges where node is source OR target (undirected).

        Args:
            node_id: Node ID to search from
            edge_type: Optional edge type filter
            namespace: Namespace filter

        Returns:
            QuerySet of edges involving the node
        """
        from tortoise.expressions import Q

        q_source = Q(source_id=node_id, namespace=namespace)
        q_target = Q(target_id=node_id, namespace=namespace)
        filters = q_source | q_target
        if edge_type is not None:
            filters &= Q(edge_type=edge_type)
        return cls.filter(filters)

    @classmethod
    def outgoing(
        cls,
        source_id: UUID,
        edge_type: str | None = None,
        namespace: str = "default",
    ) -> QuerySet[Self]:
        """Get outgoing edges from a node.

        Args:
            source_id: Source node ID
            edge_type: Optional edge type filter
            namespace: Namespace filter

        Returns:
            QuerySet of outgoing edges
        """
        filters: dict[str, str | UUID] = {
            "source_id": source_id,
            "namespace": namespace,
        }
        if edge_type is not None:
            filters["edge_type"] = edge_type
        filter_method = cast(Callable[..., "QuerySet[Self]"], cls.filter)
        return filter_method(**filters).order_by("created_at")

    @classmethod
    def incoming(
        cls,
        target_id: UUID,
        edge_type: str | None = None,
        namespace: str = "default",
    ) -> QuerySet[Self]:
        """Get incoming edges to a node.

        Args:
            target_id: Target node ID
            edge_type: Optional edge type filter
            namespace: Namespace filter

        Returns:
            QuerySet of incoming edges
        """
        filters: dict[str, str | UUID] = {
            "target_id": target_id,
            "namespace": namespace,
        }
        if edge_type is not None:
            filters["edge_type"] = edge_type
        filter_method = cast(Callable[..., "QuerySet[Self]"], cls.filter)
        return filter_method(**filters).order_by("created_at")

    @property
    def is_self_loop(self) -> bool:
        """Check if this edge is a self-loop."""
        return self.source_id == self.target_id
