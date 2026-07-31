"""Graph edge base class for typed relationships.

Provides GraphEdge base class for edges between graph nodes with types and weights.
Requires: PostgreSQL + Tortoise ORM

Usage::

    from tortoise import models, fields
    from tortoise_extended.graph.edge import GraphEdge

    class Relationship(GraphEdge):
        properties = fields.JSONField(default=dict)

        class Meta:
            table = "relationships"
"""

from typing import TYPE_CHECKING, Self, override

from tortoise import fields
from tortoise.models import Model

from tortoise_extended._types import LibraryAny

if TYPE_CHECKING:
    from tortoise.queryset import QuerySet


class GraphEdge(Model):
    """Base class for graph edges (relationships between nodes).

    Features:
    - source_id and target_id for directed edges
    - edge_type for categorization (e.g., "parent_of", "references")
    - weight for weighted edges
    - properties for arbitrary edge metadata
    - namespace for multi-tenancy
    - bidirectional flag for undirected edges

    Usage::

        class Relationship(GraphEdge):
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
        source_id: str,
        target_id: str,
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
        filters: dict[str, LibraryAny] = {  # pyright: ignore[reportExplicitAny]
            "source_id": source_id,
            "target_id": target_id,
            "namespace": namespace,
        }
        if edge_type is not None:
            filters["edge_type"] = edge_type
        return cls.filter(**filters)

    @classmethod
    def between_any(
        cls,
        node_id: str,
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
        source_id: str,
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
        filters: dict[str, LibraryAny] = {  # pyright: ignore[reportExplicitAny]
            "source_id": source_id,
            "namespace": namespace,
        }
        if edge_type is not None:
            filters["edge_type"] = edge_type
        return cls.filter(**filters).order_by("created_at")

    @classmethod
    def incoming(
        cls,
        target_id: str,
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
        filters: dict[str, LibraryAny] = {  # pyright: ignore[reportExplicitAny]
            "target_id": target_id,
            "namespace": namespace,
        }
        if edge_type is not None:
            filters["edge_type"] = edge_type
        return cls.filter(**filters).order_by("created_at")

    @property
    def is_self_loop(self) -> bool:
        """Check if this edge is a self-loop."""
        return self.source_id == self.target_id
